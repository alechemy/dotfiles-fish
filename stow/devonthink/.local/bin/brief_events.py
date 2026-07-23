#!/usr/bin/env python3
"""
brief_events.py — ties briefing calendar events to DEVONthink notes.

An event's identity is its key: "YYYY-MM-DD-<slug of title>", stored in the
LinkedEvent custom-metadata field of every note attached to that event. The
metadata is the durable record; the daily note's event bullets are a rendering
of it. dt-morning-brief re-derives each event's note links from LinkedEvent on
every regeneration (its scheduled runs rebuild each event's machine sub-lines,
so anything spliced into the text alone would not survive a RunAtLoad rerun),
while the writers stamp the metadata:

  - dtnote-open.py: the dtnote:// handler behind a note-less event title —
    opens the owning note, creating it fully stamped only when missing.
  - adopt-meeting-note: backstop for meeting notes that arrive by hand (tag
    "Meeting Note", name "YYYY-MM-DD <event title>") — stamps them and swaps
    the day's event-title link for the note's item link in place.
  - post-enrich-and-archive: handwritten notes matched to an event by name —
    adds the note as an indented sub-bullet under the event line.

All writers also pre-set DailyNoteLinked so an event-linked note never
double-lists as its own timeline bullet.

Two event-line grammars coexist. Current daily notes are a single flat
timeline whose event bullets read "- <h:mmam>: 📅 <title>"; notes from before
the flatten carry a "## Briefing" section whose bullets read
"- <h:mmam> — <title>". Parsing accepts both (a note is one or the other, and
matching may run against yesterday's note across the cutover); edits emit in
the grammar of the line they touch.

The timeline grammar's machine/manual discriminator is a type emoji directly
after the time separator (📅 calendar, 🔗 web, 📄 PDF, ✏️ handwritten,
📝 note, 📔 journal — the untimed pinned form); machine sub-lines under an
event open with ✏️/📝 (note links), 👤 (people), ⚠️ (warnings), or a
"YYYY-MM-DD — " news-date prefix. Manual bullets carry no leading emoji and
are never rewritten; is_machine_bullet/is_machine_subline are the shared
classifiers (entity-filing strips machine lines before fact extraction).

Matching is deliberately conservative: stopword-filtered token overlap with a
unique winner required, so "Call with Priya" finds "Call Priya" but a note
titled just "Roundtable" refuses to choose between two roundtable events and
falls back to the plain timeline-bullet path.

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

EVENT_EMOJI = "\U0001F4C5"
JOURNAL_EMOJI = "\U0001F4D4"
# Bare forms; every pattern below allows an optional trailing U+FE0F, since
# ✏️/⚠️ are written with the variation selector and the rest without.
MACHINE_EMOJI = "\U0001F4C5\U0001F517\U0001F4C4✏\U0001F4DD\U0001F4D4"
SUBLINE_EMOJI = "✏\U0001F4DD\U0001F464⚠"

ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$")
EVENT_LINE_RE = re.compile(r"^- (\d{1,2}:\d{2}(?:am|pm)) — (.+)$")
TIMELINE_EVENT_RE = re.compile(
    rf"^- (\d{{1,2}}:\d{{2}}(?:am|pm)): {EVENT_EMOJI}\ufe0f? (.+)$")
MACHINE_BULLET_RE = re.compile(
    rf"^- (?:\d{{1,2}}:\d{{2}}(?:am|pm): )?[{MACHINE_EMOJI}]\ufe0f? ")
MACHINE_SUBLINE_RE = re.compile(
    rf"^\s+- (?:\[?[{SUBLINE_EMOJI}]\ufe0f? |\d{{4}}-\d{{2}}-\d{{2}} — )")
LINKED_TITLE_RE = re.compile(r"^\[(.+?)\]\(([^)\s]*)\)(.*)$")
TENTATIVE_RE = re.compile(r"\s*\(tentative\)\s*$")
HEADING_RE = re.compile(r"^#{1,2}\s")
ITEM_LINK_RE = re.compile(r"x-devonthink-item://([A-Za-z0-9-]+)")
TIMED_BULLET_RE = re.compile(r"^- (\d{1,2}):(\d{2})(am|pm)\b")
EMPTY_BULLET_RE = re.compile(r"^\s*[-*]\s*$")


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


def dtnote_url(date, title):
    """Create-on-click link for a note-less event, handled by DTNote.app →
    dtnote-open.py: opens the event's owning note, creating it first only if
    missing — semantics x-devonthink://createMarkdown cannot provide (it
    neither checks for an existing record nor navigates, so every click
    minted a fresh one)."""
    q = lambda s: quote(s, safe="")
    return f"dtnote://open?date={q(date)}&title={q(title)}"


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


def root_span(lines):
    """(start, end) line indexes of the root timeline: the lines after the
    leading H1, up to the next level-1-or-2 heading (a flat note has none, so
    the span runs to the end of the body)."""
    start = 0
    for i, line in enumerate(lines):
        if HEADING_RE.match(line):
            start = i + 1
            break
    end = len(lines)
    for i in range(start, len(lines)):
        if HEADING_RE.match(lines[i]):
            end = i
            break
    return start, end


def is_machine_bullet(line):
    """True for a top-level timeline bullet the pipeline owns: an optional
    time prefix followed directly by a type emoji."""
    return bool(MACHINE_BULLET_RE.match(line))


def is_machine_subline(line):
    """True for an indented line the pipeline owns under an event bullet:
    a note-link, person, or warning token (legacy inside-link ✏️/📝 forms
    included), or a news fact's date prefix."""
    return bool(MACHINE_SUBLINE_RE.match(line))


