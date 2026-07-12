#!/usr/bin/python3
"""
dt-morning-brief.py — contextual resurfacing for the entity layer.

Builds a "who am I about to meet" briefing from today's calendar plus the
Person records in Lorebook/20_ENTITIES/People and appends it to today's
daily note as a `## Briefing` section. On Mondays (or with --weekly) it
also appends a `## Reconnect` section listing people whose LastContact
has drifted past their relationship tier's threshold. A `## Birthdays`
section lists roster-matched macOS Contacts whose birthday falls within
the next two weeks — matched, not all of Contacts, which is why the
all-of-Contacts Birthdays calendar stays in SKIP_CALENDARS. Whenever
filing proposals sit unreviewed in `/20_ENTITIES/_Review`, an
`## Entity Review` line reports the backlog count.

The brief reads live from the records, so it is never stale. Mutations are
limited to the daily-note section insert (idempotent via an HTML comment
marker per section per day) and LastContact bumps from two sources: every
person matched in YESTERDAY's calendar — yesterday because the day is
complete, so a meeting that was cancelled after the morning run never
counts as contact — and everyone texted with since yesterday, read from
Messages' chat.db (messages have no cancellation concept, so today's
count too). Both keep the Reconnect digest honest for people whose
contact happens outside filed facts; bump_lastcontact only ever raises
the date, so re-runs are harmless.

The Messages read is deliberately narrow: a read-only SQLite connection
that selects handle identifiers, dates, and is_from_me — structurally
never the text column — and maps handles to the roster live through
macOS Contacts (per the entity-layer decision, phone numbers are never
stored in DEVONthink). Received messages attribute to their sender in
any chat; sent messages count only in 1:1 chats, so a group broadcast
never marks every member as contacted.

Section placement: jots are inserted relative to the `## Today's Notes`
header (see insert-jot-into-daily-note.py), targeting the last content
bullet BEFORE it. The briefing must therefore sit AFTER that header, so
this script guarantees `## Today's Notes` exists before appending its own
sections at the end of the note.

Calendar access goes through calendar-events-json.js (EventKit via
osascript, Apple-signed TCC identity), Contacts access through
contacts-json.js (same pattern), and DEVONthink access through
entity-dt-bridge.js. All are invoked via /usr/bin/osascript.

Besides the daily note, each run writes the sections' structured data to
~/.local/state/devonthink/morning-brief.json and hands it to
trmnl-push-brief.py, which mirrors the brief onto a TRMNL e-ink dashboard
(silent no-op until its webhook is configured). The snapshot carries
Reconnect every day, not just Mondays — a glance surface benefits from it
daily and the data is computed on every run anyway.

Usage:
    dt-morning-brief.py              # normal launchd-driven run
    dt-morning-brief.py --dry-run    # print the sections, write nothing
    dt-morning-brief.py --force      # bypass battery/role gates
    dt-morning-brief.py --weekly     # include the Reconnect section today
    dt-morning-brief.py --date YYYY-MM-DD
    dt-morning-brief.py --backfill-contacts [--days N]
                                     # replay past calendar days into
                                     # LastContact (default 365); run once
                                     # after seeding People
    dt-morning-brief.py --backfill-messages [--days N]
                                     # replay Messages history into
                                     # LastContact (default 365); run once

Config (~/.config/dt-pipeline/entities.conf, shared with entity-filing.py):
    SKIP_ATTENDEE_PATTERN=<regex>    attendee names matching this are not
                                     people. Exchange reports conference
                                     rooms with participantType Person and
                                     is otherwise indistinguishable from a
                                     human, so the name is the only signal.
"""

import calendar as calmod
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log

log = setup_log("morning-brief")

BRIDGE = os.path.expanduser("~/.local/bin/entity-dt-bridge.js")
CALENDAR = os.path.expanduser("~/.local/bin/calendar-events-json.js")
CONTACTS = os.path.expanduser("~/.local/bin/contacts-json.js")
MESSAGES_DB = os.path.expanduser("~/Library/Messages/chat.db")

# Received messages attribute to their sender in any chat; sent messages
# attribute to the peer only in single-participant chats, so a group
# broadcast never counts as contact with every member. item_type 0 keeps
# group housekeeping events (renames, joins) from counting. Only handle
# identifiers and dates are ever selected — never message text.
MESSAGES_QUERY = """
SELECT h.id, MAX(m.date) FROM message m
JOIN handle h ON h.ROWID = m.handle_id
WHERE m.date >= ? AND m.is_from_me = 0 AND m.item_type = 0
GROUP BY h.id
UNION ALL
SELECT h.id, MAX(m.date) FROM message m
JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
JOIN chat_handle_join chj ON chj.chat_id = cmj.chat_id
JOIN handle h ON h.ROWID = chj.handle_id
WHERE m.date >= ? AND m.is_from_me = 1 AND m.item_type = 0
  AND (SELECT COUNT(*) FROM chat_handle_join c2
       WHERE c2.chat_id = cmj.chat_id) = 1
GROUP BY h.id
"""
BRIEF_HEADER = "## Briefing"
RECONNECT_HEADER = "## Reconnect"
BIRTHDAYS_HEADER = "## Birthdays"
BIRTHDAY_LOOKAHEAD = 14
REVIEW_HEADER = "## Entity Review"
ON_THIS_DAY_HEADER = "## On This Day"
ON_THIS_DAY_YEARS = 5
ON_THIS_DAY_PER_YEAR = 5
JOURNAL_HEADER = "## Journal"
JOURNAL_STATE = os.path.expanduser(
    "~/.local/state/devonthink/boox/state.json")
