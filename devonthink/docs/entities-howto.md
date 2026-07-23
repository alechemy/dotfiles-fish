# How to Use the Entity Layer

A copy of this guide lives in DEVONthink at `20_ENTITIES` so it's at hand
while reviewing. Technical internals: `devonthink/docs/entities.md` in the
dotfiles repo.

## What it is, in three sentences

Every person who matters gets a Markdown record in `20_ENTITIES/People` that
accumulates dated facts with links back to the note each fact came from. A
morning agent writes each of the day's meetings into today's daily note as a
timed bullet — who you're meeting, what you know about them — and a Reconnect
digest on the TRMNL dashboard lists people you've lost touch with. A filing agent reads your meeting notes, handwritten notes, and daily
jots through a local LLM and *proposes* new facts — and, when a note
documents a trip, celebration, or gathering, an Event record with its
attendees. The morning agent can also propose a new Person from strong calendar
evidence. Nothing is created or changed until you approve it.

## Start here: seed People before filing can help

Filing does not extract while `20_ENTITIES/People` is empty — it logs
"extraction paused until /20_ENTITIES/People is seeded" and stops. That is
deliberate. The extraction prompt's only way to know who anyone is is the
roster it carries, and each note is extracted exactly once, so an empty
roster would burn every note on a proposal naming bare first names it can't
resolve. Seed one person and the next run resumes on its own.

Before the very first seeding, create the entity groups once: run
`~/.local/bin/dt-entity-bootstrap` in a terminal. It makes the
`20_ENTITIES/{People,Places,Events,_Review,_Review/Approved}` groups (if they
aren't there already), excludes the sensitive ones from AI chat, and stamps
`EntityType` on any hand-made records — all idempotent, so it's safe to re-run
whenever you add records by hand.

So the first sitting is: **Data → New from Template → Entities → Person** for
your inner circle, and for each one fill in the DEVONthink **aliases** field
with every short form you actually use — "Alison", "Ali". Aliases are what
let a proposal that says `ensure_person "Alison"` land in the record you
just made for "Alison Vance" instead of creating a second one beside it.
Approving an existing proposal without the alias is caught rather than
silently duplicated (the run warns and leaves the proposal in `Approved`),
but the alias is what makes it *work* rather than merely fail safely.

Then, once, replay your calendar history into everyone's contact dates:

```bash
~/.local/bin/dt-morning-brief.py --backfill-contacts --dry-run   # preview
~/.local/bin/dt-morning-brief.py --backfill-contacts             # 365 days
```

The daily run only ever looks at yesterday, so without this a person you
seeded today shows as "no recorded contact" in Reconnect even though you
have a year of meetings with them. It's idempotent — re-run it any time you
add people. The same replay teaches the resolver where each person normally
appears. Set `PERSONAL_CALENDARS` and `WORK_CALENDARS` in
`~/.config/dt-pipeline/entities.conf` first. Each is a comma-separated list of
calendar titles, EventKit calendar identifiers, or source identifiers.

## Your daily rhythm

- **Morning:** the daily note has a timed `📅` bullet for each of the day's meetings by ~5:15 (or moments after the Mac wakes). Read them.
  That's the whole habit.
- **During the day:** jot like you always have. The one adjustment: name
  names. "Bob's new role is X, Alice expecting in March" files itself;
  "talked to him about the thing" files nothing.
- **Right after a meeting:** for a fact you want filed *now* rather than a day
  later, use the **Capture Person Fact** Drafts action (Mac or phone) — type
  e.g. "Dana Parker's daughter started at Reed College" and run it. See
  "Capturing a fact" below.
- **Every day or two (2 minutes):** open `20_ENTITIES/_Review` and process
  proposals — see the walkthrough below. The TRMNL brief's entity-review
  count shows what's waiting; a proposal still sitting in `_Review/Approved`
  means the run refused to apply it (the pipeline log has the reason).
- **Whenever you feel like it:** open `20_ENTITIES/_Candidates` — one record
  per unknown person seen in your notes or meetings, holding everything
  observed about them so far. Move it to `Approved` to start tracking them
  (their accumulated facts file automatically), to `Ignored` to never hear
  about them again, or just leave it — undecided candidates sit silently and
  are never re-proposed. The brief only nags about a candidate when a fact
  you deliberately captured is waiting on one.
- **Reconnect:** the TRMNL brief's Reconnect list shows active people past
  their contact threshold (family/close friends 30 days, friends 60,
  colleagues 90). Text someone, or set their `entitystatus` to `dormant` to
  silence them.

Contact tracking is mostly automatic: yesterday's calendar events (matched by
attendee or by a name in the event title, like "Call with Jake") bump
`LastContact` without any LLM involved. Only the work calendar carries
attendees — personal iCloud events are matched by title alone, so name people
in the event title if you want them tracked. Exact email and full-name matches
win. A bare first name is treated as weak evidence. If that name has only ever
been established on work calendars, a personal event remains unresolved rather
than being filed against the colleague.

