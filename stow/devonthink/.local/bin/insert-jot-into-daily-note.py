#!/usr/bin/python3
"""
insert-jot-into-daily-note.py — Insert a single jot bullet into the correct
position of a daily note's markdown body.

Invoked from the "Process Jots" smart rule
(stow/devonthink/Library/Application Scripts/com.devon-technologies.think/
Smart Rules/process-jots.applescript) via `do shell script` after the
AppleScript has determined which day's note to mutate and what bullet to
insert. The AppleScript reads the note's `plain text`, writes it to a temp
file, invokes this script on stdin, and writes the modified body back via
`set plain text of targetNote to ...`.

Wire format:
    JOT_LINE  env var — the bullet to insert (a "- <h:mmam>: <text>" line from
              the Drafts action, already carrying its `<!-- jot:UUID -->`
              idempotency marker built in AppleScript)
    stdin     full note body
    stdout    modified body (no trailing newline)

The jot lands at its timestamp's chronological position in the root timeline
(brief_events.timeline_insert — the same placement rule the bridge's
timelineMerge uses), so a jot processed after the morning brief has populated
future event times slots in among them rather than appending below tonight's
meetings. A pre-flatten note that still has a "## Today's Notes" section takes
the legacy path: after the last content bullet before that header, replacing
the skeleton's empty placeholder bullet when the root list is empty.

Shebang is `/usr/bin/python3` (the Apple-signed system interpreter) to match
the AppleScript caller's `do shell script "/usr/bin/python3 <path>"`. The
script is pure stdlib and doesn't itself send AppleEvents, so the choice is
for testing parity (direct invocation runs the same interpreter as the smart
rule) rather than TCC stability — same principle the project's CLAUDE.md
applies to tier-1 scripts.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brief_events as be

LEGACY_HEADER = "## Today's Notes"


def legacy_insert(lines, jot, h2):
    empty_bullet = re.compile(r"^\s*[-*]\s*$")
    content_bullet = re.compile(r"^\s*[-*]\s+\S")

    last_content = None
    for i in range(h2 - 1, -1, -1):
        if content_bullet.match(lines[i]):
            last_content = i
            break

    if last_content is not None:
        insert_at = last_content + 1
        while insert_at < h2 and re.match(r"^[ \t]", lines[insert_at]):
            insert_at += 1
        lines.insert(insert_at, jot)
        return lines

    placeholder = None
    for i in range(h2 - 1, -1, -1):
        if empty_bullet.match(lines[i]):
            placeholder = i
            break

    if placeholder is not None:
        lines[placeholder] = jot
        return lines

    ins = h2
    while ins > 0 and lines[ins - 1].strip() == "":
        ins -= 1
    lines[ins:h2] = ["", jot, ""]
    return lines


def insert(note: str, jot: str) -> str:
    lines = note.splitlines()
    h2 = None
    for i, line in enumerate(lines):
        if line.strip() == LEGACY_HEADER:
            h2 = i
            break
    if h2 is not None:
        return "\n".join(legacy_insert(lines, jot, h2))
    return "\n".join(be.timeline_insert(lines, [jot]))


def main() -> int:
    try:
        jot = os.environ["JOT_LINE"]
    except KeyError as exc:
        print(f"missing required env var: {exc.args[0]}", file=sys.stderr)
        return 2

    note = sys.stdin.read()
    sys.stdout.write(insert(note, jot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
