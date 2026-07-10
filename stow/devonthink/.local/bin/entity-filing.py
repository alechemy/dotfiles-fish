#!/usr/bin/python3
"""
entity-filing.py — AI filing step for the entity layer.

Scans processed pipeline documents (Granola meeting notes, handwritten
notes, past daily notes) for facts about people, resolves each mention
against Lorebook/20_ENTITIES/People, and files dated, provenance-linked
bullets into each person's `## Biographical Log`. The LLM only performs
the messy-text -> structured-JSON extraction; everything that writes to
DEVONthink is deterministic (entity-dt-bridge.js ops built here).

Safety model:
  - suggest mode (default): every extraction becomes a proposal record in
    /20_ENTITIES/_Review containing a human summary plus the exact ops as
    a fenced JSON block. Moving a proposal into _Review/Approved makes the
    next run apply it; deleting it rejects it. Nothing touches a Person
    record without review.
  - auto mode (FILING_MODE=auto): ops for unambiguous existing people
    apply immediately; new-person creations and ambiguous matches still
    become proposals. A permanent manual-review path, per the design doc.
  - Meeting attendance (GranolaParticipants) bumps LastContact for
    matched people deterministically on every scan — no LLM involved, so
    it applies in both modes. Dormant in practice: Granola reads a
    subscribed Google calendar, and Google strips attendee lists from
    one-way imports, so the field is always empty. Calendar-derived
    contact tracking lives in dt-morning-brief.py instead.
  - Extraction is gated on a seeded roster (MIN_ROSTER). The prompt's
    whole resolution step is the roster, and a source is only ever
    extracted once (its UUID lands in processed_ids), so extracting
    against an empty People group burns each source on a proposal full of
    unresolvable bare first names.
  - An approved proposal is re-verified against the *live* roster before
    its ops run: a frozen `ensure_person "Alison"` written before
    "Alison Vance" was seeded would otherwise create a duplicate record.

Privacy: daily notes live in /10_DAILY, which is excluded from
DEVONthink's AI chat by design. This script honors that boundary — daily
notes are only ever extracted through a local Ollama model, never through
DT chat (which may be a cloud provider). Meeting notes and handwritten
notes already flow through DT chat for enrichment, so either transport is
acceptable for them.

Config (~/.config/dt-pipeline/entities.conf, KEY=VALUE):
  TRANSPORT=auto|local|omlx|ollama|dtchat|off
                                     auto: omlx, else ollama, else dtchat.
                                     local: omlx, else ollama, never dtchat
  OMLX_MODEL=<name>                  model id as listed by /v1/models
  OMLX_URL=http://127.0.0.1:8000
  OMLX_API_KEY=<key>                 required when oMLX auth is enabled
                                     (Settings -> auth.api_key)
  OLLAMA_MODEL=<name>                required for the ollama transport
  OLLAMA_URL=http://127.0.0.1:11434
  FILING_MODE=suggest|auto           default suggest
  MAX_PER_RUN=<n>                    extraction budget per run, default 3
  MIN_ROSTER=<n>                     extract only once People holds at least
                                     this many records, default 1. Applying
                                     approved proposals and the attendance
                                     pass are never gated. TRANSPORT=off is
                                     the blunter pause: it stops extraction
                                     without touching processed_ids either.
  SELF_NAME=<name>                   extra self-alias to exclude
  SKIP_SOURCE_TITLES=<regex>         sources whose name matches are never
                                     extracted (recurring standups etc.);
                                     case-insensitive, unanchored
  IDLE_MINUTES=<n>                   run local (Ollama) extraction only when
                                     the user has been idle this long, so
                                     inference never spins fans mid-work;
                                     default 10, 0 disables the gate

Usage:
    entity-filing.py                 # launchd-driven scan + apply
    entity-filing.py --dry-run       # print planned ops, write nothing
    entity-filing.py --force UUID    # re-extract one source record
    entity-filing.py --apply-only    # only process _Review/Approved
    entity-filing.py --scan-only     # skip the apply phase
"""

import fcntl
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log

log = setup_log("entity-filing")

