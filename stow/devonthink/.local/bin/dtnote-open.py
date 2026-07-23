#!/usr/bin/python3
"""
dtnote-open.py — the dtnote:// URL handler behind the briefing's event links.

dt-morning-brief renders every note-less briefing event title as
`dtnote://open?date=YYYY-MM-DD&title=<event title>`. DTNote.app (built by
scripts/build-dtnote-handler.sh from devonthink/utils/dtnote-handler.applescript)
owns the scheme and hands the URL here. This script gives the click the
semantics a URL command cannot: look up the event's owning meeting note by
its LinkedEvent key and open it, creating it first — fully stamped — only
when it doesn't exist. Clicking is therefore idempotent; a second click
opens the same note instead of minting a duplicate (the failure mode of the
x-devonthink://createMarkdown command, which can neither check for an
existing record nor navigate to the one it creates).

Notes are created in /99_ARCHIVE — their permanent home, not a stop on the
way to one (nothing may move a note someone is mid-meeting typing into) —
and retrieved by tag/DocumentType, not location. Creation stamps everything
the Adopt Meeting Note smart rule would otherwise have to backfill
(LinkedEvent, EventDate, DocumentType "Meeting Notes", DailyNoteLinked,
the "Meeting Note" tag), so that rule stays a backstop for hand-tagged
notes. A note the handler creates leaves the briefing's dtnote link in
place; the next regeneration swaps it for the item link, and until then the
dtnote link keeps resolving to the same record.

Only notes whose DocumentType contains "Meeting" count as the click target:
a handwritten note linked to the same event renders as a sub-bullet, and
clicking the title while one exists still creates the typed note — the
title's behavior must not depend on what else got attached.

All DEVONthink I/O goes through entity-dt-bridge.js under /usr/bin/osascript,
so the click never raises a new Automation prompt (the applet itself sends
no AppleEvents). Errors print to stderr; the applet surfaces them as a
notification.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import date as date_type
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.expanduser("~/.local/bin"))
import brief_events as be

BRIDGE = os.path.expanduser("~/.local/bin/entity-dt-bridge.js")
ARCHIVE_PATH = "/99_ARCHIVE"


def parse_url(url):
    """(date, title) from dtnote://open?date=…&title=…, or ValueError."""
    parts = urlsplit(url)
    if parts.scheme != "dtnote" or (parts.netloc or parts.path.strip("/")) != "open":
        raise ValueError(f"not a dtnote open URL: {url}")
    q = parse_qs(parts.query)
    date = (q.get("date") or [""])[0]
    title = (q.get("title") or [""])[0].strip()
    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise ValueError(f"bad date in dtnote URL: {date!r}")
    if not title:
        raise ValueError("dtnote URL carries no title")
    return date, title


def run_bridge(ops):
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"ops": ops}, f)
        proc = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", BRIDGE, path],
            capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(path)
    out = json.loads(proc.stdout or "{}")
    if not out.get("ok"):
        raise RuntimeError(out.get("error") or proc.stderr.strip()
                           or "bridge failed")
    return out["results"]


def owning_note(records):
    for r in records:
        if "meeting" in (r.get("documenttype") or "").casefold():
            return r["uuid"]
    return None


def main(argv):
    if len(argv) != 1:
        sys.stderr.write("usage: dtnote-open.py <dtnote://open?date=…&title=…>\n")
        return 2
    date, title = parse_url(argv[0])
    key = be.event_key(date, title)
    uuid = owning_note(
        run_bridge([{"op": "find_by_field", "field": "LinkedEvent",
                     "value": key}])[0])
    if uuid is None:
        uuid = run_bridge([{
            "op": "create_record", "name": f"{date} {title}",
            "path": ARCHIVE_PATH, "text": f"# {title}\n\n",
            "fields": {"LinkedEvent": key, "EventDate": date,
                       "DocumentType": "Meeting Notes", "DailyNoteLinked": 1},
            "tags": [be.CREATE_TAG],
        }])[0]["uuid"]
    run_bridge([{"op": "open_record", "uuid": uuid}])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)