JOURNAL_STAGING = os.path.expanduser(
    "~/.local/state/devonthink/boox/staging")
JOURNAL_NOTEBOOK_RE = re.compile(r"^\d{4} Journal$")
JOURNAL_LAPSE_DAYS = 7
REVIEW_PATH = "/20_ENTITIES/_Review"
APPROVED_PATH = REVIEW_PATH + "/Approved"
LOG_BULLET_RE = re.compile(r"^- \d{4}-\d{2}-\d{2} — ")
FACT_MARKER_RE = re.compile(r"\s*<!--\s*fact:[0-9a-f]+\s*-->")

# Days without contact before a person surfaces in the Reconnect digest,
# keyed by the Relationship field. Absent/other relationships never surface.
RECONNECT_DAYS = {
    "family": 30,
    "close-friend": 30,
    "friend": 60,
    "colleague": 90,
}
# Recognized, but deliberately without a threshold — never surfaced, never warned.
RECONNECT_NEVER = {"acquaintance"}
RECONNECT_LIMIT = 10
# Only "active" surfaces in Reconnect; the rest are recognized ways to be silent.
ENTITY_STATUSES = {"active", "dormant", "archived", "deceased"}

# Calendars that never contain meetings worth briefing on.
SKIP_CALENDARS = {"Birthdays", "Siri Suggestions", "US Holidays", "Holidays"}

# Unmatched attendees are listed individually (they prompt record creation)
# only up to this many; past it, one summary line — a 38-person CAB meeting
# must not dump 38 noise lines into the daily note.
UNMATCHED_LIST_MAX = 8

CONFIG_FILE = os.path.expanduser("~/.config/dt-pipeline/entities.conf")
ENTITY_STATE_FILE = os.path.expanduser(
    "~/.local/state/devonthink/entity-filing-state.json")
SNAPSHOT_FILE = os.path.expanduser(
    "~/.local/state/devonthink/morning-brief.json")
TRMNL_PUSH = os.path.expanduser("~/.local/bin/trmnl-push-brief.py")
DEFAULT_SKIP_ATTENDEE = r"\bVC\b|\bConference\b|\bRoom\b|\d+\s?ppl"
BACKFILL_DAYS = 365
PARKED_LIST_MAX = 5


def run_osascript(script, args, timeout=120):
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", script] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{os.path.basename(script)} failed: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


class BridgeUnavailable(RuntimeError):
    """DEVONthink is not answering or the Lorebook database is not open.

    Transient by nature (app relaunch, database still loading); the brief's
    launchd retries at 05:45/06:30/08:00 cover it, so the run ends quietly.
    """


def run_bridge(ops):
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"ops": ops}, f)
        out = run_osascript(BRIDGE, [path])
    finally:
        os.unlink(path)
    if not out.get("ok"):
        if out.get("unavailable"):
            raise BridgeUnavailable(out.get("error"))
        raise RuntimeError(f"bridge error: {out.get('error')}")
    return out["results"]


def load_skip_attendee_re():
    pattern = DEFAULT_SKIP_ATTENDEE
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("SKIP_ATTENDEE_PATTERN="):
                    pattern = line.partition("=")[2].strip()
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        log.warning("bad SKIP_ATTENDEE_PATTERN regex, ignoring: %s", exc)
        return None


def real_attendees(ev, skip_re):
    """Other humans on an event. Rooms and resources are indistinguishable
    from people on every EventKit field under Exchange, so they are excluded
    by name."""
    out = []
    for a in ev["attendees"]:
        if a["is_self"] or not a["is_person"]:
            continue
        if not (a["name"] or a["email"]):
            continue
        if skip_re and a["name"] and skip_re.search(a["name"]):
            continue
        out.append(a)
    return out


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def md_enum(value):
    """Fold a hand-typed metadata value onto its canonical token: DEVONthink
    types EntityStatus and Relationship as free text, so "Close Friend" and
    "close_friend" reach us verbatim and would silently miss an exact
    lookup — dropping the person out of Reconnect with no error."""
    return re.sub(r"[\s_]+", "-", norm(value))


def person_index(people):
    """Map normalized names, aliases, and emails to person dicts."""
    index = {}
    for p in people:
        keys = [norm(p["name"])]
        keys += [norm(a) for a in p.get("aliases", "").split(",")]
        # The Email field is url-typed in DT, so a GUI-entered value can
        # carry a mailto: prefix even though scripts store bare addresses.
        email = norm(p.get("md", {}).get("mdemail", "")).removeprefix("mailto:")
        if email:
            keys.append(email)
        for k in keys:
            if k:
                index.setdefault(k, []).append(p)
    return index


def match_person(index, name, email):
    for key in (norm(email), norm(name)):
        hits = index.get(key)
        if key and hits:
            return hits[0] if len(hits) == 1 else None
    return None


def fmt_time(iso):
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S")
    return t.strftime("%-I:%M%p").lower()


def person_summary_line(p):
    md = p.get("md", {})
    bits = []
    role = md.get("mdrole", "")
    employer = md.get("mdemployer", "")
    if role and employer:
        bits.append(f"{role} at {employer}")
    elif role or employer:
        bits.append(role or employer)
    if md.get("mdcity"):
        bits.append(md["mdcity"])
    if md.get("mdlastcontact"):
        bits.append(f"last contact {md['mdlastcontact']}")
    link = f"[{p['name']}](x-devonthink-item://{p['uuid']})"
    return f"- {link} — {' · '.join(bits)}" if bits else f"- {link}"


