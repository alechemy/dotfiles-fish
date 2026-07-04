# How to Use the Entity Layer

A copy of this guide lives in DEVONthink at `20_ENTITIES` so it's at hand
while reviewing. Technical internals: `devonthink/docs/entities.md` in the
dotfiles repo.

## What it is, in three sentences

Every person who matters gets a Markdown record in `20_ENTITIES/People` that
accumulates dated facts with links back to the note each fact came from. A
morning agent writes a briefing into today's daily note — who you're meeting,
what you know about them — and a Monday digest lists people you've lost touch
with. A filing agent reads your meeting notes, handwritten notes, and daily
jots through a local LLM and *proposes* new facts — and, when a note
documents a trip, celebration, or gathering, an Event record with its
attendees; nothing is created or changed until you approve it.

## Your daily rhythm

- **Morning:** the daily note has a `## Briefing` section by ~6:40. Read it.
  That's the whole habit.
- **During the day:** jot like you always have. The one adjustment: name
  names. "Bob's new role is X, Alice expecting in March" files itself;
  "talked to him about the thing" files nothing.
- **Every day or two (2 minutes):** open `20_ENTITIES/_Review` and process
  proposals — see the walkthrough below.
- **Monday:** the `## Reconnect` section lists active people past their
  contact threshold (family/close friends 30 days, friends 60, colleagues
  90). Text someone, or set their `entitystatus` to `dormant` to silence
  them.

Contact tracking is mostly automatic: meeting attendance and yesterday's
calendar events (matched by attendee or by a name in the event title, like
"Call with Jake") bump `LastContact` without any LLM involved.

## Reviewing a proposal — walkthrough

Open any proposal in `_Review`. It has two parts: a human-readable
**Proposed** list, and an **Ops** section with a fenced JSON block. **The
JSON is what executes** — the prose is only a preview. Editing the prose
changes nothing.

You have three moves:

1. **Approve** — move the record into `_Review/Approved` (drag, or ⌃⌘M →
   Approved). Within 30 minutes the ops run and the proposal deletes itself.
   Immediately: run `entity-filing.py --apply-only` in a terminal.
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
2. **Set `relationship` on people you care about** — without it they never
   appear in the Reconnect digest. `email` makes calendar matching exact.
3. **Seed your inner circle by hand** (Data → New from Template → Entities →
   Person) rather than waiting for proposals; the automation enriches
   records far better than it bootstraps them.
4. **Review promptly and delete freely.** A lean record of ten real facts
   beats a bloated one; the briefing shows the *last three* log entries, so
   filler crowds out signal.
5. Recurring standups/roundtables are deliberately never extracted (they
   produce workplace trivia). If a specific one mattered, force it:
   `entity-filing.py --force <uuid>`.

## What runs when

| When | What |
| --- | --- |
| 06:40 daily | Briefing into the daily note; LastContact bumps from yesterday's calendar; Reconnect on Mondays |
| Every 30 min | Apply anything in `_Review/Approved`, then extract up to 3 unprocessed notes (local model, on AC power only) |

Everything logs to `~/Library/Logs/devonthink-pipeline.log` (components
`morning-brief` and `entity-filing`). Preview commands:
`dt-morning-brief.py --dry-run`, `entity-filing.py --dry-run`.
