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

The daily note has no `📅` event bullets (or one looks out of date).

The brief writes the day's events into the note's flat timeline as timed
`📅` bullets through the bridge's `merge_timeline` — each scheduled run
reconciles the machine event bullets against the latest calendar in one
read-modify-write (manual bullets and manual sub-lines are never clobbered;
a stale event bullet is removed only when it carries no manual sub-lines,
and a calendar-fetch failure skips the merge rather than emptying the day).
Reconnect, birthdays, entity review, journal status, and On This Day never
render into the note — they ride the TRMNL snapshot. Scheduled at ~05:15
with retries at 05:45 / 06:30 / 08:00.

```bash
# Preview without writing, then run for real.
~/.local/bin/dt-morning-brief.py --dry-run
~/.local/bin/dt-morning-brief.py
```

If it's still empty: confirm this Mac is the driver; confirm the Calendars
grant for osascript (`osascript -l JavaScript ~/.local/bin/calendar-events-json.js`
once interactively) — the brief creates today's daily note itself
(`get_or_create_daily`) if one doesn't exist yet, so a missing note isn't the
blocker. Details: [entities.md](entities.md).

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
interval agents (daily-note, morning-brief, entity-filing, boox-process,
github-stars, database-archive) that are booted out or stale,
because those have no resident process to restart. After editing a plist template, re-render
with `scripts/build-launchd-plists.sh`, then `bootout` + `bootstrap`.

## Unreadable / corrupt state file

An importer or filing run aborts complaining a state file is unreadable or has
an unrecognized schema. **This is deliberate:** every state loader fails closed
rather than treat a damaged file as empty and re-import/re-propose everything.

State files (`~/.local/state/devonthink/`):

- `entity-filing-state.json` — entity filing
- `github-stars-imported.json` — GitHub Stars importer

```bash
# Inspect it.
cat ~/.local/state/devonthink/<file>.json | jq . 2>&1 | head

# If unrepairable, remove it — each component rebuilds from DEVONthink:
#   entity filing  ← EntityFiled audit flag
#   GitHub Stars   ← bookmark URLs
rm ~/.local/state/devonthink/<file>.json

# Rebuild explicitly (also happens automatically on the next run when the
# file is missing). No import happens — it only re-derives the cache.
~/.local/bin/entity-filing.py --rebuild-state
python3 ~/.local/bin/import-github-stars.py --rebuild-state
```

## Duplicate imports

Two records for the same meeting or repo.

Creation is now idempotent **against the database**, so this is largely
self-healing: GitHub Stars adopts an existing record by canonical URL before
creating anything, and rebuilds its local cache from the database when it's
missing. A lost or restored state file therefore no longer floods the inbox.

```bash
# Re-derive the cache from what the database already holds.
python3 ~/.local/bin/import-github-stars.py --rebuild-state
```

Duplicates created *before* this behavior existed won't disappear on their own —
merge or trash them by hand (DT's Filter Duplicates / a URL search helps).

## Granola import (retired)

The local-store importer is retired, not just disabled. Granola 7.417.0 moved
the SQLCipher key into the macOS Keychain under the app's Team-ID access
group, so the local-decryption approach no longer works and can't be
reproduced outside the signed app. No launchd agent is registered for it and
no plist template exists; the entry-point script (`import-granola.py`)
remains as a skeleton for a rewrite against Granola's public API — see its
docstring.

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
a source is **parked**; the morning brief's entity-review digest (on the
TRMNL snapshot) carries a parked-source count so they stay visible.

A parked source retries automatically when its content changes, or on demand:

```bash
# Re-extract one source (bypasses the park and the skip-title list).
~/.local/bin/entity-filing.py --force <source-UUID>

# See what filing would do, no writes.
~/.local/bin/entity-filing.py --dry-run
```

Fix the underlying note first if the extraction kept failing on bad input.
Detail: [entities.md](entities.md).

## Boox page parked, or a note/journal entry missing

A handwritten note never appeared in DT, a day's journal entry never
appeared in `/15_JOURNAL`, or the log shows `parked <notebook> page N`.
Parked pages never retry on their own — same input, same misread.

```bash
# Which pages are parked, and why (OCR failure; for the journal also
# weekday mismatch, no date, out of order). Regular notebooks file only
# once every page has transcribed.
~/.local/bin/boox-process.py --status

# Re-queue parked pages and run now (bypasses battery/memory-pressure gates).
~/.local/bin/boox-process.py --force

# Nothing staged at all? The notebook must be named on the device —
# unnamed Notebook-<n> exports are deleted by the watcher, never staged.
rg 'boox-(stage|process)' ~/Library/Logs/devonthink-pipeline.log | tail
```

Do **not** reset `Recognized`/`Commented` on a handwritten record to
re-process it — that re-arms the vestigial cloud OCR rules; re-export
from the device or use `--force` instead. A weekday-mismatch park
usually means the handwritten date really is ambiguous; fix the page on
the device and re-export. Detail: [boox-local.md](boox-local.md).

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

Use this section to plan recovery or an isolated restore drill. Opening a
restored database on the normal driver is not a harmless inspection: sync,
smart rules, and launch agents can act on it. A renamed package can retain
the same database and record UUIDs. Preserve the original database, archives,
and machine-local state before making recovery changes.

### Establish the available recovery point