def recent_log_bullets(p, limit=3):
    """Newest bullets by fact date, not append order — a backlog drain can
    append years-old facts after current ones. Renders in document order."""
    lines = (p.get("body") or "").split("\n")
    bullets = [
        ln for ln in lines
        if LOG_BULLET_RE.match(ln) and not ln.rstrip().endswith("— Created.")
    ]
    by_date = sorted(enumerate(bullets), key=lambda t: (t[1][2:12], t[0]))
    newest = sorted(by_date[-limit:] if limit else [], key=lambda t: t[0])
    return ["  " + FACT_MARKER_RE.sub("", ln).rstrip() for _, ln in newest]


def title_matches(people, title):
    """People whose name or alias appears in the event title. Personal
    calendars rarely carry structured attendees ("Call with Jake"), so the
    title is a first-class matching surface, not a fallback."""
    hay = f" {norm(title)} "
    hits = []
    for p in people:
        keys = [norm(p["name"])] + [norm(a) for a in p.get("aliases", "").split(",")]
        for k in keys:
            if k and re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", hay):
                hits.append(p)
                break
    return hits


def brief_blocks(events, people, skip_re):
    """Structured briefing blocks per briefable event: matched roster people
    (attendee order, then title matches) and unmatched-attendee labels."""
    index = person_index(people)
    blocks = []
    for ev in events:
        if ev["all_day"] or ev["declined"]:
            continue
        if ev["calendar"] in SKIP_CALENDARS:
            continue
        others = real_attendees(ev, skip_re)
        by_title = title_matches(people, ev["title"])
        if not others and not by_title:
            continue
        seen = set()
        matched = []
        unmatched = []
        for a in others:
            ident = a["email"] or norm(a["name"])
            if ident in seen:
                continue
            seen.add(ident)
            p = match_person(index, a["name"], a["email"])
            if p:
                if p["uuid"] in seen:
                    continue
                seen.add(p["uuid"])
                matched.append(p)
            else:
                who = a["name"] or a["email"]
                detail = f" ({a['email']})" if a["name"] and a["email"] else ""
                unmatched.append(f"{who}{detail}")
        for p in by_title:
            if p["uuid"] in seen:
                continue
            seen.add(p["uuid"])
            matched.append(p)
        blocks.append({"time": fmt_time(ev["start"]), "title": ev["title"],
                       "people": matched, "unmatched": unmatched})
    return blocks


def render_brief(blocks, today):
    if not blocks:
        return None
    out = []
    for b in blocks:
        lines = [f"### {b['time']} — {b['title']}", ""]
        for p in b["people"]:
            lines.append(person_summary_line(p))
            lines.extend(recent_log_bullets(p))
        if len(b["unmatched"]) <= UNMATCHED_LIST_MAX:
            lines.extend(f"- {u} — no entity record yet"
                         for u in b["unmatched"])
        else:
            lines.append(
                f"- {len(b['unmatched'])} attendees without entity records")
        out.append("\n".join(lines))
    return f"<!-- brief:{today} -->\n\n" + "\n\n".join(out)


def build_brief(events, people, today, skip_re):
    return render_brief(brief_blocks(events, people, skip_re), today)


def contact_bumps(events, people, day, skip_re):
    """One bump op per person per day. `day` is the fallback for a single-day
    dump; a range dump tags each event with its own date."""
    index = person_index(people)
    ops = []
    seen = set()
    for ev in events:
        if ev["all_day"] or ev["declined"] or ev["calendar"] in SKIP_CALENDARS:
            continue
        when = ev.get("date") or day
        matched = [
            p for p in (match_person(index, a["name"], a["email"])
                        for a in real_attendees(ev, skip_re))
            if p
        ]
        matched.extend(title_matches(people, ev["title"]))
        for p in matched:
            key = (p["uuid"], when)
            if key in seen:
                continue
            seen.add(key)
            ops.append({"op": "bump_lastcontact", "uuid": p["uuid"], "date": when})
    return ops


def reconnect_overdue(people, today):
    """Active tiered people past their contact threshold, most overdue first:
    (overdue_ratio, days_since_contact, person); days None means no recorded
    contact at all (maximally overdue)."""
    today_d = date.fromisoformat(today)
    overdue = []
    for p in people:
        md = p.get("md", {})
        status = md_enum(md.get("mdentitystatus", "")) or "active"
        if status not in ENTITY_STATUSES:
            # Fail open: an unrecognized status surfaces the person alongside a
            # warning, rather than hiding them the way a typo'd "dormant" would.
            log.warning(
                "unknown EntityStatus %r on %s — treating as active; expected "
                "one of %s", md.get("mdentitystatus"), p["name"],
                ", ".join(sorted(ENTITY_STATUSES)),
                extra={"record_name": p["name"], "record_uuid": p["uuid"]})
            status = "active"
        if status != "active":
            continue
        rel = md_enum(md.get("mdrelationship", ""))
        threshold = RECONNECT_DAYS.get(rel)
        if not threshold:
            if rel and rel not in RECONNECT_NEVER:
                log.warning(
                    "unknown Relationship %r — %s will never appear in "
                    "Reconnect; expected one of %s",
                    md.get("mdrelationship"), p["name"],
                    ", ".join(sorted(set(RECONNECT_DAYS) | RECONNECT_NEVER)),
                    extra={"record_name": p["name"], "record_uuid": p["uuid"]})
            continue
        last = md.get("mdlastcontact", "")
        if not last:
            # A tiered relationship with no contact ever logged is the most
            # overdue case possible, not an exempt one.
            overdue.append((float("inf"), None, p))
            continue
        try:
            days = (today_d - date.fromisoformat(last)).days
        except ValueError:
            # Free-text field; hiding the person on a hand-typed date would be
            # worse than the missing-LastContact case it otherwise resembles.
            log.warning(
                "unparseable LastContact %r on %s — treating as no recorded "
                "contact", last, p["name"],
                extra={"record_name": p["name"], "record_uuid": p["uuid"]})
            overdue.append((float("inf"), None, p))
            continue
        if days > threshold:
            overdue.append((days / threshold, days, p))
    overdue.sort(key=lambda x: -x[0])
    return overdue