## Capturing a fact fast (Drafts)

The **Capture Person Fact** action sends a one-line fact straight to
`20_ENTITIES/_Facts`, and filing resolves the person and files it — separately
from your daily-note jots, and without the day's delay a daily note carries.

**Setup, once:** run `~/.local/bin/dt-entity-bootstrap` — it creates the
`_Facts` group and prints its UUID. Paste that UUID into `FACTS_GROUP_UUID` at
the top of `drafts/drafts-capture-fact.js`, then paste the script into a new
Drafts action named "Capture Person Fact". The same UUID works on Mac and iOS.
Before relying on it, run one capture from each device and confirm the record
lands in `_Facts`.

**The habit:** name the person as specifically as you can. Their full name or a
known alias in the capture ("Dana Parker's kid…", "Bobby Vega got the job") is
what lets the fact **auto-file** to their record; a bare first name ("Dana
moved…") still works but waits as a review proposal so you can confirm which
Dana. Whatever you type, it's never lost: if the model can't make a fact of it,
a "Review capture" item appears in `_Review` for you to file by hand or delete.

Timing: it files on the next filing run (≤30 min, on power and while you're
away from the keyboard), not the instant you hit the action. To file the
backlog immediately at the Mac: `~/.local/bin/entity-filing.py --scan-only`.
Don't put a colored label on a `_Facts` record — that archives it before filing
sees it.

## Reviewing a proposal — walkthrough

Open any proposal in `_Review`. It has two parts: a human-readable
**Proposed** list, and an **Ops** section with a fenced JSON block. **The
JSON is what executes** — the prose is only a preview. Editing the prose
changes nothing.

You have three moves:

1. **Approve** — move the record into `_Review/Approved` (drag, or ⌃⌘M →
   Approved). Within 30 minutes the ops run and the proposal is moved to
   Trash. Immediately: run `entity-filing.py --apply-only` in a terminal.
2. **Reject** — delete the proposal. Nothing happens, ever. The source note
   is already marked processed, so it won't be re-proposed unless you ask.
3. **Edit, then approve** — fix the JSON first. This is the interesting one.

### Worked example: the graduation proposal

The proposal sitting in `_Review` ("File: 2026-06-13 Maya's Graduation
Weekend") proposes one new Person record — Maya, with her graduation fact
— and one Event record ("Maya's Graduation Weekend") carrying the date and
six attendees. Things you might do to it:

- **Drop a person entirely:** delete their whole
  `{"op": "ensure_person", ...}` object from the JSON array. Mind the comma
  between array elements.
- **Trim the event's attendee list:** remove names from the `attendees`
  array — attendees who have Person records become links on the event's
  `**Who:**` line, the rest stay plain text (which is fine; no records are
  created for them).

### Calendar Person candidates

The morning agent can place `Calendar person: …` proposals in `_Review` without
using the filing LLM. An unmatched structured attendee is sufficient evidence.
A full name parsed from an event title must also match a macOS Contacts card.
For a bare title such as "Meet with Jordan", the Contacts match must be unique
and every roster record aliased `Jordan` must have established history only in
the opposite calendar context.

These proposals create only a Person record, optionally with an email address.
They do not invent facts or set `LastContact` before the meeting happens. If the
proposal lists a possible existing record, it is already marked as a distinct
identity, so approving it creates the new person without a JSON edit. If it is
actually an alias gap, add the candidate name as an alias to the existing record
and delete the proposal. Deleting a candidate rejects that identity permanently,
so morning retries do not recreate it.

That rejection is remembered in `~/.local/state/devonthink/identity-provenance.json`,
under `candidates` — not on the proposal record itself, which is already gone.
To let a rejected candidate be proposed again, open that file, find the entry
(search for the event title or date you rejected it from), and delete it.
Deleting the whole `candidates` object resets every remembered rejection at
once and has no effect on anyone already filed.

- **Fix a name before it becomes a record:** change `"name": "Maya"` to
  `"name": "Maya Chen"` (or whoever she is). The record is created under
  the name in the JSON, so fix it here, not after.
- **File to an existing person instead of creating a duplicate:**
  `ensure_person` first looks for an existing record matching the name *or
  any alias* — it only creates when nothing matches. So if a record for her
  already exists as "Maya Chen", either change the JSON name to exactly
  that, or add "Maya" as an alias on her record. Same mechanism behind the
  "possible existing match: …" hints on new-person proposals: usually the
  right fix is adding the alias to the existing record, deleting the
  proposal, and re-running `entity-filing.py --force <source-uuid>`.
  You don't have to catch this by eye. Apply re-checks each
  `ensure_person` against the roster as it stands *now*, and a proposal that
  would create "Maya" next to an existing "Maya Chen" is refused and left in
  `Approved` with a warning in the log. Add the alias and it applies on the
  next run; if they really are two different Mayas, put
  `"confirm_new": true` in that op to say so.
- **Add a fact of your own** while you're there: append a string to that
  person's `log_lines`, format `"- 2026-06-13 — Whatever you know."` (the
  source link is optional for hand-added lines).
- **Trim a weak fact:** delete its line from `log_lines`.

Then drag to `Approved`. If the JSON you left behind is malformed, nothing
is lost — the run logs a warning and leaves the proposal in place for
another edit.

**Event proposals** (`EVENT: …` lines) work the same way: approving an
`ensure_event` op creates a record in `20_ENTITIES/Events` with the date,
place, and attendee list. Attendees who have Person records become links;
the rest stay plain names — and that's fine. Trim the attendee list or fix
the title in the JSON just like anything else.

## Reviewing from Things — any device

If you'd rather process proposals from your phone (or just from your task
list), turn the Things mirror on:

```
echo "THINGS_SYNC=on" >> ~/.config/dt-pipeline/entities.conf
```

Within a tick every pending proposal appears as a to-do in the **Entity
Filing** project in Things (the project is created for you; rename it via
`THINGS_PROJECT=` if you like — it's tracked by identity afterwards, so
renaming in Things is also fine). Then:

- **Approve:** complete the to-do. One to two ticks later the filing runs —
  the same apply path as dragging the proposal to `Approved` in DEVONthink.
- **Reject:** cancel or delete the to-do. The proposal is trashed.
- **Edit first:** the note below `=== proposed v1 ===` *is* the proposal.
  Delete a line to drop it, fix a name/date/fact/field inline, then
  complete. What you left in the note is exactly what gets filed — the ops
  are regenerated from it against the current roster.

Calendar Person candidates have no source note, so they appear in Things as
**review-in-DEVONthink stubs**, not as editable line-format proposals. Review
the JSON in DEVONthink. Completing the Things task moves the frozen proposal to
`Approved`, including when it intentionally creates a person whose name resembles
an existing record. If it needs another edit, make the change and approve it in
DEVONthink instead. Its Things task then completes automatically. Canceling or
deleting the task still rejects it normally.

The note's line format, by example:

```
PERSON Maya Chen (new, met)
- 2026-06-13 — Graduated from law school.
- city = Chicago
EVENT Maya's Graduation Weekend (2026-06-13 at Chicago)
- with: Maya Chen, Bob Carter
- 2026-06-13 — Weekend celebrating the graduation.
```

`met` on the header is what bumps `LastContact`; the four editable fields
are `employer`, `role`, `city`, `email`. If an edit doesn't parse, or a name
is ambiguous or shadows an existing record, the to-do **pops back to open
with a `⚠` banner** explaining exactly what to fix; for a genuinely new
person who happens to share a name, completing a second time with the name
unchanged says "yes, really new". A proposal too gnarly to edit as text
(very long, or hand-crafted ops) arrives as a review-in-DEVONthink stub —
completing it applies the ops exactly as written.

