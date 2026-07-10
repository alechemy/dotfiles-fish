# Granola Integration

[Granola](https://granola.ai) is a meeting transcription and notes app. `import-granola.py` runs under launchd every 30 minutes, reads Granola's local store directly, converts each new meeting's notes to markdown, and imports it into DEVONthink with pre-set metadata so the document drops into the standard pipeline.

The implementation is **local-only**. It decrypts Granola's on-disk SQLCipher store rather than calling `api.granola.ai`. Granola's documented public surface is its companion CLI, which is gated behind a server-side feature flag we don't currently have access to. The local-store path is the only viable option without a maintenance-liability dependency on undocumented HTTP endpoints.

The encryption recipe, schema, and debug procedures live in `~/.local/share/granola-import/NOTES.md` (gitignored, kept out of this repo so the recipe isn't published). Read that file before modifying the import code.

## How It Works

```
Granola app
  ├─ ~/Library/Application Support/Granola/IndexedDB/.../app_ui_0.indexeddb.leveldb
  │    (KEK + wrapped DEK)
  └─ ~/Library/Application Support/Granola/granola.db
       (SQLCipher v4: documents, document_panels)
                    │
                    ▼
        import-granola-parse.py     (uv + PEP 723 deps:
        decrypt, query, render       cryptography,
                    │                sqlcipher3-wheels,
                    │                ccl-chromium-reader)
                    │
                    │  JSON over stdin/stdout
                    ▼
        import-granola.py           (/usr/bin/python3, stdlib only:
        osascript → DEVONthink       owns state, logging,
                    │                AppleEvents, deferral)
                    ▼
        DEVONthink Lorebook / 00_INBOX
        (NeedsProcessing=1, NameLocked=1,
         EventDate + GranolaID + GranolaParticipants pre-set)
                    │
                    ▼
        Standard pipeline: Native Text Bypass → AI Enrichment
        → Post-Enrich & Archive
```

For each meeting in the local store that hasn't been imported yet, the parser:

1. Snapshots the IndexedDB folder and SQLCipher database files to a temp dir. Granola holds the originals open at runtime, so working from copies avoids fighting the live writer.
2. Recovers the SQLCipher key from IndexedDB and opens `granola.db` read-only.
3. Pulls the meeting row plus its `document_panels` (AI-enhanced note panels stored as ProseMirror JSON).
4. Renders ProseMirror to markdown, falling back to the raw `notes_markdown` field if no enhanced panels exist.
5. Emits one JSON object per meeting (id, title, event_date, participants, markdown, source) on stdout.

The sender then imports each meeting via osascript with these custom metadata fields:

| Field                 | Value                                                          |
|-----------------------|----------------------------------------------------------------|
| `GranolaID`           | Meeting UUID. Idempotency key.                                 |
| `EventDate`           | `yyyy-mm-dd` from the linked Google Calendar event start time. |
| `DocumentType`        | `Meeting Notes`.                                               |
| `GranolaParticipants` | Comma-separated attendee names.                                |
| `NeedsProcessing`     | `1`. Enters the pipeline.                                      |
| `NameLocked`          | `1`. Preserves Granola's title through AI enrichment.          |
| `Recognized`          | `1`. Skips OCR — markdown is already text.                     |
| `Commented`           | `1`. Skips comment mirroring for the same reason.              |

The record's name is `{event_date} {title}`. AI enrichment still runs to generate tags and a summary.

## Two-script architecture (TCC stability)

The pipeline is split across two scripts so the AppleEvent-sending process is Apple-signed:

- **`~/.local/bin/import-granola.py`** — entry point. Shebang `#!/usr/bin/python3` (Apple-signed interpreter at a stable path, for a stable TCC identity — this script owns the AppleEvents). Stdlib only. Owns logging, state file, Granola version detection, AppleScript blocks, all `osascript` calls, deferral logic, and failure reporting. The launchd plist invokes this directly.
- **`~/.local/bin/import-granola-parse.py`** — internal helper. Shebang `#!/usr/bin/env -S uv run --script` with PEP 723 inline deps (`cryptography`, `sqlcipher3-wheels`, `ccl-chromium-reader`). The sender invokes it as a subprocess and exchanges JSON over stdin/stdout. The parser never sends AppleEvents.

The launchd plist's `ProgramArguments[0]` must be `/usr/bin/python3`, never uv. macOS TCC keys AppleEvents grants to the sending process's code identity. Apple-signed binaries like `/usr/bin/python3` get a stable Designated Requirement that survives version updates. Adhoc-signed binaries (uv, Homebrew Python, mise Python) fall back to path + CDHash, both of which rotate on every upgrade. Before this split, `brew upgrade uv` (weekly) re-prompted "uv wants to control data in other apps" via launchd, blocking the pipeline whenever the user wasn't there to click through. With the split, uv is invisible to TCC because the parser doesn't drive AppleEvents. The entry binary stays Apple-signed and TCC stays quiet.

The same pattern applies to any future launchd-driven script that needs to send AppleEvents. See the project memory `feedback_launchd_appleevents_split.md`.

## Deferral Logic

Granola generates AI-enhanced panel notes after a meeting ends. This usually finishes within a few minutes, occasionally longer. The script handles three cases per meeting:

- **Has notes (>50 chars in `notes_markdown` or any panel):** import normally.
- **No notes, created <60 minutes ago:** defer. The next 30-minute tick will retry.
- **No notes, created ≥60 minutes ago:** mark as imported with no DT record. Granola may have abandoned panel generation for that meeting (e.g., the user dismissed it before content arrived). We won't try again.

`--force <id>` overrides the imported-IDs check but **not** the no-notes deferral. An empty meeting can't be made non-empty by re-running.

## Meeting ↔ Handwritten Note Linking

When a Granola meeting and a handwritten Boox note share the same `EventDate`, both get wikilinked on the same daily note in `10_DAILY`. This happens automatically through Post-Enrich & Archive's daily-note linking. No Granola-specific configuration is needed.

## Running

```bash
# Manual run (real import)
~/.local/bin/import-granola.py

# Preview without importing
~/.local/bin/import-granola.py --dry-run

# Re-import a specific meeting (Granola UUID)
~/.local/bin/import-granola.py --force <granola-id>

# Run the parser standalone — useful when isolating a parse failure
# from a DEVONthink/AppleScript failure
echo '{"imported_ids": [], "force_id": null}' \
  | ~/.local/bin/import-granola-parse.py | jq '.meetings | length'
```

## Installation

Most of the pipeline is tracked and installs through the normal bootstrap — **only the parser is private**:

- `stow/devonthink/.local/bin/import-granola.py` — **tracked.** The sender holds no decryption detail (it owns state, logging, and AppleEvents), so it lives in the repo and `setup.sh` stows it like any other file.
- `stow/devonthink/Library/LaunchAgents/com.user.granola-import.plist.template` — **tracked.** `setup.sh` renders it (`__HOME__` expanded) and, on the driver, loads the agent.
- `stow/devonthink/.local/bin/import-granola-parse.py` — **gitignored.** This is the only sensitive file: its PEP 723 metadata pins a specific `ccl-chromium-reader` commit and, together with `NOTES.md`, documents enough of Granola's encryption to be worth keeping out of a public repo. It must be restored from a trusted backup.

So a clean rebuild on a fresh machine is:

```bash
# 1. Run the normal bootstrap. This seeds the GranolaID + GranolaParticipants
#    custom metadata fields (they ship in CustomMetaData.plist — see the README's
#    "Seeding and reconciliation"), stows the tracked sender, renders the plist
#    template, and — on the driver — loads the launchd agent.
./scripts/setup.sh

# 2. Restore the one private file from backup and make it executable. Its
#    location and the full bootstrap checklist are in
#    ~/.local/share/granola-import/NOTES.md.
cp <backup>/import-granola-parse.py ~/.local/bin/import-granola-parse.py
chmod +x ~/.local/bin/import-granola-parse.py

# 3. Confirm with a dry-run before letting the schedule handle it.
~/.local/bin/import-granola.py --dry-run

# 4. (Optional) Trigger immediately instead of waiting for the next 30-minute tick.
launchctl kickstart -k "gui/$(id -u)/com.user.granola-import"
```

To unload: `launchctl bootout "gui/$(id -u)/com.user.granola-import"`.

To reload after editing the plist template: re-render with the build script, then `bootout` followed by `bootstrap`.

## State and Logs

State files live in `~/.local/state/devonthink/`:

- `granola-imported.json` — sorted list of UUIDs already imported (or marked as no-content). It's a performance cache, not the idempotency boundary: import first checks DEVONthink for an existing record with the same `GranolaID` and **adopts** it instead of creating a duplicate, so deleting this file no longer floods the database — the next run re-derives it (and adopts anything already present). To re-derive it explicitly without importing, run `--rebuild-state`; it also rebuilds automatically when the file is missing. For a deliberate selective re-import use `--force <id>`.
- `granola-version.json` — last-seen Granola app version. The script logs a one-liner whenever this transitions, useful for correlating regressions with specific releases.
- `granola-failure.json` — signature of the last reported failure, used to dedupe (see Failure Reporting below). Cleared automatically on the next successful run.

Logs:

- `~/Library/Logs/granola-import.log` — script's own log calls.
- `/tmp/granola-import.log` — launchd stdout/stderr.

## Failure Reporting

Unhandled exceptions post a record to DEVONthink's `00_INBOX` with the traceback so silent breakage surfaces in the same place imports normally land. The record is dedup'd by `(exception class, last frame)` signature: a persistent failure spawns one record on the first occurrence and stays quiet until either the failure mode changes or the run succeeds. Records are flagged `DocumentType=Pipeline Error` and pre-flagged `NameLocked=Recognized=Commented=1` so the AI pipeline doesn't touch them.

This means a broken Granola version (schema drift, key-derivation regression, etc.) becomes one inbox-visible report rather than a steady drip of duplicates every 30 minutes.

## Notes

- **No API calls.** The pipeline reads `~/Library/Application Support/Granola/IndexedDB/...` and `granola.db` directly. Granola does not need to be running.
- **DEVONthink must be running.** The sender uses `osascript` to create records. If DT is quit, each meeting's import fails, is logged as `FAILED:`, and is retried on the next run (a meeting only enters the state file on success); only unhandled importer exceptions become pipeline-error inbox records per the dedup logic above.
- **uv must be installed.** The parser shebang is `#!/usr/bin/env -S uv run --script`. uv resolves and caches the PEP 723 deps on first run. uv is in `Brewfile`.
- **Idempotency.** Meetings are tracked by UUID in the state file. Running the script multiple times is safe, as is kickstarting the LaunchAgent.
- **Brittle points** (in approximate order of likelihood to break): Granola's IndexedDB origin path, SQLCipher schema, IDB key entry names, cipher params. `~/.local/share/granola-import/NOTES.md` enumerates each case and how to debug it.

## See Also

- `~/.local/share/granola-import/NOTES.md` (gitignored) — encryption key chain, SQLCipher schema reference, ProseMirror conversion details, debug recipes (manual decryption, IDB inspection, SQLCipher CLI), brittle points, and instructions for bumping the `ccl-chromium-reader` git pin. Read this before modifying the import code.
- Project memory `feedback_launchd_appleevents_split.md` — the parse/send split pattern; apply to any new launchd-driven script that drives AppleEvents.
- Project memory `project_granola_local_only.md` — the rationale for local-store decryption over the API or the companion CLI.