def render_reconnect(overdue, today):
    if not overdue:
        return None
    lines = [f"<!-- reconnect:{today} -->", ""]
    for _, days, p in overdue[:RECONNECT_LIMIT]:
        md = p.get("md", {})
        rel = md.get("mdrelationship", "")
        link = f"[{p['name']}](x-devonthink-item://{p['uuid']})"
        if days is None:
            lines.append(f"- {link} — {rel} · no recorded contact")
        else:
            lines.append(
                f"- {link} — {rel} · last contact"
                f" {md.get('mdlastcontact')} ({days} days)"
            )
    return "\n".join(lines)


def build_reconnect(people, today):
    return render_reconnect(reconnect_overdue(people, today), today)


def match_contact(index, contact):
    """Resolve a Contacts card to a roster person: emails first (the precise
    key), then full name, then nickname — the first key with any roster hit
    decides, and ambiguity on that key means no match, same as match_person."""
    keys = list(contact.get("emails") or [])
    keys += [contact.get("name", ""), contact.get("nickname", "")]
    for key in (norm(k) for k in keys):
        hits = index.get(key)
        if key and hits:
            return hits[0] if len(hits) == 1 else None
    return None


def birthday_occurrence(month, day, start, lookahead):
    """Date the birthday is celebrated within [start, start+lookahead], or
    None. A Feb 29 birthday falls on Feb 28 in non-leap years."""
    for off in range(lookahead + 1):
        d = start + timedelta(days=off)
        if (d.month, d.day) == (month, day):
            return d
        if ((month, day) == (2, 29) and (d.month, d.day) == (2, 28)
                and not calmod.isleap(d.year)):
            return d
    return None


def birthday_rows(contacts, people, today, lookahead=BIRTHDAY_LOOKAHEAD):
    """Upcoming birthdays for roster-matched Contacts cards only — the whole
    point vs. the skipped all-of-Contacts Birthdays calendar. Read live from
    Contacts each run, never stored on the Person record: identifiers are
    matching keys, not knowledge, and a stored copy would drift. Rows are
    (celebration_date, age_or_None, person), soonest first."""
    index = person_index(people)
    start = date.fromisoformat(today)
    rows = []
    seen = set()
    for c in contacts:
        b = c.get("birthday") or {}
        month, day = b.get("month"), b.get("day")
        if not month or not day:
            continue
        p = match_contact(index, c)
        if not p or p["uuid"] in seen:
            continue
        when = birthday_occurrence(month, day, start, lookahead)
        if not when:
            continue
        seen.add(p["uuid"])
        year = b.get("year")
        # Plausibility guard: some clients store year-less birthdays with a
        # sentinel year (1604) rather than omitting it.
        age = when.year - year if year and 1900 <= year <= when.year else None
        rows.append((when, age, p))
    rows.sort(key=lambda r: (r[0], norm(r[2]["name"])))
    return rows


def render_birthdays(rows, today):
    if not rows:
        return None
    start = date.fromisoformat(today)
    lines = [f"<!-- birthdays:{today} -->", ""]
    for when, age, p in rows:
        link = f"[{p['name']}](x-devonthink-item://{p['uuid']})"
        what = f"turns {age}" if age is not None else "birthday"
        suffix = " (today!)" if when == start else ""
        lines.append(f"- {when.isoformat()} — {link} — {what}{suffix}")
    return "\n".join(lines)


def build_birthdays(contacts, people, today, lookahead=BIRTHDAY_LOOKAHEAD):
    return render_birthdays(
        birthday_rows(contacts, people, today, lookahead), today)


def apple_ns(day):
    """Local midnight of an ISO day as Apple nanoseconds (Cocoa epoch)."""
    local_midnight = datetime.fromisoformat(day).astimezone()
    epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
    return int((local_midnight - epoch).total_seconds() * 1_000_000_000)


def apple_ts_to_local_date(raw):
    """chat.db stores nanoseconds since the Cocoa epoch, except rows migrated
    from pre-High-Sierra installs, which are plain seconds."""
    secs = raw / 1_000_000_000 if raw > 1e12 else raw
    dt = datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=secs)
    return dt.astimezone().date().isoformat()


def norm_handle(h):
    """Messages handles are phone numbers or iMessage emails. Phones fold to
    their last 10 digits so +1/formatting variants between chat.db and the
    Contacts card can't miss; anything shorter (short codes) stays as-is and
    simply never matches a card."""
    h = (h or "").strip().lower()
    if "@" in h:
        return h
    digits = re.sub(r"\D", "", h)
    return digits[-10:] if len(digits) >= 10 else digits