def bullet_minutes(line):
    """Minutes since midnight of a top-level timed bullet, else None."""
    m = TIMED_BULLET_RE.match(line)
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3) == "pm":
        hour += 12
    return hour * 60 + int(m.group(2))


def timeline_insert(lines, block):
    """A new line list with `block` (a bullet plus any indented sub-lines)
    inserted into the root timeline at its chronological position — parity
    with the bridge's timelineMerge insertion rule: before the first
    strictly-later timed bullet or the first pinned (untimed machine) bullet,
    else after the last non-blank line; untimed manual lines are anchors and
    are stepped over. A virgin skeleton's empty placeholder bullet is
    replaced instead. An untimed block sorts as later than every timed one,
    which lands it just before the pinned run (or at the end)."""
    start, end = root_span(lines)
    minutes = bullet_minutes(block[0])

    span = lines[start:end]
    if all(not l.strip() or EMPTY_BULLET_RE.match(l) for l in span):
        for i in range(start, end):
            if EMPTY_BULLET_RE.match(lines[i]):
                return lines[:i] + list(block) + lines[i + 1:]

    last = None
    seen = False
    i = start
    while i < end:
        line = lines[i]
        if not line.strip():
            if not seen:
                last = i + 1
            i += 1
            continue
        group_end = i + 1
        if line.startswith("- "):
            while group_end < end and lines[group_end][:1].isspace() \
                    and lines[group_end].strip():
                group_end += 1
        seen = True
        t = bullet_minutes(line)
        if t is not None and minutes is not None and t > minutes:
            return lines[:i] + list(block) + lines[i:]
        if t is None and MACHINE_BULLET_RE.match(line):
            return lines[:i] + list(block) + lines[i:]
        last = group_end
        i = group_end
    at = last if last is not None else start
    return lines[:at] + list(block) + lines[at:]


def _parse_event_rest(rest):
    """(title, url, suffix) from the text after an event line's prefix."""
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
    return title, url, suffix


def parse_events(text):
    """Event lines in either grammar — the root timeline's "- <time>: 📅 …"
    bullets and a legacy ## Briefing section's "- <time> — …" bullets —
    however the title is rendered: plain, item-linked, or carrying a dtnote://
    URL, with or without a trailing "(tentative)". Each entry carries its
    grammar as "style" ("timeline" or "legacy") so edits can emit in kind.
    Redacted events are withheld — a private event's title is not in the
    text, so nothing can (or should) match it."""
    lines = text.splitlines()
    out = []
    spans = [(root_span(lines), TIMELINE_EVENT_RE, "timeline")]
    legacy = brief_span(lines)
    if legacy is not None:
        spans.append((legacy, EVENT_LINE_RE, "legacy"))
    for (start, end), line_re, style in spans:
        for i in range(start, end):
            m = line_re.match(lines[i])
            if not m:
                continue
            time_str, rest = m.groups()
            title, url, suffix = _parse_event_rest(rest)
            if title == REDACTED_TITLE:
                continue
            out.append({"line": i, "time": time_str, "title": title,
                        "url": url, "suffix": suffix, "style": style})
    out.sort(key=lambda ev: ev["line"])
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


def event_line(ev, uuid):
    """The event's bullet line, item-linked, rendered in its own grammar."""
    linked = f"[{ev['title']}](x-devonthink-item://{uuid}){ev['suffix']}"
    if ev["style"] == "legacy":
        return f"- {ev['time']} — {linked}"
    return f"- {ev['time']}: {EVENT_EMOJI} {linked}"


def link_title(text, date, key, uuid):
    """Point the key's event-title link at the note's item link, replacing a
    dtnote:// URL or wrapping a plain title. Already-item-linked lines are
    left alone, so the first note to claim an event keeps it."""
    lines = text.splitlines()
    trailing = "\n" if text.endswith("\n") else ""
    for ev in parse_events(text):
        if event_key(date, ev["title"]) != key:
            continue
        if ev["url"] and ev["url"].startswith("x-devonthink-item://"):
            return text
        lines[ev["line"]] = event_line(ev, uuid)
        return "\n".join(lines) + trailing
    return text


def insert_subbullet(text, date, key, bullet):
    """Add `bullet` as the first sub-line under the key's event line,
    two-space indented to match the renderer. No-ops when the note's item
    link is already anywhere in the event's own span (the root timeline, or
    a legacy note's briefing section)."""
    lines = text.splitlines()
    trailing = "\n" if text.endswith("\n") else ""
    m = ITEM_LINK_RE.search(bullet)
    marker = m.group(1) if m else bullet.strip()
    for ev in parse_events(text):
        if event_key(date, ev["title"]) != key:
            continue
        span = brief_span(lines) if ev["style"] == "legacy" else root_span(lines)
        if marker in "\n".join(lines[span[0]:span[1]]):
            return text
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
