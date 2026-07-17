# Entity Layer ("Lorebook Memory")

A person/place/event memory layer inside the Lorebook database: each important
person is a Markdown record that accumulates dated, provenance-linked facts
over time, a morning briefing surfaces what you know about the people you're
about to meet, and an AI filing step keeps the records growing from documents
the pipeline already ingests (Granola meetings, handwritten notes, daily
notes). Adapted from the "Lorebook Memory Layer" design guide; the core
division of labor is **LLM extracts, script files** — the language model only
turns messy text into structured JSON, and deterministic code does every write.

## Data model

Groups (all in Lorebook):

| Group | Purpose |
| --- | --- |
| `/20_ENTITIES/People` | One Markdown record per person. Filename = canonical name; DT record **aliases** carry nicknames ("Bob, Bobby") for matching and WikiLinks |
| `/20_ENTITIES/Places` | Hand-authored place records (backlinks answer "who/what is connected to Chicago") |
| `/20_ENTITIES/Events` | Event records for trips, gatherings, milestones — proposed automatically by filing when a note documents a distinct occasion, or hand-authored; recurring meetings stay ordinary meeting notes |
| `/20_ENTITIES/_Review` | Filing proposals awaiting review |
| `/20_ENTITIES/_Review/Approved` | Drop zone: move a proposal here and the next filing run applies it |
| `/20_ENTITIES/_Facts` | Person-fact captures from the "Capture Person Fact" Drafts action, awaiting extraction (source kind `fact`) |

**Bootstrap the groups first.** The bridge assumes all of the above already
exist; nothing in `setup.sh` creates them. Run `~/.local/bin/dt-entity-bootstrap`
once on the driver before seeding People — it is idempotent and does three
things: creates any missing entity group, applies the `exclude from chat`
flag to `People`, `_Review`, `_Review/Approved`, and `_Facts`, and stamps `EntityType`
(`Person`/`Place`/`Event`) on any hand-authored markdown record inside the
matching group that lacks it (scripted creation already stamps it; this covers
records made from templates by hand). Re-run it any time you hand-author a
batch of entity records. It logs to `~/Library/Logs/dt-entity-bootstrap.log`.

Person records use a small metadata schema (see the Custom Metadata table in
the [README](../README.md)): `EntityType`, `EntityStatus`, `City`, `Employer`,
`Role`, `Relationship`, `Email`, `LastContact`. Everything narrative — partner,
kids, how-we-met, and the dated fact history — lives in the record body:

Two of those are enums that DEVONthink stores as free text, so a hand-typed
value can miss silently. `Relationship` resolves to `family` / `close-friend`
(30 days), `friend` (60), `colleague` (90) — its Reconnect threshold — or
`acquaintance`, which is recognized but never surfaces. `EntityStatus` is
`active`, `dormant`, `archived`, or `deceased`; only `active` surfaces **in
Reconnect** — `reconnect_overdue()` is the only consumer that reads the field,
so a non-`active` person is still briefed, still matched, and still gets their
`LastContact` bumped. Status is lifecycle, not suppression; to stop briefing
someone entirely, see `BriefingSuppressed` below. The
brief folds case, spaces, and underscores before matching, then warns on
anything left unresolved: an unknown `Relationship` skips Reconnect (as a
blank one does), while an unknown `EntityStatus` is treated as `active` —
failing open, so a typo can't quietly hide a person. Adding a value to the
README's table without adding it here silences or resurfaces people.

```markdown
# Bob Carter

**Role:** Architect
**City:** Chicago
**Partner:** Alice Jones
**How we met:** introduced by Dave at the 2022 conference

## Biographical Log

- 2026-06-20 — Moved from Acme to Globex. ([source](x-devonthink-item://…))
- 2026-03-02 — Alice got a promotion. ([source](x-devonthink-item://…))
```

Rules the automation enforces:

- **Append, never overwrite.** Facts are appended to `## Biographical Log`
  with the fact's date and an `x-devonthink-item://` link to the source
  document. When a queryable field changes (new employer), the field is
  updated *and* a `Employer: old → new` line is logged, so history survives.
- **Newest first.** The section is re-sorted by fact date, descending, on
  every write. Facts arrive in *filing* order, which is not fact order — a
  backlog drain, a `--force` re-extraction, or a note about something that
  happened months ago all file below entries dated after them — so a log that
  is only appended to ends up in no order at all. Same-date facts keep the
  order they were filed in, so the sort is stable and idempotent; blank lines,
  prose, and undated bullets hold their positions, and an indented line under
  a fact travels with it. The brief renders a person's news in document order,
  so it inherits newest-first too. `## Log` on Place and Event records sorts
  the same way. Records the filer never touches are repaired on demand with
  the bridge's `sort_logs` op (see [Operations](#operations)).
- **Idempotent by fact.** A log line already in the body — same source, date,
  and text, ignoring auto-link decoration — is skipped, so re-running filing
  never duplicates facts, while re-extracting a corrected note (`--force`)
  still files the genuinely new facts it surfaces.
- **Known entities are auto-linked.** When a log line is filed, the bridge
  wraps the first mention of any *existing* Person/Place/Event name or alias
  in an item link (longest name wins, never inside an existing link, never
  the record linking to itself). Creating a Place or Event record is
  therefore all it takes for future facts to start feeding its backlinks —
  linking is deterministic and never creates records.
- **Volatile values are computed, not stored** — record kids as names + birth
  years in the body, never an "age" field.
- Reverse lookup: `mdcity:Chicago` in DT search (or a saved smart group)
  answers "who do I know in Chicago"; the Mentions/Incoming Links inspector on
  a Person record shows every meeting and daily note that links to them.

Templates for hand-authoring live in DT under **Data → New from Template →
Entities** (stowed to `~/Library/Application Support/DEVONthink/
Templates.noindex/Entities/`).

## Components

All DEVONthink I/O flows through one JXA gateway so the Python orchestrators
stay stdlib-only (tier-1 `/usr/bin/python3`, stable TCC identity):