def handle_index(contacts, people):
    """Normalized phone/email handle -> roster person, via each card's
    match_contact resolution. A handle claimed by cards resolving to two
    different people is dropped rather than guessed."""
    index = person_index(people)
    out = {}
    ambiguous = set()
    for c in contacts:
        p = match_contact(index, c)
        if not p:
            continue
        for raw in list(c.get("phones") or []) + list(c.get("emails") or []):
            k = norm_handle(raw)
            if not k:
                continue
            if k in out and out[k]["uuid"] != p["uuid"]:
                ambiguous.add(k)
                continue
            out[k] = p
    for k in ambiguous:
        out.pop(k, None)
    return out


def message_bumps(handle_dates, index):
    """One bump op per person at their newest message date across all of
    their handles. handle_dates rows are (handle, raw_apple_timestamp)."""
    latest = {}
    for handle, raw in handle_dates:
        p = index.get(norm_handle(handle))
        if not p:
            continue
        d = apple_ts_to_local_date(raw)
        if d > latest.get(p["uuid"], ""):
            latest[p["uuid"]] = d
    return [{"op": "bump_lastcontact", "uuid": u, "date": d}
            for u, d in latest.items()]


def query_messages(since_day):
    """Latest message date per handle since local midnight of since_day.
    Read-only; any failure (no Full Disk Access, schema drift, database
    locked) degrades to no bumps with a warning the watchdog surfaces."""
    try:
        conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True,
                               timeout=5)
        try:
            cutoff = apple_ns(since_day)
            return conn.execute(MESSAGES_QUERY, (cutoff, cutoff)).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Messages query failed: %s", exc)
        return []


def parked_sources():
    """Sources entity-filing gave up on after repeated failures. Read from
    its state file; anything unreadable degrades to 'no parked sources'
    rather than blocking the brief."""
    try:
        with open(ENTITY_STATE_FILE) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    parked = state.get("parked")
    return parked if isinstance(parked, dict) else {}


def parked_lines(parked):
    if not parked:
        return []
    noun = "source" if len(parked) == 1 else "sources"
    lines = [f"- {len(parked)} {noun} parked after repeated extraction "
             f"failures — fix and retry with `entity-filing.py --force "
             f"<uuid>`, or edit the source (any change retries it):"]
    ordered = sorted(parked.items(),
                     key=lambda kv: kv[1].get("parked_at", ""), reverse=True)
    for uuid, info in ordered[:PARKED_LIST_MAX]:
        name = info.get("name") or uuid
        err = str(info.get("last_error") or "").strip()
        suffix = f" — {err[:100]}" if err else ""
        lines.append(f"  - [{name}](x-devonthink-item://{uuid}){suffix}")
    if len(parked) > PARKED_LIST_MAX:
        lines.append(f"  - … and {len(parked) - PARKED_LIST_MAX} more")
    return lines


def review_backlog(today):
    """Filing review backlog: pending/approved counts, the parked state-file
    dict, and each review group's UUID so the nudge can link straight to it.
    None when the bridge count failed (BridgeUnavailable still propagates)."""
    try:
        children, approved, review_group, approved_group = run_bridge([
            {"op": "list_group", "path": REVIEW_PATH},
            {"op": "list_group", "path": APPROVED_PATH},
            {"op": "get_at_path", "path": REVIEW_PATH},
            {"op": "get_at_path", "path": APPROVED_PATH},
        ])
    except BridgeUnavailable:
        raise
    except Exception as exc:
        log.warning("could not count review proposals: %s", exc)
        return None
    pending = [c for c in children if c["name"] != "Approved"]
    return {
        "pending": len(pending),
        "approved": len(approved),
        "parked": parked_sources(),
        "review_uuid": (review_group or {}).get("uuid"),
        "approved_uuid": (approved_group or {}).get("uuid"),
    }


def group_link(label, uuid):
    """Markdown item link to a DEVONthink group, degrading to the bare path
    when the UUID lookup came back empty — a dead link is worse than text."""
    if not uuid:
        return f"`{label}`"
    return f"[{label}](x-devonthink-item://{uuid})"


def render_review(backlog, today):
    """Surface the filing review backlog so proposals don't sit unseen — the
    Approved subgroup is the apply drop-zone, so it isn't a backlog, but a
    proposal filing refused to apply stays there and would otherwise be
    invisible: nothing counts it and its log line is only a WARNING. Parked
    sources are included for the same reason: a note that repeatedly failed
    extraction would otherwise vanish from every review surface."""
    if backlog is None:
        return None
    pending = backlog["pending"]
    approved = backlog["approved"]
    parked = backlog["parked"]
    if not pending and not approved and not parked:
        return None
    lines = [f"<!-- review-nudge:{today} -->", ""]
    if pending:
        noun = "proposal" if pending == 1 else "proposals"
        link = group_link("20_ENTITIES/_Review", backlog["review_uuid"])
        lines.append(f"- {pending} filing {noun} awaiting review in {link}")
    if approved:
        noun = "proposal" if approved == 1 else "proposals"
        link = group_link("20_ENTITIES/_Review/Approved",
                          backlog["approved_uuid"])
        lines.append(f"- {approved} approved {noun} in {link} did not apply "
                     f"— see `entity-filing` in the pipeline log")
    lines.extend(parked_lines(parked))
    return "\n".join(lines)


