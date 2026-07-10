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

Person records use a small metadata schema (see the Custom Metadata table in
the [README](../README.md)): `EntityType`, `EntityStatus`, `City`, `Employer`,
`Role`, `Relationship`, `Email`, `LastContact`. Everything narrative — partner,
kids, how-we-met, and the dated fact history — lives in the record body:

```markdown
# Bob Carter

**Role:** Architect
**City:** Chicago
**Partner:** Alice Jones
**How we met:** introduced by Dave at the 2022 conference

## Biographical Log

- 2026-03-02 — Alice got a promotion. ([source](x-devonthink-item://…))
- 2026-06-20 — Moved from Acme to Globex. ([source](x-devonthink-item://…))
```

Rules the automation enforces:

- **Append, never overwrite.** Facts are appended to `## Biographical Log`
  with the fact's date and an `x-devonthink-item://` link to the source
  document. When a queryable field changes (new employer), the field is
  updated *and* a `Employer: old → new` line is logged, so history survives.
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
| `dt-morning-brief.py` | Daily ~05:15 — retried 05:45/06:30/08:00 for standby-missed triggers, idempotent (`com.user.dt-morning-brief`): calendar + Person records → `## Briefing` section in today's daily note; Mondays also `## Reconnect` |
| `entity-filing.py` | Every 30 min (`com.user.entity-filing`): applies approved proposals, then extracts facts from unprocessed sources and files them (suggest mode by default) |

### Morning brief (resurfacing + contact tracking)

For each timed, non-declined calendar event, attendees are matched against
Person records by email, name, or alias — and person names are also matched
against the **event title** ("Call with Jake"), because personal-calendar
events rarely carry structured attendees. Matched people get their header
facts plus the three most recent Biographical Log entries; unmatched attendees
are listed as "no entity record yet" (collapsed to a count past
`UNMATCHED_LIST_MAX`, so a 200-person CAB invite costs one line). The brief
reads live from records, so it can never go stale.

Attendees only exist on the Exchange calendar; iCloud events carry none, which
is why title matching exists. Exchange also reports **conference rooms with
`participantType` Person** — identical to a human on every EventKit field — so
rooms are excluded by name via `SKIP_ATTENDEE_PATTERN`. Note that EventKit's
enums come back from JXA as *strings*: `calendar-events-json.js` compares them
with `Number(...)`, and dropping that coercion silently makes `is_person` and
`declined` false for everyone.

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

The daily run only ever looks at yesterday, so a person seeded today starts
with no contact history. `dt-morning-brief.py --backfill-contacts [--days N]`
replays a range (default 365 days) through the same matcher, keeping only each
person's most recent date — one calendar dump, a few seconds, idempotent.
Run it once after a seeding session. The calendar is the **only** historical
source of contact dates; see the note on `GranolaParticipants` below.

The brief also writes an `## Entity Review` section counting proposals that
need attention: those awaiting review in `_Review`, and separately any left
sitting in `_Review/Approved`, which means filing refused to apply them (bad
ops JSON, a failing op, or the stale-`ensure_person` guard below). Nothing
else counts the Approved group — it is normally emptied by the next run — and
those refusals only log at `WARNING`, which is below the watchdog's
notification threshold, so without this line they would be invisible.

Note: only calendars in macOS Calendar are visible. Work meetings appear in
the brief because your company email account is
added in Settings → Internet Accounts — re-add it on a fresh machine.
Granola reads the work calendar through its own integration and is
unaffected either way.

### Filing (extract → resolve → file)

Sources: records with `DocumentType` containing "Meeting", records with
`Handwritten=1`, and past daily notes in `/10_DAILY` (never today's — it's
still being written). Processed source UUIDs live in
`~/.local/state/devonthink/entity-filing-state.json` (fail-closed, like the
Granola importer); newest sources first, `MAX_PER_RUN` extractions per run.

**Extraction is gated on a seeded roster** (`MIN_ROSTER`, default 1): below
the threshold the scan logs why and stops before any extraction, while the
apply phase, the attendance pass, and `--force` keep working. The roster *is*
the prompt's entire resolution step, and a source is extracted exactly once —
its UUID goes into `processed_ids` whether or not the extraction was any good
— so running against an empty People group spends every source on a proposal
full of bare first names ("Alison", "Mom") that resolve to nothing. The gate
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

Meeting attendance is deterministic and LLM-free: any `GranolaParticipants`
name that uniquely matches a Person record bumps its `LastContact` on every
scan (bump only ever raises the date). The pass is bounded to meetings from
the last 60 days — an older meeting can no longer raise anyone's `LastContact`,
so re-scanning the whole archive each tick is skipped.

**This pass is dormant, and not because of a bug here.** Granola reads a
*subscribed* Google calendar (`…@import.calendar.google.com`), and Google
strips attendee lists from one-way ICS imports, so `documents.people.attendees`
and `google_calendar_event.attendees` are empty on every meeting and
`import-granola.py` has nothing to write into `GranolaParticipants`. The code
is kept because it starts working the moment Granola is pointed at a real
Google Calendar connection. Until then, all contact tracking comes from the
macOS Calendar via `dt-morning-brief.py`, whose Exchange events do carry
attendees. Do not "fix" this by reading attendees out of the meeting note's
body text — the pipeline's guarantee is that attendance is LLM-free.

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

### Transports and privacy

`~/.config/dt-pipeline/entities.conf` (KEY=VALUE, all optional):

