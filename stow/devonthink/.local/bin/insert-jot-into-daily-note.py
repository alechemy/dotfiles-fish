#!/usr/bin/python3
"""
insert-jot-into-daily-note.py — Insert a single bullet line into the correct
position of a daily note's markdown body.

Invoked from the "Process Jots" smart rule
(stow/devonthink/Library/Application Scripts/com.devon-technologies.think/
Smart Rules/process-jots.applescript) via `do shell script` after the
AppleScript has determined which day's note to mutate and what bullet to
insert. The AppleScript reads the note's `plain text`, writes it to a temp
file, invokes this script on stdin, and writes the modified body back via
`set plain text of targetNote to ...`.

Wire format:
    JOT_LINE       env var — the bullet to insert (already includes any
                   `<!-- jot:UUID -->` idempotency marker built in
                   AppleScript)
    SECTION_HEADER env var — H2 line that marks the "Today's Notes" section,
                   exact-match including the "## " prefix
    stdin          full note body
    stdout         modified body (no trailing newline)

Insertion rules (matches the prior in-AppleScript Python heredoc verbatim):

  1. If SECTION_HEADER is not present in the body, append `'', JOT_LINE`.
  2. Otherwise, look for the LAST content bullet (`- text` or `* text`)
     before the section header:
       a. If one exists, insert JOT_LINE immediately after it, skipping past
          any indented continuation lines belonging to that bullet.
       b. If none exists but there is an EMPTY bullet (`-` or `*` with no
          text) before the header, replace it with JOT_LINE.
       c. If neither, insert `'', JOT_LINE, ''` immediately before the
          header, collapsing any blank lines just above the header first.

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


def insert(note: str, jot: str, marker: str) -> str:
    lines = note.splitlines()
    empty_bullet = re.compile(r"^\s*[-*]\s*$")
    content_bullet = re.compile(r"^\s*[-*]\s+\S")

    h2 = None
    for i, line in enumerate(lines):
        if line.strip() == marker:
            h2 = i
            break

    if h2 is None:
        lines += ["", jot]
        return "\n".join(lines)

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
        return "\n".join(lines)

    placeholder = None
    for i in range(h2 - 1, -1, -1):
        if empty_bullet.match(lines[i]):
            placeholder = i
            break

    if placeholder is not None:
        lines[placeholder] = jot
        return "\n".join(lines)

    ins = h2
    while ins > 0 and lines[ins - 1].strip() == "":
        ins -= 1
    lines[ins:h2] = ["", jot, ""]
    return "\n".join(lines)


def main() -> int:
    try:
        jot = os.environ["JOT_LINE"]
        marker = os.environ["SECTION_HEADER"]
    except KeyError as exc:
        print(f"missing required env var: {exc.args[0]}", file=sys.stderr)
        return 2

    note = sys.stdin.read()
    sys.stdout.write(insert(note, jot, marker))
    return 0


if __name__ == "__main__":
    sys.exit(main())