BRIDGE = os.path.expanduser("~/.local/bin/entity-dt-bridge.js")
CONFIG_FILE = os.path.expanduser("~/.config/dt-pipeline/entities.conf")
STATE_DIR = os.path.expanduser("~/.local/state/devonthink")
STATE_FILE = os.path.join(STATE_DIR, "entity-filing-state.json")
LOCK_FILE = os.path.join(STATE_DIR, "entity-filing.lock")
STATE_SCHEMA_VERSION = 1
REVIEW_PATH = "/20_ENTITIES/_Review"
APPROVED_PATH = "/20_ENTITIES/_Review/Approved"
MAX_ATTEMPTS = 5
UPDATE_FIELDS = ("employer", "role", "city", "email")
BUMP_WINDOW_DAYS = 60

CHAT_ROLE = (
    "You are a personal-CRM extraction assistant that responds only with JSON."
)

PROMPT_TEMPLATE = """\
Extract facts about PEOPLE from the note below. Respond with JSON only, in
exactly this shape:

{{"people": [{{"name": "<canonical full name>",
  "match": "<exact name from KNOWN PEOPLE, or null>",
  "facts": [{{"date": "yyyy-mm-dd or null", "fact": "<one concise sentence>"}}],
  "updates": {{"employer": null, "role": null, "city": null, "email": null}}}}],
 "events": [{{"name": "<short reusable title>", "date": "yyyy-mm-dd or null",
  "location": "<place name or null>", "attendees": ["<name>"],
  "summary": "<one sentence or null>"}}]}}

Rules:
- Only real individual humans the note's author personally interacted with or
  learned something about. No public figures mentioned in passing, no
  companies, no product or project names.
- Resolve pronouns and nicknames to one canonical person before extracting.
- Record durable biographical or relationship facts: job or role changes,
  moves, partner and family news, health, notable plans, how the author met
  them, significant personal things discussed WITH them. Skip meeting
  logistics, task assignments, and technical minutiae.
- Do NOT record workplace working style: tool preferences, how someone uses
  AI, how they run meetings, opinions on process, or what they said in a
  work discussion. A work fact belongs only when it changes their biography
  (new job, new role, promotion, leaving, relocation).
- When unsure whether a fact is durable and personally meaningful, omit it.
  Fewer, better facts. An empty list is a good answer for a technical note.
- "events" is for a distinct real-world occasion the note documents: a trip,
  celebration, milestone, or one-off gathering. Routine or recurring work
  meetings, standups, syncs, and 1:1 calls are NEVER events. Give it a short
  reusable title (e.g. "Portland Hiking Trip"). Most notes have no event —
  an empty "events" list is the normal answer.
- Set an "updates" value only when the note states that person's CURRENT
  employer, job role, home city, or email address; otherwise leave it null.
- If the note contains no such facts, return {{"people": []}}.
- The note is dated {source_date}; use that date for facts phrased in the
  present tense.

KNOWN PEOPLE (use these exact names in "match"; aliases in parentheses):
{roster}

NOTE ({source_name}):
{content}
"""

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "match": {"type": ["string", "null"]},
                    "facts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": ["string", "null"]},
                                "fact": {"type": "string"},
                            },
                            "required": ["fact"],
                        },
                    },
                    "updates": {
                        "type": "object",
                        "properties": {
                            f: {"type": ["string", "null"]} for f in UPDATE_FIELDS
                        },
                    },
                },
                "required": ["name", "facts"],
            },
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    "location": {"type": ["string", "null"]},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": ["string", "null"]},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["people", "events"],
}


# ---------------------------------------------------------------------------
# Config / state
# ---------------------------------------------------------------------------


