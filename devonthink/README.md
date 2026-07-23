# DEVONthink PKM Pipeline

Behold my needlessly complex, fully-automated document-processing pipeline. It's built on DEVONthink 4 smart rules. Documents enter from multiple sources (handwritten Boox notebooks via Dropbox, web bookmarks, scans, manual imports) and flow through a series of gated processing steps: OCR/transcription, LLM-powered metadata enrichment (title, date, tags, summary), action item extraction to Things 3, and daily note aggregation. Each step is controlled by boolean metadata flags so that documents can be re-processed or debugged independently.

Pipeline scripts live in [`../stow/devonthink/`](../stow/devonthink/) (stowed to `$HOME` via GNU Stow). Standalone utilities and documentation live here.

When something breaks, jump to the [Runbook](docs/runbook.md) — recovery is organized by symptom (stuck inbox record, missing brief, dead agent, duplicate imports, database restore, …).

## Multi-Mac Topology (Driver / Follower)

The Lorebook database syncs across every Mac over CloudKit, but exactly **one** machine — the *driver* — runs the document-mutating automation: the ingest watchers, the scheduled importers, the morning brief, entity filing, and the daily-note/archive jobs. Every other Mac is a *follower*: it keeps DEVONthink and Maestral alive and serves reads and UI, but never writes to the synced database. Two drivers would race each other's mutations over the same synced records, which is exactly what this split prevents.

**Role resolution** — `~/.local/bin/should-run-dt-driver` decides, first match wins:

1. `~/.config/dt-pipeline/role` containing the word `driver` or `follower`.
2. This host's `LocalHostName` (`scutil --get LocalHostName`) listed one-per-line in `~/.config/dt-pipeline/driver-hosts`.
3. **Default: follower.** A Mac with no marker sits passive rather than becoming an accidental co-driver.

`--urgent` / `--force` always pass, so a deliberate manual run works on any Mac regardless of role. `setup.sh` prompts for the role the first time you opt into the pipeline and writes the role file.

**What runs where:**

| Launch agent | Driver | Follower |
| --- | --- | --- |
| `com.user.dt-watchdog` | ✅ | ✅ (keeps DT + database open on every Mac) |
| `com.user.dt-daily-note` | ✅ | — |
| `com.user.singlefile-watcher` | ✅ | — |
| `com.user.boox-import-watcher` | ✅ | — |
| `com.user.boox-process` | ✅ | — |
| `com.user.github-stars-import` | ✅ | — |
| `com.user.dt-morning-brief` | ✅ | — |
| `com.user.entity-filing` | ✅ | — |
| `com.user.dt-database-archive` | ✅ | — |

On a follower, `setup.sh` loads only `dt-watchdog` and **boots out** any of the driver-only labels that an earlier bootstrap left loaded, so a demotion is a single re-run.

**Runtime backstops** (defense in depth, for a follower still carrying a driver agent from a pre-role-split bootstrap):

- The two fswatch watchers (`singlefile-watcher.sh`, `boox-import-watcher.sh`) re-check the role at startup **and on every event** — a follower's watcher parks in a wait-for-promotion loop rather than exiting (an exit would churn launchd's `KeepAlive` throttle) and skips each file.
- `dt-watchdog.sh` gates its Maestral launch, watcher-liveness, interval-agent-liveness, and stuck-capture/stale-export checks on the driver role, so a follower's watchdog never pages about agents that are deliberately absent.
- `create-daily-note.sh` self-skips on a follower (an explicit date argument counts as an urgent manual run).
- Both network importers call `should-run-dt-driver` before touching the database, and every auto-firing mutating smart rule guards on it too — a follower's rule engine fires then no-ops while the database syncs in over CloudKit.

**Promote or demote a Mac** — edit the role file and re-run setup; setup reconciles the loaded agents to the role (booting out the driver-only ones on a demotion):

```bash
echo driver > ~/.config/dt-pipeline/role      # or: echo follower > ~/.config/dt-pipeline/role
./scripts/setup.sh
```

To flip roles by hand without a full setup run, boot out the driver-only agents yourself:

```bash
for l in dt-daily-note singlefile-watcher boox-import-watcher boox-process \
         github-stars-import dt-morning-brief entity-filing dt-database-archive; do
  launchctl bootout "gui/$(id -u)/com.user.$l" 2>/dev/null
done
```

**Verify what's loaded:**

```bash
launchctl list | grep com.user.
```

A driver shows all nine `com.user.*` agents; a follower shows only `com.user.dt-watchdog`.

## Custom Metadata Setup

