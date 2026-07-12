# DEVONthink Pipeline Runbook

Recovery organized by symptom. Everything here assumes you are on the
**driver** Mac (see the README's
[Multi-Mac Topology](../README.md#multi-mac-topology-driver--follower)); a
follower deliberately runs almost none of this. Deeper explanations live in
the linked docs — this file is the fast path from "something's wrong" to a
command.

## Where to look first

```bash
# Central pipeline log — every component writes here; grep by record UUID to
# trace one document's whole journey.
tail -f ~/Library/Logs/devonthink-pipeline.log
grep -E ' (WARN|ERROR) ' ~/Library/Logs/devonthink-pipeline.log

# Watchdog + per-importer logs
tail ~/Library/Logs/dt-watchdog.log
tail ~/Library/Logs/granola-import.log
tail ~/Library/Logs/github-stars-import.log
tail ~/Library/Logs/dt-daily-note.log

# What launchd currently has loaded
launchctl list | grep com.user.
```

Machine-local state lives under `~/.local/state/devonthink/`; database archives
under `~/Backups/DEVONthink/`; the role marker at `~/.config/dt-pipeline/role`.

`dt-watchdog` (every 5 min) is what turns silent failures into macOS
notifications, so most problems announce themselves. When one does, find the
matching symptom below.

## Stuck inbox record

A document sits in `00_INBOX` and never reaches `99_ARCHIVE`.

```bash
# Trace it — every rule logs against the UUID.
grep 'uuid=<UUID>' ~/Library/Logs/devonthink-pipeline.log
```

Check the flag ladder in DT's Info inspector; the rules advance it in order
`NeedsProcessing → Recognized → Commented → AIEnriched → archived`
(see the README smart-rule sections):

- **Never primed** (`NeedsProcessing` empty) — the `Prime`/`Sweep` rules set it
  on the next Every-Minute tick; if it never fires the record may be a jot
  (`IsJot`, or a `Jot`-prefixed name), which is handled elsewhere.
- **Enrichment wedged** — `EnrichStartedAt` enforces a 5-minute timeout. To
  force a fresh LLM pass, run the **Prepare for Re-Enrichment** on-demand rule
  (clears enrichment state), or clear `EnrichInputHash` to defeat the input-hash
  cache.
- **Bookmark** — check `NeedsSingleFile` / `SkipSingleFile`; see
  [failed SingleFile captures](#failed-singlefile-capture) below.

## Missing or stale morning brief

The daily note has no `## Briefing` (or a section looks out of date).

The brief writes `## Briefing`, `## Reconnect`, `## Entity Review`, and
`## On This Day` as **marker-bounded upserts** — each scheduled run replaces
its own section against the latest note body (jots are never clobbered) and a
section removes itself when empty. Scheduled at ~05:15 with retries at
05:45 / 06:30 / 08:00.

```bash
# Preview without writing, then run for real.
~/.local/bin/dt-morning-brief.py --dry-run
~/.local/bin/dt-morning-brief.py

# Force the Monday Reconnect section any day.
~/.local/bin/dt-morning-brief.py --dry-run --weekly
```

If it's still empty: confirm today's daily note exists (a missing note blocks
the brief — see [daily note](#no-daily-note)); confirm this Mac is the driver;
confirm the Calendars grant for osascript
(`osascript -l JavaScript ~/.local/bin/calendar-events-json.js` once
interactively). Details: [entities.md](entities.md).

## No daily note

`10_DAILY` is missing today's note.

```bash
# Backfills from the last existing note through today (idempotent).
~/.local/bin/create-daily-note.sh

# Create one specific date.
~/.local/bin/create-daily-note.sh 2026-03-15
```

The 05:00 launchd job seeds it; a missed run (Mac asleep) self-heals on the
next no-arg run, and the morning brief / web-capture paths also create it on
demand. See the README "Daily Notes (Scheduled)" section.

## Dead or booted-out agent

`dt-watchdog` alerts that an agent is down, not loaded, or "loaded but silent."

```bash
launchctl list | grep com.user.          # what's actually loaded

# KeepAlive watchers (singlefile / boox): restart in place.
launchctl kickstart -k "gui/$(id -u)/com.user.singlefile-watcher"
launchctl kickstart -k "gui/$(id -u)/com.user.boox-import-watcher"

# Interval agents that were booted out: reload from the plist.
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.user.entity-filing.plist

# Boot an agent out (e.g. before reloading after a plist edit).
launchctl bootout "gui/$(id -u)/com.user.entity-filing"
```

The watchdog kickstarts the two KeepAlive watchers itself; it only *reports*
interval agents (daily-note, morning-brief, entity-filing, granola,
github-stars, database-archive) that are booted out or stale, because those
have no resident process to restart. After editing a plist template, re-render
with `scripts/build-launchd-plists.sh`, then `bootout` + `bootstrap`.

## Unreadable / corrupt state file

An importer or filing run aborts complaining a state file is unreadable or has
an unrecognized schema. **This is deliberate:** every state loader fails closed
rather than treat a damaged file as empty and re-import/re-propose everything.

State files (`~/.local/state/devonthink/`):

- `entity-filing-state.json` — entity filing
- `granola-imported.json` — Granola importer
- `github-stars-imported.json` — GitHub Stars importer

```bash
# Inspect it.
cat ~/.local/state/devonthink/<file>.json | jq . 2>&1 | head

# If unrepairable, remove it — each component rebuilds from DEVONthink:
#   entity filing  ← EntityFiled audit flag
#   Granola        ← GranolaID metadata
#   GitHub Stars   ← bookmark URLs
rm ~/.local/state/devonthink/<file>.json

# Rebuild explicitly (also happens automatically on the next run when the
# file is missing). No import happens — it only re-derives the cache.
~/.local/bin/entity-filing.py --rebuild-state
~/.local/bin/import-granola.py --rebuild-state
python3 ~/.local/bin/import-github-stars.py --rebuild-state
```

## Duplicate imports

Two records for the same meeting or repo.

Creation is now idempotent **against the database**, so this is largely
self-healing: Granola adopts an existing record by `GranolaID` and GitHub Stars
by canonical URL before creating anything, and both rebuild their local cache
from the database when it's missing. A lost or restored state file therefore no
longer floods the inbox.

```bash
# Re-derive the caches from what the database already holds.
~/.local/bin/import-granola.py --rebuild-state
python3 ~/.local/bin/import-github-stars.py --rebuild-state
```

Duplicates created *before* this behavior existed won't disappear on their own —
merge or trash them by hand (DT's Filter Duplicates / a URL search helps).

## Granola schema drift

Granola shipped an update and imports break or go empty.

Signals, in the logs:

- `~/Library/Logs/granola-import.log` — malformed-panel `WARN`s, or a
  version-transition line emitted when `granola-version.json` changes (useful
  to pin a regression to a release).
- A `DocumentType=Pipeline Error` record in `00_INBOX` (one per failure
  signature) carrying the traceback.

A no-content meeting is retried for a 3-day window before it's marked imported,
so a late-arriving panel isn't lost. Before touching the parser, read the
gitignored design notes — schema, key chain, and debug recipes:

```bash
less ~/.local/share/granola-import/NOTES.md

# Isolate a parse failure from a DEVONthink/AppleScript failure.
echo '{"imported_ids": [], "force_id": null}' \
  | ~/.local/bin/import-granola-parse.py | jq '.meetings | length'
```

Full detail: [granola.md](granola.md).

## Failed SingleFile capture

`dt-watchdog` alerts about an `.html` "stuck capture awaiting ingest," or a
bookmark never gets its snapshot.

Failed captures **stay** in `~/Downloads/SingleFile/` by design (deleting would
destroy the only copy). `NeedsSingleFile` is cleared only after the whole
bookmark + HTML + markdown triad commits, so a retry repairs a partial triad by
URL rather than duplicating it.

```bash
ls -la ~/Downloads/SingleFile/            # what's stuck

# Re-ingest one staged file.
~/.local/bin/ingest-singlefile-html.py ~/Downloads/SingleFile/<file>.html

# Re-drain the queue of NeedsSingleFile=1 bookmarks (drives the browser).
~/.local/bin/capture-bookmarks-batch.py

# Force a fresh capture of one bookmark, bypassing the skip list.
~/.local/bin/capture-bookmarks-batch.py --uuid <bookmark-UUID>
```

Post-compression HTML over 25 MB is flagged `SingleFileTooLarge=1` and skipped
rather than retried forever. See the README "SingleFile Ingestion Pipeline."

## Parked entity source

A note stopped producing proposals. After `MAX_ATTEMPTS` (5) failed extractions
a source is **parked**; the morning brief's `## Entity Review` section lists
parked sources so they stay visible.

A parked source retries automatically when its content changes, or on demand:

```bash
# Re-extract one source (bypasses the park and the skip-title list).
~/.local/bin/entity-filing.py --force <source-UUID>

# See what filing would do, no writes.
~/.local/bin/entity-filing.py --dry-run
```

Fix the underlying note first if the extraction kept failing on bad input.
Detail: [entities.md](entities.md).

## Journal page parked or missing

A day's journal entry never appeared in `/15_JOURNAL`, or the log shows
`parked <notebook> page N`. Parked pages never retry on their own — same
input, same misread.

```bash
# Which pages are parked, and why (weekday mismatch, no date, out of order).
~/.local/bin/journal-process.py --status

# Re-queue parked pages and run now (bypasses battery/idle gates).
~/.local/bin/journal-process.py --force

# Nothing staged at all? The notebook must be named "<year> Journal" on the
# device — unnamed exports are deleted by the watcher, never staged.
rg 'journal-(import|process)' ~/Library/Logs/devonthink-pipeline.log | tail
```

A weekday-mismatch park usually means the handwritten date really is
ambiguous; fix the page on the device and re-export. Detail:
[journal.md](journal.md).

## Driver / follower mistake

Two Macs mutating the synced database (accidental co-driver), or a demoted Mac
still running ingest agents.

```bash
cat ~/.config/dt-pipeline/role            # driver | follower
launchctl list | grep com.user.           # a follower shows ONLY dt-watchdog
```

A driver shows all nine `com.user.*` agents; a follower shows only
`com.user.dt-watchdog`. To demote a Mac, set the role and re-run setup (it boots
out the driver-only agents for you):

```bash
echo follower > ~/.config/dt-pipeline/role
./scripts/setup.sh
```

The full promote/demote procedure and the manual bootout loop are in the
README's [Multi-Mac Topology](../README.md#multi-mac-topology-driver--follower).

## Database restore

The Lorebook database is lost or corrupt, and CloudKit + Time Machine aren't
enough (e.g. sync-store loss plus a dead machine).

A weekly **verified** archive runs on the driver (`dt-database-archive`,
verify → compress → CRC-check → keep the newest 4):

```bash
ls -lt ~/Backups/DEVONthink/              # Lorebook-YYYY-MM-DD.dtBase2.zip

# Force an archive right now (bypasses the battery/role/cadence gates).
~/.local/bin/dt-database-archive.sh --force
```

To restore: quit DEVONthink, unzip the newest archive, open the resulting
`.dtBase2` in DT, and run **Tools → Verify & Repair** before trusting it. Then
rebuild every machine-local cache from the restored database so imports don't
duplicate:

```bash
~/.local/bin/entity-filing.py --rebuild-state
~/.local/bin/import-granola.py --rebuild-state
python3 ~/.local/bin/import-github-stars.py --rebuild-state
```

Background on the backup layers: the README "Database Backup & Recovery"
section.