def review_nudge(today):
    return render_review(review_backlog(today), today)


def journal_status_info(today, state, staged_count):
    """Non-None only when yesterday's journal page never arrived, so a broken
    Boox→Dropbox sync is distinguishable from simply not journaling — the
    watchdog only catches dead agents, not an empty staging folder. Quiet
    unless the habit is active: no report before the first entry ever files,
    and none once the newest entry is older than JOURNAL_LAPSE_DAYS."""
    notebooks = {name: nb for name, nb in
                 (state or {}).get("notebooks", {}).items()
                 if JOURNAL_NOTEBOOK_RE.match(name)}
    entry_dates = {d for nb in notebooks.values() for d in nb.get("entries", {})}
    if not entry_dates:
        return None
    t = date.fromisoformat(today)
    yesterday = (t - timedelta(days=1)).isoformat()
    if yesterday in entry_dates:
        return None
    if (t - date.fromisoformat(max(entry_dates))).days > JOURNAL_LAPSE_DAYS:
        return None
    pending = sum(1 for nb in notebooks.values() for p in nb.get("pages", [])
                  if not p.get("date") and not p.get("parked"))
    parked = sum(1 for nb in notebooks.values() for p in nb.get("pages", [])
                 if p.get("parked"))
    return {"pending": pending, "parked": parked, "staged": staged_count}


def render_journal(info, today):
    if info is None:
        return None
    pending, parked, staged = info["pending"], info["parked"], info["staged"]
    lines = [f"<!-- journal-status:{today} -->", ""]
    if pending or staged:
        detail = f"{pending} page(s) pending OCR" if pending else \
            "an export is staged"
        if parked:
            detail += f", {parked} parked"
        lines.append(f"- Yesterday's journal entry hasn't been processed "
                     f"yet ({detail}) — journal-process catches up on "
                     f"AC/idle")
    elif parked:
        lines.append(f"- {parked} journal page(s) are parked — "
                     f"`journal-process.py --status` has the reasons")
    else:
        lines.append("- No journal entry arrived for yesterday — if you "
                     "wrote one, check the Boox's Dropbox sync")
    return "\n".join(lines)


def journal_status_lines(today, state, staged_count):
    return render_journal(journal_status_info(today, state, staged_count),
                          today)


def load_journal_state():
    """(state, staged_export_count) from the Boox pipeline's files, or None
    when there is no readable state — the brief then stays silent rather
    than reporting a broken habit it can't see."""
    try:
        with open(JOURNAL_STATE) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        staged = len([n for n in os.listdir(JOURNAL_STAGING)
                      if n.endswith(".pdf")])
    except OSError:
        staged = 0
    return state, staged


def build_journal_status(today):
    loaded = load_journal_state()
    if loaded is None:
        return None
    return journal_status_lines(today, *loaded)


def on_this_day_rows(today):
    """Anniversary resurfacing from metadata the pipeline already writes:
    records whose EventDate falls on this day in past years, plus the daily
    note from one year ago. Returns (rows, last_year_daily_or_None), or None
    when the lookup failed."""
    t = date.fromisoformat(today)
    ops = []
    year_dates = []
    for back in range(1, ON_THIS_DAY_YEARS + 1):
        try:
            past = t.replace(year=t.year - back).isoformat()
        except ValueError:
            continue
        year_dates.append(past)
        ops.append({"op": "search", "query": f"mdeventdate=={past}",
                    "limit": ON_THIS_DAY_PER_YEAR})
    if not year_dates:
        return None
    last_year = year_dates[0]
    ops.append({"op": "get_at_path", "path": f"/10_DAILY/{last_year}.md"})
    try:
        results = run_bridge(ops)
    except BridgeUnavailable:
        raise
    except Exception as exc:
        log.warning("on-this-day lookup failed: %s", exc)
        return None
    rows = []
    for past, hits in zip(year_dates, results):
        back = t.year - date.fromisoformat(past).year
        for h in hits or []:
            rows.append({"years": back, "name": h["name"], "uuid": h["uuid"],
                         "kind": h.get("documenttype") or ""})
    return rows, results[-1]


def render_on_this_day(got, today):
    if got is None:
        return None
    rows, daily = got
    lines = [f"<!-- on-this-day:{today} -->", ""]
    for r in rows:
        noun = "year" if r["years"] == 1 else "years"
        kind = f" ({r['kind']})" if r["kind"] else ""
        lines.append(f"- {r['years']} {noun} ago: "
                     f"[{r['name']}](x-devonthink-item://{r['uuid']}){kind}")
    if daily:
        lines.append(f"- One year ago today: "
                     f"[{daily['name']}](x-devonthink-item://{daily['uuid']})")
    return "\n".join(lines) if rows or daily else None


def build_on_this_day(today):
    return render_on_this_day(on_this_day_rows(today), today)


def person_snapshot(p):
    md = p.get("md", {})
    out = {"name": p["name"]}
    for key, field in (("role", "mdrole"), ("employer", "mdemployer"),
                       ("city", "mdcity"), ("last", "mdlastcontact")):
        if md.get(field):
            out[key] = md[field]
    return out