| File (in `../../stow/devonthink/.local/bin/`) | Role |
| --- | --- |
| `entity-dt-bridge.js` | Executes a JSON ops batch against DT (dump people, search sources, append log lines, set fields, create proposals/records, DT-chat call). Run via `/usr/bin/osascript -l JavaScript` |
| `calendar-events-json.js` | Dumps one day of calendar events (EventKit via osascript, so the Calendars TCC grant sticks to an Apple-signed binary). One interactive run to approve the prompt |
| `contacts-json.js` | Dumps macOS Contacts (Contacts framework via osascript, same TCC pattern — one interactive run): name, nickname, emails, phones, birthday. Identifiers only, never facts |
| `dt-morning-brief.py` | Daily ~05:15 — retried 05:45/06:30/08:00 for standby-missed triggers, idempotent (`com.user.dt-morning-brief`): calendar + Person records → `## Briefing` section in today's daily note; Mondays also `## Reconnect`; Contacts birthdays → `## Birthdays`; LastContact bumps from yesterday's calendar and from Messages |
| `entity-filing.py` | Every 30 min (`com.user.entity-filing`): applies approved proposals, then extracts facts from unprocessed sources and files them (suggest mode by default) |

### Morning brief (resurfacing + contact tracking)

The `## Briefing` section is **the day you agreed to**, in start order: every
timed event you accepted, on a calendar not in `SKIP_CALENDARS`, including the
ones with nobody attached. A day reads as a day, so a solo dentist appointment
lists as a bare heading rather than vanishing.

What does *not* list is an invitation you never answered, and the distinction
is subtler than it looks. Declining an Exchange invite **removes the event from
the calendar outright**, so `declined` is a status that essentially never
arrives — the state that actually has to be filtered is `unknown` (invited,
never responded), which is otherwise indistinguishable from an acceptance on
every field EventKit exposes. Filtering on `declined`, as this did originally,
is therefore a no-op that briefs every mass invite you ever ignored. The rule
now lives in one predicate, `attending()`, shared by the briefing and the
LastContact bumps (sitting in an invite you ignored is not evidence you met
anyone):

| Event | Briefs? | Why |
| --- | --- | --- |
| RSVP `accepted` | yes | — |
| RSVP `tentative` | yes, titled `… (tentative)` | a maybe is still on your day, but says so |
| RSVP `unknown` / `pending` / `declined` | no | you never said yes |
| No attendees at all | yes | nobody invited you; it is your own calendar entry |
| Attendees, but you are not among them | no | a distribution-list invite: Exchange lists the list, never you, so it has no RSVP of yours and never will |
| Organizer is you | yes | your own meeting, whatever your participant entry says |
| `EKEventStatusCanceled` | no | Exchange keeps a cancelled meeting on the calendar, retitled `Canceled: …`, **with your acceptance intact** — so it has to be caught on status, not RSVP |

`calendar-events-json.js` passes a nil calendars predicate to EventKit, so
**every** calendar is already queried — Exchange and iCloud alike. Nothing
selects a calendar at fetch time; the only filtering is `SKIP_CALENDARS`.

Roster people are attached to an event two ways, deduped: from its **attendees**
(matched by email, name, or alias) and from a roster name or alias appearing in
the **title** ("Call with Jake"). Attendees exist only on Exchange events —
iCloud events carry none — which is why title matching exists at all.

#### The roster ages; the news does not

A block carries two kinds of content that read alike and age nothing alike, and
conflating them is what made the briefing unreadable.

The **roster** — who is in the room, with their role, city and last contact — is
only news the first time. A standing meeting keeps its slot but sheds its roster
on every occurrence after the first: the thirteen people in a weekly sync are the
same thirteen as last week, and reprinting them daily buries the one ad-hoc
meeting where knowing the room actually matters. Only **ad-hoc meetings and the
first occurrence of a series** carry a roster.

The **news** — what has been filed about those people since you last saw them —
survives on every occurrence, because it is different every time and is the whole
reason to brief a meeting you have already had. With no roster to hang it on, a
repeat's news is attributed by a bare name link rather than a full summary line.

`news_bullets` decides what counts, and a fact is news exactly once:

- **Filed on or after the person's `LastContact`.** Facts carry the date they
  happened and LastContact is the day you last met, so this is precisely "arrived
  since we last spoke". It is what stops an April note about a colleague you sit
  with weekly from resurfacing every week in July. Inclusive of LastContact
  itself, because a fact filed out of your last meeting is one you have not been
  told yet. Someone you have **never met** has no cutoff — all of their facts are
  news, capped at `RECENT_FACTS`.
- **Told once per day.** A `told` set threads through every block, so a person in
  two of today's meetings is briefed under the first and not repeated under the
  second. Identity is the filer's `<!-- fact:… -->` provenance hash where there
  is one, so one fact filed to both people it mentions is still told once.
- **`apply_bumps` folds the day's LastContact writes back into the in-memory
  roster first**, or the cutoff would still be the day before yesterday's and
  yesterday's news would brief twice. Only contact *strictly before today*
  counts: a text you send this morning is contact, but it is not a chance to
  have read anything, and folding it in would age out a fact no brief has shown
  you.

The known cost: a fact is dated when it *happened*, not when it was *filed*, so a
backlog drain that files an old fact today lands before the cutoff and is never
briefed. Facts filed about the recent past are unaffected.

Recognizing a series is the hard part, because **EventKit cannot tell you**.
An Exchange series does not arrive as a series: each occurrence is an
independent event with `hasRecurrenceRules` false and its own identifier, and
the identifier splits again whenever the organizer edits the series — one
weekly meeting was observed as nine events under five identifiers. Only iCloud
events model recurrence honestly. So `series_key` ignores EventKit's recurrence
metadata entirely and identifies a meeting as `(calendar, folded title)`, and
`repeat_series` calls it a repeat if it already ran inside a
`SERIES_LOOKBACK_DAYS` (180) window — long enough that a monthly series does
not read as new every time. Consequences worth knowing:

- The lookback is a **third calendar dump** per run. If it fails, the brief
  **shows every attendee** rather than none: a noisy brief is recoverable, one
  that silently drops the people is not.
- A series whose first occurrence predates the window never re-introduces
  itself, which is correct — it is not new to you.
- Two genuinely unrelated one-offs sharing a title on one calendar ("Interview")
  read as a series, and the second loses its people. Widen the key if that bites.

