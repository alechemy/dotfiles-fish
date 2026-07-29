#!/usr/bin/python3
"""Entity Review — the web UI over the entity-filing backlog.

Serves the candidate + proposal queue as a single-page app and records
decisions through the exact primitives the 30-minute tick consumes:
candidate decisions are group moves plus the TrackTarget/CreateDistinct
metadata gestures, proposal approval is a move into _Review/Approved
(optionally after regenerating the ops fence from structured edits — the
_approve_completed trust path minus the note grammar). After a decision a
debounced `entity-filing.py --apply-only` run applies it, so the queue
clears in seconds instead of waiting for the tick.

Loopback-only; the tailnet reaches it through Caddy's /entities route
(which stamps X-Entity-Review). All DEVONthink I/O goes through
entity-dt-bridge.js; candidate writes hold the shared candidates lock.
"""

import fcntl
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

BIN = os.path.dirname(os.path.realpath(__file__))
if BIN not in sys.path:
    sys.path.insert(0, BIN)

from pipeline_log import setup as setup_log
import entity_candidates as ec


def _load(filename, module_name):
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = os.path.join(BIN, filename)
    spec = importlib.util.spec_from_file_location(
        module_name, path,
        loader=importlib.machinery.SourceFileLoader(module_name, path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ef = _load("entity-filing.py", "entity_filing")
log = setup_log("entity-review")

HOST = "127.0.0.1"
DEFAULT_PORT = 7819
ASSET = os.path.expanduser("~/.local/share/entity-review/index.html")
PROXY_HEADER = "X-Entity-Review"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
APPLY_DEBOUNCE_SECONDS = 20
APPLY_RETRY_SECONDS = 60
APPLY_MAX_ATTEMPTS = 5
MAX_BODY_BYTES = 256 * 1024
READ_TIMEOUT = 120
CSP = ("default-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'self'; base-uri 'self'")

NOTICE_RE = re.compile(r"^\*\*Needs attention:\*\* (.*)$", re.MULTILINE)
SOURCE_LINE_RE = re.compile(
    r"^Source: \[(?P<name>.*)\]\(x-devonthink-item://(?P<uuid>[A-Za-z0-9-]+)\)"
    r" \((?P<date>\d{4}-\d{2}-\d{2})\)$", re.MULTILINE)
CAPTURED_RE = re.compile(
    r"^## Captured text\n\n(.*?)\n\n## Ops\n", re.MULTILINE | re.DOTALL)
UUID_RE = re.compile(r"^[A-Za-z0-9-]{8,}$")


# ---------------------------------------------------------------------------
# Queue views
# ---------------------------------------------------------------------------


def fetch_snapshot(bridge=None):
    run = bridge or ef.run_bridge
    res = run([
        {"op": "list_candidates"},
        {"op": "list_review"},
        {"op": "dump_people", "include_bodies": False},
    ])
    return res[0], res[1], res[2]


def candidate_disposition(data, md, people, index):
    """Structured form of the promotion preflight, so the card can render
    the one primary action promotion would actually take. promotion_target
    stays the authority; the per-key hits only pick apart its bounce cases.
    `choices` are the TrackTargets promotion would accept for a multi-hit
    candidate — empty for a conflation, which only a split can fix."""
    uuid, reason, near = ef.promotion_target(data, md, people, index)
    near_refs = []
    for name in near:
        hit = index.get(ec.norm(name)) or []
        near_refs.append({"name": name,
                          "uuid": hit[0]["uuid"] if len(hit) == 1 else ""})
    if uuid is not None:
        by_uuid = {p["uuid"]: p for p in people}
        target = by_uuid.get(uuid) or {}
        return {"kind": "file_into", "near": near_refs,
                "target": {"uuid": uuid, "name": target.get("name", "")}}
    if reason is None:
        return {"kind": "new_ok", "near": near_refs}
    by_key = ef.identifier_hits(data, index)
    hits = {p["uuid"]: p for kh in by_key.values() for p in kh}
    if len(hits) > 1:
        choices = sorted(ef.resolvable_targets(by_key),
                         key=lambda u: hits[u]["name"])
        return {"kind": "ambiguous", "near": near_refs, "reason": reason,
                "hits": [{"uuid": p["uuid"], "name": p["name"]}
                         for p in hits.values()],
                "choices": [{"uuid": u, "name": hits[u]["name"]}
                            for u in choices]}
    return {"kind": "needs_choice", "near": near_refs, "reason": reason}


def candidate_view(entry, people, index, peers_by_name):
    try:
        data = ec.parse_candidate(entry["text"])
    except ValueError:
        return {"uuid": entry["uuid"], "name": entry["name"], "broken": True}
    dates = ec.sighting_dates(data)
    sightings = []
    ordered = sorted(data["sightings"].items(),
                     key=lambda kv: (kv[1].get("date", ""), kv[0]))
    for sid, s in ordered:
        sightings.append({
            "date": s.get("date", ""),
            "kind": s.get("kind", ""),
            "evidence": s.get("evidence", ""),
            "title": s.get("title") or s.get("name") or "",
            "source_uuid": sid[3:] if sid.startswith("dt:") else "",
            "facts": [[d, t] for d, t in (s.get("facts") or [])],
            "updates": dict(s.get("updates") or {}),
        })
    notice = NOTICE_RE.search(entry["text"])
    name = data["name"]
    peers = [p for p in peers_by_name.get(ec.norm(name), [])
             if p["uuid"] != entry["uuid"]]
    return {
        "uuid": entry["uuid"],
        "name": name,
        "broken": False,
        "urgent": bool(data.get("urgent")),
        "detached": bool(data.get("detached")),
        "notice": notice.group(1) if notice else "",
        "emails": list(data["emails"]),
        "variants": [v for v in data["name_variants"]
                     if ec.norm(v) != ec.norm(name)],
        "seen": {"count": len(data["sightings"]),
                 "last": dates[-1] if dates else ""},
        "sightings": sightings,
        "peers": peers,
        "disposition": candidate_disposition(data, entry.get("md") or {},
                                             people, index),
    }


def _plan_view(plan):
    out = {"kind": plan["kind"], "name": plan["name"]}
    if plan["kind"] == "event":
        out.update({"date": plan.get("date", ""),
                    "location": plan.get("location", ""),
                    "attendees": list(plan.get("attendees") or []),
                    "summary": plan.get("summary", "")})
        return out
    out.update({"interacted": bool(plan.get("interacted")),
                "facts": [[d, t] for d, t in plan.get("facts", [])],
                "updates": dict(plan.get("updates") or {})})
    return out


def proposal_view(entry, people):
    text = entry["text"]
    try:
        ops = ef.proposal_ops(text)
    except ValueError:
        ops = None
    if ops is None:
        return {"uuid": entry["uuid"], "title": entry["name"], "broken": True}
    plans, editable, source_uuid = ef.plans_from_ops(ops, people)
    m = SOURCE_LINE_RE.search(text)
    if m:
        source = {"uuid": m.group("uuid"), "name": m.group("name"),
                  "date": m.group("date")}
    else:
        source = {"uuid": source_uuid or "", "name": entry["name"], "date": ""}
    empty = not ops
    captured = ""
    if empty:
        cm = CAPTURED_RE.search(text)
        captured = cm.group(1).strip() if cm else ""
    return {
        "uuid": entry["uuid"],
        "title": entry["name"],
        "broken": False,
        "source": source,
        "plans": [_plan_view(p) for p in plans],
        "editable": bool(editable) and not empty,
        "empty": empty,
        "captured": captured,
    }


def build_queue(cand_listing, review_listing, people):
    index = ef.roster_index(people)
    pending = cand_listing.get("pending") or []
    peers_by_name = {}
    for entry in pending:
        try:
            data = ec.parse_candidate(entry["text"])
        except ValueError:
            continue
        peers_by_name.setdefault(ec.norm(data["name"]), []).append(
            {"uuid": entry["uuid"], "name": data["name"]})
    candidates = [candidate_view(e, people, index, peers_by_name)
                  for e in pending]
    candidates.sort(key=lambda c: (c.get("broken", False),
                                   not c.get("urgent", False),
                                   c.get("seen", {}).get("last", "")))
    proposals = [proposal_view(e, people)
                 for e in review_listing.get("pending") or []]
    roster = sorted(
        ({"uuid": p["uuid"], "name": p["name"],
          "aliases": [a.strip() for a in (p.get("aliases") or "").split(",")
                      if a.strip()]}
         for p in people),
        key=lambda p: p["name"].casefold())
    return {
        "candidates": candidates,
        "proposals": proposals,
        "roster": roster,
        "queued": {"candidates": len(cand_listing.get("approved") or []),
                   "proposals": len(review_listing.get("approved") or [])},
    }


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class RequestError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def candidate_decision_ops(action, uuid, target="", distinct=False):
    if target and distinct:
        raise RequestError("choose an existing person or a new one, not both")
    if target and not UUID_RE.match(target):
        raise RequestError("bad target")
    if action == "track":
        return [
            {"op": "set_fields", "uuid": uuid,
             "fields": {"TrackTarget": target or "",
                        "CreateDistinct": 1 if distinct else 0}},
            {"op": "move_to", "uuid": uuid,
             "group": ec.CANDIDATES_APPROVED_PATH},
        ]
    if action == "ignore":
        return [{"op": "move_to", "uuid": uuid,
                 "group": ec.CANDIDATES_IGNORED_PATH}]
    if action == "forget":
        return [{"op": "trash", "uuid": uuid}]
    if action == "undo":
        return [
            {"op": "set_fields", "uuid": uuid,
             "fields": {"TrackTarget": "", "CreateDistinct": 0}},
            {"op": "move_to", "uuid": uuid, "group": ec.CANDIDATES_PATH},
        ]
    raise RequestError("unknown action")


def candidate_rename_ops(uuid, new_name, text, people):
    """set_text/set_name ops correcting a candidate's canonical name before
    tracking — the old name stays a variant, so promotion aliases it and
    future sightings still resolve. Empty when the name is unchanged."""
    new_name = ef.collapse_ws(str(new_name))
    if not new_name or len(new_name) > 120:
        raise RequestError("bad name")
    try:
        data = ec.parse_candidate(text)
    except ValueError:
        raise RequestError("this record can't be read — it needs a look in "
                           "DEVONthink", 409)
    if ec.norm(new_name) == ec.norm(data["name"]):
        return []
    data["name_variants"] = [new_name] + [
        v for v in data["name_variants"] if ec.norm(v) != ec.norm(new_name)]
    data["name"] = new_name
    return [
        {"op": "set_text", "uuid": uuid,
         "text": ec.render_candidate(data, ec.near_matches(new_name, people))},
        {"op": "set_name", "uuid": uuid, "name": ec.record_name(data)},
    ]


def proposal_decision_ops(action, uuid):
    if action == "approve":
        return [{"op": "move_to", "uuid": uuid, "group": ef.APPROVED_PATH}]
    if action == "reject":
        return [{"op": "trash", "uuid": uuid}]
    if action == "undo":
        return [{"op": "move_to", "uuid": uuid, "group": ef.REVIEW_PATH}]
    raise RequestError("unknown action")


def spec_from_payload(payload):
    """people/events in parse_things_note's shape from the SPA's structured
    edit, enforcing the same hard limits the note grammar enforces — an
    edit must never be half-applied."""
    people_ext, events_ext = [], []
    for p in payload.get("people") or []:
        name = str(p.get("name", "")).strip()
        if not name:
            raise RequestError("a person entry has no name")
        if any(ec.norm(q["name"]) == ec.norm(name) for q in people_ext):
            raise RequestError(f"duplicate person: {name}")
        facts = []
        for f in p.get("facts") or []:
            d = ef.valid_date(str(f.get("date", "")))
            t = str(f.get("fact", "")).strip()
            if not d:
                raise RequestError(f"bad fact date for {name}")
            if not t or len(t) > 400:
                raise RequestError(f"fact for {name} is empty or too long")
            facts.append({"date": d, "fact": t})
        if len(facts) > 12:
            raise RequestError(f"more than 12 facts for {name}")
        updates = {}
        for field, value in (p.get("updates") or {}).items():
            field = str(field).lower()
            value = str(value or "").strip()
            if field not in ef.UPDATE_FIELDS:
                raise RequestError(f"unknown field {field!r} for {name}")
            if value:
                updates[field] = value
        if not facts and not updates:
            continue
        people_ext.append({"name": name, "match": None,
                           "interacted": bool(p.get("interacted")),
                           "facts": facts, "updates": updates})
    for e in payload.get("events") or []:
        name = str(e.get("name", "")).strip()
        if not name or len(name) > 80:
            raise RequestError("an event entry has no name, or one over 80 "
                               "characters")
        d = ef.valid_date(str(e.get("date", "")))
        if not d:
            raise RequestError(f"bad date for event {name}")
        attendees = [str(a).strip() for a in e.get("attendees") or []
                     if str(a).strip()]
        if len(attendees) > 20:
            raise RequestError(f"more than 20 attendees for {name}")
        summary = str(e.get("summary") or "").strip()
        if len(summary) > 300:
            raise RequestError(f"summary too long for {name}")
        events_ext.append({"name": name, "date": d,
                           "location": str(e.get("location") or "").strip()
                           or None,
                           "attendees": attendees, "summary": summary or None})
    if not people_ext and not events_ext:
        raise RequestError("nothing left to file — reject instead")
    return people_ext, events_ext


def approve_with_edits(uuid, payload, confirm, bridge=None):
    """Regenerate the ops fence from a structured edit and approve — the
    _approve_completed trust path with the SPA's form state standing in for
    the parsed note."""
    run = bridge or ef.run_bridge
    text = run([{"op": "get_text", "uuid": uuid}])[0]["text"]
    ops = ef.proposal_ops(text)
    if not ops:
        raise RequestError("this proposal can't be edited — approve or "
                           "reject it as-is", 409)
    people = run([{"op": "dump_people", "include_bodies": False}])[0]
    plans0, editable, _ = ef.plans_from_ops(ops, people)
    if not editable:
        raise RequestError("this proposal can't be edited — approve or "
                           "reject it as-is", 409)
    source_uuid = next((op.get("uuid") for op in ops
                        if op.get("op") == "mark_filed"), None)
    if not source_uuid:
        raise RequestError("the proposal has no source record — review it "
                           "in DEVONthink", 409)
    source = run([{"op": "get_source", "uuid": source_uuid}])[0]
    source_date = ef.source_date_of(source)
    people_ext, events_ext = spec_from_payload(payload)
    index = ef.roster_index(people)
    selves = ef.self_names(ef.load_config())
    plans = ef.build_person_plans(people_ext, index, selves, people,
                                  source_date)
    plans += ef.build_event_plans(events_ext, index, selves, source_date)
    if not plans:
        raise RequestError("nothing left to file — reject instead")
    ambiguous = [p for p in plans if p["kind"] == "ambiguous"]
    if ambiguous:
        detail = "; ".join(f"{p['name']} matches "
                           + ", ".join(p["candidates"]) for p in ambiguous)
        raise RequestError(f"ambiguous name — {detail}; pick the exact "
                           "person or edit the name", 409)
    new_ops = []
    for plan in plans:
        new_ops.extend(ef.ops_for_plan(plan, source, source_date))
    new_ops.append({"op": "mark_filed", "uuid": source_uuid})
    stale = ef.stale_person_ops(new_ops, index, people)
    if stale:
        if not confirm:
            return {"confirm": [{"name": n, "near": near} for n, near in stale]}
        confirmed = {ec.norm(n) for n, _ in stale}
        for op in new_ops:
            if op.get("op") == "ensure_person" \
                    and ec.norm(op.get("name", "")) in confirmed:
                op["confirm_new"] = True
    body = ef.proposal_body(source, source_date, plans, new_ops)
    run([{"op": "set_text", "uuid": uuid, "text": body},
         {"op": "move_to", "uuid": uuid, "group": ef.APPROVED_PATH}])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Apply worker
# ---------------------------------------------------------------------------


class ApplyScheduler:
    """Debounced background `--apply-only` runs. Purely event-driven: the
    worker thread exists only while an apply is pending, so an idle server
    holds no timers. A held run lock defers to a retry, and after
    APPLY_MAX_ATTEMPTS the 30-minute tick is the backstop."""

    def __init__(self):
        self._cond = threading.Condition()
        self._deadline = None
        self._thread = None
        self.state = "idle"
        self.last_result = ""
        self.last_finished = ""

    def kick(self, delay=APPLY_DEBOUNCE_SECONDS):
        with self._cond:
            self._deadline = time.monotonic() + delay
            self.state = "scheduled"
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work,
                                                daemon=True)
                self._thread.start()
            self._cond.notify()

    def status(self):
        with self._cond:
            return {"state": self.state, "last_result": self.last_result,
                    "last_finished": self.last_finished}

    def _lock_free(self):
        try:
            with open(ef.LOCK_FILE, "w") as fd:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except OSError:
            return False

    def _work(self):
        attempts = 0
        while True:
            with self._cond:
                if self._deadline is None:
                    self.state = "idle"
                    return
                wait = self._deadline - time.monotonic()
                if wait > 0:
                    self._cond.wait(timeout=wait)
                    continue
                if not self._lock_free():
                    attempts += 1
                    if attempts >= APPLY_MAX_ATTEMPTS:
                        log.info("apply deferred to the next filing tick "
                                 "(run lock stayed busy)")
                        self._deadline = None
                        self.state = "idle"
                        self.last_result = "deferred"
                        return
                    self._deadline = time.monotonic() + APPLY_RETRY_SECONDS
                    continue
                self._deadline = None
                self.state = "running"
            env = dict(os.environ, PIPELINE_MANUAL="1")
            try:
                proc = subprocess.run(
                    ["/usr/bin/python3", os.path.join(BIN, "entity-filing.py"),
                     "--apply-only"],
                    capture_output=True, text=True, timeout=1800, env=env)
                result = "ok" if proc.returncode == 0 else "error"
                if proc.returncode != 0:
                    log.warning("apply run failed (rc=%d): %s",
                                proc.returncode, proc.stderr.strip()[-500:])
            except Exception as exc:
                result = "error"
                log.warning("apply run failed: %s", exc)
            with self._cond:
                self.last_result = result
                self.last_finished = datetime.now().isoformat(
                    timespec="seconds")
                if self._deadline is None:
                    self.state = "idle"
                    return
            attempts = 0


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def gate(headers, method):
    """'' when the request may proceed, else a refusal reason. Host pins
    direct traffic to loopback names (DNS rebinding can't fake those), the
    Caddy marker header admits proxied tailnet traffic, and a foreign
    Origin on a POST is CSRF from a browser tab — refuse it."""
    host = (headers.get("Host") or "").split(":")[0].strip().lower()
    if host not in ALLOWED_HOSTS and not headers.get(PROXY_HEADER):
        return "unrecognized host"
    if method == "POST":
        origin = headers.get("Origin") or ""
        if origin:
            ohost = (urlsplit(origin).hostname or "").lower()
            if ohost != host and ohost not in ALLOWED_HOSTS:
                return "cross-origin request"
        if (headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return "cross-site request"
    return ""


scheduler = ApplyScheduler()


def _empty_queue(dt):
    return {"candidates": [], "proposals": [], "roster": [],
            "queued": {"candidates": 0, "proposals": 0}, "dt": dt}


def handle_queue():
    try:
        cand, review, people = fetch_snapshot()
        queue = build_queue(cand, review, people)
        queue["dt"] = "ok"
    except ef.BridgeUnavailable:
        queue = _empty_queue("closed")
    except Exception as exc:
        log.warning("queue read failed: %s", exc)
        queue = _empty_queue("error")
    queue["apply"] = scheduler.status()
    return queue


def handle_candidate(uuid, payload):
    action = str(payload.get("action", ""))
    target = str(payload.get("target", "") or "")
    new_name = str(payload.get("name", "") or "").strip()
    if new_name and (action != "track" or target):
        raise RequestError("a corrected name applies when adding a new "
                           "person, not here")
    ops = candidate_decision_ops(action, uuid, target=target,
                                 distinct=bool(payload.get("distinct")))
    lock_fd = ec.acquire_candidates_lock()
    try:
        if new_name:
            state = ef.run_bridge([
                {"op": "get_text", "uuid": uuid},
                {"op": "dump_people", "include_bodies": False},
            ])
            ops = candidate_rename_ops(uuid, new_name, state[0]["text"],
                                       state[1]) + ops
        ef.run_bridge(ops)
    finally:
        lock_fd.close()
    log.info("candidate decision: %s", action,
             extra={"record_uuid": uuid})
    if action != "undo":
        scheduler.kick()
    return {"ok": True}


def handle_proposal(uuid, payload):
    action = str(payload.get("action", ""))
    if action == "approve" and (payload.get("people") is not None
                                or payload.get("events") is not None):
        result = approve_with_edits(uuid, payload,
                                    confirm=bool(payload.get("confirm")))
        if "confirm" in result:
            return result
    else:
        ef.run_bridge(proposal_decision_ops(action, uuid))
    log.info("proposal decision: %s", action, extra={"record_uuid": uuid})
    if action != "undo":
        scheduler.kick()
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "EntityReview"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(),
                   "application/json; charset=utf-8")

    def _refuse(self, reason):
        self._json(403, {"error": reason})

    def do_GET(self):
        reason = gate(self.headers, "GET")
        if reason:
            return self._refuse(reason)
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(ASSET, "rb") as f:
                    body = f.read()
            except OSError:
                return self._json(500, {"error": "index.html missing — "
                                        "restow the devonthink package"})
            return self._send(200, body, "text/html; charset=utf-8")
        if path == "/api/queue":
            return self._json(200, handle_queue())
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        # The body must be drained before any early response — an unread
        # body poisons this keep-alive connection (which Caddy pools and
        # reuses), making the leftover bytes parse as the next request's
        # method. Oversized/chunked bodies can't be drained, so those
        # responses close the connection instead.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES \
                or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return self._json(413, {"error": "body too large"})
        raw = self.rfile.read(length) if length else b""
        reason = gate(self.headers, "POST")
        if reason:
            return self._refuse(reason)
        path = urlsplit(self.path).path
        try:
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
        except ValueError:
            return self._json(400, {"error": "bad JSON"})
        try:
            if path == "/api/apply":
                scheduler.kick(0)
                return self._json(200, {"ok": True})
            if path == "/api/open-dt":
                subprocess.run(["/usr/bin/open", "-g", "-j", "-b",
                                "com.devon-technologies.think"], check=False)
                return self._json(200, {"ok": True})
            m = re.match(r"^/api/(candidate|proposal)/([A-Za-z0-9-]{8,})$",
                         path)
            if not m:
                return self._json(404, {"error": "not found"})
            if m.group(1) == "candidate":
                return self._json(200, handle_candidate(m.group(2), payload))
            return self._json(200, handle_proposal(m.group(2), payload))
        except RequestError as exc:
            return self._json(exc.code, {"error": str(exc)})
        except ef.BridgeUnavailable:
            return self._json(503, {"error": "devonthink-closed"})
        except RuntimeError as exc:
            log.warning("decision failed: %s", exc)
            return self._json(409, {"error": "that item was already handled "
                                    "— refresh the queue"})


def main():
    port = int(os.environ.get("ENTITY_REVIEW_PORT", DEFAULT_PORT))
    httpd = ThreadingHTTPServer((HOST, port), Handler)
    log.info("listening on http://%s:%d", HOST, port)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
