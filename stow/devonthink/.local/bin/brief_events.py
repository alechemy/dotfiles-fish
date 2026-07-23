#!/usr/bin/env python3
"""
brief_events.py — ties briefing calendar events to DEVONthink notes.

An event's identity is its key: "YYYY-MM-DD-<slug of title>", stored in the
LinkedEvent custom-metadata field of every note attached to that event. The
metadata is the durable record; briefing text is a rendering of it.
dt-morning-brief re-derives each event's note links from LinkedEvent on every
regeneration (its scheduled runs replace the whole ## Briefing span, so
anything spliced into the text alone would not survive a RunAtLoad rerun),
while the smart rules stamp the metadata and splice the same links into the
already-rendered briefing so they appear without waiting for a regen:

  - adopt-meeting-note: notes born from the briefing's create-on-click links
    (tag "Meeting Note", name "YYYY-MM-DD <event title>") — swaps the event
    title's createMarkdown URL for the note's item link.
  - post-enrich-and-archive: handwritten notes matched to an event by name —
    adds the note as an indented sub-bullet under the event line.

Both rules also pre-set DailyNoteLinked so a briefing-linked note never
double-lists under ## Today's Notes.

Matching is deliberately conservative: stopword-filtered token overlap with a
unique winner required, so "Call with Priya" finds "Call Priya" but a note
titled just "Roundtable" refuses to choose between two roundtable events and
falls back to the Today's Notes path.

dt-morning-brief imports this as a module; the smart rules call the CLI:

    brief_events.py key <date> <title>            -> key
    brief_events.py adopt-key <record name>       -> "date\\tkey" ("" if the
                                                     name has no date prefix)
    brief_events.py match --name <note name> --cand <date> <file> [--cand ...]
                                                  -> "date\\tkey\\ttitle" or ""
    brief_events.py link-title <date> <key> <uuid>     < note.md > new.md
    brief_events.py insert-subbullet <date> <key> <bullet line> < note.md > new.md
"""

import re
import sys
import unicodedata
from datetime import date as date_type
from urllib.parse import quote

BRIEF_HEADER = "## Briefing"
REDACTED_TITLE = "Private event"
CREATE_TAG = "Meeting Note"
MATCH_THRESHOLD = 2 / 3

# Schedule noise, not meeting identity: dropping these lets "Call with Priya"
# reach "Call Priya", while the content tokens still have to carry the match.
STOPWORDS = frozenset("""
    a an and at for in of on or the to via w with
    call chat mtg meeting meet sync standup touchpoint checkin huddle catchup
    weekly biweekly monthly quarterly annual daily recurring series session
    edition
""".split())

ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$")
EVENT_LINE_RE = re.compile(r"^- (\d{1,2}:\d{2}(?:am|pm)) — (.+)$")
LINKED_TITLE_RE = re.compile(r"^\[(.+?)\]\(([^)\s]*)\)(.*)$")
TENTATIVE_RE = re.compile(r"\s*\(tentative\)\s*$")
HEADING_RE = re.compile(r"^#{1,2}\s")
ITEM_LINK_RE = re.compile(r"x-devonthink-item://([A-Za-z0-9-]+)")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def slug(title):
    return re.sub(r"[^a-z0-9]+", "-", norm(title)).strip("-")


def event_key(date, title):
    return f"{date}-{slug(title)}"


def parse_name_date(name):
    """(date, title) from a "YYYY-MM-DD <title>" record name, else None."""
    m = ISO_DATE_RE.match(name.strip())
    if not m:
        return None
    try:
        date_type.fromisoformat(m.group(1))
    except ValueError:
        return None
    return m.group(1), m.group(2)


def create_url(name, destination, text=""):
    """createMarkdown URL command. No `location` parameter, ever: DT treats
    a location as "download this URL as the document" and ignores `text`."""
    q = lambda s: quote(s, safe="")
    url = (f"x-devonthink://createMarkdown?title={q(name)}"
           f"&destination={q(destination)}&tags={q(CREATE_TAG)}&noselector=1")
    if text:
        url += f"&text={q(text)}"
    return url


def brief_span(lines):
    """(start, end) line indexes of the ## Briefing section body, or None.
    Same boundary rule as the bridge's sectionSpan: the section runs to the
    next level-1-or-2 heading, so ### blocks inside a section don't end it."""
    start = None
    for i, line in enumerate(lines):
        if line.strip() == BRIEF_HEADER:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start, len(lines)):
        if HEADING_RE.match(lines[i]):
            end = i
            break
    return start, end


def parse_events(text):
    """Event lines in the ## Briefing section, however they are rendered:
    plain, item-linked, or carrying a createMarkdown URL, with or without a
    trailing "(tentative)". Redacted events are withheld — a private event's
    title is not in the text, so nothing can (or should) match it."""
    lines = text.splitlines()
    span = brief_span(lines)
    if span is None:
        return []
    out = []
    for i in range(*span):
        m = EVENT_LINE_RE.match(lines[i])
        if not m:
            continue
        time_str, rest = m.groups()
        url = None
        suffix = ""
        lm = LINKED_TITLE_RE.match(rest)
        if lm:
            title, url, suffix = lm.groups()
        else:
            title = rest
            tm = TENTATIVE_RE.search(title)
            if tm:
                title, suffix = title[:tm.start()], title[tm.start():]
        if title == REDACTED_TITLE:
            continue
        out.append({"line": i, "time": time_str, "title": title,
                    "url": url, "suffix": suffix})
    return out