The [native archive job](../README.md#native-archive-job) attempts a backup at
03:30 daily but normally creates one only after seven days have elapsed since
its recorded success. It requires the driver/power gates to pass, DEVONthink
to be running, and Lorebook to be open. It verifies the database, compresses
it, checks that the ZIP is nonempty and readable, then records success and
keeps the four most recently modified matching archives.

Under an approved inspection scope:

1. Inspect archive count, dated filenames, sizes, and modification times in
   `~/Backups/DEVONthink/`. Select an explicit archive; do not assume the
   newest file is complete or predates the incident.
2. Compare it with `~/.local/state/devonthink/dt-database-archive.last-success`
   and the archive component's success/failure entries in the local pipeline
   log. Report only sanitized dates, counts, and outcomes. A loaded launch
   agent, zero exit status, or success marker alone does not establish that
   the archive still exists or is usable. Normal skips can exit successfully.
3. Test the selected ZIP without extracting or printing its member paths.
   Replace the placeholder date in this example first:

   ```bash
   archive="$HOME/Backups/DEVONthink/Lorebook-YYYY-MM-DD.dtBase2.zip"
   if [ -s "$archive" ] && unzip -tq "$archive" >/dev/null 2>&1; then
       printf '%s\n' 'Archive integrity check passed.'
   else
       printf '%s\n' 'Archive is missing, empty, or failed its integrity check.'
   fi
   ```

4. Confirm that a separate backup destination retains a usable copy. Local
   archives share the live database's disk-failure risk. Record archive age,
   integrity outcome, and backup coverage separately from restore results.

Creating a new archive is a separate operation, not part of read-only
inspection. On the intended driver, with Lorebook open and explicit approval
for the work and rotation, use:

```bash
PIPELINE_MANUAL=1 ~/.local/bin/dt-database-archive.sh --force
```

This bypasses power, role, and cadence gates. It still verifies the database
and tests the ZIP. A second forced run on the same day uses the same dated
filename; it does not create a separately named recovery point. Preserve any
archive needed for investigation before forcing a new one. A fresh archive
of the current database also does not replace a known-good pre-incident copy.

### Isolate a restore drill before opening the copy

Agree on the source archive, destination, isolation method, expected checks,
and cleanup scope before extraction. Prefer a separate offline macOS user
or VM with no production database, synced account, sync-store credentials,
or pipeline installation. Ensure networking is unavailable before opening
the restored copy. Do not import production application settings or smart
rules into that environment, and verify that no automation is configured to
act on the copy. Arrange any required application access before the drill.

A follower role alone is insufficient isolation. The watchdog still runs on
followers, and sync remains available. Quitting DEVONthink on the normal
account is likewise insufficient because the watchdog can reopen it. If an
isolated environment is unavailable, stop and agree on another procedure
rather than opening a duplicate in the production session.

In the approved environment:

1. Copy the archive and extract it to the agreed local test directory, never
   over the live database or inside a cloud-synced folder. Keep the source
   archive unchanged.
2. Open only the test copy. Inspect DEVONthink's opening verification, then
   use **File → Verify & Repair Database** to check consistency. Any repair
   applies only to the disposable copy; record whether repair was required.
3. Check expected record identity, internal links, and representative
   attachment readability in the UI. A same-UUID database requires that the
   production copy remain unavailable during link checks. Account for any
   indexed files whose contents live outside the database package.
4. Record sanitized outcomes and the tested app/macOS versions. Excluded
   personal content stays out of agent output; the user confirms those checks
   in the UI. ZIP integrity alone does not complete this drill.
5. Close the test database and remove only the approved test artifacts.
   Never enable production sync or run pipeline state rebuilds for the copy.

An encrypted or revision-proof archive also needs its original key and a
compatible macOS version. Check all intended recovery/follower machines
before adopting 4.4's APFS encryption format. This drill does not authorize
converting the production database.

### Promote a recovery only under a separate plan

A successful drill is not authorization to replace Lorebook. Actual recovery
must define which copy is authoritative, where the pre-recovery files and
state are preserved, how production automation is stopped, and how sync and
followers will rejoin without reintroducing unwanted changes. Resolve the
sync-store procedure against current vendor guidance before reconnecting.

Reconcile machine-local state only after selecting the authoritative
production database and before resuming automation. The available rebuild
commands are not a universal rollback:

- `entity-filing.py --rebuild-state` adds missing processed entries from
  `EntityFiled`; it does not discard newer existing state.
- `import-github-stars.py --rebuild-state` unions database-derived IDs with
  existing state and retains its retry queue. It does not remove IDs that
  exist only in a newer cache.
- `boox-process.py --rebuild-state` reseeds journal information, not every
  notebook page cache or staging/dedup marker. Review the
  [Boox recovery constraints](boox-local.md#debugging) before resuming it.

Preserve and reconcile each affected cache and staging area against the
recovery point. Do not blindly delete state or run these commands against a
test database. Authorized manual pipeline maintenance sets `PIPELINE_MANUAL=1`
and keeps excluded content out of agent output.

The [4.4 plan](devonthink-4.4-plan.md#dt44-02-correct-recovery-documentation-and-verify-archive-recovery)
tracks archive integrity and isolated restore validation separately. The
[README](../README.md#database-backup--recovery) describes the backup layers.
