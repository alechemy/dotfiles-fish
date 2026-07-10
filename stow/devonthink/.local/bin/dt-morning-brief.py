#!/usr/bin/python3
"""
dt-morning-brief.py — contextual resurfacing for the entity layer.

Builds a "who am I about to meet" briefing from today's calendar plus the
Person records in Lorebook/20_ENTITIES/People and appends it to today's
daily note as a `## Briefing` section. On Mondays (or with --weekly) it
also appends a `## Reconnect` section listing people whose LastContact
has drifted past their relationship tier's threshold. Whenever filing
proposals sit unreviewed in `/20_ENTITIES/_Review`, an `## Entity Review`
line reports the backlog count.

The brief reads live from the records, so it is never stale. Two mutations
only: the daily-note section insert (idempotent via an HTML comment marker
per section per day), and a LastContact bump for every person matched in
YESTERDAY's calendar — yesterday because the day is complete, so a
meeting that was cancelled after the morning run never counts as contact.
This keeps the Reconnect digest honest for people whose contact happens
on the calendar rather than in filed facts (calls with family, social
events); bump_lastcontact only ever raises the date, so re-runs are
harmless.

Section placement: jots are inserted relative to the `## Today's Notes`
header (see insert-jot-into-daily-note.py), targeting the last content
bullet BEFORE it. The briefing must therefore sit AFTER that header, so
this script guarantees `## Today's Notes` exists before appending its own
sections at the end of the note.

Calendar access goes through calendar-events-json.js (EventKit via
osascript, Apple-signed TCC identity). DEVONthink access goes through
entity-dt-bridge.js. Both are invoked via /usr/bin/osascript.

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

Config (~/.config/dt-pipeline/entities.conf, shared with entity-filing.py):
    SKIP_ATTENDEE_PATTERN=<regex>    attendee names matching this are not
                                     people. Exchange reports conference
                                     rooms with participantType Person and
                                     is otherwise indistinguishable from a
                                     human, so the name is the only signal.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log

log = setup_log("morning-brief")

BRIDGE = os.path.expanduser("~/.local/bin/entity-dt-bridge.js")
CALENDAR = os.path.expanduser("~/.local/bin/calendar-events-json.js")
NOTES_HEADER = "## Today's Notes"
BRIEF_HEADER = "## Briefing"
RECONNECT_HEADER = "## Reconnect"
REVIEW_HEADER = "## Entity Review"
REVIEW_PATH = "/20_ENTITIES/_Review"
APPROVED_PATH = REVIEW_PATH + "/Approved"
LOG_BULLET_RE = re.compile(r"^- \d{4}-\d{2}-\d{2} — ")

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
DEFAULT_SKIP_ATTENDEE = r"\bVC\b|\bConference\b|\bRoom\b|\d+\s?ppl"
BACKFILL_DAYS = 365


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
        email = norm(p.get("md", {}).get("mdemail", ""))
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
    lines = (p.get("body") or "").split("\n")
    bullets = [
        ln for ln in lines
        if LOG_BULLET_RE.match(ln) and not ln.rstrip().endswith("— Created.")
    ]
    return ["  " + ln for ln in bullets[-limit:]]


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


def build_brief(events, people, today, skip_re):
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
        lines = [f"### {fmt_time(ev['start'])} — {ev['title']}", ""]
        seen = set()
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
                lines.append(person_summary_line(p))
                lines.extend(recent_log_bullets(p))
            else:
                who = a["name"] or a["email"]
                detail = f" ({a['email']})" if a["name"] and a["email"] else ""
                unmatched.append(f"- {who}{detail} — no entity record yet")
        for p in by_title:
            if p["uuid"] in seen:
                continue
            seen.add(p["uuid"])
            lines.append(person_summary_line(p))
            lines.extend(recent_log_bullets(p))
        if len(unmatched) <= UNMATCHED_LIST_MAX:
            lines.extend(unmatched)
        else:
            lines.append(f"- {len(unmatched)} attendees without entity records")
        blocks.append("\n".join(lines))
    if not blocks:
        return None
    return f"<!-- brief:{today} -->\n\n" + "\n\n".join(blocks)


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


def build_reconnect(people, today):
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
            continue
        if days > threshold:
            overdue.append((days / threshold, days, p))
    if not overdue:
        return None
    overdue.sort(key=lambda x: -x[0])
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


def review_nudge(today):
    """Surface the filing review backlog so proposals don't sit unseen — the
    Approved subgroup is the apply drop-zone, so it isn't a backlog, but a
    proposal filing refused to apply stays there and would otherwise be
    invisible: nothing counts it and its log line is only a WARNING."""
    try:
        children, approved = run_bridge([
            {"op": "list_group", "path": REVIEW_PATH},
            {"op": "list_group", "path": APPROVED_PATH},
        ])
    except BridgeUnavailable:
        raise
    except Exception as exc:
        log.warning("could not count review proposals: %s", exc)
        return None
    pending = [c for c in children if c["name"] != "Approved"]
    if not pending and not approved:
        return None
    lines = [f"<!-- review-nudge:{today} -->", ""]
    if pending:
        noun = "proposal" if len(pending) == 1 else "proposals"
        lines.append(f"- {len(pending)} filing {noun} awaiting review in "
                     f"`20_ENTITIES/_Review`")
    if approved:
        noun = "proposal" if len(approved) == 1 else "proposals"
        lines.append(f"- {len(approved)} approved {noun} did not apply — see "
                     f"`entity-filing` in the pipeline log")
    return "\n".join(lines)


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


def append_section(note_text, header, content):
    text = note_text
    if NOTES_HEADER not in text:
        text = text.rstrip("\n") + f"\n\n{NOTES_HEADER}\n"
    return text.rstrip("\n") + f"\n\n{header}\n\n{content}\n"


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    weekly = "--weekly" in args
    backfill = "--backfill-contacts" in args
    days = BACKFILL_DAYS
    if "--days" in args:
        days = int(args[args.index("--days") + 1])
    today = date.today().isoformat()
    if "--date" in args:
        today = args[args.index("--date") + 1]
    user_invoked = dry_run or force or "--date" in args or weekly or backfill

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

    events = []
    try:
        cal = run_osascript(CALENDAR, [today], timeout=60)
        if cal.get("ok"):
            events = cal["events"]
        else:
            log.warning("calendar unavailable: %s", cal.get("error"))
    except Exception as exc:
        log.warning("calendar query failed: %s", exc)

    people = run_bridge([{"op": "dump_people"}])[0]
    log.info("loaded %d people, %d events", len(people), len(events))

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

    sections = []
    brief = build_brief(events, people, today, skip_re)
    if brief:
        sections.append((BRIEF_HEADER, brief, f"<!-- brief:{today} -->"))
    if weekly or date.fromisoformat(today).weekday() == 0:
        reconnect = build_reconnect(people, today)
        if reconnect:
            sections.append(
                (RECONNECT_HEADER, reconnect, f"<!-- reconnect:{today} -->")
            )
    nudge = review_nudge(today)
    if nudge:
        sections.append(
            (REVIEW_HEADER, nudge, f"<!-- review-nudge:{today} -->")
        )

    if not sections:
        log.info("nothing to write (no briefable meetings, no reconnects)")
        return

    if dry_run:
        for header, content, _ in sections:
            print(f"\n{header}\n\n{content}")
        return

    heading = datetime.strptime(today, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    daily = run_bridge(
        [{"op": "get_or_create_daily", "date": today, "heading": heading}]
    )[0]
    text = daily["text"]
    wrote = []
    for header, content, marker in sections:
        if marker in text:
            continue
        text = append_section(text, header, content)
        wrote.append(header)
    if not wrote:
        log.info("sections already present, nothing to do")
        return
    run_bridge([{"op": "set_text", "uuid": daily["uuid"], "text": text}])
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
