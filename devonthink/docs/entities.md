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
- **Idempotent by provenance.** A log line whose source link already appears
  in the body is skipped, so re-running filing never duplicates facts.
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
| `dt-morning-brief.py` | Daily 06:40 (`com.user.dt-morning-brief`): calendar + Person records → `## Briefing` section in today's daily note; Mondays also `## Reconnect` |
| `entity-filing.py` | Every 30 min (`com.user.entity-filing`): applies approved proposals, then extracts facts from unprocessed sources and files them (suggest mode by default) |

### Morning brief (resurfacing + contact tracking)

For each timed, non-declined calendar event, attendees are matched against
Person records by email, name, or alias — and person names are also matched
against the **event title** ("Call with Jake"), because personal-calendar
events rarely carry structured attendees. Matched people get their header
facts plus the three most recent Biographical Log entries; unmatched attendees
are listed as "no entity record yet". The brief reads live from records, so it
can never go stale.

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

Note: only calendars in macOS Calendar are visible. Work meetings appear in
the brief only if the work Google account is added to macOS Calendar
(Settings → Internet Accounts); Granola reads Google Calendar directly and is
unaffected.

### Filing (extract → resolve → file)

Sources: records with `DocumentType` containing "Meeting", records with
`Handwritten=1`, and past daily notes in `/10_DAILY` (never today's — it's
still being written). Processed source UUIDs live in
`~/.local/state/devonthink/entity-filing-state.json` (fail-closed, like the
Granola importer); newest sources first, `MAX_PER_RUN` extractions per run.

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
scan (bump only ever raises the date).

**Review loop:** proposals land in `/20_ENTITIES/_Review` with a human
summary and the exact ops as a fenced JSON block. Move a proposal into
`_Review/Approved` → next run (≤30 min, or `entity-filing.py --apply-only`)
executes the ops and trashes the proposal. Delete a proposal to reject it.
Editing the JSON block before approving is supported — the ops are the truth,
the prose is just a rendering.

### Transports and privacy

`~/.config/dt-pipeline/entities.conf` (KEY=VALUE, all optional):

```
TRANSPORT=ollama      # auto | ollama | dtchat | off
OLLAMA_MODEL=qwen3:30b-a3b
OLLAMA_URL=http://127.0.0.1:11434
FILING_MODE=suggest   # suggest | auto
MAX_PER_RUN=3
SELF_NAME=            # extra self-alias to exclude from extraction
SKIP_SOURCE_TITLES=Round ?Table|Standup|…   # sources never extracted
```

The deployed posture is **local-first**: `qwen3.5:35b-a3b` (35B MoE, ~3B
active, ~23 GB, ~30–90 s per extraction on this machine) via Ollama, kept
resident by `brew services start ollama`. With `TRANSPORT=ollama`,
extraction *waits* when the model is unavailable rather than falling back to
a cloud provider; `auto` restores the DT-chat fallback for meeting/
handwritten notes if availability ever matters more than consistency.

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