def load_config():
    config = {
        "TRANSPORT": "auto",
        "OMLX_MODEL": "",
        "OMLX_URL": "http://127.0.0.1:8000",
        "OMLX_API_KEY": "",
        "OLLAMA_MODEL": "",
        "OLLAMA_URL": "http://127.0.0.1:11434",
        "FILING_MODE": "suggest",
        "MAX_PER_RUN": "3",
        "MIN_ROSTER": "1",
        "SELF_NAME": "",
        "SKIP_SOURCE_TITLES": "",
        "IDLE_MINUTES": "10",
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
    return config


def load_state():
    """Fail closed like the Granola importer: an unreadable state file must
    pause filing, not silently re-extract (and re-propose) every source."""
    if not os.path.exists(STATE_FILE):
        return {"version": STATE_SCHEMA_VERSION, "processed_ids": [], "attempts": {}}
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"State file {STATE_FILE} is unreadable ({exc}). Filing is paused "
            f"until the file is inspected and repaired or removed."
        ) from exc
    if (
        isinstance(data, dict)
        and data.get("version") == STATE_SCHEMA_VERSION
        and isinstance(data.get("processed_ids"), list)
    ):
        data.setdefault("attempts", {})
        return data
    raise RuntimeError(
        f"State file {STATE_FILE} has an unrecognized schema. Filing is "
        f"paused until the file is inspected and repaired or removed."
    )


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".entity-filing.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def acquire_lock():
    """Hold a non-blocking exclusive lock for the run's lifetime so a
    hand-looped --scan-only never interleaves with the 30-minute launchd
    tick — concurrent runs would duplicate proposals and drop each other's
    processed_ids (the state file is last-writer-wins). Returns the open fd
    (kept referenced to hold the lock) or None if another run holds it."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


# ---------------------------------------------------------------------------
# Bridge / transports
# ---------------------------------------------------------------------------


class BridgeUnavailable(RuntimeError):
    """DEVONthink is not answering or the Lorebook database is not open.

    Transient by nature (app relaunch, database still loading), so callers
    end the run quietly rather than charging a source an attempt for it.
    """


def run_bridge(ops, timeout=300):
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"ops": ops}, f)
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", BRIDGE, path],
            capture_output=True, text=True, timeout=timeout,
        )
    finally:
        os.unlink(path)
    if result.returncode != 0:
        raise RuntimeError(f"bridge failed: {result.stderr.strip()}")
    out = json.loads(result.stdout)
    if not out.get("ok"):
        if out.get("unavailable"):
            raise BridgeUnavailable(out.get("error"))
        raise RuntimeError(
            f"bridge op {out.get('failed_op')} failed: {out.get('error')}"
        )
    return out["results"]


def user_idle_seconds():
    try:
        out = subprocess.check_output(
            ["/usr/sbin/ioreg", "-c", "IOHIDSystem"], text=True)
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                return int(line.split("=")[-1].strip()) / 1_000_000_000
    except Exception:
        pass
    return None


_availability_cache = {}


def ollama_available(config):
    if "ollama" in _availability_cache:
        return _availability_cache["ollama"]
    ok = False
    if config["OLLAMA_MODEL"]:
        try:
            with urllib.request.urlopen(
                config["OLLAMA_URL"] + "/api/tags", timeout=3
            ) as resp:
                tags = json.load(resp)
            names = {m.get("name", "") for m in tags.get("models", [])}
            model = config["OLLAMA_MODEL"]
            ok = model in names or f"{model}:latest" in names
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            ok = False
    _availability_cache["ollama"] = ok
    return ok


def omlx_available(config):
    if "omlx" in _availability_cache:
        return _availability_cache["omlx"]
    ok = False
    if config["OMLX_MODEL"]:
        try:
            req = urllib.request.Request(
                config["OMLX_URL"] + "/v1/models",
                headers=_omlx_headers(config),
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                models = json.load(resp)
            ok = config["OMLX_MODEL"] in {
                m.get("id", "") for m in models.get("data", [])
            }
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            ok = False
    _availability_cache["omlx"] = ok
    return ok


def _omlx_headers(config):
    headers = {"Content-Type": "application/json"}
    if config["OMLX_API_KEY"]:
        headers["Authorization"] = "Bearer " + config["OMLX_API_KEY"]
    return headers


def extract_omlx(config, prompt):
    payload = json.dumps({
        "model": config["OMLX_MODEL"],
        "messages": [
            {"role": "system", "content": CHAT_ROLE},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 4096,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "extraction",
                "strict": True,
                "schema": EXTRACTION_SCHEMA,
            },
        },
        # Qwen3.5 thinks by default; constrained extraction neither needs
        # nor wants a reasoning phase.
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(
        config["OMLX_URL"] + "/v1/chat/completions",
        data=payload,
        headers=_omlx_headers(config),
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        out = json.load(resp)
    return out["choices"][0]["message"]["content"]


def extract_ollama(config, prompt):
    payload = json.dumps({
        "model": config["OLLAMA_MODEL"],
        "messages": [
            {"role": "system", "content": CHAT_ROLE},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": EXTRACTION_SCHEMA,
        # keep_alive: return the ~22 GB of unified memory promptly after a
        # batch instead of Ollama's 5-minute default residency.
        "keep_alive": "1m",
        # num_ctx: Ollama's default silently truncates the head of long
        # prompts (capped note + roster can reach ~12k tokens).
        # presence_penalty: some tags ship nonzero defaults, which degrade
        # repetitive JSON keys.
        "options": {"temperature": 0, "num_ctx": 16384, "presence_penalty": 0},
    }).encode()
    req = urllib.request.Request(
        config["OLLAMA_URL"] + "/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        out = json.load(resp)
    return out["message"]["content"]


def extract_dtchat(prompt):
    return run_bridge(
        [{"op": "chat", "prompt": prompt, "role": CHAT_ROLE}], timeout=360
    )[0]["text"]


def parse_extraction(raw):
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("people"), list):
        raise ValueError("extraction JSON missing 'people' array")
    events = data.get("events")
    return data["people"], events if isinstance(events, list) else []


# ---------------------------------------------------------------------------
# Matching / ops
# ---------------------------------------------------------------------------


def norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def self_names(config):
    names = {norm(config["SELF_NAME"])} if config["SELF_NAME"] else set()
    try:
        gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
        if gecos:
            names.add(norm(gecos))
            names.add(norm(gecos.split()[0]))
    except Exception:
        pass
    names.discard("")
    return names


def roster_index(people):
    index = {}
    for p in people:
        keys = [norm(p["name"])] + [norm(a) for a in p.get("aliases", "").split(",")]
        for k in keys:
            if k:
                index.setdefault(k, []).append(p)
    return index


def roster_text(people):
    if not people:
        return "(none yet)"
    lines = []
    for p in sorted(people, key=lambda x: x["name"]):
        aliases = p.get("aliases", "").strip()
        lines.append(f"- {p['name']}" + (f" ({aliases})" if aliases else ""))
    return "\n".join(lines)


def valid_date(s):
    if not isinstance(s, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        date.fromisoformat(s)
        return s
    except ValueError:
        return None


def source_date_of(source):
    d = valid_date(source.get("eventdate", ""))
    if d:
        return d
    d = valid_date(source.get("name", "")[:10])
    if d:
        return d
    return valid_date(source.get("added", "")) or date.today().isoformat()


def fact_line(d, fact, source_uuid):
    fact = fact.rstrip(".") + "."
    return f"- {d} — {fact} ([source](x-devonthink-item://{source_uuid}))"


def near_matches(name, people, limit=3):
    """Roster people sharing a name token with an unmatched extraction —
    surfaces likely alias gaps ("Robert Carter" vs an existing "Bob Carter")
    in the proposal so dedup is a checkbox, not detective work."""
    toks = {t for t in norm(name).split() if len(t) >= 3}
    if not toks:
        return []
    out = []
    for p in people:
        ptoks = set()
        for n in [p["name"]] + p.get("aliases", "").split(","):
            ptoks.update(t for t in norm(n).split() if len(t) >= 3)
        if toks & ptoks:
            out.append(p["name"])
    return out[:limit]


def build_person_plans(extracted, index, selves, people, source_date):
    """Deterministic resolution: LLM output in, per-person op plans out."""
    plans = []
    for person in extracted:
        name = str(person.get("name", "")).strip()
        if not name or norm(name) in selves:
            continue
        facts = []
        for f in (person.get("facts") or [])[:12]:
            text = str(f.get("fact", "")).strip()
            if text and len(text) <= 400:
                facts.append((valid_date(f.get("date")) or source_date, text))
        updates = {}
        for field in UPDATE_FIELDS:
            v = (person.get("updates") or {}).get(field)
            if isinstance(v, str) and v.strip():
                updates[field] = v.strip()
        if not facts and not updates:
            continue

        claimed = str(person.get("match") or "").strip()
        hits = index.get(norm(claimed)) or index.get(norm(name)) or []
        if len(hits) == 1:
            plans.append({
                "kind": "existing",
                "name": hits[0]["name"],
                "uuid": hits[0]["uuid"],
                "md": hits[0].get("md", {}),
                "facts": facts,
                "updates": updates,
            })
        elif len(hits) > 1:
            plans.append({
                "kind": "ambiguous",
                "name": name,
                "candidates": [h["name"] for h in hits],
                "facts": facts,
                "updates": updates,
            })
        else:
            plans.append({
                "kind": "new",
                "name": name,
                "single_token": len(name.split()) < 2,
                "near": near_matches(name, people),
                "facts": facts,
                "updates": updates,
            })
    return plans


def build_event_plans(events_raw, selves, source_date):
    plans = []
    for ev in events_raw[:4]:
        name = str(ev.get("name", "")).strip()
        if not name or len(name) > 80:
            continue
        attendees = []
        for a in (ev.get("attendees") or [])[:20]:
            a = str(a).strip()
            if a and norm(a) not in selves and a not in attendees:
                attendees.append(a)
        summary = str(ev.get("summary") or "").strip()[:300]
        plans.append({
            "kind": "event",
            "name": name,
            "date": valid_date(ev.get("date")) or source_date,
            "location": str(ev.get("location") or "").strip()[:80],
            "attendees": attendees,
            "summary": summary,
        })
    return plans


def ops_for_plan(plan, source, source_date):
    src = source["uuid"]
    if plan["kind"] == "event":
        return [{"op": "ensure_event", "name": plan["name"],
                 "date": plan["date"], "location": plan["location"],
                 "attendees": plan["attendees"], "summary": plan["summary"],
                 "source_uuid": src}]
    lines = [fact_line(d, fact, src) for d, fact in plan["facts"]]
    ops = []
    if plan["kind"] == "existing":
        for field, value in plan["updates"].items():
            previous = str(plan["md"].get("md" + field, "") or "")
            if norm(previous) == norm(value):
                continue
            ops.append({"op": "set_field", "uuid": plan["uuid"],
                        "field": field, "value": value})
            if previous:
                lines.append(fact_line(
                    source_date,
                    f"{field.capitalize()}: {previous} → {value}", src))
        if lines:
            ops.append({"op": "append_log", "uuid": plan["uuid"], "lines": lines})
        ops.append({"op": "bump_lastcontact", "uuid": plan["uuid"],
                    "date": source_date})
    else:
        fields = dict(plan["updates"])
        ops.append({"op": "ensure_person", "name": plan["name"],
                    "fields": fields, "log_lines": lines})
    return ops


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


def proposal_body(source, source_date, plans, ops):
    lines = [
        f"# File: {source['name']}",
        "",
        f"Source: [{source['name']}](x-devonthink-item://{source['uuid']})"
        f" ({source_date})",
        "",
        "Move this record into `20_ENTITIES/_Review/Approved` to apply it on"
        " the next filing run, or delete it to reject.",
        "",
        "## Proposed",
        "",
    ]
    for plan in plans:
        if plan["kind"] == "event":
            who = ", ".join(plan["attendees"]) or "—"
            where = f" at {plan['location']}" if plan["location"] else ""
            lines.append(f"- **EVENT: {plan['name']}** ({plan['date']}{where})"
                         f" — who: {who}")
            if plan["summary"]:
                lines.append(f"  - {plan['summary']}")
        elif plan["kind"] == "existing":
            lines.append(f"- **{plan['name']}** (existing record)")
        elif plan["kind"] == "ambiguous":
            cands = ", ".join(plan["candidates"])
            lines.append(f"- **{plan['name']}** — AMBIGUOUS: matches {cands};"
                         " edit the ops JSON before approving")
        else:
            flag = " — single-word name, verify before approving" \
                if plan.get("single_token") else ""
            lines.append(f"- **{plan['name']}** (new Person record){flag}")
            if plan.get("near"):
                cands = ", ".join(plan["near"])
                lines.append(
                    f"  - possible existing match: {cands} — if same person,"
                    " add this name as an alias there, delete this proposal,"
                    " and re-run `entity-filing.py --force <source-uuid>`")
        for d, fact in plan.get("facts", []):
            lines.append(f"  - {d} — {fact}")
        for field, value in plan.get("updates", {}).items():
            lines.append(f"  - {field} = {value}")
    lines += ["", "## Ops", "", "```json", json.dumps(ops, indent=2), "```", ""]
    return "\n".join(lines)


def stale_person_ops(ops, index, people):
    """`ensure_person` ops whose name no longer resolves the way the proposal
    assumed. The ops are frozen when the proposal is written, but the roster
    keeps growing: a proposal that says `ensure_person "Alison"` because People
    was empty at extraction time would, once "Alison Vance" is seeded, match
    nothing and quietly create a second record. `ensure_person` resolves on
    exact name/alias only, so a shared name token is the signal. Returns
    [(name, [near matches])]; `"confirm_new": true` in an op opts out."""
    stale = []
    for op in ops:
        if op.get("op") != "ensure_person" or op.get("confirm_new"):
            continue
        name = str(op.get("name", "")).strip()
        if not name or index.get(norm(name)):
            continue
        near = near_matches(name, people)
        if near:
            stale.append((name, near))
    return stale


def apply_approved(dry_run):
    approved = run_bridge([{"op": "list_group", "path": APPROVED_PATH}])[0]
    if not approved:
        return
    people = run_bridge([{"op": "dump_people", "include_bodies": False}])[0]
    index = roster_index(people)
    for rec in approved:
        text = run_bridge([{"op": "get_text", "uuid": rec["uuid"]}])[0]["text"]
        blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if not blocks:
            log.warning("approved proposal has no ops block, skipping",
                        extra={"record_name": rec["name"],
                               "record_uuid": rec["uuid"]})
            continue
        try:
            ops = json.loads(blocks[-1])
        except json.JSONDecodeError as exc:
            log.error("approved proposal has invalid ops JSON (%s), skipping",
                      exc, extra={"record_name": rec["name"],
                                  "record_uuid": rec["uuid"]})
            continue
        if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
            log.error("approved proposal ops are not a JSON array of objects, "
                      "skipping", extra={"record_name": rec["name"],
                                         "record_uuid": rec["uuid"]})
            continue
        stale = stale_person_ops(ops, index, people)
        if stale:
            for name, near in stale:
                log.warning(
                    'proposal would create "%s" alongside %s — add "%s" as an '
                    'alias on the existing record, or set "confirm_new": true '
                    "in the op to create a separate person; leaving in Approved",
                    name, ", ".join(near), name,
                    extra={"record_name": rec["name"],
                           "record_uuid": rec["uuid"]})
            continue
        if dry_run:
            log.info("[dry-run] would apply %d ops from %s", len(ops), rec["name"])
            continue
        try:
            run_bridge(ops + [{"op": "trash", "uuid": rec["uuid"]}])
        except BridgeUnavailable:
            raise
        except Exception as exc:
            log.error("applying proposal failed (%s), leaving in Approved: %s",
                      type(exc).__name__, exc,
                      extra={"record_name": rec["name"],
                             "record_uuid": rec["uuid"]})
            continue
        log.info("applied %d ops", len(ops),
                 extra={"record_name": rec["name"], "record_uuid": rec["uuid"]})
        if any(op.get("op") == "ensure_person" for op in ops):
            people = run_bridge([{"op": "dump_people", "include_bodies": False}])[0]
            index = roster_index(people)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def cap_words(text, head=6000, tail=1000):
    words = text.split()
    if len(words) <= head + tail:
        return text
    return " ".join(words[:head]) + "\n[...truncated...]\n" + " ".join(words[-tail:])


LOCAL_TRANSPORTS = ("omlx", "ollama")


def pick_transport(config, kind):
    transport = config["TRANSPORT"]
    if transport == "off":
        return None

    def local_pick(allowed):
        if "omlx" in allowed and omlx_available(config):
            return "omlx"
        if "ollama" in allowed and ollama_available(config):
            return "ollama"
        return None

    if transport in LOCAL_TRANSPORTS:
        return local_pick((transport,))
    if transport == "local":
        return local_pick(LOCAL_TRANSPORTS)
    if kind == "daily":
        # /10_DAILY is excluded from DT chat by design; local model only.
        return local_pick(LOCAL_TRANSPORTS) if transport == "auto" else None
    if transport == "dtchat":
        return "dtchat"
    return local_pick(LOCAL_TRANSPORTS) or "dtchat"


def scan(config, state, dry_run, force_uuid, user_invoked):
    people, sources = run_bridge([
        {"op": "dump_people", "include_bodies": False},
        {"op": "list_sources"},
    ])
    index = roster_index(people)
    selves = self_names(config)
    processed = set(state["processed_ids"])

    # Deterministic attendance pass: meeting participants bump LastContact
    # whether or not extraction can run. bump_lastcontact only ever raises
    # the date, so a meeting older than the window can no longer change any
    # LastContact — re-scanning the whole archive each tick is pure waste.
    window_start = (date.today() - timedelta(days=BUMP_WINDOW_DAYS)).isoformat()
    bump_ops = []
    for source in sources:
        if source["kind"] != "meeting":
            continue
        d = source_date_of(source)
        added = source.get("added", "")
        if d < window_start and (not added or added < window_start):
            continue
        for raw_name in (source.get("participants") or "").split(","):
            n = norm(raw_name)
            if not n or n in selves:
                continue
            hits = index.get(n) or []
            if len(hits) == 1:
                bump_ops.append({"op": "bump_lastcontact",
                                 "uuid": hits[0]["uuid"], "date": d})
    if bump_ops and not dry_run:
        run_bridge(bump_ops)

    min_roster = int(config["MIN_ROSTER"])
    if len(people) < min_roster and not force_uuid:
        log.info("People holds %d record(s), MIN_ROSTER is %d — extraction "
                 "paused until /20_ENTITIES/People is seeded",
                 len(people), min_roster)
        return

    skip_re = None
    if config["SKIP_SOURCE_TITLES"]:
        try:
            skip_re = re.compile(config["SKIP_SOURCE_TITLES"], re.IGNORECASE)
        except re.error as exc:
            log.warning("bad SKIP_SOURCE_TITLES regex, ignoring: %s", exc)

    if force_uuid:
        candidates = [s for s in sources if s["uuid"] == force_uuid]
        if not candidates:
            try:
                candidates = [run_bridge(
                    [{"op": "get_source", "uuid": force_uuid}])[0]]
            except Exception as exc:
                log.error("--force uuid %s could not be fetched: %s",
                          force_uuid, exc)
                return
    else:
        candidates = [
            s for s in sources
            if s["uuid"] not in processed
            and not (skip_re and skip_re.search(s["name"]))
        ]
        candidates.sort(key=source_date_of, reverse=True)

    limit = int(config["MAX_PER_RUN"])
    filing_mode = config["FILING_MODE"]
    idle_min = float(config["IDLE_MINUTES"])
    idle_ok = True
    if idle_min > 0 and not user_invoked:
        idle = user_idle_seconds()
        idle_ok = idle is None or idle >= idle_min * 60
    idle_skip_logged = False
    no_transport = 0
    extracted_count = 0
    for source in candidates:
        if extracted_count >= limit:
            break
        attempts = state["attempts"].get(source["uuid"], 0)
        if attempts >= MAX_ATTEMPTS and not force_uuid:
            log.warning("giving up after %d attempts, marking processed",
                        attempts, extra={"record_name": source["name"],
                                         "record_uuid": source["uuid"]})
            state["processed_ids"].append(source["uuid"])
            save_state(state)
            continue

        transport = pick_transport(config, source["kind"])
        if transport is None:
            no_transport += 1
            continue  # no eligible transport (e.g. daily note without Ollama)
        if transport in LOCAL_TRANSPORTS and not idle_ok:
            # Local inference is deferrable by design; never spin fans while
            # the user is actively working. Candidates wait for an idle run.
            if not idle_skip_logged:
                log.info("user active, deferring local extraction to an idle run")
                idle_skip_logged = True
            continue

        source_date = source_date_of(source)
        text = run_bridge([{"op": "get_text", "uuid": source["uuid"]}])[0]["text"]
        if len(text.split()) < 20:
            state["processed_ids"].append(source["uuid"])
            if not dry_run:
                save_state(state)
            continue

        prompt = PROMPT_TEMPLATE.format(
            source_date=source_date,
            roster=roster_text(people),
            source_name=source["name"],
            content=cap_words(text),
        )
        extracted_count += 1
        log.info("extracting via %s", transport,
                 extra={"record_name": source["name"],
                        "record_uuid": source["uuid"]})
        try:
            if transport == "omlx":
                raw = extract_omlx(config, prompt)
            elif transport == "ollama":
                raw = extract_ollama(config, prompt)
            else:
                raw = extract_dtchat(prompt)
            extracted_people, extracted_events = parse_extraction(raw)
        except BridgeUnavailable:
            raise
        except Exception as exc:
            state["attempts"][source["uuid"]] = attempts + 1
            if not dry_run:
                save_state(state)
            log.error("extraction failed: %s: %s", type(exc).__name__, exc,
                      extra={"record_name": source["name"],
                             "record_uuid": source["uuid"]})
            continue

        try:
            plans = build_person_plans(extracted_people, index, selves, people,
                                       source_date)
            plans += build_event_plans(extracted_events, selves, source_date)
            file_source(config, state, source, source_date, plans, filing_mode,
                        dry_run)
        except BridgeUnavailable:
            raise
        except Exception as exc:
            state["attempts"][source["uuid"]] = attempts + 1
            if not dry_run:
                save_state(state)
            log.error("filing failed: %s: %s", type(exc).__name__, exc,
                      extra={"record_name": source["name"],
                             "record_uuid": source["uuid"]})
            continue

    if no_transport and not extracted_count:
        log.info("%d candidate source(s) waiting: no eligible transport "
                 "(TRANSPORT=%s)", no_transport, config["TRANSPORT"])


def file_source(config, state, source, source_date, plans, filing_mode, dry_run):
    direct_ops = []
    proposal_plans = []
    for plan in plans:
        if filing_mode == "auto" and plan["kind"] == "existing":
            direct_ops.extend(ops_for_plan(plan, source, source_date))
        else:
            proposal_plans.append(plan)

    proposal_ops = []
    for plan in proposal_plans:
        proposal_ops.extend(ops_for_plan(plan, source, source_date))
    proposal_ops.append({"op": "mark_filed", "uuid": source["uuid"]})

    if dry_run:
        log.info("[dry-run] %s: %d direct ops, %d proposal plans",
                 source["name"], len(direct_ops), len(proposal_plans))
        print(json.dumps({"source": source["name"], "direct": direct_ops,
                          "proposal": proposal_ops}, indent=2))
        return

    if direct_ops:
        run_bridge(direct_ops)
        log.info("auto-applied %d ops", len(direct_ops),
                 extra={"record_name": source["name"],
                        "record_uuid": source["uuid"]})

    if proposal_plans:
        body = proposal_body(source, source_date, proposal_plans, proposal_ops)
        run_bridge([{
            "op": "create_record",
            "name": f"File: {source['name']}",
            "path": REVIEW_PATH,
            "text": body,
            "fields": {"documenttype": "Entity Filing Proposal"},
        }])
        log.info("proposal created (%d people)", len(proposal_plans),
                 extra={"record_name": source["name"],
                        "record_uuid": source["uuid"]})
    else:
        run_bridge([{"op": "mark_filed", "uuid": source["uuid"]}])

    state["processed_ids"].append(source["uuid"])
    state["attempts"].pop(source["uuid"], None)
    save_state(state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    apply_only = "--apply-only" in args
    scan_only = "--scan-only" in args
    force_uuid = None
    if "--force" in args:
        idx = args.index("--force")
        if idx + 1 < len(args):
            force_uuid = args[idx + 1]
    user_invoked = bool(dry_run or force_uuid or apply_only or scan_only)

    subprocess.run(
        [os.path.expanduser("~/.local/bin/pipeline-record-run"),
         "entity-filing", "1800"],
        check=False,
    )

    if not user_invoked:
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-background-job")],
            capture_output=True, text=True,
        )
        if gate.returncode != 0:
            log.info("skipping: battery gate")
            return
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-dt-driver")],
            capture_output=True, text=True,
        )
        if gate.returncode != 0:
            log.info("skipping: follower machine")
            return

    lock_fd = None
    if not dry_run:
        lock_fd = acquire_lock()
        if lock_fd is None:
            log.info("another entity-filing run holds the lock, exiting")
            return

    config = load_config()
    state = load_state()

    try:
        if not scan_only:
            apply_approved(dry_run)
        if not apply_only:
            scan(config, state, dry_run, force_uuid, user_invoked)
    except BridgeUnavailable as exc:
        log.info("skipping: %s", exc)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("FATAL: %s: %s", type(exc).__name__, exc)
        sys.exit(1)
