#!/usr/bin/env python3
"""Insert a content block under a section header in a daily note.

Reads the note from stdin, inserts the content block under the specified
section header (creating it at the end if missing), and prints the
updated note to stdout.

Usage: insert-daily-note-section.py --header "## Section" --content "block text"

The content block is inserted just before the next ## or # heading after
the section header. List items are merged without a blank separator when
the previous line is also a list item. List items prefixed with a time
(e.g. "- 6:10am: ...") are kept in chronological order; async syncs that
arrive late are slotted into the correct position rather than appended.
"""

import argparse
import re
import sys

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--header", required=True)
    parser.add_argument("--content", required=True)
    args = parser.parse_args()

    note = sys.stdin.read()
    lines = note.splitlines()
    block_lines = args.content.rstrip("\n").split("\n")

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == args.header:
            header_idx = i
            break

    if header_idx is None:
        lines.append("")
        lines.append(args.header)
        lines.append("")
        lines += block_lines
        header_idx = len(lines) - len(block_lines) - 1
    else:
        section_end = len(lines)
        for i in range(header_idx + 1, len(lines)):
            if re.match(r"^#{1,2}\s", lines[i]):
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

    print("\n".join(lines), end="")


if __name__ == "__main__":
    main()
