#!/usr/bin/env python3
"""Insert a machine bullet block into a daily note.

Reads the note from stdin, inserts the content block (a bullet plus any
indented sub-lines) at its chronological position in the root timeline, and
prints the updated note to stdout.

Usage: insert-daily-note-section.py --content "block text"

Placement follows brief_events.timeline_insert: before the first
strictly-later timed bullet, stepping over untimed manual lines, ahead of the
pinned untimed machine bullets at the end. A pre-flatten note that still has
a "## Today's Notes" section takes the legacy path instead — the block is
slotted into that section (kept in chronological order) — so writers touching
the last sectioned note across the cutover keep its shape.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brief_events as be

LEGACY_HEADER = "## Today's Notes"

TIME_PREFIX_RE = re.compile(
    r"^\s*-\s+(\d{1,2}):(\d{2})\s*(am|pm)\b", re.IGNORECASE
)


def time_key(line):
    m = TIME_PREFIX_RE.match(line)
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        hour += 12
    return hour * 60 + int(m.group(2))


def sort_timed_items(lines, header_idx):
    start = header_idx + 1
    while start < len(lines) and lines[start].strip() == "":
        start += 1
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("- "):
        end += 1
    if end - start < 2:
        return
    block = lines[start:end]
    # Items without a time prefix keep their original relative position at the end.
    decorated = [
        ((0, t, i) if (t := time_key(line)) is not None else (1, 0, i), line)
        for i, line in enumerate(block)
    ]
    decorated.sort(key=lambda x: x[0])
    lines[start:end] = [line for _, line in decorated]


def legacy_insert(lines, block_lines):
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == LEGACY_HEADER:
            header_idx = i
            break

    section_end = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if re.match(r"^#{1,2}\s", lines[i].strip()):
            section_end = i
            break
    # Insert immediately after the last non-blank line within the section,
    # so a trailing blank before the next heading doesn't split the list.
    insert_idx = section_end
    while insert_idx > header_idx + 1 and lines[insert_idx - 1].strip() == "":
        insert_idx -= 1
    first_is_list = block_lines[0].lstrip().startswith("- ")
    prev_is_list = insert_idx > 0 and lines[insert_idx - 1].lstrip().startswith(
        "- "
    )
    if not (prev_is_list and first_is_list):
        lines.insert(insert_idx, "")
        insert_idx += 1
    for bl in block_lines:
        lines.insert(insert_idx, bl)
        insert_idx += 1

    sort_timed_items(lines, header_idx)
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content", required=True)
    args = parser.parse_args()

    note = sys.stdin.read()
    lines = note.splitlines()
    # --content is built in AppleScript, where `return` is a CR: split it the
    # same way the body is split, or the whole block lands as one line.
    block_lines = args.content.replace("\r\n", "\n").replace(
        "\r", "\n").rstrip("\n").split("\n")

    if any(l.strip() == LEGACY_HEADER for l in lines):
        lines = legacy_insert(lines, block_lines)
    else:
        lines = be.timeline_insert(lines, block_lines)

    print("\n".join(lines), end="")


if __name__ == "__main__":
    main()