Both sides stay honest: approve or delete a proposal inside DEVONthink and
its Things to-do is completed or cancelled for you; delete the to-do's task
in Things and only that proposal is rejected. If the pipeline ever seems to
ignore a decision, check that Things.app is running on the Mac (the poller
launches it hidden, but it can't read cloud pushes that never arrived) and
that the morning brief isn't flagging a stuck approval. If
`~/.local/state/devonthink/things-filing-map.json` is ever corrupted, move
it aside — the next run rebuilds it from the to-dos themselves.

Know the trade: proposal names and facts sync through Things Cloud. The
rest of the entity layer stays on-device; this mirror is the one deliberate
exception, and it's off by default.

## Places, events, and automatic linking

Whenever a fact is filed, the first mention of any *existing* entity's name
or alias gets wrapped in a link — "Moved to Chicago" links to your Chicago
record the moment one exists. Linking never creates records, so Places work
on a simple rule: **create a Place record when you first care about a
place** (Data → New from Template → Entities → Place), and every future
filed fact that mentions it feeds its backlinks automatically. Open the
record and check the Mentions/Incoming Links inspector to see everything
connected to it. Events accrue the same way, plus the filing agent proposes
them for you when a note describes a distinct occasion.

## Correcting things after the fact

Person records are plain Markdown plus a few metadata fields — edit them
directly, any time, on Mac or phone. The automation only ever *appends*; it
never rewrites what's there.

- **Wrong fact got filed:** delete the bullet from the Biographical Log.
- **Duplicate people:** copy the log bullets into the record you're keeping,
  add the other spelling as an alias there, trash the duplicate.
- **Someone changed jobs / moved:** just tell a jot about it ("Bob left
  Globex for Initech") and let filing propose it — or edit the `employer`
  field in the Info inspector yourself and add a log line.
- **Stop hearing about someone:** set `entitystatus` to `dormant`.
- **A note was extracted badly:** fix the note if needed, then
  `entity-filing.py --force <uuid of the note>` re-extracts it and makes a
  fresh proposal.

## Getting the best results

1. **Aliases are the whole ballgame.** Every nickname, short form, and
   alternate spelling you add to a Person record improves jot filing,
   calendar matching, and proposal resolution. When anything mismatches, the
   fix is almost always "add an alias".
2. **Set `relationship` to one of `family`, `close-friend`, `friend`,
   `colleague`** (or `acquaintance`, which never surfaces) — case and spacing
   don't matter ("Close Friend" works), but any other value only earns a
   warning in the log. Without it they never appear in the Reconnect digest.
   `email` makes calendar matching exact.
3. **Seed your inner circle by hand** (Data → New from Template → Entities →
   Person) rather than waiting for proposals; the automation enriches
   records far better than it bootstraps them.
4. **Review promptly and delete freely.** A lean record of ten real facts
   beats a bloated one; the briefing surfaces up to three facts filed since
   you last met them that it hasn't already told you, so filler crowds out
   signal.
5. Recurring standups/roundtables are deliberately never extracted (they
   produce workplace trivia). If a specific one mattered, force it:
   `entity-filing.py --force <uuid>`.

## What runs when

| When | What |
| --- | --- |
| ~05:15 daily (retries 05:45/06:30/08:00 if asleep) | The day's events merged into the daily-note timeline, calendar identity provenance, calendar Person candidates, yesterday's LastContact bumps, and the TRMNL digests (Reconnect, birthdays, entity review, journal status, On This Day) |
| Every 30 min | Apply anything in `_Review/Approved`, then extract up to 3 unprocessed notes — local model, on AC power, and only after ~10 min of user inactivity, so it never competes with active work |

**Draining the backlog by hand.** The scheduled runs only extract while
you're on power *and* away from the keyboard, so the initial backlog clears
over days of normal breaks. To speed that up, run

```
~/.local/bin/entity-filing.py --scan-only
```

a few times — manual runs bypass the battery and memory-pressure gates, and
each pass extracts `MAX_PER_RUN` notes (fish: `for i in (seq 10); ~/.local/bin/entity-filing.py
--scan-only; end`). Stop whenever; there's no penalty for leaving the rest
to the schedule. Each note takes a few seconds.

Everything logs to `~/Library/Logs/devonthink-pipeline.log` (components
`morning-brief` and `entity-filing`). Preview commands:
`dt-morning-brief.py --dry-run`, `entity-filing.py --dry-run`.