def build_snapshot(today, blocks, overdue, bdays, backlog, journal_info, otd):
    """Everything the TRMNL screen renders, unabridged — trmnl-push-brief.py
    owns fitting it to the webhook byte budget. Reconnect is included every
    day even though the daily note carries it only on Mondays: the e-ink
    dashboard is a glance surface and the data is computed anyway."""
    start = date.fromisoformat(today)
    snap = {
        "date": today,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "meetings": [
            {"time": b["time"], "title": b["title"],
             "people": [person_snapshot(p) for p in b["people"]],
             "unmatched": b["unmatched"]}
            for b in blocks
        ],
        "reconnect": [
            {"name": p["name"],
             "relationship": p.get("md", {}).get("mdrelationship", ""),
             "days": days,
             "last": p.get("md", {}).get("mdlastcontact") or None}
            for _, days, p in overdue[:RECONNECT_LIMIT]
        ],
        "birthdays": [
            {"date": when.isoformat(), "name": p["name"], "age": age,
             "today": when == start}
            for when, age, p in bdays
        ],
        "review": None,
        "journal": None,
        "on_this_day": [],
    }
    if backlog is not None:
        snap["review"] = {"pending": backlog["pending"],
                          "approved": backlog["approved"],
                          "parked": len(backlog["parked"])}
    if journal_info is not None:
        state = ("pending" if journal_info["pending"] or journal_info["staged"]
                 else "parked" if journal_info["parked"] else "missing")
        snap["journal"] = {"state": state, **journal_info}
    if otd is not None:
        rows, daily = otd
        snap["on_this_day"] = [
            {"years": r["years"], "name": r["name"], "kind": r["kind"]}
            for r in rows
        ]
        if daily:
            snap["on_this_day"].append(
                {"years": 1, "name": daily["name"], "kind": "daily note"})
    return snap