def tokens(s):
    toks = {t for t in re.findall(r"[a-z0-9]+", norm(s))
            if len(t) > 1 or t.isdigit()}
    content = toks - STOPWORDS
    return content or toks


def best_match(note_name, titles):
    """(title, status) — status is "match", "ambiguous", or "none".

    Overlap coefficient (|A∩B| / min(|A|,|B|)) over stopword-filtered tokens,
    threshold 2/3, and the winner must be strictly ahead of every other
    candidate: two same-day events a note matches equally well is a choice a
    heuristic must not make.
    """
    note_toks = tokens(note_name)
    scored = {}
    for title in titles:
        k = slug(title)
        if k in scored:
            continue
        ev_toks = tokens(title)
        inter = note_toks & ev_toks
        score = len(inter) / min(len(note_toks), len(ev_toks)) if inter else 0.0
        scored[k] = (score, title)
    if not scored:
        return None, "none"
    ranked = sorted(scored.values(), key=lambda st: -st[0])
    best_score, best_title = ranked[0]
    if best_score < MATCH_THRESHOLD:
        return None, "none"
    if len(ranked) > 1 and ranked[1][0] == best_score:
        return None, "ambiguous"
    return best_title, "match"


def link_title(text, date, key, uuid):
    """Point the key's event-title link at the note's item link, replacing a
    createMarkdown URL or wrapping a plain title. Already-item-linked lines
    are left alone, so the first note to claim an event keeps it."""
    lines = text.splitlines()
    trailing = "\n" if text.endswith("\n") else ""
    for ev in parse_events(text):
        if event_key(date, ev["title"]) != key:
            continue
        if ev["url"] and ev["url"].startswith("x-devonthink-item://"):
            return text
        lines[ev["line"]] = (f"- {ev['time']} — [{ev['title']}]"
                             f"(x-devonthink-item://{uuid}){ev['suffix']}")
        return "\n".join(lines) + trailing
    return text


def insert_subbullet(text, date, key, bullet):
    """Add `bullet` as the first sub-line under the key's event line,
    two-space indented to match the renderer. No-ops when the note's item
    link is already anywhere in the briefing section."""
    lines = text.splitlines()
    trailing = "\n" if text.endswith("\n") else ""
    span = brief_span(lines)
    if span is None:
        return text
    section = "\n".join(lines[span[0]:span[1]])
    m = ITEM_LINK_RE.search(bullet)
    marker = m.group(1) if m else bullet.strip()
    if marker in section:
        return text
    for ev in parse_events(text):
        if event_key(date, ev["title"]) != key:
            continue
        pos = ev["line"] + 1
        return "\n".join(lines[:pos] + ["  " + bullet.strip()] + lines[pos:]) + trailing
    return text


def match_note(name, candidates):
    """First candidate day whose briefing has a unique winner for `name`.

    `candidates` is [(date, briefing text)] in priority order. An ambiguous
    day stops the search rather than falling through: the note most plausibly
    belongs to that day, and linking it to the *other* day because this one
    had two lookalike events would be a confident wrong answer.
    """
    parsed = parse_name_date(name)
    if parsed:
        name = parsed[1]
    for date, text in candidates:
        titles = [ev["title"] for ev in parse_events(text)]
        title, status = best_match(name, titles)
        if status == "match":
            return {"date": date, "key": event_key(date, title), "title": title}
        if status == "ambiguous":
            return None
    return None


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "key" and len(argv) == 3:
        print(event_key(argv[1], argv[2]))
        return 0
    if cmd == "adopt-key" and len(argv) == 2:
        parsed = parse_name_date(argv[1])
        if parsed:
            print(f"{parsed[0]}\t{event_key(parsed[0], parsed[1])}")
        return 0
    if cmd == "match":
        name = None
        candidates = []
        i = 1
        while i < len(argv):
            if argv[i] == "--name" and i + 1 < len(argv):
                name = argv[i + 1]
                i += 2
            elif argv[i] == "--cand" and i + 2 < len(argv):
                with open(argv[i + 2], encoding="utf-8") as f:
                    candidates.append((argv[i + 1], f.read()))
                i += 3
            else:
                return 2
        if name is None:
            return 2
        hit = match_note(name, candidates)
        if hit:
            print(f"{hit['date']}\t{hit['key']}\t{hit['title']}")
        return 0
    if cmd == "link-title" and len(argv) == 4:
        sys.stdout.write(link_title(sys.stdin.read(), argv[1], argv[2], argv[3]))
        return 0
    if cmd == "insert-subbullet" and len(argv) == 4:
        sys.stdout.write(insert_subbullet(sys.stdin.read(), argv[1], argv[2], argv[3]))
        return 0
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