An alias may not claim a full name the roster has never heard of. "Meeting with
Jordan Pike" names a stranger who happens to share a first name with Jordan Vale,
and briefing Jordan Vale there — dating their LastContact to a meeting they were
never in — is a write made on a coincidence. `title_matches` therefore matches on
*tokens*, not characters, and an occurrence immediately followed by a capitalized
word survives only if the two words together are themselves a roster key: that is
what keeps "Priya Raman" matching Priya Raman, and lets "Avery North" outrank the
person merely aliased "Avery". The cost is a title like "Jordan Retro", where a
capitalized non-surname follows a bare alias and the match is lost — a missed
enrichment, which is the failure worth having when the alternative is a false
write.

Identity evidence is ranked. Exact attendee email wins, followed by structured
full name, exact multi-word title name or alias, calendar-context affinity, and
finally a bare title alias. Calendar context is configured privately with
`PERSONAL_CALENDARS` and `WORK_CALENDARS` in `entities.conf`. Values can be
calendar titles, stable EventKit calendar identifiers, or source identifiers.

Strong matches from the 180-day lookback accumulate in
`~/.local/state/devonthink/identity-provenance.json`, keyed by Person UUID and a
hashed event identity. Two observations in one context, with none in the other,
make a bare-name match from the other context unresolved. Context is a prior,
not an identity key: exact email or full name always overrides it, and one
observation never establishes a hard boundary. Bare-name matches never teach
provenance, which prevents an initial mistake from reinforcing itself.

Unknown-person discovery is asynchronous and proposal-only. An unmatched
structured attendee can create a Person proposal directly. A full name parsed
from a narrow `with X` title pattern must also match a macOS Contacts card. This
keeps the old broad parser's false positives such as bands, films, clinics, and
project titles out of `_Review`. The proposal contains only `ensure_person`,
optionally with the attendee email. It invents no facts and does not set
`LastContact` before the meeting occurs. Candidate identities are remembered by
hash so rejecting one does not recreate it on every morning retry. A bare title
such as "Meet with Jordan" can also propose the unique full-name Contacts card,
but only when every roster record aliased `Jordan` has established history in
the opposite calendar context. When a candidate shares a name token with an
existing record, its op carries `confirm_new` because the candidate resolver has
already classified it as a distinct identity. Approval is the human confirmation;
no second JSON edit is required to bypass the generic near-name guard.

Matched people get their header facts plus newly filed Biographical Log entries.
Unrecognised **attendees** are still listed as "no entity record yet" (collapsed
to a count past `UNMATCHED_LIST_MAX`, so a 200-person CAB invite costs one line).
The brief reads live from records, so it can never go stale.

Exchange reports **conference rooms with `participantType` Person** — identical
to a human on every EventKit field — so rooms are excluded by name via
`SKIP_ATTENDEE_PATTERN`. Note that EventKit's enums come back from JXA as
*strings*: `calendar-events-json.js` coerces them with `Number(...)`, and
dropping that coercion silently makes `is_person` false for everyone and every
`rsvp` read as `unknown` — which now **empties the briefing** rather than
merely flattening it, so `test_calendar_canary.py` asserts against the live
calendar that some invitation still reads as `accepted`.

#### Suppressing a person (BriefingSuppressed)

**The policy lives on the Person record, not in config.** `BriefingSuppressed` is
a boolean custom-metadata flag, keyed by the record's stable **UUID**. That is the
point: a name list in a file can be defeated by a stale entry, drift out of sync
with the record, or vanish when the file does. There is exactly one authority, and
clearing the flag is sufficient to undo it. (`EntityStatus` is *not* that
authority — `reconnect_overdue()` is its only reader, so an `archived` person is
still briefed and still bumped. Lifecycle and privacy are different policies.)

It takes **two** mechanisms, and it needs both. Getting this wrong is easy and the
failure is silent.

1. **Roster rejection.** Flagged people are rejected by every person-derived
   consumer — `match_person`, `match_contact`, `title_matches`,
   `reconnect_overdue` — which silences `Briefing`, `Reconnect`, `Birthdays`,
   and `LastContact` bumps.

   They are *not* deleted from the roster, and that is deliberate. A suppressed
   record still **owns its keys**: an alias it shares with a visible person must
   stay ambiguous. Drop it and the visible person becomes sole owner of that
   alias — at which point the suppressed person's own Contacts card resolves to
   them, handing over their birthday and their Messages handle. Ambiguity is
   computed against the *whole* roster; rejection happens at the match sites.

2. **Text redaction** (`suppression_keys` → `excluded_re` / `names_excluded`).
   Filtering the roster **cannot redact raw calendar data.** An event title, an
   attendee label, and a past record's name are plain strings that no roster
   filter ever reads — so on a timeline that renders every event, dropping the
   Person record still leaves `### 2:30pm — <name>: flight to LAX` on the page.
   This was a real regression: showing every event is exactly what turned a
   harmless "no roster match" into a rendered name. **If you add a surface that
   renders text the pipeline did not compose, it needs this filter.**

**The redaction vocabulary is identity-derived, never guessed.** It is built from
the flagged record's own fields — filed name, explicit aliases, email — and then
widened by its matched Contacts card (a card-only nickname, a second address, a
phone the entity layer deliberately never stores). Contacts only *augments*; a
nickname whose suppression must be guaranteed belongs on the record as an alias.
This is what makes it work in practice, because a calendar title uses the
nickname while an attendee arrives as a bare email or a `tel:` URL, and only the
record knows they are one person.