def write_snapshot(snap):
    snap_dir = os.path.dirname(SNAPSHOT_FILE)
    os.makedirs(snap_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=snap_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SNAPSHOT_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def push_snapshot():
    """Hand the snapshot to the TRMNL pusher, which no-ops without a webhook
    config. Fire-and-forget: the pusher does its own pipeline logging, and a
    failed push must never take the daily-note write down with it."""
    if not os.path.exists(TRMNL_PUSH):
        return
    try:
        subprocess.run(["/usr/bin/python3", TRMNL_PUSH],
                       capture_output=True, timeout=120, check=False)
    except Exception as exc:
        log.info("TRMNL push failed to launch: %s", exc)


def backfill_contacts(today, days, skip_re, dry_run):
    """Replay past calendar days into LastContact. Meeting attendance is the
    only historical source of contact dates — Granola's copy of the calendar
    carries no attendees — and the daily run only ever looks at yesterday, so
    a person seeded today starts with no history without this."""
    start = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    end = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    cal = run_osascript(CALENDAR, [start, end], timeout=300)
    if not cal.get("ok"):
        log.error("calendar unavailable: %s", cal.get("error"))
        return
    people = run_bridge([{"op": "dump_people", "include_bodies": False}])[0]
    if not people:
        log.info("no Person records yet, nothing to backfill")
        return

    latest = {}
    for op in contact_bumps(cal["events"], people, end, skip_re):
        if op["date"] > latest.get(op["uuid"], ""):
            latest[op["uuid"]] = op["date"]
    ops = [{"op": "bump_lastcontact", "uuid": u, "date": d}
           for u, d in latest.items()]

    if dry_run:
        by_uuid = {p["uuid"]: p["name"] for p in people}
        print(f"[dry-run] {start}..{end}: {len(cal['events'])} events, "
              f"{len(ops)} people would be bumped")
        for op in sorted(ops, key=lambda o: o["date"], reverse=True):
            print(f"  {op['date']}  {by_uuid.get(op['uuid'], op['uuid'])}")
        return
    if not ops:
        log.info("backfill: no roster matches in %s..%s", start, end)
        return
    changed = sum(1 for r in run_bridge(ops) if r.get("changed"))
    log.info("backfill: %d of %d people had LastContact raised (%s..%s)",
             changed, len(ops), start, end)


def backfill_messages(today, days, dry_run):
    """Replay Messages history into LastContact — the non-work contact
    signal the calendar never carries. Run once; the daily pass covers
    everything from then on."""
    start = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    rows = query_messages(start)
    if not rows:
        log.info("backfill: no Messages rows since %s", start)
        return
    cj = run_osascript(CONTACTS, [], timeout=60)
    if not cj.get("ok"):
        log.error("contacts unavailable: %s", cj.get("error"))
        return
    people = run_bridge([{"op": "dump_people", "include_bodies": False}])[0]
    if not people:
        log.info("no Person records yet, nothing to backfill")
        return
    ops = message_bumps(rows, handle_index(cj["contacts"], people))
    if dry_run:
        by_uuid = {p["uuid"]: p["name"] for p in people}
        print(f"[dry-run] {start}..: {len(rows)} texted handles, "
              f"{len(ops)} people would be bumped")
        for op in sorted(ops, key=lambda o: o["date"], reverse=True):
            print(f"  {op['date']}  {by_uuid.get(op['uuid'], op['uuid'])}")
        return
    if not ops:
        log.info("backfill: no roster matches in Messages since %s", start)
        return
    changed = sum(1 for r in run_bridge(ops) if r.get("changed"))
    log.info("backfill: %d of %d people had LastContact raised from "
             "Messages (since %s)", changed, len(ops), start)


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    weekly = "--weekly" in args
    backfill = "--backfill-contacts" in args
    backfill_msgs = "--backfill-messages" in args
    days = BACKFILL_DAYS
    if "--days" in args:
        days = int(args[args.index("--days") + 1])
    today = date.today().isoformat()
    if "--date" in args:
        today = args[args.index("--date") + 1]
    user_invoked = (dry_run or force or "--date" in args or weekly
                    or backfill or backfill_msgs)

    subprocess.run(
        [os.path.expanduser("~/.local/bin/pipeline-record-run"),
         "dt-morning-brief", "86400"],
        check=False,
    )

    if not user_invoked:
        # --urgent: the brief is deadline-bound (it must exist before the
        # first meeting), so battery alone doesn't skip it.
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-background-job"),
             "--urgent"],
            capture_output=True, text=True,
        )
        if gate.returncode != 0:
            log.info("skipping: battery gate")
            return
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-dt-driver")],
            capture_output=True, text=True,
        )
        if gate.returncode != 0:
            log.info("skipping: follower machine")
            return

    skip_re = load_skip_attendee_re()

    if backfill:
        backfill_contacts(today, days, skip_re, dry_run)
        return
    if backfill_msgs:
        backfill_messages(today, days, dry_run)
        return

    events = []
    try:
        cal = run_osascript(CALENDAR, [today], timeout=60)
        if cal.get("ok"):
            events = cal["events"]
        else:
            log.warning("calendar unavailable: %s", cal.get("error"))
    except Exception as exc:
        log.warning("calendar query failed: %s", exc)

    contacts = []
    try:
        cj = run_osascript(CONTACTS, [], timeout=60)
        if cj.get("ok"):
            contacts = cj["contacts"]
        else:
            log.warning("contacts unavailable: %s", cj.get("error"))
    except Exception as exc:
        log.warning("contacts query failed: %s", exc)

    people = run_bridge([{"op": "dump_people"}])[0]
    log.info("loaded %d people, %d events, %d contacts",
             len(people), len(events), len(contacts))

    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    try:
        ycal = run_osascript(CALENDAR, [yesterday], timeout=60)
        bumps = contact_bumps(ycal["events"], people, yesterday, skip_re) \
            if ycal.get("ok") else []
    except Exception as exc:
        log.warning("yesterday's calendar query failed: %s", exc)
        bumps = []
    if bumps:
        if dry_run:
            print(f"[dry-run] would bump LastContact to {yesterday} for "
                  f"{len(bumps)} people")
        else:
            run_bridge(bumps)
            log.info("bumped LastContact for %d people from %s calendar",
                     len(bumps), yesterday)

    mops = message_bumps(query_messages(yesterday),
                         handle_index(contacts, people))
    if mops:
        if dry_run:
            print(f"[dry-run] would bump LastContact from Messages for "
                  f"{len(mops)} people")
        else:
            changed = sum(1 for r in run_bridge(mops) if r.get("changed"))
            log.info("bumped LastContact for %d people from Messages "
                     "(since %s)", changed, yesterday)

    # Every section is upserted (not append-once): the 05:45/06:30/08:00
    # retries refresh a 05:15 brief built from incomplete calendar sync, and
    # a cleared review backlog removes its stale nudge (empty content).
    blocks = brief_blocks(events, people, skip_re)
    overdue = reconnect_overdue(people, today)
    bdays = birthday_rows(contacts, people, today)
    backlog = review_backlog(today)
    journal_loaded = load_journal_state()
    journal_info = (journal_status_info(today, *journal_loaded)
                    if journal_loaded else None)
    otd = on_this_day_rows(today)

    sections = [(BRIEF_HEADER, render_brief(blocks, today))]
    if weekly or date.fromisoformat(today).weekday() == 0:
        sections.append((RECONNECT_HEADER, render_reconnect(overdue, today)))
    sections.append((BIRTHDAYS_HEADER, render_birthdays(bdays, today)))
    sections.append((REVIEW_HEADER, render_review(backlog, today)))
    sections.append((JOURNAL_HEADER, render_journal(journal_info, today)))
    sections.append((ON_THIS_DAY_HEADER, render_on_this_day(otd, today)))

    # The TRMNL screen updates even on an empty day — "no meetings" is a
    # displayable state — and never for a --date replay, which would clobber
    # the device with a stale day.
    if not dry_run and today == date.today().isoformat():
        write_snapshot(build_snapshot(today, blocks, overdue, bdays,
                                      backlog, journal_info, otd))
        push_snapshot()

    if not any(content for _, content in sections):
        log.info("nothing to write (no briefable meetings, no reconnects)")
        return

    if dry_run:
        for header, content in sections:
            if content:
                print(f"\n{header}\n\n{content}")
        return

    heading = datetime.strptime(today, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    daily = run_bridge(
        [{"op": "get_or_create_daily", "date": today, "heading": heading}]
    )[0]
    results = run_bridge([
        {"op": "upsert_section", "uuid": daily["uuid"], "header": header,
         "content": content or ""}
        for header, content in sections
    ])
    wrote = [header for (header, _), res in zip(sections, results)
             if res.get("changed")]
    if not wrote:
        log.info("sections already current, nothing to do")
        return
    log.info(
        "wrote %s to daily note %s", ", ".join(wrote), today,
        extra={"record_name": today, "record_uuid": daily["uuid"]},
    )


if __name__ == "__main__":
    try:
        main()
    except BridgeUnavailable as exc:
        log.info("skipping: %s", exc)
    except Exception as exc:
        log.error("FATAL: %s: %s", type(exc).__name__, exc)
        sys.exit(1)
