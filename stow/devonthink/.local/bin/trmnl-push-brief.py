#!/usr/bin/python3
"""
trmnl-push-brief.py — mirror the morning brief onto a TRMNL e-ink dashboard.

Reads the snapshot dt-morning-brief.py writes on every run
(~/.local/state/devonthink/morning-brief.json) and POSTs it as
merge_variables to a TRMNL private plugin's webhook. Without a configured
webhook URL the script is a silent no-op, so it can ship wired into the
brief before the plugin exists.

Config (~/.config/dt-pipeline/trmnl.conf, KEY=VALUE lines, # comments):
    TRMNL_WEBHOOK_URL=https://trmnl.com/api/custom_plugins/<plugin-uuid>
    TRMNL_PAYLOAD_LIMIT=2048    # bytes; TRMNL+ accounts may raise to 5120

TRMNL's webhook accepts at most 2 kB per request (5 kB for TRMNL+) and 12
requests/hour, so the payload is fitted to the byte budget by a fixed
ladder of degradations — least valuable data first (on-this-day tail,
person detail fields, list caps) — and an unchanged payload is never
re-POSTed: the last pushed body's hash lives in
~/.local/state/devonthink/trmnl-push-state.json. The brief's own
4x-morning launchd schedule is the retry cadence — a failed push leaves
status != "ok" in the state file, so the next run retries even though the
payload hash is unchanged.

Failure logging is deliberately quiet: network errors, 5xx, and 429 log
INFO (transient, self-healing on the next scheduled run, not worth a 5am
watchdog page); other HTTP 4xx logs WARNING (the config is wrong and needs
a human). No battery gate of its own: launchd-driven runs arrive as a
child of the already-gated brief, and a direct invocation is by definition
user-invoked.

Usage:
    trmnl-push-brief.py            # push if changed (or nothing configured)
    trmnl-push-brief.py --dry-run  # print payload, byte size, ladder steps
    trmnl-push-brief.py --force    # push even if the payload is unchanged
"""

import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log

log = setup_log("trmnl-push")

SNAPSHOT_FILE = os.path.expanduser(
    "~/.local/state/devonthink/morning-brief.json")
CONFIG_FILE = os.path.expanduser("~/.config/dt-pipeline/trmnl.conf")
STATE_FILE = os.path.expanduser(
    "~/.local/state/devonthink/trmnl-push-state.json")
DEFAULT_PAYLOAD_LIMIT = 2048


def load_config():
    cfg = {}
    try:
        with open(CONFIG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                cfg[key.strip()] = value.strip()
    except OSError:
        return {}
    return cfg


def body_bytes(payload):
    return json.dumps({"merge_variables": payload}, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _cap(key, n):
    def fn(p):
        del p[key][n:]
    return fn


def _drop_person_field(field):
    def fn(p):
        for m in p["meetings"]:
            for person in m["people"]:
                person.pop(field, None)
    return fn


def _cap_unmatched(n):
    def fn(p):
        for m in p["meetings"]:
            extra = len(m["unmatched"]) - n
            if extra > 0:
                del m["unmatched"][n:]
                m["more_unmatched"] = m.get("more_unmatched", 0) + extra
    return fn


def _cap_people(n):
    def fn(p):
        for m in p["meetings"]:
            del m["people"][n:]
    return fn


def _truncate_titles(n):
    def fn(p):
        for m in p["meetings"]:
            if len(m["title"]) > n:
                m["title"] = m["title"][:n - 1].rstrip() + "…"
    return fn


# Applied top to bottom until the body fits; each step must be idempotent
# and only ever shrink the payload.
LADDER = [
    ("on_this_day:4", _cap("on_this_day", 4)),
    ("unmatched:2", _cap_unmatched(2)),
    ("people:-city", _drop_person_field("city")),
    ("reconnect:6", _cap("reconnect", 6)),
    ("birthdays:5", _cap("birthdays", 5)),
    ("people:-last", _drop_person_field("last")),
    ("on_this_day:2", _cap("on_this_day", 2)),
    ("titles:48", _truncate_titles(48)),
    ("reconnect:3", _cap("reconnect", 3)),
    ("people:-employer", _drop_person_field("employer")),
    ("people:-role", _drop_person_field("role")),
    ("on_this_day:0", _cap("on_this_day", 0)),
    ("people:3", _cap_people(3)),
    ("meetings:6", _cap("meetings", 6)),
    ("unmatched:0", _cap_unmatched(0)),
    ("birthdays:3", _cap("birthdays", 3)),
    ("reconnect:0", _cap("reconnect", 0)),
    ("meetings:4", _cap("meetings", 4)),
    ("people:2", _cap_people(2)),
    ("birthdays:0", _cap("birthdays", 0)),
]


def compact(snapshot, limit):
    """Fit the snapshot to the byte budget. Returns (payload, applied_steps)
    or (None, applied_steps) when even the full ladder can't fit it."""
    payload = json.loads(json.dumps(snapshot))
    payload["truncated"] = False
    applied = []
    if len(body_bytes(payload)) <= limit:
        return payload, applied
    payload["truncated"] = True
    for label, fn in LADDER:
        fn(payload)
        applied.append(label)
        if len(body_bytes(payload)) <= limit:
            return payload, applied
    return None, applied


def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    state_dir = os.path.dirname(STATE_FILE)
    os.makedirs(state_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=1)
        os.replace(tmp, STATE_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def push(url, body):
    """POST the webhook body. Returns True on success; logs and returns
    False otherwise."""
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("pushed %d bytes (HTTP %d)", len(body), resp.status)
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            log.info("push deferred (HTTP %d) — next brief run retries",
                     exc.code)
        else:
            log.warning("push rejected (HTTP %d) — check trmnl.conf "
                        "webhook URL", exc.code)
    except OSError as exc:
        log.info("push failed (%s) — next brief run retries", exc)
    return False


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args

    cfg = load_config()
    url = cfg.get("TRMNL_WEBHOOK_URL", "")
    limit = int(cfg.get("TRMNL_PAYLOAD_LIMIT", DEFAULT_PAYLOAD_LIMIT))

    if not url and not dry_run:
        return

    try:
        with open(SNAPSHOT_FILE) as f:
            snapshot = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.info("no usable snapshot: %s", exc)
        return

    payload, applied = compact(snapshot, limit)
    if payload is None:
        log.warning("payload cannot fit %d bytes even fully truncated "
                    "(steps: %s)", limit, ", ".join(applied))
        return
    body = body_bytes(payload)

    if dry_run:
        print(json.dumps({"merge_variables": payload}, ensure_ascii=False,
                         indent=2))
        print(f"\n{len(body)} bytes (limit {limit})"
              + (f", ladder: {', '.join(applied)}" if applied else ""),
              file=sys.stderr)
        if not url:
            print("no TRMNL_WEBHOOK_URL in ~/.config/dt-pipeline/trmnl.conf "
                  "— a real run would be a no-op", file=sys.stderr)
        return

    digest = hashlib.sha256(body).hexdigest()
    state = load_state()
    if (not force and state.get("hash") == digest
            and state.get("status") == "ok"):
        return

    ok = push(url, body)
    save_state({"hash": digest, "status": "ok" if ok else "error",
                "pushed_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "bytes": len(body)})


if __name__ == "__main__":
    main()
