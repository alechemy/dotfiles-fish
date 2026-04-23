#!/usr/bin/env python3
"""
sync-markdown-h1.py — Set a markdown document's H1 to match a given title.

Reads markdown from stdin, ensures the first H1 (outside frontmatter and
fenced code blocks) matches the given title. Prints the result to stdout.

- H1 matches title → output unchanged
- H1 differs → H1 line replaced
- No H1 found → injected after frontmatter (or at the top)

Usage:
    python3 sync-markdown-h1.py "Document Title" < input.md
"""

import re
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: sync-markdown-h1.py TITLE < input.md", file=sys.stderr)
        sys.exit(1)

    title = sys.argv[1]
    text = sys.stdin.read()

    # Refuse to synthesize content from nothing. If the caller hands us
    # empty or whitespace-only input, return it unchanged — never inject
    # an H1 into a record that has no body, since that turns "body was
    # wiped" into "H1-only record" and masks the real failure upstream.
    if not text.strip():
        sys.stdout.write(text)
        return

    lines = text.split("\n")

    # Locate end of YAML frontmatter (if any)
    i = 0
    fm_end = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines):
            if lines[i].strip() == "---":
                fm_end = i + 1
                i += 1
                break
            i += 1
        else:
            # No closing --- found; treat entire file as frontmatter
            fm_end = len(lines)
            i = len(lines)

    # Search for first H1 outside fenced code blocks
    in_code = False
    h1_index = -1
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = not in_code
        elif not in_code and re.match(r"^#\s+.+", stripped):
            h1_index = i
            break
        i += 1

    h1_line = f"# {title}"

    if h1_index >= 0:
        existing = re.sub(r"^#\s+", "", lines[h1_index].strip())
        if existing == title:
            sys.stdout.write(text)
            return
        lines[h1_index] = h1_line
    else:
        if fm_end > 0:
            insert_at = fm_end
            # Add blank line before H1 if the line before isn't blank
            if insert_at > 0 and lines[insert_at - 1].strip() != "":
                lines.insert(insert_at, "")
                insert_at += 1
            lines.insert(insert_at, h1_line)
            # Add blank line after H1 if next line isn't blank
            if insert_at + 1 < len(lines) and lines[insert_at + 1].strip() != "":
                lines.insert(insert_at + 1, "")
        else:
            lines.insert(0, h1_line)
            if len(lines) < 2 or lines[1].strip() != "":
                lines.insert(1, "")

    sys.stdout.write("\n".join(lines))


if __name__ == "__main__":
    main()
