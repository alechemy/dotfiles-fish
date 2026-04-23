#!/usr/bin/env python3
"""
Classify input text for the DT new-note KM macros.

Reads RAW_TEXT from the environment. Exits 1 on empty/whitespace input.
Otherwise prints one of:

    bookmark\\n<url>
    markdown\\n<title>\\n<<<SPLIT>>>\\n<body>

URL detection first strips wrapping chars (quotes, brackets, angle brackets,
backticks) and trailing sentence punctuation; if the result is a single
http(s) URL, the output is a bookmark. Otherwise the original text flows
into the same first-line-as-title heuristic used by the Drafts actions.
"""

import datetime
import os
import re
import sys

TITLE_MAX_LEN = 80
URL_PATTERN = re.compile(r"^https?://\S+$", re.IGNORECASE)
WRAPPING_PAIRS = [
    ("<", ">"),
    ('"', '"'),
    ("'", "'"),
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("`", "`"),
]


def extract_url_candidate(s):
    prev = None
    while s != prev:
        prev = s
        s = s.strip()
        s = re.sub(r"[.,;:!?]+$", "", s)
        for left, right in WRAPPING_PAIRS:
            if len(s) >= 2 and s.startswith(left) and s.endswith(right):
                s = s[1:-1]
                break
    return s


def generic_title():
    now = datetime.datetime.now()
    h = now.hour % 12 or 12
    ampm = "PM" if now.hour >= 12 else "AM"
    return f"New Markdown Note {now:%Y-%m-%d} at {h}.{now:%M.%S}{ampm}"


def resolve_markdown(text):
    lines = text.split("\n")
    first = lines[0]

    m = re.match(r"^#+\s+(.+)$", first)
    if m:
        return m.group(1).strip(), text

    only = len(lines) == 1
    blank_after = len(lines) > 1 and lines[1].strip() == ""
    if len(first) <= TITLE_MAX_LEN and (only or blank_after):
        title = first.strip()
        body = "# " + first + ("" if only else "\n" + "\n".join(lines[1:]))
        return title, body

    return generic_title(), text


def main():
    text = os.environ.get("RAW_TEXT", "").strip()
    if not text:
        return 1

    candidate = extract_url_candidate(text)
    if URL_PATTERN.match(candidate):
        sys.stdout.write("bookmark\n" + candidate)
        return 0

    title, body = resolve_markdown(text)
    sys.stdout.write("markdown\n" + title + "\n<<<SPLIT>>>\n" + body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