The pipeline keys on the following custom metadata fields. You do **not** create them by hand on a fresh machine — they are seeded automatically (see [Seeding and reconciliation](#seeding-and-reconciliation) below); this table is the reference for what each one means. To inspect or add display names, see Settings → Data → Custom Metadata. The Type column uses that pane's exact picker labels ("Single-line Text", not "Text"), so a hand-added field can be matched against this table without guessing.

| Field               | Type             | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Handwritten         | Boolean          | Set at filing by boox-process.py for Boox handwritten notes. Marks the Finder comment as the record's AI-readable text (transcription); notebook dedup keys on SourceFile                                                                                                                                                                                                                                                                                                                                                                                       |
| NeedsProcessing     | Boolean          | Tracks whether a record requires processing through the pipeline                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Recognized          | Boolean          | Tracks whether OCR/transcription has been run on a record                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Commented           | Boolean          | Tracks whether text has been mirrored to comment field                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| AIEnriched          | Boolean          | Tracks whether the combined AI enrichment step (rename + tag + summarize) has been run on a record                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| NameLocked          | Boolean          | When On, prevents Enrich: AI Metadata from overwriting the document's name. Set automatically by the on-rename guard rule, by the Boox importer when it replaces an existing note in place, and by Enrich: AI Metadata itself after a successful AI rename                                                                                                                                                                                                                                                                                                      |
| Summary             | Multi-line Text  | Stores a brief AI-generated summary of the document's content                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| DocumentType        | Single-line Text | Stores the AI-assigned document type label (e.g. "Receipt", "Invoice", "Meeting Notes", "Manual")                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| EventDate           | Single-line Text | Stores the event date in yyyy-mm-dd format for time-bound documents (e.g. meeting notes, calls). Extracted from content when available, otherwise from document metadata                                                                                                                                                                                                                                                                                                                                                                                        |
| LowConfidence       | Boolean          | Flagged by the AI when document content is too unclear or ambiguous to produce a reliable title and summary. Use for filtering records that need manual review                                                                                                                                                                                                                                                                                                                                                                                                  |
| PreviousName        | Single-line Text | Stores the document's name before the most recent AI rename, enabling a one-step revert                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| SourceFile          | Identifier       | Stores the original Boox filename (without extension) as a stable dedup key                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| RecognizedAt        | Date             | Timestamp set before OCR begins by Extract: Boox Handwritten. Used by Format: Boox Comments to detect timeout if `plain text` is never populated (the AI-driven "Recognize" rule action is async, unlike the builtin "OCR" action). Not used by the standard OCR path, which is synchronous.                                                                                                                                                                                                                                                                    |
| EnrichStartedAt     | Date             | Timestamp set by Enrich: AI Metadata on first attempt, used to enforce a 5-minute timeout so records aren't stuck retrying indefinitely                                                                                                                                                                                                                                                                                                                                                                                                                         |
| EnrichInputHash     | Single-line Text | SHA-256 of the inputs the LLM saw (record name + filtered/truncated content) on the last successful enrichment. If the hash matches on a retry, Enrich: AI Metadata skips the LLM call entirely. Clear this field to force a fresh LLM call on otherwise-unchanged content                                                                                                                                                                                                                                                                                      |
| ErrorCount          | Integer Number   | Tracks the number of times a document has timed out or failed processing in a pipeline step                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| PreviousTasks       | Multi-line Text  | Stores a newline-separated list of tasks already sent to Things 3 to prevent duplicates on notebook updates                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| DailyNoteLinked     | Boolean          | Tracks whether a document has been linked into its daily-note timeline — as a sub-bullet under a matched 📅 event bullet or as a timed link bullet of its own. Pre-set by Adopt Meeting Note so briefing-owned notes never double-list                                                                                                                                                                                                                                                                                                                           |
| LinkedEvent         | Single-line Text | Event key `yyyy-mm-dd-<slug of title>` tying a note to a briefing calendar event. Stamped by Adopt Meeting Note (create-on-click notes) and Post-Enrich & Archive (name-matched documents); `dt-morning-brief.py` re-derives each event's machine bullet line and sub-lines from it on every merge. Set or clear it by hand to fix a wrong match                                                                                                                                                                                                                |
| PreviousDailyNotes  | Multi-line Text  | Stores a newline-separated list of extracted daily notes to prevent duplicates on notebook updates                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| NeedsSingleFile     | Boolean          | Set on bookmarks by Extract: Web Content when the URL's hostname is NOT on the skip list. Signals to `capture-bookmarks-batch.py` that the bookmark still needs a browser-driven HTML snapshot. Cleared by `ingest-singlefile-html.py` once the bookmark has been captured                                                                                                                                                                                                                                                                                      |
| SkipSingleFile      | Boolean          | Set on bookmarks by Extract: Web Content when the URL's hostname matches `~/.config/devonthink-pipeline/singlefile-skip-domains.txt` (e.g. youtube, spotify), or manually by the user to opt a single bookmark out. The queue-drain path of `capture-bookmarks-batch.py` filters these out; the selection path (--uuid) bypasses the check so explicit user selection always captures. **Skip wins:** the hourly `Util: Metadata Cleanup` rule clears `NeedsSingleFile` whenever `SkipSingleFile` is also set, so the two flags are never simultaneously true   |
| SingleFileTooLarge  | Boolean          | Set on a bookmark by `ingest-singlefile-html.py` when the captured HTML exceeds `MAX_INGEST_BYTES` (25 MB post-compression). The ingester clears `NeedsSingleFile`, deletes the staging HTML, and flags the bookmark so the user can review / re-capture manually instead of the pipeline retrying indefinitely. A desktop capture with no bookmark record (Scenario 1) is instead moved to `~/Desktop/DT_Import_Errors/` — deleting it would destroy the only copy of a deliberate capture with no trace in DT                                                 |
| WebClipSource       | Item Link        | Points back to the source bookmark from a derived record. Set on the HTML and markdown by `ingest-singlefile-html.py` during a single atomic AppleScript pass                                                                                                                                                                                                                                                                                                                                                                                                   |
| WebClipMarkdown     | Item Link        | Set on the bookmark and HTML by `ingest-singlefile-html.py`; points to the readable markdown record                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| WebClipSnapshot     | Item Link        | Set on the bookmark by `ingest-singlefile-html.py`; points to the HTML snapshot record                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| GranolaID           | Single-line Text | Granola meeting UUID (historical — `import-granola.py` is retired; field persists on already-imported records)                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| GranolaParticipants | Multi-line Text  | Comma-separated meeting attendee names from Granola (historical — importer retired; field persists on already-imported records)                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| SummarySource       | Item Link        | Set on summary records created by the Summarize skill; item link pointing back to the source record (bookmark, PDF, etc.) that was summarized                                                                                                                                                                                                                                                                                                                                                                                                                   |
| IsJot               | Boolean          | Set by the Drafts Quick Jot action on iOS. Gates the Process: Jots smart rule, which inserts the jot into the matching daily note                                                                                                                                                                                                                                                                                                                                                                                                                               |
| AIChatTranscript    | Boolean          | Set on markdown records that came from an AI chat snapshot (claude.ai, gemini.google.com, chatgpt.com). The defuddle output is rewritten as a topic-organized writeup by `ingest-singlefile-html.py` before import (see [SingleFile Ingestion Pipeline](#singlefile-ingestion-pipeline) → "AI chat transcript rewrite"). Useful for filtering / re-running the rewrite if the prompt is tweaked                                                                                                                                                                 |
| RewriteSource       | Item Link        | Set on rewrite records created by the prose-check skill; item link pointing back to the source record that was rewritten. The Prose Check (On Demand) rule passes the source UUID to the skill, which sets this on the output record it creates in `00_INBOX`                                                                                                                                                                                                                                                                                                   |
| EntityType          | Single-line Text | Entity-layer record class: `Person` / `Place` / `Event` / `Candidate`. Set by `entity-dt-bridge.js` when a Person or candidate record is created; robust to records being moved out of their home group                                                                                                                                                                                                                                                                                                                                                         |
| TrackTarget         | Single-line Text | Promotion control on a candidate record (`/20_ENTITIES/_Candidates`): a Person record UUID to file the candidate's accumulated evidence into instead of creating a new Person — the alias-merge gesture. Read at approval time; see `docs/entities.md` → Candidates                                                                                                                                                                                                                                                                                             |
| CreateDistinct      | Boolean          | Promotion control on a candidate record: confirms creating a new Person despite roster near-matches or a single-word name, where bare approval would bounce. Read at approval time                                                                                                                                                                                                                                                                                                                                                                              |
| EntityStatus        | Single-line Text | Entity-layer lifecycle: `active` / `dormant` / `archived` / `deceased`. Only the Reconnect digest reads this — a non-`active` person is still briefed, still matched, and still gets their `LastContact` bumped. It is lifecycle, not suppression; for that see `BriefingSuppressed`                                                                                                                                                                                                                                                                            |
| BriefingSuppressed  | Boolean          | Never brief this person again. Drops them from the roster (silencing Briefing, Reconnect, Birthdays and `LastContact`) **and** redacts any event, attendee, parked source or On This Day record whose raw text names them — keyed by the record's stable UUID, so the policy cannot be lost with a config file. Tick it in the Info panel, or set it by script. The redaction vocabulary is built from this record's name, aliases and email, plus its matched Contacts card; a nickname that must be suppressed belongs here as an alias, not left to Contacts |
| FilingSuppressed    | Boolean          | Never file facts about this person again. `entity-filing.py` drops every proposed fact, field update and `LastContact` bump for them, and their `**Who:**` line on an Event — but keeps them in the LLM roster so mentions still resolve instead of rebounding as duplicate-person proposals. A noise control for someone who saturates the sources (a partner, a housemate), **independent of** `BriefingSuppressed`: it mutes filing, not the brief, and does not redact free text                                                                            |
| City                | Single-line Text | Person's home city — the reverse-lookup key (`mdcity:Chicago` answers "who do I know in Chicago")                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Employer            | Single-line Text | Person's current employer. Changes are mirrored into the Biographical Log with the previous value                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Role                | Single-line Text | Person's current job title. Changes logged like Employer                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Relationship        | Single-line Text | `family` / `close-friend` / `friend` / `colleague` / `acquaintance`. Sets the Reconnect digest threshold (30/30/60/90 days; acquaintances never surface)                                                                                                                                                                                                                                                                                                                                                                                                        |
| Email               | URL              | Person's email — the strongest calendar-attendee matching key for the morning brief                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| LastContact         | Single-line Text | yyyy-mm-dd of last interaction. Bumped by `entity-filing.py` from meeting attendance and filed facts; only ever raised, never lowered                                                                                                                                                                                                                                                                                                                                                                                                                           |
| FieldAsOf           | Multi-line Text  | JSON object mapping a Person field name to the yyyy-mm-dd date of the source that last set it (`{"employer": "2026-05-01"}`). Written by `entity-dt-bridge.js`'s `set_field`, which refuses an update dated before the field's recorded date — an older source processed later can't clobber current state. Never edit by hand; a corrupt blob is treated as empty, degrading the guard to `expected_previous` checks                                                                                                                                           |
| EntityFiled         | Boolean          | Set on source documents (meeting notes, handwritten notes, daily notes) once the entity-filing step has extracted them (or a proposal was applied). Authoritative gate is the state file; this flag is the in-DT audit trail                                                                                                                                                                                                                                                                                                                                    |

> **Migration Note — `AI-Renamed` retired.** The earlier `AI-Renamed` boolean flag has been replaced by `NameLocked`. If any existing records still carry `AI-Renamed` metadata, you can safely ignore or batch-clear it; it is no longer referenced by any rule or script.

### Seeding and reconciliation

These fields — and the smart rules, smart groups, and batch-processing presets the pipeline depends on — live in `~/Library/Application Support/DEVONthink/` as plists that DEVONthink rewrites at runtime, so they can't be stowed as symlinks (an atomic-rename save would replace a symlink with a real file and silently de-stow it). Instead the repo keeps a tracked seed under `stow/devonthink/_seed/` and `setup.sh` runs [`scripts/seed-devonthink-config.sh`](../scripts/seed-devonthink-config.sh) to copy it into place. Seeded files:

- `CustomMetaData.plist` — every field in the table above (including `GranolaID` / `GranolaParticipants`), so the fields exist the first time DT launches.
- `SmartRules.plist`, `SmartGroups.plist`, `BatchProcessing.plist` — the rule engine, smart groups, and batch presets.

Seeding is **copy-if-absent**: once a destination plist exists, DEVONthink owns it, so the seed never clobbers a live file. That makes it idempotent and safe to run with DT open — but it also means a repo update to a seeded rule or field does **not** reach a machine that already has the plist; `setup.sh` alone can't push it.

`CustomMetaData.plist` is the one exception, because it is a **schema** rather than user-authored content: copy-if-absent would strand every existing machine on an old field list, and pipeline code keying on a field added later would silently read it as empty. Missing field *definitions* are therefore **merged** into the live file by identifier; an existing definition is never touched. DEVONthink rewrites this plist at runtime, so the merge refuses while DT is running (quit it and re-run `setup.sh`), backs the live file up to `~/.local/state/devonthink/seed-backups/` first, and writes atomically. A newly added field needs a DEVONthink restart before it appears in the Info panel.

To pull repo seed changes onto an existing machine, use the reconciler ([`scripts/reconcile-devonthink-seed.sh`](../scripts/reconcile-devonthink-seed.sh)):

```bash
# Report drift only — a table of missing | same | differs. Nothing is written.
# (Plists are compared after plutil canonicalization, so byte-order noise isn't drift.)
./scripts/reconcile-devonthink-seed.sh

# Apply seed files over the live ones. Each live file is backed up first to
# ~/.local/state/devonthink/seed-backups/<timestamp>/. Quit DEVONthink first —
# it rewrites these plists at runtime and would clobber/be clobbered by the copy;
# --apply refuses while DT is running unless you add --force.
./scripts/reconcile-devonthink-seed.sh --apply             # everything that differs
./scripts/reconcile-devonthink-seed.sh --apply "Library/Application Support/DEVONthink/SmartRules.plist"
```

Going the other way — after editing **any** rule, smart group, metadata field, or batch preset in DEVONthink's GUI — refresh the seed so the change is versioned: quit DT and run [`scripts/dump-devonthink-seed.sh`](../scripts/dump-devonthink-seed.sh) (the reverse copy; `--force` to run with DT open), then commit the diff. The seed is the only carrier of that opaque plist state, so it goes stale the moment you edit config in the app and don't dump.

## Boox -> Dropbox -> Mac

- After closing a document, Boox exports a vector PDF
- Boox uploads PDF to Dropbox folder
- On Mac, Dropbox folder is mapped via Maestral to a local directory, "Notebooks"

## Boox Import Watcher (local-only pipeline)

New Boox PDF exports landing in the Maestral-synced "Notebooks" folder are staged by a `launchd` + `fswatch` watcher and processed entirely **on-device**: a local vision model (oMLX) transcribes the handwriting, a local metadata pass supplies EventDate/tags/summary, and the record enters `00_INBOX` with all pipeline flags pre-set — so no handwritten content ever reaches the cloud-backed smart-rule stages (DT OCR, chat formatting, chat enrichment). Full design: [docs/boox-local.md](docs/boox-local.md).

| Component | Location | Role |
| --- | --- | --- |
| `boox-paths.sh` | `~/.local/bin/` | Sourced, not executed. Single definition of the synced Boox folder (`BOOX_DEVICE`, `BOOX_NOTEBOOKS_DIR`), shared by the watcher and `dt-watchdog.sh`. Both fail loudly if it is unreadable — a watchdog silently skipping its check is indistinguishable from one finding nothing wrong. |
| `boox-import-watcher.sh` | `~/.local/bin/` | `fswatch` loop on the Notebooks folder. On each new `.pdf` (`Created` or `Renamed` event, recursing into subfolders) it waits for the file size to settle, then invokes the stager. Untitled `Notebook-<n>` / `Infinite-<n>` quick-note exports are deleted rather than staged. Sweeps the tree for a backlog on startup. Runs under the launchd agent. |
| `boox-stage.sh` | `~/.local/bin/` | Byte-hash short-circuits (the Boox re-emits unchanged notebooks on every sync), atomically copies the PDF into `~/.local/state/devonthink/boox/staging/`, deletes the Maestral source. No OCR, no DT writes. |
| `boox-process.py` | `~/.local/bin/` | The heavy worker (launchd `com.user.boox-process`, WatchPaths on staging + 30-min interval, gated on AC power, driver role, and normal memory pressure). Renders pages, diffs them by pixel signature, OCRs only new/changed pages via the local model, then files: the "<year> Journal" notebook becomes one markdown record per day in `/15_JOURNAL/`; every other notebook becomes/updates a monochrome Group4 TIFF in `00_INBOX`, deduplicated by `SourceFile` (a re-export replaces the backing file in place, preserving UUID/name/tags/WikiLinks), with the assembled markdown transcription in the Finder comment. |
| `com.user.boox-import-watcher.plist` | `~/Library/LaunchAgents/` | Keeps the watcher alive. `RunAtLoad=true`, `KeepAlive=true`. |

- **Watched folder:** `~/Dropbox (Maestral)/onyx/NoteMax/Notebooks`, recursing into any category subfolders. Defined once in `boox-paths.sh` and sourced by both consumers (this watcher and `dt-watchdog.sh`'s stale-export check) — Dropbox names the folder after the device model, so swapping the Boox is a one-line edit to `BOOX_DEVICE`. Files arrive via Maestral sync; the watcher acts on both `Created` and `Renamed` fswatch events because a sync client can finalize a downloaded file by renaming it into place — `--event Created` alone would miss those.
- **Failures keep the export staged:** a render/TIFF-conversion failure or an oversized TIFF logs an error and leaves the staged PDF for the next tick (`boox-process.py --status` shows parked pages and their reasons; `--force` re-queues them).
- **Untitled notes ignored:** an unnamed notebook exports as `<Template>-<n>.pdf` — the template type plus the Boox's incrementing counter (`Notebook-<n>` for the default template, `Infinite-<n>` for infinite canvas). These are throwaway quick notes, so the watcher deletes them instead of staging — titling a note on the device is the deliberate signal that it should enter DEVONthink. Because untitled notes never reach the database, a `SourceFile` match at filing is always the same intentionally-named notebook being updated, not a name collision. A new device model can introduce further template names; an unrecognized one imports as though deliberately titled, so check a new Boox's untitled exports against this pattern.
- **Ghostscript:** ImageMagick needs Ghostscript to decode PDFs. If you see a "no decode delegate" error, install it via `brew install ghostscript`.
- Records arrive in `00_INBOX` only after their transcription is complete, with `Recognized=1, Commented=1, AIEnriched=1` already set — of the smart rules, only the LLM-free [Post-Enrich & Archive](#post-enrich--archive) matches (daily-note extraction, Things tasks, archive).

## DT Smart Rules

### Sweep: Global Inbox

- Search in
  - Global Inbox
- Criteria
  - Kind is Any Document
  - IsJot is Off
  - Name does not begin with "Jot "
- Trigger
  - Every Minute
  - On Import
  - On Moving
- Actions
  - Change NeedsProcessing to 1
  - Move to 00_INBOX

> **Jot exclusion.** Records created by the Drafts Quick Jot action (iOS via DTTG `x-callback-url` → `Name begins with "Jot "`, or macOS fallback → `IsJot=1`) must not be swept into `00_INBOX`. They are consumed by [Process: Jots](#process-jots), which inserts the jot into the matching daily note and trashes the record. Without this exclusion, Sweep races Process: Jots on `On Import` and the `Every Minute` poll will always eventually win — the jot lands in `00_INBOX` with `NeedsProcessing=1`, flows through enrichment, gets renamed to `YYYY-MM-DD Jot`, and triggers a DTTG sync loop.

### Sweep: Lorebook Inbox

- Search in
  - Lorebook > Inbox
- Criteria
  - Kind is Any Document
- Trigger
  - Every Minute
  - On Import
  - On Moving
- Actions
  - Change NeedsProcessing to 1
  - Move to 00_INBOX

> Routes non-Boox documents that land in Lorebook's built-in Inbox (manual drops, DTTG) into `00_INBOX`. Boox notes no longer pass through here — `boox-process.py` files them straight into `00_INBOX` and does its own `SourceFile` dedup at that point (see [Boox Import Watcher](#boox-import-watcher-local-only-pipeline)).

### Sweep: Lorebook Root

- Exclude Subgroups: Checked
- Search in
  - Lorebook
- Criteria
  - Kind is Any Document
- Trigger
  - Hourly
  - On Import
  - On Moving
- Actions
  - Change NeedsProcessing to 1
  - Move to 00_INBOX

### Prime: Direct 00_INBOX Arrivals

Sets `NeedsProcessing=1` on records that land in `00_INBOX` directly, bypassing the Sweep rules. Records arriving via Global Inbox or Lorebook get primed by their respective [Sweep](#sweep-global-inbox) on the move. But when a client (the Drafts "New Inbox Note" action, the Keyboard Maestro `km-new-inbox-note` macro, or any future direct-create path) drops a record straight into `00_INBOX` by UUID, no sweep fires — without this rule the record would sit untouched by the pipeline.

The embedded condition filters already-primed records out of the scripts's working set, and the script itself re-checks `NeedsProcessing` so a manually-set `NeedsProcessing=0` is not overwritten.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is empty
- Trigger
  - On Import
  - Every Minute (catches records that arrived via a path that didn't fire On Import, or that otherwise got stuck without `NeedsProcessing` set)
- Actions
  - Run AppleScript (external) — see [`mark-inbox-needs-processing.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/mark-inbox-needs-processing.applescript)

### Extract: Boox Handwritten

> **Vestigial since the local-only Boox pipeline.** Boox records now arrive with `Recognized=1` already set (transcription happens on-device in `boox-process.py`), so this rule's criteria never match a new arrival. It only acts if someone manually resets `Recognized=0` on a handwritten record in `00_INBOX` — which would send the content through DT's cloud OCR; to re-process a handwritten note locally instead, re-export it from the device (or `boox-process.py --force`). Safe to disable in the GUI (refresh the seed afterwards).

Runs OCR on handwritten Boox notes. A small AppleScript timestamps the record (via `RecognizedAt`) before recognition begins so that the downstream formatting rule (Format: Boox Comments) can detect if OCR stalls. The `Handwritten` flag is set at import, so this rule only matches documents that originated from the Boox → Dropbox → import-watcher path.

> **Design Note — Async Recognition.** DEVONthink's "Recognize" action runs asynchronously: `plain text` may not be populated by the time subsequent actions in the same rule execute. For this reason, comment mirroring and formatting are handled by a separate rule (Format: Boox Comments) that polls for `plain text` availability on the next cycle.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is Off
  - Handwritten is On
- Trigger
  - Every Minute
- Actions
  - Run AppleScript (embedded)

    ```applescript
    on performSmartRule(theRecords)
        tell application id "DNtp"
            repeat with theRecord in theRecords
                add custom meta data (current date) for "RecognizedAt" to theRecord
            end repeat
        end tell
    end performSmartRule
    ```

  - Recognize - Transcribe Text & Notes
  - Change Recognized to 1

### Format: Boox Comments

> **Vestigial since the local-only Boox pipeline** (see [Extract: Boox Handwritten](#extract-boox-handwritten)): new Boox records arrive with `Commented=1` and the formatted transcription already in the Finder comment. Safe to disable in the GUI (refresh the seed afterwards).

Waits for the async "Recognize" action in "Extract: Boox Handwritten" to populate `plain text`, then sends the raw transcription to the LLM (via DEVONthink's `get chat response for message` command) for markdown formatting. The formatted text is written to the Finder Comment. If `plain text` is still empty, the rule skips the record and retries on the next poll. A 5-minute timeout (based on the `RecognizedAt` timestamp set by Extract: Boox Handwritten) prevents records from staying in limbo indefinitely if OCR stalls.

The `Commented` flag is flipped inside the script (not as a declarative action) so it is only set when `plain text` was actually available and processed.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is On
  - Handwritten is On
  - Commented is Off
- Trigger
  - Every Minute
- Actions
  - Run AppleScript (embedded) — see [`format-boox-comments.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/format-boox-comments.applescript)
  - _(No declarative "Change Commented" action — the script handles it conditionally)_

### Extract: Scans & Images

Documents not flagged as handwritten Boox notes but lacking a text layer (e.g., flat PDFs, screenshots, photos of receipts) are run through standard OCR. Because DT's built-in OCR is synchronous, both `Recognized` and `Commented` are set in the same rule immediately after OCR completes — no separate verification step is needed.

`Word Count is 0` operationalizes "lacking a text layer." A born-digital PDF carries selectable text (`Word Count > 0`), so it is deliberately *not* matched here — it is fast-tracked by [Extract: Native Text Bypass](#extract-native-text-bypass-true-fast-track) instead, which preserves its native text layer rather than replacing it with a lower-fidelity re-OCR.

Use "OCR - Apply" (not "OCR - To searchable PDF" or "OCR & Continue"). The latter two create a new record imported to the Global Inbox, which the Sweep rule picks up and recycles through this rule indefinitely, producing one new duplicate per minute.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Handwritten is Off
  - Recognized is Off
  - Word Count is 0
  - Any of the following are true:
    - Kind is Image
    - Kind is PDF/PS
- Trigger
  - Every Minute
- Actions
  - OCR - Apply
  - Change Recognized to 1
  - Change Commented to 1

### Extract: Web Content

Handles Bookmark records arriving in `00_INBOX` in one pass: cleans the title, flags for later SingleFile capture (if a URL is present), inserts a timed `🔗` link bullet into today's daily note at its timeline position (via `insert-daily-note-section.py`), and archives directly to `99_ARCHIVE`. The actual browser-based capture happens separately via the [SingleFile Ingestion Pipeline](#singlefile-ingestion-pipeline).

The bookmark title is cleaned via [`clean-web-title`](../stow/devonthink/.local/bin/clean-web-title): fullwidth punctuation introduced by browser filename sanitizers is mapped back to ASCII (e.g. `：` → `:`, `｜` → `|`), Unicode whitespace is collapsed, and any trailing `| Site Name` brand suffix is stripped. This is the only chance to fix the bookmark name — bookmarks skip AI enrichment.

Bookmarks with no URL are archived the same way, just without the `NeedsSingleFile` flag.

**Dedup on arrival.** Before doing anything else, the rule calls `lookup records with URL` against Lorebook. If another bookmark with the same URL already exists outside `00_INBOX` (i.e. has been processed and archived), the incoming record is logged and moved to the database Trash — no title clean, no flag-setting, no daily-note entry, no archive. This prevents the downstream capture queue from re-fetching already-captured URLs when a user re-saves a bookmark they already have. Concurrent `00_INBOX` arrivals (two records with the same URL, both still in the inbox) intentionally fall through so the rule doesn't race-delete a sibling.

This rule owns the bookmark's entire journey — it does not rely on `Post-Enrich & Archive` to finish the job. Previous versions set fast-track flags (`Recognized`/`Commented`/`AIEnriched`) purely to match `Post-Enrich & Archive`'s criteria, which meant the bookmark sat in `00_INBOX` across two rule firings with ~6 metadata writes, each triggering a DT re-index + DTTG sync event. Phone-synced bookmarks were the worst case — DT's UI can transiently double-render records mid-mutation, and the effect compounded across several sync cycles. Consolidating to one rule cuts the writes to ~3 and keeps them all inside a single AppleScript `tell` block.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is Off
  - Kind is Bookmark
- Trigger
  - On Import / Every Minute
- Actions
  - Run AppleScript (external) — see [`extract-web-content.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/extract-web-content.applescript)

## SingleFile Ingestion Pipeline

Bookmark → HTML → markdown ingestion lives outside DT smart rules, in three Python/bash scripts plus a launchd watcher. DT smart rules were a poor fit: the work is a multi-step sequential operation that races against DT's own ingestion, Sweep, and every-minute ticks when expressed as several scoped rules coordinating via custom-metadata flags. The Python ingester does the whole thing in one pass and hands DT finished records.

### Two scenarios

- **Scenario 1 — desktop SingleFile save.** User hits the SingleFile hotkey in Chrome. The extension writes an HTML file to `~/Downloads/SingleFile/` (a plain folder, not symlinked to DT's inbox). `fswatch`, running under a launchd agent, notices the new file and invokes the ingester. Ingester parses the URL from the SingleFile comment header, runs `defuddle` for markdown, and creates three cross-linked records in DT: a new bookmark in `99_ARCHIVE`, the HTML snapshot in `99_ARCHIVE`, and the markdown in `00_INBOX` (which enters AI enrichment). Staging file is deleted.
- **Scenario 2 — batched capture of queued bookmarks.** User saves a bookmark directly to DEVONthink (typically from phone, via DTTG). [Extract: Web Content](#extract-web-content) flags it with `NeedsSingleFile=1` on arrival. Later, the user manually runs `capture-bookmarks-batch` (CLI or KM hotkey) to drain the queue. The batch script queries DT for pending bookmarks, drives Chromium + SingleFile one URL at a time, and hands each resulting HTML to the ingester with `--bookmark <UUID>` so the existing bookmark is reused rather than re-created. The ingester clears `NeedsSingleFile` on success.

### Components

| Component | Location | Role |
| --- | --- | --- |
| `ingest-singlefile-html.py` | `~/.local/bin/` | Shared ingester. Parses SingleFile header, runs defuddle, compresses images, creates (or reuses) bookmark + HTML snapshot + markdown in a single atomic AppleScript call. Deletes staging file on success. |
| `capture-bookmarks-batch.py` | `~/.local/bin/` | Scenario 2 runner. Finds `NeedsSingleFile=1` bookmarks, drives browser via `capture-with-singlefile`, pipes each capture to the ingester. Invoked manually (CLI or hotkey). |
| `singlefile-watcher.sh` | `~/.local/bin/` | `fswatch` loop on `~/Downloads/SingleFile/`. Fires the ingester on each new `.html`. Executes under the launchd agent. |
| `com.user.singlefile-watcher.plist` | `~/Library/LaunchAgents/` | Keeps the watcher alive. `RunAtLoad=true`, `KeepAlive=true`. |
| `capture-with-singlefile` | `~/.local/bin/` | Existing script — drives running Chromium via AppleScript, triggers SingleFile via `Cmd+D` keystroke, returns the output file path. Unchanged. |

### Records produced

Both scenarios converge on the same three-record set:

| Record | Group | Notes |
| --- | --- | --- |
| Bookmark | `99_ARCHIVE` | Lightweight live link. Fast-track flags set (`Recognized`, `Commented`, `AIEnriched` = 1). `NameLocked=1`. In Scenario 2, this is the existing bookmark the user saved. |
| HTML snapshot | `99_ARCHIVE` | Faithful SingleFile HTML. `NameLocked=1`. Bypasses AI enrichment via `AIEnriched=1` when a markdown record was produced (the markdown carries enrichment); if defuddle failed, the HTML gets `NeedsProcessing=1` instead and flows through enrichment itself as a fallback. |
| Markdown extract | `00_INBOX` | Defuddle output. `NeedsProcessing=1`, `NameLocked=1`. Enters the standard enrichment pipeline. |

Cross-links are always set: `WebClipSource` on HTML and markdown point back to the bookmark; `WebClipSnapshot` and `WebClipMarkdown` on the bookmark point forward to the HTML and markdown respectively.

### Untitled-page fallback

The `%if-empty<{page-title}|No title>` pattern in SingleFile's filename template means pages without a `<title>` tag land as `No title.html`. Without intervention, all three records (bookmark, HTML, markdown) inherit `"No title"` as their name and get `NameLocked=1`, so AI enrichment never replaces it.

`ingest-singlefile-html.py` detects the placeholder (`No title`, `Untitled`) at import time and:

1. Augments the displayed name with a URL-derived suffix (e.g. `"No title — courses.mooc.fi/.../chapter-2"`) so the three records remain visually distinguishable in DT before enrichment runs.
2. Leaves `NameLocked` unset on all three records.

`Enrich: AI Metadata` then renames the markdown using its content (the prompt explicitly recognizes `"No title"`-style names as generic). [Post-Enrich & Archive](#post-enrich--archive) walks `WebClipSource` → bookmark → `WebClipSnapshot` → HTML and propagates the AI-derived name to both siblings via `replaceIfPlaceholder`, which only replaces names still beginning with `"No title"` so any manually-edited title is preserved.

### SingleFile extension setup

1. Install Chromium and the SingleFile extension.
2. In SingleFile → Options → File name, use:

    ```
    SingleFile/%if-empty<{page-title}|No title> ({date-iso} {time-locale}).{filename-extension}
    ```

    (This matches `singlefile-extension-settings.json`, the canonical settings file. The date-time suffix keeps repeat captures of the same page from colliding; `derive_title` strips it on ingest.)

3. Bind SingleFile's keyboard shortcut to `Cmd+D` in `chrome://extensions/shortcuts`. (Used by `capture-with-singlefile` for Scenario 2 and by the user directly for Scenario 1.)
4. Ensure `~/Downloads/SingleFile/` exists as a real folder (not a symlink to DT's inbox — that's what the pre-refactor setup used, and what this architecture explicitly avoids).

### Invoking the batch from DT (optional)

Scenario 2 can be triggered from inside DEVONthink via an on-demand smart rule, rather than dropping to the shell or Keyboard Maestro. Create a rule:

- Search in: (any — criteria below make it no-op if nothing's pending)
- Criteria:
  - NeedsSingleFile is On
  - Kind is Bookmark
- Trigger: On Demand
- Actions:
  - Run AppleScript (external) — see [`capture-bookmarks-on-demand.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/capture-bookmarks-on-demand.applescript)

Fire it from Tools → Apply Rules → the rule. Two invocation modes:

- **Nothing selected (or Apply Rules from a group view):** the batch drains the full `NeedsSingleFile=1` queue.
- **One or more records selected:** the rule passes those UUIDs through and the batch captures only those. Non-bookmark or URL-less records are silently skipped; the `NeedsSingleFile` flag is not consulted in this mode, so you can force a re-capture of any bookmark on demand.

Either way, `capture-bookmarks-batch.py` launches in the background, a macOS notification confirms the launch, and progress lands in `~/Library/Logs/devonthink-pipeline.log`. Pathological pages (post-compression HTML over 25 MB) are skipped and flagged with `SingleFileTooLarge=1` on the bookmark rather than stalling the batch.

### AI chat transcript rewrite

When the SingleFile capture's source URL is hosted on `claude.ai`, `gemini.google.com`, or `chatgpt.com`, the defuddle output is a raw turn-by-turn transcript that reads poorly as a reference document. Before import, `ingest-singlefile-html.py` calls DEVONthink's `get chat response` with a curated rewrite prompt that reorganizes the transcript by topic, drops conversational framing (greetings, "great question", model signatures, the user's questions restated), and applies the prose style rules from `~/.claude/CLAUDE.md`. The result is a topic-organized writeup, not a summary — every fact, recommendation, and caveat the assistant produced is preserved.

A provenance line is prepended to the markdown body inside the import AppleScript: `*Generated from a conversation with Claude on YYYY-MM-DD. Original capture: [title](x-devonthink-item://...).*` The link points at the HTML snapshot record so the original conversation is one click away.

The markdown record is flagged `AIChatTranscript=1`. The HTML snapshot is unchanged. If the LLM call fails or times out (240 s budget), the ingest falls through to the raw defuddle transcript and logs a warning — the pipeline does not block on the rewrite. Hosts to detect are listed in `AI_CHAT_HOSTS` at the top of `ingest-singlefile-html.py`.

### Skipping domains that don't benefit from SingleFile

Some pages either won't produce a useful SingleFile snapshot (YouTube, Spotify) or already get a clean defuddle extract without one (most static-content sites). To stop those from entering the capture queue in the first place, Extract: Web Content consults `~/.config/devonthink-pipeline/singlefile-skip-domains.txt` via `~/.local/bin/should-skip-singlefile` at ingest time. Bookmarks whose hostname matches a listed domain (suffix match, so `youtube.com` covers `m.youtube.com`) get `SkipSingleFile=1` instead of `NeedsSingleFile=1`.

The queue-drain path of `capture-bookmarks-batch.py` filters out any bookmark with `SkipSingleFile=1`, so editing the skip list retroactively stops future batch runs from touching already-flagged-for-capture records. The selection path (`--uuid`, used by the on-demand rule when invoked against a selection) bypasses the check — selecting a bookmark and running the rule is treated as explicit intent to capture, overriding the skip.

Default blocklist is `youtube.com`, `youtu.be`, `spotify.com`. Edit the file to add more.

### Logs

Most pipeline components — the ingest smart rules, Python scripts, shell watchers — write to a single central log at `~/Library/Logs/devonthink-pipeline.log`. See the [Pipeline Logging](#pipeline-logging) section for the format and how to grep it. Exceptions: `import-granola.py` and `import-github-stars.py` keep their own log files, `create-daily-note.sh` writes to `~/Library/Logs/dt-daily-note.log`, and the Util/formatting rules (`Process: Jots`, `Format: Boox Comments`, `Lint Markdown`, H1 sync) log only to DT's Log window. The SingleFile watcher's raw stdout/stderr also lands at `/tmp/singlefile-watcher.log` (launchd's capture) in case the pipeline log itself fails to write.

`dt-watchdog.sh` (every 5 minutes) is the consumer that makes failures visible: it scans the newly-written regions of the central log, `dt-daily-note.log`, and `github-stars-import.log` for `ERROR`/`WARN`/`ALERT` lines and raises a macOS notification per new failure signature (digit-stripped, re-notified at most daily; state in `~/.local/state/devonthink/watchdog-scan/`). It also verifies the two fswatch watcher agents have live processes (kickstarting and alerting when not) and flags `.html` files stuck in `~/Downloads/SingleFile/` for more than 15 minutes.

## Pipeline Logging

Every component of the pipeline appends to a single file so a record's full journey can be traced without hunting across DT's Log window, per-script log files, and shell stderr.

**Central log:** `~/Library/Logs/devonthink-pipeline.log`

**Format:** `YYYY-MM-DDTHH:MM:SS LEVEL [Component] message (record="Name"|uuid=…)`

The `(record=…)` suffix is optional; when present, grepping by UUID surfaces every event for a single record in order.

**Example:**

```
2026-04-24T14:32:15 INFO [Extract: Web Content] archived bookmark (record="Some Article"|uuid=A1B2-…)
2026-04-24T14:32:17 INFO [singlefile-watcher] ingesting /Users/alec/Downloads/SingleFile/Some Article.html
2026-04-24T14:32:20 INFO [singlefile-ingest] ingest complete: Some Article.html (A1B2-…|C3D4-…|E5F6-…)
2026-04-24T14:33:01 INFO [Enrich: AI Metadata] enriched (type=Article) (record="Some Article"|uuid=E5F6-…)
2026-04-24T14:33:04 INFO [Post-Enrich & Archive] archived (record="Some Article"|uuid=E5F6-…)
```

**Helpers** — scripts don't write the central log directly; they go through one of two thin wrappers:

| Helper                                | Used by                                | Interface                                                                                                    |
| ------------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| [`~/.local/bin/pipeline-log`](../stow/devonthink/.local/bin/pipeline-log)         | AppleScript smart rules, bash scripts  | `pipeline-log <component> <level> <message> [<record-name> [<record-uuid>]]`                                 |
| [`~/.local/bin/pipeline_log.py`](../stow/devonthink/.local/bin/pipeline_log.py)   | Python scripts                         | `from pipeline_log import setup as setup_log; log = setup_log("component"); log.info(...)`                   |

**Adding logging to a new smart rule:** append the shared `pipelineLog` handler to the bottom of the script (see [`extract-web-content.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/extract-web-content.applescript) for the canonical copy) and call `my pipelineLog(component, level, msg, recName, recUUID)` at significant events. Existing `log message` calls can stay alongside — they feed DT's Log window for real-time in-app monitoring.

**Common greps:**

```bash
# Trace one record's journey
grep 'uuid=A1B2-ABCD' ~/Library/Logs/devonthink-pipeline.log

# Errors and warnings only
grep -E ' (WARN|ERROR) ' ~/Library/Logs/devonthink-pipeline.log

# Everything from one rule
grep '\[Enrich: AI Metadata\]' ~/Library/Logs/devonthink-pipeline.log

# Live tail during debugging
tail -f ~/Library/Logs/devonthink-pipeline.log
```

## DT Smart Rules (continued)

### Extract: Native Text Bypass (True Fast-Track)

Documents that are natively text-based (Markdown, RTF, Web Archives, HTML pages, or PDFs that already contain a text layer) bypass OCR entirely. This rule flips both flags in a single pass and advances the document straight to AI enrichment. For Markdown files, a lint step runs `markdownlint --fix` (with tab-to-space conversion) over the record's in-memory content before the flags are set, ensuring house style compliance before downstream processing. Linting reads `plain text of theRecord`, round-trips it through a temp file for `markdownlint`, and writes the result back via `set plain text` — never touching `path of theRecord` directly — so programmatically-created records (e.g. from the `summarize` and `prose-check` skills) aren't at risk of a `synchronize record` racing DT's buffered disk write and wiping the body.

Bookmarks are excluded because they are handled by [Extract: Web Content](#extract-web-content). HTML pages from SingleFile captures never reach `00_INBOX` — they're handed to DT as finished records in `99_ARCHIVE` by the [SingleFile Ingestion Pipeline](#singlefile-ingestion-pipeline). Any HTML that does land in `00_INBOX` (e.g. user-dragged, DTTG-synced) is fast-tracked by this rule.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Handwritten is Off
  - Recognized is Off
  - Word Count is greater than 0
  - Kind is not Image
  - Kind is not Bookmark
- Trigger
  - Every Minute
  - On Import
- Actions
  - Run AppleScript (embedded) — see [`lint-markdown.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/lint-markdown.applescript)
  - Change Recognized to 1
  - Change Commented to 1

> **Design note — the scan-vs-native-text split keys on `Word Count`, not `Kind`.** A born-digital PDF and a scanned PDF are both `Kind PDF/PS`; the text layer is what separates them. [Extract: Scans & Images](#extract-scans--images) takes `Word Count is 0` (no text layer → needs OCR); this rule takes `Word Count is greater than 0` (already has selectable text → skip OCR). That is why `Kind is not PDF/PS` was **removed** from this rule — a text-bearing PDF has to be allowed in. Don't re-add it or drop either `Word Count` condition: either change orphans born-digital PDFs, which then match no extraction rule and stall in `00_INBOX` with no OCR error to flag them.

### Enrich: AI Metadata

A single LLM call per document generates a title, event date, document type, tags, summary, and confidence flag in one structured JSON response. The embedded AppleScript calls `get chat response` directly with `as "JSON"` so DEVONthink returns a native AppleScript record — no `jq` parsing or markdown-fence stripping needed.

**Token-usage controls** — two guards keep spend bounded:

1. **Content cap** — Before the LLM call, the script filters out daily-notes and action-items sections (those are extracted separately by Post-Enrich & Archive and shouldn't steer the title/summary), then caps very long documents at a head+tail window (6000 words from the front + 2000 from the back + a truncation marker). Covers markdown, txt, rtf, PDFs (post-OCR), and HTML — any record with populated `plain text` goes through this path.
2. **Input-hash cache** — After a successful enrichment, the SHA-256 of `recName + filteredText` is stored in `EnrichInputHash`. If `AIEnriched` gets reset to 0 later (manual retry, `ErrorCount` cleanup, etc.) and the content hasn't changed, the rule skips the LLM call entirely — applied fields from the prior successful run stay in place, just `AIEnriched` flips back to 1. To force a fresh LLM call on otherwise-unchanged content, clear the `EnrichInputHash` field too.

The script reads each field from the record and applies it:

- **Title** — If the document already has a clear, descriptive title (from its filename or a heading within the content), the AI preserves it as-is. A new title is only generated when the existing name is generic (e.g. "Untitled", "IMG_0042"). The `NameLocked` flag prevents the rename from overwriting names that were set intentionally (see [#after-renaming-lock-name](##after-renaming-lock-name---lock-name-on-rename)).
- **Event Date** (`eventDate`) — Set if and only if the document is anchored to a single specific calendar date (e.g. a receipt, a bill, a meeting, a call, a journal entry, an appointment). Not set for documents that span a period or have no specific date (e.g. W-2, annual report, manual, walkthrough, bookmark, contract). The AI will not construct or infer a date from a referenced period. The date comes from content; the file creation/modification date is used as a fallback only when the content doesn't state it explicitly. The date is prepended to the title by the script (not the AI) and stored in the `EventDate` custom metadata field.
- **Type** — A document type label (e.g. "Receipt", "Manual", "Meeting Notes") stored in the `DocumentType` custom metadata field, separate from topical tags.
- **Tags** — 1–3 topical/thematic tags, deduplicated against existing tags before appending.
- **Summary** — A 1–2 sentence summary stored in the `Summary` custom metadata field.
- **Low Confidence** — A boolean flag stored in `LowConfidence` when the AI determines the content is too unclear for reliable extraction. Useful for filtering records that need manual review.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is On
  - Commented is On
  - AIEnriched is Off
- Trigger
  - Every Minute
- Actions
  - Execute Script (AppleScript, embedded) — see [`enrich-ai-metadata.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/enrich-ai-metadata.applescript)

### Post-Enrich & Archive

Runs after AI enrichment completes. Performs five steps in a single pass:

1. **Action Items** — Parses the document for sections titled "Action Items", "Todos", or similar, and sends any bulleted tasks to Things 3 via AppleScript. Deduplication is handled via `PreviousTasks`. Skipped for web clip records (those with `WebClipSource` set).
2. **Daily Notes** — Extracts "Daily Notes", "Today", "Journal", or "Log" sections from handwritten documents and inserts them into today's daily note as a timed `- <h:mmam>: ✏️ [name](item-link)` bullet with the extracted lines as indented sub-lines. Then links the document into its daily-note timeline: if its name matches a briefed event title (`brief_events.py match` — candidate days are the `EventDate` when set, else the creation day plus the day before), the document gets `LinkedEvent` stamped and a `✏️`/`📝` sub-bullet spliced under that day's `📅` event bullet; otherwise a timed link bullet is inserted at its chronological position on the `EventDate`-else-creation date's note. Deduplication is handled via `PreviousDailyNotes` and `DailyNoteLinked`. Skipped for web clip records. See `docs/entities.md` → "Every event title is a note link".
3. **Sync H1** — For markdown documents, ensures the first `# Heading` matches the record's filename (minus extension). If the H1 differs it's replaced; if absent it's injected after any YAML frontmatter. This guarantees the AI-enriched title is reflected in the document body.
4. **Web clip name propagation** — For markdown web clips (records with `WebClipSource` set), if the linked bookmark or HTML snapshot still carries a `"No title"` placeholder name, propagate the markdown's (post-enrichment) name to it. See [Untitled-page fallback](#untitled-page-fallback). `NameLocked` is set on the sibling _before_ the rename so `After Renaming, Lock Name` doesn't double-fire.
5. **Archive** — Moves the record to `99_ARCHIVE` and clears `NeedsProcessing`. The move happens first; the flag is only cleared on success to prevent silent data loss.

This consolidates the previous Extract: Action Items, Process: Daily Notes, and Archive: Processed Items rules into one script, eliminating two "Every Minute" polling rules.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is On
  - Commented is On
  - AIEnriched is On
- Trigger
  - Every Minute
- Actions
  - Execute Script (AppleScript, external) — see [`post-enrich-and-archive.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/post-enrich-and-archive.applescript)

### Adopt Meeting Note

Backstop for meeting notes that arrive without the `dtnote://` handler. `dt-morning-brief.py` renders every note-less briefing event title as a `dtnote://open?date=…&title=…` link, and DTNote.app → `dtnote-open.py` opens-or-creates the note fully stamped, so the normal click path never needs this rule. It exists for the hand-made variant — a markdown note named `"YYYY-MM-DD <event title>"` and tagged `Meeting Note` by hand: it derives `EventDate` and the `LinkedEvent` key from the record name (`brief_events.py adopt-key`), stamps `DocumentType="Meeting Notes"` (so entity filing sweeps the note as a source) and `DailyNoteLinked=1` (so it never gets a second timeline bullet of its own), then swaps the title link in that day's `📅` event bullet for the note's item link in place. Handler-created notes carry `LinkedEvent` already, so the rule's guard skips them. See `docs/entities.md` → "Every event title is a note link".

- Search in
  - Lorebook
- Criteria
  - Kind is Markdown
  - Tag is `Meeting Note`
- Trigger
  - On Creation; Every Minute (catch-up — the script no-ops once `LinkedEvent` is set)
- Actions
  - Execute Script (AppleScript, external) — see [`adopt-meeting-note.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/adopt-meeting-note.applescript)

### Process: Jots

Handles jot documents created from the Drafts **Quick Jot** action on iOS. Each jot arrives as a small markdown document matched by `IsJot=1` custom metadata or a `Jot ` name prefix (DTTG's `x-callback-url` scheme can't set custom metadata, so iOS jots carry the prefix instead), with the body already formatted as a timestamped bullet (e.g. `- 7:19am: Look into Fyxer AI`). The rule inserts the jot into the matching daily note's timeline at the jot's own timestamp position, deduplicates by content, and trashes the jot document.

On macOS the Drafts action modifies the daily note directly via AppleScript, so this rule only fires for jots created on iOS via DEVONthink To Go's `x-callback-url` scheme and synced back.

- Search in
  - Global Inbox
- Criteria
  - Any of: IsJot is On **or** Name begins with "Jot "
  - Kind is Markdown
- Trigger
  - On Import
  - Every Minute
- Actions
  - Execute Script (AppleScript, embedded) — see [`process-jots.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/process-jots.applescript)

### After Renaming, Lock Name

Automatically sets `NameLocked` whenever a document is renamed outside the pipeline (e.g. by the user in DEVONthink or Finder). This prevents Enrich: AI Metadata from overwriting an intentional name the next time the document is processed.

AI-initiated renames from Enrich: AI Metadata's own AppleScript are **not** caught by this rule because it sets `NameLocked=1` _before_ performing the rename, so the criteria below (`NameLocked is Off`) no longer match by the time the rename event fires.

- Search in
  - Lorebook (entire database)
- Criteria
  - Kind is Any Document
  - NameLocked is Off
- Trigger
  - On Renaming
- Actions
  - Change NameLocked to 1

> **Scope Note.** This rule searches the entire Lorebook database, not just `00_INBOX`. That means renaming a document in `99_ARCHIVE` (or any other group) will also lock its name — which is the desired behaviour, since it protects the name if the document is ever re-processed.

### Util: Metadata Cleanup

Hourly catch-all for trivial metadata inconsistencies that accumulate over time (manual edits, cross-device sync, retired flags, etc.). Each cleanup is a self-contained script handler that re-checks its own preconditions; the DT-level criteria are the explicit union of currently-handled cases, so DT pre-filters to just the records that need work and yields zero in normal state.

Current cleanups:

- **`SkipSingleFile` vs `NeedsSingleFile`** — if both are 1, clears `NeedsSingleFile`. Skip is the user's "off switch" and wins by design. To force-capture a previously-skipped bookmark, clear `SkipSingleFile` first, or select the record and run the on-demand rule (selection mode bypasses both flags).

- Search in
  - Lorebook (entire database)
- Criteria
  - Kind is Bookmark
  - SkipSingleFile is On
  - NeedsSingleFile is On
- Trigger
  - Hourly
- Actions
  - Execute Script (AppleScript, external) — see [`util-metadata-cleanup.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/util-metadata-cleanup.applescript)

> **Adding a new cleanup case.** Add an `OR` group to the DT criteria covering the new case's preconditions (DT 4 supports nested AND/OR groups), then add a new `my cleanupX(theRecord)` call inside `performSmartRule` plus the corresponding handler in the script. The handler should re-check preconditions defensively so it's safe even if DT yields a record that matches a different case's criteria.

### Restore Previous Name

Reverts a document's filename to the value stored in `PreviousName` (the name it had just before the most recent AI rename). After restoring, it clears `PreviousName` so the rule no longer matches the record. `NameLocked` stays **On** so Enrich: AI Metadata won't overwrite the restored name.

> **Tip — Re-running AI enrichment.** If you want the AI to take another shot at naming a document after restoring its old name, also clear `NameLocked` and `AIEnriched` on that record so it re-enters the AI pipeline.

- Search in
  - Lorebook (entire database)
- Criteria
  - Kind is Any Document
  - PreviousName is not empty
- Trigger
  - On Demand
- Actions
  - Execute Script (AppleScript, embedded) — see [`util-restore-previous-name.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/util-restore-previous-name.applescript)

> **Hardening Note — Why an AppleScript instead of declarative move actions**
>
> An earlier version used two declarative actions: `Change NeedsProcessing to 0` followed by `Move to 99_ARCHIVE`. If the move failed silently, the flag was already cleared, so the rule would never re-match. The AppleScript replacement moves **first**, then clears the flag only on success.

### Other utility rules

The remaining seeded rules are small conveniences. Most use embedded scripts or declarative actions whose definitions travel only in the seeded `SmartRules.plist`, so the notes here are intent-level — open the rule in DT for exact criteria. To refresh the seed after editing any rule, run `scripts/dump-devonthink-seed.sh` and commit.

- **Add/Update H1** (on demand) and **After Renaming Markdown, Add/Update H1** (automatic) — insert or refresh a markdown record's H1 from its name; the automatic variant fires after a rename so the body heading tracks the new name.
- **After Saving Markdown, Sync H1 and Filename** (automatic) — the bidirectional H1↔name sync: renames the record to match its first H1, or inserts/updates the H1 from the filename, skipping YAML frontmatter. External script: [`sync-h1-and-filename.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/sync-h1-and-filename.applescript).
- **After Labelling, Move to 99_ARCHIVE** (automatic) — declarative: putting a label on a record archives it.
- **Skip SingleFile (On Demand)** — flips selected bookmarks to `SkipSingleFile=1`, the manual opt-out described in the `SkipSingleFile` metadata row.
- **Prepare for Re-Enrichment** (on demand) — clears enrichment state on the selection so Enrich: AI Metadata takes a fresh pass (the workflow the `EnrichInputHash` row describes).
- **Markdownify Text** (on demand) — converts the selected records to markdown.
- **Convert User-AI Conversation to Reference** (on demand) — reshapes a captured user↔AI conversation record into a reference document.
- **Lint Markdown (On Demand)** — manual invocation of the markdown lint pass ([`lint-markdown.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/lint-markdown.applescript)).
- **Prose Check** and **Summarize** (on demand) — front doors to the Claude Code skills; they hand the selection to the skill in the background ([`prose-check-on-demand.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/prose-check-on-demand.applescript), [`summarize-on-demand.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/summarize-on-demand.applescript); see [docs/summarize.md](docs/summarize.md)).
- **Capture Bookmarks Batch (On Demand)** — documented under [Invoking the batch from DT](#invoking-the-batch-from-dt-optional).

DT-stock rules (Reminders, Filter Duplicates, Bates Numbering, chat-suggestion helpers, etc.) are not part of the pipeline and aren't documented here.

> **Entity fact captures and the two global rules.** The "Capture Person Fact" Drafts action drops a Markdown record into `20_ENTITIES/_Facts` (source kind `fact`; see [docs/entities.md](docs/entities.md) → "Fact capture"). Because every enrichment/sweep/archive rule is scoped to `00_INBOX`, a `_Facts` record is never swept or archived — but the two **global** utility rules above still see it: the capture ships a body whose H1 already equals its title so **Sync H1 and Filename** is a no-op, and a `_Facts` record must **never be labelled**, or **After Labelling, Move to 99_ARCHIVE** would pull it out of `_Facts` before filing reads it.

## Notebook Dedup (at import)

Boox re-exports are deduplicated twice: `boox-stage.sh` byte-hash short-circuits identical re-emissions before any work happens, and `boox-process.py` dedups by the `SourceFile` custom-metadata field (the original Boox filename, minus extension) at filing time. A new notebook imports into `00_INBOX` with its transcription already in the Finder comment and all pipeline flags set. A re-export whose `SourceFile` matches an existing record replaces that record's backing file in place — preserving its UUID, name, tags, and WikiLinks — refreshes the comment and metadata, sets `NeedsProcessing=1`, and moves it back to `00_INBOX`, where Post-Enrich & Archive re-runs its idempotent pass. Only new or edited pages are re-transcribed (per-page pixel signatures).

Doing this at import — rather than in a smart rule after the record exists — means it never depends on DEVONthink's smart-rule trash or action-ordering semantics. The earlier `Handle Updated Notebooks` rule did this from within *Sweep: Lorebook Inbox*: it soft-trashed the duplicate import and relied on the rule stopping, but the rule's later declarative `Move to 00_INBOX` action pulled the trashed record back into the pipeline as a second copy.

## Daily Notes (Scheduled)

A daily note is automatically created in the **10_DAILY** group of the Lorebook database every morning at 5:00 AM local. The mechanism uses `launchd` (macOS's native scheduler) to run a shell script that talks to DEVONthink via AppleScript.

The schedule is set to a daytime wake-hour rather than the small hours: `StartCalendarInterval` does not fire while the Mac is asleep (and `WakeFromSleep` is not reliable when the lid is closed in standby), so an early-morning trigger that the user is consistently around for is more reliable than a 03:00 trigger that gets silently skipped. If the trigger is still missed (rare), the script's no-arg backfill path seeds today's note the next time it runs.

### How It Works

1. `launchd` fires the job at 05:00 every day.
2. The shell script computes today's date, builds the markdown content from an embedded template, and calls `osascript`.
3. The AppleScript block checks whether a note with today's filename already exists in 10_DAILY — if so it exits cleanly (idempotent). Otherwise it creates the new markdown record.
4. If a note was created, the script triggers a DEVONthink cloud sync (`synchronize database`) so the note is available on other devices immediately.
5. All activity is logged to `~/Library/Logs/dt-daily-note.log`.

### Pipeline Integration

The primary DEVONthink smart rule pipeline integrates directly with daily notes through [Post-Enrich & Archive](#post-enrich--archive) (which absorbed the retired standalone "Process: Daily Notes" rule):

- **Extracting Daily Logs:** For handwritten notes, the pipeline searches for headers like "Daily Notes", "Today", "Journal", or "Log". If found, it extracts the content beneath them and inserts it into today's daily note as a timed `✏️` link bullet with the extracted lines as indented sub-lines. Deduplication ensures that repeated notebook updates don't result in duplicated entries.
- **Linking Briefing Events:** Before the fallback below, the pipeline tries to match the document's name against that day's briefed event titles (see [Adopt Meeting Note](#adopt-meeting-note) and `docs/entities.md` → "Every event title is a note link"); a match files the document as a sub-bullet under that `📅` event bullet instead of as a timeline bullet of its own.
- **Linking Temporal Events:** For any document processed by the pipeline, if the AI enrichment step identified a specific `EventDate` (e.g., from meeting notes), the pipeline inserts a timed link bullet for that document at its chronological position on the daily note corresponding to that specific date. If the target note doesn't exist yet — a past/future `EventDate`, or a morning where the 5:00 AM `create-daily-note.sh` run was missed — Post-Enrich & Archive creates it on demand (matching `create-daily-note.sh`'s heading and `Daily Note` tag) rather than dropping the link. Extract: Web Content and `ingest-singlefile-html.py` create today's note on demand the same way, so captures landing between midnight and the 05:00 seeder keep their daily-note entry.

### Template Format

Each daily note follows this structure (generated by `create-daily-note.sh`, and by the rules that create a missing note on demand):

```
# Wednesday, January 21, 2026

-
```

- **Heading** — full day-of-week, month, day, year.
- **Bullet** — empty starter bullet for quick capture; the first machine insert into a virgin note replaces it.
- **Tag** — the DT tag `Daily Note` is applied to the record via AppleScript (not embedded in the document body), enabling smart groups and filtering.

The body grows into a single flat chronological timeline — no `##` sections. Every entry is a top-level bullet in the grammar `- <h:mmam>: <content>` (12-hour lowercase time; the espanso trigger `:now` expands to the current-time prefix for hand-typed bullets). Machine-written bullets carry a type emoji immediately after the separator, outside any link — `📅` calendar event, `🔗` web/bookmark, `📄` PDF, `✏️` handwritten, `📝` other note, `📔` journal (untimed, pinned at the end) — e.g. `- 6:41am: 🔗 [noclip](x-devonthink-item://UUID)`, and every inserter places its bullet at the entry's chronological position. An event title not yet backed by a note renders as an italicized `dtnote://` create-on-click link (`*[Title](dtnote://…)*`); the italics disappear when the title link becomes an item link. Manual jots carry no emoji (`- 9:12am: reviewed the roadmap doc`): the emoji is the machine/manual discriminator — machine code never rewrites a line without one, so a hand-typed bullet must not start with one of the six type emoji. Machine sub-lines under a `📅` event bullet likewise open with a type token (`✏️`/`📝` attached-note links, `👤` person lines, `⚠️` warnings); manual sub-lines typed under an event are preserved verbatim across re-runs.

### create-daily-note.sh

Source: [`stow/devonthink/.local/bin/create-daily-note.sh`](../stow/devonthink/.local/bin/create-daily-note.sh). Stowed to `~/.local/bin/create-daily-note.sh`.

### launchd Plist

Template (tracked): [`stow/devonthink/Library/LaunchAgents/com.user.dt-daily-note.plist.template`](../stow/devonthink/Library/LaunchAgents/com.user.dt-daily-note.plist.template). The generated plist (gitignored) is rendered to the same directory by `scripts/build-launchd-plists.sh`, which substitutes `__HOME__`, and is then stowed to `~/Library/LaunchAgents/com.user.dt-daily-note.plist`.

### Installation

Both the script and the launchd job are installed as part of the standard bootstrap — there is nothing to copy by hand:

```bash
./scripts/setup.sh
```

That run will (when you opt into the DEVONthink pipeline at the prompt):

1. Run `scripts/build-launchd-plists.sh` to render `*.plist.template` → `*.plist` with `__HOME__` expanded.
2. Stow `devonthink` so `~/.local/bin/create-daily-note.sh` and `~/Library/LaunchAgents/com.user.dt-daily-note.plist` become symlinks back into the repo.
3. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.dt-daily-note.plist` to load the job (idempotent; "already loaded" is tolerated).

To re-render the plist after editing the template:

```bash
./scripts/build-launchd-plists.sh
launchctl kickstart -k "gui/$(id -u)/com.user.dt-daily-note"   # pick up the new plist
```

To unload:

```bash
launchctl bootout "gui/$(id -u)/com.user.dt-daily-note"
```

To test the script directly (writes today's daily note if missing, then exits):

```bash
~/.local/bin/create-daily-note.sh
```

### Backfilling Missed Dates

If the Mac was offline for several days you can backfill:

```bash
for d in 2026-01-18 2026-01-19 2026-01-20; do
  ~/.local/bin/create-daily-note.sh "$d"
done
```

### Notes

- **Idempotency** — The script checks for an existing note with the same filename before creating. Running it twice for the same date is harmless.
- **Sleep/wake** — `StartCalendarInterval` does *not* fire while the Mac is asleep, and it does not catch up on wake unless `WakeFromSleep` is set (which is unreliable in clamshell/S5 standby). The 05:00 schedule targets a ready note before the 6am workday. If the trigger is missed in standby, the morning brief's `get_or_create_daily` (which retries at 05:15/05:45/06:30/08:00) or the script's own no-arg backfill creates today's note on the next opportunity (idempotent).
- **DEVONthink must be running** — The AppleScript targets `application id "DNtp"`. DEVONthink does not need to be frontmost, but it must be launched. The `dt-watchdog` launchd job (fires every 5 minutes) keeps DT and Maestral running, so this is generally not something to worry about.
- **Cloud sync** — After creating a note, the script calls `synchronize database` to push it to DEVONthink's configured sync store. If sync fails for any reason (e.g., no network), the note is still created locally and will sync on the next automatic or manual sync cycle.
- **Logging** — Check `~/Library/Logs/dt-daily-note.log` for creation results and `/tmp/dt-daily-note.log` for any launchd-level stdout/stderr.

## Entity Layer (Lorebook Memory)

A person/place/event memory layer under `/20_ENTITIES`: Person records
accumulate dated, provenance-linked facts in a `## Biographical Log`; a ~05:15
launchd agent (`com.user.dt-morning-brief`) merges the day's calendar into
today's daily-note timeline — one timed `📅` bullet per event, with "who am I
about to meet" sub-lines from the Person records — and carries the digest data
(Reconnect sorted on `LastContact`, birthdays, review backlog, journal status,
On This Day) solely in the TRMNL snapshot ([docs/trmnl-brief.md](docs/trmnl-brief.md)); a
30-minute agent (`com.user.entity-filing`) extracts people-facts from meeting
notes, handwritten notes, and past daily notes, and files them — in suggest
mode by default: proposals land in `/20_ENTITIES/_Review`, and moving one into
`_Review/Approved` applies it on the next run. Notes documenting a distinct
occasion (trip, celebration, gathering) additionally propose an Event record
with date/place/attendees, and every filed fact auto-links the first mention
of any existing Person/Place/Event name or alias, so hand-authored records
accrue backlinks without any extra effort.

Division of labor: the LLM only converts messy text to structured JSON;
deterministic scripts do all matching and writing through a single JXA
gateway (`entity-dt-bridge.js`). Extraction is local-only (`TRANSPORT=local`:
Qwen3-VL-32B-Instruct-4bit via oMLX/MLX in seconds, never cloud — it waits out server
outages instead); recurring standup-class meetings are skipped
by title regex; the brief also bumps `LastContact` from yesterday's completed
calendar so the reconnect digest doesn't depend on jot discipline. Its identity
resolver ranks attendee email, full names, calendar-context history, and bare
title aliases. Unmatched attendees and Contacts-corroborated calendar-title
names become review-only Person proposals. Person and proposal groups
are excluded from DT's AI chat. Full design, config
(`~/.config/dt-pipeline/entities.conf`), operations, and failure modes:
[docs/entities.md](docs/entities.md).

## Live-only GUI state (fresh-machine checklist)

State the repo can't stow or seed — reproduce by hand (or with the noted one-liners) when standing up a new machine. Current values verified 2026-07-03.

- **Markdown stylesheet + JavaScript assignment** — the CSS/JS files are stowed, but the *selection* lives in DT's preferences. Restore with:

  ```bash
  defaults write com.devon-technologies.think MarkdownStyleSheet "$HOME/Library/Application Support/DEVONthink/StyleSheets/Readable-Universal.css"
  defaults write com.devon-technologies.think MarkdownJavaScript "$HOME/Library/Application Support/DEVONthink/StyleSheets/theme-toggle.js"
  ```

  (or pick both in Settings → Media. DT must be quit when writing defaults directly.)
- **WikiLinks settings** (Settings → WikiLinks): automatic WikiLinks on, Names & Aliases mode, square-bracket auto-update on. Defaults keys if scripting: `AutomaticWikiLinks=1`, `WikiLinkNamesAndAliases=1`, `WikiLinkMode=1`, `WikiLinkOptions=2`, `UpdateSquareBracketWikiLinksAutomatically=1`.
- **`10_DAILY` is excluded from AI chat** (`excludeFromChat=true`), so daily notes never enter LLM context. This is database-level state and syncs with the database; after a from-scratch rebuild re-apply with:

  ```bash
  osascript -e 'tell application id "DNtp" to set exclude from chat of (get record at "/10_DAILY" in database "Lorebook") to true'
  ```

- **`20_ENTITIES/People`, `20_ENTITIES/_Review`, `20_ENTITIES/_Review/Approved`, and `20_ENTITIES/_Facts` are excluded from AI chat** for the same reason, applied the same way — Person records are distilled dossiers and `_Facts` holds raw fact captures, both more sensitive than any single note. Entity-layer automation reads them via AppleScript/JXA and is unaffected; DT chat and the DT MCP server cannot.

- **AI engine configuration** (Settings → AI): provider + model selection; API keys live in the macOS Keychain and are never captured by the repo.
- **MCP server privacy** (Settings → AI): private-information **redaction is enabled** on the MCP server; re-toggle it on a fresh machine and confirm which databases are exposed before pointing any external AI client at them. Record/group AI exclusions (`/10_DAILY`, `20_ENTITIES/People`, `20_ENTITIES/_Review`, `20_ENTITIES/_Review/Approved`, `20_ENTITIES/_Facts`) are database-level state and sync on their own.
- **Keyboard Maestro macros** — the AppleScripts they run are tracked in [`../keyboard-maestro/`](../keyboard-maestro/); the macro wrappers (hotkey triggers → Execute AppleScript) sync via KM's own iCloud syncing (`~/Library/Mobile Documents/com~apple~CloudDocs/Keyboard Maestro/Keyboard Maestro Macros.kmsync`), so a fresh machine gets them by signing into iCloud and enabling KM sync.
- **Calendars access for osascript** — the morning brief reads EventKit from `/usr/bin/osascript`; the TCC grant can only be created interactively. Run `osascript -l JavaScript ~/.local/bin/calendar-events-json.js` once in a terminal and approve the prompt (or toggle osascript under System Settings → Privacy & Security → Calendars).
- **oMLX (entity-extraction server)** — install the `.dmg` from the omlx GitHub releases (the Homebrew formula does not build on macOS 27), complete the app's first-run setup so `omlx start` manages the server across reboots, download the MLX model into `~/.omlx/models/` (`uvx --from 'huggingface_hub[cli]' hf download mlx-community/Qwen3-VL-32B-Instruct-4bit --local-dir ~/.omlx/models/Qwen3-VL-32B-Instruct-4bit`), copy `auth.api_key` from `~/.omlx/settings.json` into `OMLX_API_KEY` in `~/.config/dt-pipeline/entities.conf` (chmod 600), and set the per-model idle TTL in `http://localhost:8000/admin` — the field is **seconds** (300 ≈ 5 min). oMLX is the only extraction transport; extraction simply waits when it is down.
- **Entity metadata display titles** — the entity fields were created by script, so DT shows their identifiers (`entitytype`, `lastcontact`, …) rather than CamelCase titles in the Info inspector. Cosmetic only; add display names in Settings → Data if it grates.
- **Work calendar in macOS Calendar** — the brief only sees calendars added to macOS Calendar. Add your company email account with Calendars enabled in Settings → Internet Accounts for work-meeting briefs.
- **Calendar identity contexts** — add `PERSONAL_CALENDARS` and
  `WORK_CALENDARS` to `~/.config/dt-pipeline/entities.conf`. Each value is a
  comma-separated list of calendar titles, EventKit calendar identifiers, or
  source identifiers. Keep the values machine-local because calendar and account
  names are personal data.

## Database Backup & Recovery

Nothing in this repo backs up `~/Databases/Lorebook.dtBase2` — the repo rebuilds the *machinery* (scripts, agents, seeded rules), not the data. The database survives via two independent channels:

1. **CloudKit sync** — continuous, and the recovery path for a single-machine loss. Script-driven sync also runs after each daily-note creation.
2. **Time Machine** — the package is included in the hourly backup (verified 2026-07-03; local destination `MacBookBackup`). Caveat: TM snapshots the package while DT may be mid-write, so a restored copy should get **Tools → Verify & Repair** before trusting it. For a consistency-guaranteed archive (e.g. before risky bulk operations), use **File → Export → Database Archive**, which verifies and zips the closed database.

A *sync-store* loss plus a dead machine is the only scenario with no automated answer; the Time Machine copy is the fallback there.

## Integrations

- [GitHub Stars Integration](docs/github-stars.md) — automated bookmark import for starred repos
- [Summarize Skill](docs/summarize.md) — on-demand content summarization via Claude Code
- [Entity Layer](docs/entities.md) — person/place/event memory: morning briefings, reconnect digests, AI fact filing
- [Runbook](docs/runbook.md) — recovery procedures organized by symptom