```
TRANSPORT=local       # auto | local | omlx | ollama | dtchat | off
OMLX_MODEL=Qwen3.5-35B-A3B-4bit
OMLX_URL=http://127.0.0.1:8000
OMLX_API_KEY=…        # oMLX auth key (Settings → auth.api_key); conf is 600
OLLAMA_MODEL=         # optional fallback server; empty = not installed
OLLAMA_URL=http://127.0.0.1:11434
FILING_MODE=suggest   # suggest | auto
MAX_PER_RUN=3
MIN_ROSTER=1          # extract only once People holds this many records
SELF_NAME=            # extra self-alias to exclude from extraction
SKIP_ATTENDEE_PATTERN=\bVC\b|\bConference\b|\bRoom\b|\d+\s?ppl
                      # calendar attendees that are rooms, not people
SKIP_SOURCE_TITLES=Round ?Table|Standup|…   # sources never extracted
IDLE_MINUTES=10       # local extraction waits for user inactivity; 0 = off
```

Resource behavior: a run with nothing to extract never loads the model (the
availability check is a tags ping), local extraction runs only after
`IDLE_MINUTES` of user inactivity (HIDIdleTime; `--dry-run`/`--force` bypass)
so it can't spin fans or take memory mid-work, and `keep_alive: 1m` returns
the model's ~22 GB right after each batch instead of Ollama's 5-minute
default. Once the backlog drains, inference happens only when a new
meeting/handwritten/daily note appears — a few short runs a day.

The deployed posture is **local-only** (`TRANSPORT=local`): extraction runs
on **oMLX** (`Qwen3.5-35B-A3B-4bit`, MLX backend, ~2–10 s per extraction)
and *waits* when the server is down rather than ever falling back to a
cloud provider — filing is latency-tolerant by design, so an outage costs
nothing but delay. The code also carries an Ollama transport (same `local`
chain, tried after oMLX); it is currently uninstalled — reinstate with
`brew install ollama`, a model pull, and `OLLAMA_MODEL=` in the conf.
`auto` restores the DT-chat fallback for meeting/handwritten notes if
availability ever matters more than consistency — but the extraction prompt
embeds the full People roster (every name and alias), so under `auto` each
DT-chat extraction ships the whole roster to DT chat's configured provider,
not just the note being extracted; the local-only posture keeps this moot
today. oMLX serves an OpenAI-compatible API on :8000
(`extract_omlx` uses `response_format: json_schema` +
`chat_template_kwargs: {enable_thinking: false}`); models are MLX builds
from HuggingFace in `~/.omlx/models/`. The oMLX app (menu-bar,
auto-updating; the Homebrew formula does not build on macOS 27) manages the
server across reboots once its first-run setup has been completed in the
GUI; set a per-model idle TTL in the admin panel
(`http://localhost:8000/admin`) so weights unload between batches.

The model was picked by a three-way bake-off on real notes (same production
prompt/schema): Qwen3.5 was the only candidate with correct per-person fact
attribution (the baseline `qwen3:30b-a3b`, still installed as rollback,
merged one person's fact onto another — the most dangerous failure class);
`gemma4:26b` was disqualified outright (extracted the author with workflow
trivia, and hard-failed JSON generation under Ollama's constrained decoding).
Swapping models is a one-line change (`OLLAMA_MODEL=` + `ollama pull`), but
any replacement must pass the same gate: run a few `--force --dry-run`
extractions on known notes and check attribution, omissions, and schema
validity before trusting it unattended. Requirements: instruction-tuned,
strict JSON-schema adherence under Ollama structured outputs, ≥16k usable
context, ≤~25 GB quantized.

Boundaries hard-coded regardless of config:

- **Daily notes are local-only.** `/10_DAILY` is excluded from DT's AI chat
  by design, and the filing step honors that: daily notes are only ever
  extracted through Ollama, never DT chat.
- **`/20_ENTITIES/People` and `_Review` are excluded from DT's AI chat**
  (`excludeFromChat`), because Person records are distilled dossiers — more
  sensitive than any single source note. The automation is unaffected
  (AppleScript/JXA reads aren't gated), but DT chat and the DT MCP server
  cannot read them. Revert deliberately if conversational retrieval over the
  graph is ever wanted:
  `osascript -e 'tell application id "DNtp" to set exclude from chat of (get record at "/20_ENTITIES/People" in database "Lorebook") to false'`

Extraction runs at temperature 0 with a JSON schema (`format`) and
`num_ctx=16384` (Ollama's default context would silently truncate long
prompts) on the Ollama path; the DT-chat path uses a JSON-only role prompt
plus fence-stripping and strict validation in Python.

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

# drain the extraction backlog by hand — manual runs bypass the battery and
# idle gates entirely; each pass extracts MAX_PER_RUN sources, so repeat (or
# loop) until the log stops saying "extracting"
~/.local/bin/entity-filing.py --scan-only

# logs
rg 'entity-filing|morning-brief' ~/Library/Logs/devonthink-pipeline.log
```

Both agents are driver-only (loaded by `setup.sh` alongside the ingest
agents) and gate on `should-run-background-job` + `should-run-dt-driver`; the
brief passes `--urgent` to the battery gate because it is deadline-bound.

## Deliberately not built (yet)

- **Anki deck** of stable facts (guide Phase 4) — revisit only if rote recall
  of stable facts (names of close friends' kids) proves genuinely needed;
  cards must be generated from records, one-way, and never hold mutable facts.
- **Organization records** — add `/20_ENTITIES/Organizations` when "who else
  do I know at X" becomes a real question; `Employer` is a string until then.
- **Mesh/CRM enrichment feeds** — rejected as a second source of truth.
- **Multi-hop queries** ("friends of my Chicago friends") — known weak spot of
  the DT-native approach; use the DEVONthink MCP server from an AI client for
  these rather than building query infrastructure.