Absorption runs to a **fixed point** so it cannot depend on the order Contacts
returns cards in, but it only ever traverses an identifier that **exactly one
card claims**. A handle two cards share — a household landline — proves nothing
about identity, and traversing it would drag an unrelated person's name and
address into the vocabulary, silently redacting *their* events. The shared number
is still redacted (it is the suppressed person's too); what it must not do is
link the other card in. The cost of this conservatism is that a genuine duplicate
card reachable *only* through a shared address is not absorbed — which is why the
record, not Contacts, is the authority: put the alias on the record.

Phone identifiers need one more step. A key is canonical digits (`norm_handle`
folds to the last 10), so it can never match the punctuation a human actually
writes. Every phone-shaped run in a title, an attendee name, or an attendee email
is therefore folded through `norm_handle` before it is judged — `Call +1 (212)
555-0101` redacts, and a flight number does not.

`norm()` **casefolds**, it does not lowercase. Only casefold folds the case pairs
that are not one-to-one, so `STRASSE` and `Straße` reach the same key; with
`lower()`, a suppressed name could be written past its own redaction just by
shouting it.

**Contacts is load-bearing, so its failure is fatal.** The card carries the
nickname, the second address and the phone the record never stores — half the
vocabulary. If the Contacts query fails while anyone is flagged, the run exits
non-zero rather than briefing with identifiers it cannot recognize.

Two boundaries matter, and both were bugs once:

- **Bare first names are never synthesised.** Deriving `Robin` from
  `Robin Sandoval` would suppress every unrelated Robin — silently deleting events
  from a timeline that promises the whole day. A first name earns a key only by
  being a *recorded alias*.
- **The trailing word boundary is `\w`, not `[\w']`.** An apostrophe there exempts
  the possessive — `Robin's flight` — which is exactly the form a personal
  calendar tends to use. The `\w` boundaries still keep a longer name that merely
  *contains* a suppressed one (`Robinson`) from matching.

**Suppression is narrow, and it is not deletion.** Redaction applies to the
smallest thing that carries the name:

| what names them | result |
| --- | --- |
| the event **title** | the slot survives as `Private event` at its original time; title, people and location are dropped. Deleting the event would leave a silent hole in a timeline that promises the whole day |
| only an **attendee** | the event renders normally, minus that attendee |
| an On This Day record, or a parked source (name *or* `last_error`) | the row is dropped |

A redacted title is never mined for `LastContact`, so nobody is credited with
contact from it; a structured attendee still is. If Contacts is unavailable while
anyone is flagged, the run exits non-zero rather than briefing with a
card-only nickname it cannot redact.

Suppressing a *calendar* is the one privacy control that does live in config:
`SKIP_CALENDARS` excludes whole calendars by name, added to the built-in
defaults, in `~/.config/dt-pipeline/entities.conf` (machine-local, mode 600,
never tracked — a real calendar name belongs there and nowhere in this repo).
Because it is a privacy control, a config file that exists but **cannot be read
is fatal**: degrading to an empty dict would quietly brief a calendar the user
asked never to see. A file that is simply absent means "never configured", which
is different, and fine.

Suppressing a *person* never touches config — see `BriefingSuppressed` above.

`BriefingSuppressed` must exist as a custom-metadata field for the flag to be
settable in the GUI, so `seed-devonthink-config.sh` **merges** missing field
definitions into an existing `CustomMetaData.plist` rather than copy-if-absent
(which would strand every machine that already owns the file on an old schema).
DEVONthink needs a restart to pick up a newly added field.

#### Muting fact-filing (FilingSuppressed)

`BriefingSuppressed` governs the *brief*; `FilingSuppressed` governs
*fact-filing* (`entity-filing.py`), and they are independent flags on the same
Person record. Neither reads the other. The distinction is privacy vs. noise: a
briefing-suppressed person is redacted from rendered output because their
presence is sensitive; a filing-suppressed person is muted because they
*saturate the sources* — a partner or housemate whom every journal entry
mentions, where the proposals are all things already known.

A flagged person is dropped from `build_person_plans` (no fact, field update,
or `LastContact` bump is ever proposed) and from an Event's `**Who:**` line. The
drop happens only once the roster **positively identifies one person**: an
ambiguous alias two people share is still proposed, because the mention is not
known to be theirs and dropping it would silently discard a fact about the
other. Crucially they **stay in the roster the LLM is prompted with** —
removing them there would make every mention fail to resolve and rebound as a
`new` proposal to create a second record for someone who already has one, which
is louder than the noise the flag silences.

The mute is a noise control, not a privacy one: it filters structured plans, not
free text, so an Event *summary* that names them is not scrubbed. To keep a name
out of rendered output, that is `BriefingSuppressed`'s job. Set both if a person
needs both.

Sections are appended *after* `## Today's Notes` (created if missing) because
`insert-jot-into-daily-note.py` targets the last bullet *before* that header —
a briefing above it would swallow incoming jots. Each section carries an
HTML-comment marker (`<!-- brief:YYYY-MM-DD -->`) making re-runs no-ops.

The brief also bumps `LastContact` for everyone matched in **yesterday's**
calendar (attendees or title). This keeps the Reconnect digest honest for
people whose contact is calendared rather than jotted — calls with family,
social plans — without waiting for a filed fact. Yesterday, not today,
because a completed day can't have its meetings cancelled out from under the
bump; and bumps only ever raise the date, so re-runs are harmless.

A `## Birthdays` section lists people whose macOS Contacts card carries a
birthday falling within the next 14 days. Cards are matched against the
roster the same way attendees are (email first, then name, then the card's
nickname against record aliases); only roster-matched people surface, which
is the whole point versus the all-of-Contacts "Birthdays" calendar that
stays in `SKIP_CALENDARS`. Birthdays are read live from Contacts on every
run and deliberately **not** stored on the Person record: identifiers from
Contacts are matching keys, not knowledge, and a stored copy would drift the
moment the card is edited. Year-less birthdays (Contacts allows them) render
without an age; a Feb 29 birthday surfaces on Feb 28 in non-leap years. The
Contacts read is one-way and identifier-only — nothing is ever written back,
per the boundaries above. JXA gotchas discovered here, guarded by
`test_contacts_canary.py`: ObjC nil must be spelled `$()` (a JS `null`
predicate silently returns zero containers), `keysToFetch` must be built as
an NSArray ObjC-side (a JS array crashes the fetch), and a year-less
birthday's `year` comes back as NSIntegerMax — bound-check before trusting
it.

LastContact is also bumped from **Messages** — the real non-work contact
signal. Each morning the brief reads `~/Library/Messages/chat.db` for
messages since yesterday's local midnight (messages have no cancellation
concept, so unlike the calendar there is no reason to exclude today's) and
bumps everyone whose handle resolves to the roster. The boundary is
deliberately narrow and hard-coded: a **read-only** SQLite connection whose
query selects handle identifiers, dates, and `is_from_me` — structurally
never the text column — and logs carry person + date only. Semantics:
received messages attribute to their sender in any chat (so family group
chats register the members who actually talk); sent messages count only in
1:1 chats, so a group broadcast never marks every member as contacted;
`item_type = 0` keeps renames/joins from counting. Handles map to people
**live** through Contacts (`norm_handle` folds phones to their last 10
digits; a handle claimed by two people's cards is dropped, not guessed) —
per the identifier decision above, phone numbers are never stored in
DEVONthink. Requires Full Disk Access on `/usr/bin/python3` (the
chromium-bookmarks agent already established that grant pattern); without
it, or on schema drift after a macOS update, the query degrades to a
logged warning the watchdog surfaces, and the brief itself is unaffected.
`--backfill-messages [--days N]` replays history once (run 2026-07-11 at
730 days).

The daily run only ever looks at yesterday, so a person seeded today starts
with no contact history. `dt-morning-brief.py --backfill-contacts [--days N]`
replays a range (default 365 days) through the same matcher, keeping only each
person's most recent date — one calendar dump, a few seconds, idempotent.
Run it once after a seeding session. The calendar and Messages are the only
historical sources of contact dates.

The brief also writes an `## Entity Review` section counting proposals that
need attention: those awaiting review in `_Review`, and separately any left
sitting in `_Review/Approved`, which means filing refused to apply them (bad
ops JSON, a failing op, or the stale-`ensure_person` guard below). It also
lists sources parked after `MAX_ATTEMPTS` failed extractions (see below), so a
note that never became entity knowledge stays visible. Nothing else surfaces
the Approved group in the daily workflow — it is normally emptied by the next
run. `dt-watchdog` *does* notify on those refusal `WARNING`s (its scan pattern
` WARN(ING)? ` matches Python's ` WARNING ` levelname), but that is a
transient, per-signature-deduplicated alert; this line keeps a standing count
in front of you each morning.

Note: only calendars in macOS Calendar are visible. Work meetings appear in
the brief because your company email account is
added in Settings → Internet Accounts — re-add it on a fresh machine.
Granola reads the work calendar through its own integration and is
unaffected either way.

### Filing (extract → resolve → file)

Sources: records with `DocumentType` containing "Meeting", records with
`Handwritten=1`, past daily notes in `/10_DAILY` (never today's — it's
still being written), and Person-fact captures in `/20_ENTITIES/_Facts` (kind
`fact`, see below), each gated on its upstream pipeline being finished
(`NeedsProcessing` clear — a Boox record mid-OCR has no text yet).
Daily-note text is stripped of the brief's machine-generated sections
(`## Briefing`, `## Reconnect`, `## Birthdays`, `## Entity Review`,
`## Journal`, `## On This Day`) before hashing and extraction, so the model
only sees the human-authored remainder. Without the strip, briefing
scaffolding round-trips into pseudo-facts: attendee lists become "X attended
the 9:00am meeting" log entries (complete with "no entity record yet" echoed
from the brief's own annotation), attendee emails become email/employer
updates, an event canceled after the 05:15 brief becomes "was scheduled to
attend a canceled meeting", and On This Day re-surfaces old entries as if
dated today — all while `SKIP_SOURCE_TITLES` is bypassed, since the skipped
meeting's title re-enters through a source named after the date. A note that
is all scaffolding now falls under the minimum-word gate instead of spending
an extraction.
Completion state lives in
`~/.local/state/devonthink/entity-filing-state.json` (fail-closed, like the
Granola importer) and is keyed on a content hash plus DEVONthink's
modification date, not a bare UUID: a late OCR pass, notebook re-export, or
hand edit re-enters filing automatically, while a metadata-only touch is
recognized by hash and re-baselined without spending an extraction. Newest
sources first, `MAX_PER_RUN` extractions per run; a source that fails
`MAX_ATTEMPTS` times is parked — surfaced in the brief's `## Entity Review`
— and retries when its content changes or via `--force`.
`entity-filing.py --rebuild-state` re-derives the processed set from the
`EntityFiled` audit flag (and runs automatically when the state file is
missing).

**Extraction is gated on a seeded roster** (`MIN_ROSTER`, default 1): below
the threshold the scan logs why and stops before any extraction, while the
apply phase and `--force` keep working. The roster *is*
the prompt's entire resolution step, and a source is only extracted again
when its content changes — so running against an empty People group spends
every source on a proposal full of bare first names ("Alison", "Mom") that
resolve to nothing until the source itself is edited. The gate
is self-clearing: seed one person and the next tick resumes. `TRANSPORT=off`
is the blunter pause — `pick_transport` returns `None` before the source is
ever read, so nothing is marked processed and nothing is charged an attempt;
sources simply queue until the transport comes back.

The extraction prompt carries the current People roster (names + aliases) so
the model resolves nicknames and pronouns to known people; the script then
re-verifies every claimed match deterministically:

- exactly one roster hit → file to that record (auto mode applies it;
  suggest mode proposes it)
- multiple hits → flagged AMBIGUOUS, always a proposal
- no hit → new-person plan, always a proposal (single-word names are flagged
  for extra scrutiny, and any roster person sharing a name token is listed as
  a possible existing match — usually the fix is adding an alias to that
  record rather than approving a duplicate)

Extraction also carries an `events` channel: when a note documents a
distinct occasion (trip, celebration, milestone — never a routine meeting or
call), filing proposes an Event record with the date, location, and attendee
list; `ensure_event` links attendees who have Person records and leaves the
rest as plain names, and Event creation is always a proposal, even in auto
mode. Attendance lands on the event's `**Who:**` line rather than as
per-person log spam — backlinks give the person→event view for free.

Recurring low-biography meetings (standups, roundtables, retros) are excluded
from extraction entirely via the `SKIP_SOURCE_TITLES` regex — their yield is
workplace-workflow trivia, and a review queue full of trivia is how the
review habit dies. `--force <uuid>` bypasses the skip for a specific record.
The extraction prompt itself also sets a high bar: biographical changes only,
no working-style observations, and "an empty list is a good answer".

**Review loop:** proposals land in `/20_ENTITIES/_Review` with a human
summary and the exact ops as a fenced JSON block. Move a proposal into
`_Review/Approved` → next run (≤30 min, or `entity-filing.py --apply-only`)
executes the ops and trashes the proposal. Delete a proposal to reject it.
Editing the JSON block before approving is supported — the ops are the truth,
the prose is just a rendering.

A proposal's ops freeze at extraction time but the roster keeps growing, so
apply re-verifies every `ensure_person` against the **live** roster first:
if its name matches no record or alias yet shares a name token with one
(`ensure_person "Alison"` after `Alison Vance` was seeded), the proposal is
left in `Approved` with a warning naming the collision, rather than silently
creating a second record. Resolve it by adding the short form as an alias on
the existing record, or by setting `"confirm_new": true` on that op when the
two really are different people. This is the same near-match check that
writes the "possible existing match" hints into a proposal, applied a second
time at the moment the ops actually run.

#### Fact capture (Drafts → `_Facts`, kind `fact`)

The **Capture Person Fact** Drafts action (`drafts/drafts-capture-fact.js`)
drops a one-line fact — *"Dana Parker's daughter started at Reed College"* —
straight into `/20_ENTITIES/_Facts` as a Markdown record, on Mac and iOS alike
(`createMarkdown` with `destination = <group UUID>`; the UUID is stable across
DEVONthink sync). It is a dedicated, prompt path for a fact you learned just
now — distinct from a daily-note jot, which is only extracted a day later,
bundled with everything else you wrote, and never same-day. Filing treats the
`fact` kind specially:

- **Not date-gated.** Unlike today's daily note, a capture is a complete
  thought at write time, so `list_sources` surfaces it immediately and the
  fact-first sort tie-breaker lets it lead same-day meetings for a
  `MAX_PER_RUN` slot.
- **Terse by design.** The 20-word scaffolding gate (which drops a short daily
  note as noise) is lowered to 1 word for `fact`, and a one-line preface tells
  the model this is a deliberate fact to extract even when brief. The capture's
  leading `# <title>` H1 — present so the global "Sync H1 and Filename" rule
  no-ops — is stripped before the prompt.
- **Local-only**, like daily/journal/handwritten: a hand-typed fact never
  reaches DT chat regardless of `TRANSPORT`.
- **Auto-files when the person is named clearly.** The `fact` kind forces
  `auto` mode, but only applies directly when the resolved person's full name
  or a multi-word alias appears *verbatim* in the capture. A bare first name
  still resolves but lands as a proposal — the model can expand "Dana" to a
  full name in `match` and slip past `weak_match`, and single first names
  collide silently as the roster grows, so "name them clearly for zero-touch"
  is the contract. Ambiguous / new-person / event cases are proposals as
  always.
- **Never bumps `LastContact`.** A typed fact is knowledge, not contact
  evidence — the calendar and Messages passes own that clock. Field updates
  (employer/role/city/email) still auto-apply, logged with a transition line
  and stale-guarded.
- **Never silently lost.** If extraction yields nothing to apply or propose,
  filing writes a *"Review capture: …"* stub to `_Review` (empty `[]` ops
  fence, so approving it is a harmless clear) rather than marking the source
  filed with no trace — surfaced in the brief's `## Entity Review` count.
- The capture record persists in `_Facts` (`EntityFiled=1`) as the clickable
  `([source](…))` provenance anchor. **Don't label a `_Facts` record** — the
  global "After Labelling, Move to 99_ARCHIVE" rule would archive it out of
  reach of filing.

Latency: filing runs on the ≤30-min tick under the usual AC + idle gates, so a
capture files on the next idle/AC tick, not instantly. `entity-filing.py
--scan-only` drains it now. Setup (bootstrap creates `_Facts` and prints its
UUID; paste into the action) is in `entities-howto.md` and `drafts/README.md`.

### Things review loop (optional, `THINGS_SYNC=on`)

The review loop above requires being at the Mac inside DEVONthink. With
`THINGS_SYNC=on`, every pending proposal is mirrored as a to-do in a Things 3
project (`THINGS_PROJECT`, default "Entity Filing") and scheduled for **Today**
so it surfaces without opening the project, and the decision travels
back: **complete** the to-do to approve, **cancel or delete** it to reject —
from any device Things syncs to. Both new phases ride the existing 30-minute
entity-filing tick (no new launchd agent, same battery/driver gates);
`--scan-only` skips them, `--dry-run` logs without firing anything.

The to-do's note is an *editable* rendering of the proposal below a
`=== proposed v1 ===` sentinel — one `PERSON Name (kind[, met])` or
`EVENT Name (YYYY-MM-DD[ at Location])` header per entity, with
`- YYYY-MM-DD — fact`, `- field = value`, and `- with: a, b` lines under it.
Delete a line to drop that assertion, edit a line to correct it, then
complete the to-do. On approval the ops are **regenerated from the parsed
note against the live roster** (via the same `build_person_plans` /
`ops_for_plan` path as extraction), written back into the proposal's ops
fence, and the proposal is moved to `Approved` — so the apply pass, including
its live-roster re-verification, is byte-identical to a hand-drag in
DEVONthink. The grammar is strict on purpose: any line that doesn't parse
(and structural limits the plan builders would otherwise enforce by silently
dropping content) **bounces** — the to-do is re-opened with a `⚠` banner
naming the offending line, never half-applied. Ambiguous or near-matching
names bounce too; completing again with the name unchanged confirms a
genuinely new person (`confirm_new`), tracked per person, not per proposal.

Mechanics worth knowing (mostly in `things_bridge.py`):

- Writes are `open -g things:///…` URLs (no Automation grant, no focus
  steal); every write is confirmed by reading Things' SQLite store, and a
  to-do is identified by the `Proposal: x-devonthink-item://…` marker in its
  note — the state map at `~/.local/state/devonthink/things-filing-map.json`
  is only a cache and rebuilds from those markers if lost or moved aside.
- Terminal task states **settle for one tick** before acting: Things Cloud
  can deliver a completion before the final note revision from another
  device, so acting immediately could file stale content. Approval latency is
  therefore one to two ticks.
- The `set_text` + `move_to` apply is crash-safe: the regenerated fence hash
  is persisted (`prepared_hash`) before touching DEVONthink, so a retry can
  tell "nothing happened" from "retry the move" from "someone else edited
  the proposal" (which bounces rather than clobbering the DT-side edit).
- A task row that *vanishes* (emptied Things trash) is **not** a rejection —
  the mapping is dropped and a still-pending proposal gets a fresh task.
  Only an explicit cancel/trash rejects.
- Closing/re-opening tasks needs `THINGS_AUTH_TOKEN` (read from the managed
  block in `~/.zshenv`; launchd sources no shell profile). Without it the
  loop degrades: proposals still apply, but bounced tasks stay completed and
  the brief's `## Entity Review` section remains the backstop.
- Reading the Things DB needs Full Disk Access on `/usr/bin/python3` — the
  same grant the Messages→LastContact pass established. The local DB only
  receives cloud pushes while Things.app runs, so the poller pre-warms it
  hidden (`open -g -j -a Things3`).

**Privacy trade, stated plainly:** task titles and notes carry person names
and facts, and they sync through Things Cloud. The entity layer is otherwise
deliberately local-only; `THINGS_SYNC=on` is an explicit, documented
exception scoped to proposal content (never the roster, never source bodies).

### Transports and privacy

`~/.config/dt-pipeline/entities.conf` (KEY=VALUE, all optional):

```
TRANSPORT=local       # local | off
OMLX_MODEL=Qwen3-VL-32B-Instruct-4bit
OMLX_URL=http://127.0.0.1:8000
OMLX_API_KEY=…        # oMLX auth key (Settings → auth.api_key); conf is 600
FILING_MODE=suggest   # suggest | auto
MAX_PER_RUN=3
MIN_ROSTER=1          # extract only once People holds this many records
SELF_NAME=            # extra self-alias to exclude from extraction
SKIP_ATTENDEE_PATTERN=\bVC\b|\bConference\b|\bRoom\b|\d+\s?ppl
                      # calendar attendees that are rooms, not people
SKIP_CALENDARS=       # calendar names never briefed on (comma-separated),
                      # added to the built-in defaults
PERSONAL_CALENDARS=   # personal calendar titles, calendar IDs, or source IDs
WORK_CALENDARS=       # work calendar titles, calendar IDs, or source IDs
SKIP_SOURCE_TITLES=Round ?Table|Standup|…   # sources never extracted
IDLE_MINUTES=10       # local extraction waits for user inactivity; 0 = off
THINGS_SYNC=off       # on = mirror proposals to Things 3 (see review loop)
THINGS_PROJECT=Entity Filing   # Things project holding the proposal to-dos
```

Resource behavior: a run with nothing to extract never loads the model (the
availability check is a tags ping), local extraction runs only after
`IDLE_MINUTES` of user inactivity (HIDIdleTime) and only on AC power — any
manual flag (`--dry-run`, `--force`, `--apply-only`, `--scan-only`,
`--rebuild-state`) bypasses both gates — so it can't spin fans or take memory
mid-work, and oMLX's per-model idle TTL
(admin panel, seconds) unloads the ~18 GB of weights shortly after each
batch. Once the backlog drains, inference happens only when a new
meeting/handwritten/daily note appears — a few short runs a day.

The deployed posture is **local-only** (`TRANSPORT=local`), which is also the
**code default** — the value the script uses when `entities.conf` is missing
or unreadable — so a lost or damaged config fails safe on-device. Extraction
runs on **oMLX** (`Qwen3-VL-32B-Instruct-4bit`, MLX backend, ~10–60 s per
extraction — the model is shared with journal OCR so only one ~18 GB set
of weights is ever resident) and
*waits* when the server is down rather than falling back anywhere else —
filing is latency-tolerant by design, so an outage costs nothing but delay.
There is no cloud transport: the privacy boundary holds by construction, not
by a config gate. `TRANSPORT=off` pauses extraction entirely.

oMLX serves an OpenAI-compatible API on :8000
(`extract_omlx` decodes free-form at temperature 0 with
`chat_template_kwargs: {enable_thinking: false}` — do **not** add
`response_format: json_schema`: oMLX's strict constrained decoding
degenerates with some models, burning the full `max_tokens` per call and
returning an empty object, observed with Qwen3-VL; `parse_extraction`
validates the output instead); models are MLX builds
from HuggingFace in `~/.omlx/models/`. The oMLX app (menu-bar,
auto-updating; the Homebrew formula does not build on macOS 27) manages the
server across reboots once its first-run setup has been completed in the
GUI; set a per-model idle TTL in the admin panel
(`http://localhost:8000/admin`) so weights unload between batches.

Model history: `Qwen3.5-35B-A3B-4bit` won a three-way bake-off on real
notes (the baseline `qwen3:30b-a3b` merged one person's fact onto another —
the most dangerous failure class; `gemma4:26b` extracted the author with
workflow trivia and hard-failed constrained JSON). Extraction then
consolidated onto `Qwen3-VL-32B-Instruct-4bit` — the journal-OCR vision
model — after an A/B on the production prompt showed comparable
attribution quality (~2.7× slower per token, irrelevant at background
cadence), trading a little speed for a single resident model. Any future
replacement must pass the same gate: run a few extractions on known notes
and check attribution, omissions, and JSON validity before trusting it
unattended. Requirements: instruction-tuned, reliable JSON output at
temperature 0, ≥16k usable context, ≤~25 GB quantized.

Boundaries hard-coded regardless of config:

- **Every source kind is local-only.** Daily notes, journal entries,
  handwritten notes, meeting notes, and fact captures are only ever
  extracted through oMLX, never DT chat. `/10_DAILY`, `/15_JOURNAL`, and
  `/20_ENTITIES/_Facts` are additionally excluded from DT's AI chat by
  design, and handwritten notebooks are transcribed on-device (see
  `boox-local.md`). Handwritten sources read from the Finder comment (the
  transcription), not the image's OCR text layer.
- **`/20_ENTITIES/People`, `_Review`, and `_Review/Approved` are excluded from
  DT's AI chat** (`excludeFromChat`), because Person records are distilled dossiers — more
  sensitive than any single source note. The automation is unaffected
  (AppleScript/JXA reads aren't gated), but DT chat and the DT MCP server
  cannot read them. Revert deliberately if conversational retrieval over the
  graph is ever wanted:
  `osascript -e 'tell application id "DNtp" to set exclude from chat of (get record at "/20_ENTITIES/People" in database "Lorebook") to false'`

## Fact provenance and correction propagation

Every machine-filed log bullet ends with an invisible provenance marker:

```markdown
- 2026-07-10 — Moved to Denver. ([source](x-devonthink-item://SRC)) <!-- fact:3f9a1c22 -->
```

The 8-hex ID is `sha1(source-uuid|date|text)[:8]` — deterministic, so
re-filing the identical fact from the same source reuses the ID, while a
rephrased fact gets a new one. HTML comments never render in DEVONthink's
markdown preview; the marker exists only in the raw text. Dedup ignores it
(`factSignature` strips it along with item links), so hand-deleting a marker
never causes a duplicate re-file, and the morning brief strips it from the
bullets it surfaces.

Together with the `([source](…))` link, the marker gives the future
correction-propagation workflow three guarantees at zero migration cost,
because it exists on the first fact ever filed:

- **Machine vs. hand:** only bullets carrying a `fact:` marker were written
  by filing. Anything authored or edited by hand is unmarked and permanently
  off-limits to automated retraction.
- **Addressability:** a reconciliation proposal can name the exact bullet it
  wants to retract or supersede (`fact:3f9a1c22`), immune to line-number
  drift and auto-link decoration.
- **Source join:** every bullet filed from a source is recoverable via the
  source link, so a re-extraction diff has a well-defined "old" set.

**Not built yet — the reconciliation workflow itself.** When a source's
content changes, filing re-extracts and appends genuinely new facts; it
never retracts or rewrites previously filed ones. The planned shape,
deferred until real facts exist to validate against: re-extraction gathers
the source's previously filed bullets (by marker), compares them with the
new extraction, and renders added / changed / removed as a reconciliation
proposal in `_Review`; only marked bullets are ever eligible for retraction.
The hard part will be rephrasings — an unchanged fact reworded by the model
looks removed-plus-added — which is why reconciliation must stay a
review-gated proposal, never an auto-apply.

## Failure modes and their mitigations

- **Entity mis-resolution** (the biggest risk): conservative exact
  name/alias matching only, ambiguity always → proposal, suggest mode until
  resolution is trusted, and a permanent proposal path for new people even in
  auto mode.
- **Silent overwrites**: append-only log with `Previously`-style change lines;
  DT versioning as backstop.
- **Hallucinated facts**: every bullet carries a source link — one click to
  verify during review; the LLM never merges, only extracts.
- **Repeat extraction cost**: processed-state file + per-source attempt cap
  (5) so a persistently failing source is eventually parked.
- **DEVONthink mid-relaunch**: the bridge resolves the Lorebook database by
  enumeration before running any op and answers `{"unavailable": true}` when
  the app or the database isn't there, so both orchestrators log a skip and
  exit 0 instead of dying on a bare `Can't get object.` — and a source is
  never charged an attempt for someone else's outage.
- **Schema sprawl**: metadata is capped at the queryable few; everything else
  goes in the body. Resist adding fields.
- **Upkeep fatigue**: the only manual acts are jotting (which you'd do anyway)
  and reviewing proposals. If you find yourself opening Person records to file
  facts by hand, the automation has failed — fix it instead.

## Operations

```bash
# preview today's brief without writing
~/.local/bin/dt-morning-brief.py --dry-run

# force the weekly reconnect section
~/.local/bin/dt-morning-brief.py --dry-run --weekly

# see what filing would do, without writing
~/.local/bin/entity-filing.py --dry-run

# re-extract one source (e.g. after editing it)
~/.local/bin/entity-filing.py --force <UUID>

# apply approved proposals right now
~/.local/bin/entity-filing.py --apply-only

# after seeding People: replay past calendar days into LastContact
~/.local/bin/dt-morning-brief.py --backfill-contacts --dry-run
~/.local/bin/dt-morning-brief.py --backfill-contacts --days 365

# one-time: replay Messages history into LastContact (handles+dates only)
~/.local/bin/dt-morning-brief.py --backfill-messages --dry-run --days 730
~/.local/bin/dt-morning-brief.py --backfill-messages --days 730

# drain the extraction backlog by hand — manual runs bypass the battery and
# idle gates entirely; each pass extracts MAX_PER_RUN sources, so repeat (or
# loop) until the log stops saying "extracting"
~/.local/bin/entity-filing.py --scan-only

# re-sort every entity log newest-first (`dry_run: true` to preview). The
# filer sorts what it writes; this is for records it has no reason to touch,
# and for logs hand-edited out of order
echo '{"ops":[{"op":"sort_logs"}]}' > /tmp/ops.json
osascript -l JavaScript ~/.local/bin/entity-dt-bridge.js /tmp/ops.json | jq .

# logs
rg 'entity-filing|morning-brief' ~/Library/Logs/devonthink-pipeline.log
```

Both agents are driver-only (loaded by `setup.sh` alongside the ingest
agents) and gate on `should-run-dt-driver`; filing also gates on
`should-run-background-job` — the brief has no battery gate, since it is
deadline-bound and runs on a fixed morning schedule regardless of power
source.

## Deliberately not built (yet)

- **Anki deck** of stable facts (guide Phase 4) — revisit only if rote recall
  of stable facts (names of close friends' kids) proves genuinely needed;
  cards must be generated from records, one-way, and never hold mutable facts.
- **Organization records** — add `/20_ENTITIES/Organizations` when "who else
  do I know at X" becomes a real question; `Employer` is a string until then.
- **Mesh/CRM enrichment feeds** — rejected as a second source of truth.
- **Multi-hop queries** ("friends of my Chicago friends") — known weak spot of
  the DT-native approach; no query infrastructure is built for these. Note the
  DEVONthink MCP server **cannot** answer them: `/20_ENTITIES/People`,
  `_Review`, and `_Review/Approved` are excluded from AI chat, so MCP refuses those records by design
  (do not lift the exclusion for this — the records are distilled dossiers).
  The retrieval path over People is the bridge or plain osascript, which read
  entity records directly. To pull the whole roster with bodies, run the
  `dump_people` op through an ops file:

  ```bash
  echo '{"ops":[{"op":"dump_people","include_bodies":true}]}' > /tmp/ops.json
  osascript -l JavaScript ~/.local/bin/entity-dt-bridge.js /tmp/ops.json | jq .
  ```

  then do the graph walk over that JSON however you like.
