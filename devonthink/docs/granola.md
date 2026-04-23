# Granola Integration

[Granola](https://granola.ai) records and transcribes virtual meetings, generating AI-powered meeting notes. The `import-granola.py` script fetches enhanced notes from Granola's API, reads meeting metadata from the local cache, and imports the result into DEVONthink's pipeline — flowing through the same enrichment, action-item extraction, and daily-note linking as every other document.

## How It Works

```
Granola app → local cache (meeting metadata, transcripts)
                │
                │    Granola API (enhanced AI-generated notes)
                │        │
                ▼        ▼
         import-granola.py (launchd, every 30 min)
                    │
                    ▼
          DEVONthink Lorebook / 00_INBOX
         (NeedsProcessing=1, NameLocked=1,
          EventDate + GranolaID pre-set)
                    │
                    ▼
          Standard pipeline: Native Text Bypass → AI Enrichment
          → Post-Enrich & Archive → Wiki Export
```

For each meeting in the Granola cache that hasn't been imported yet, the script:

1. **Fetches enhanced notes** from the Granola API (`get-document-panels`), which returns ProseMirror JSON. The script converts this to clean markdown. Falls back to the cache's `notes_markdown` field, then to a speaker-attributed transcript if neither is available.
2. **Imports** the document into DEVONthink via AppleScript with pre-set metadata:
   - **GranolaID** — the Granola meeting UUID (idempotency key)
   - **EventDate** — from the Google Calendar event start time
   - **DocumentType** — "Meeting Notes"
   - **NameLocked=1** — preserves Granola's title through AI enrichment
   - **GranolaParticipants** — comma-separated attendee names
3. Sets `NeedsProcessing=1` so the document enters the standard pipeline. AI enrichment still runs to generate tags and a summary.

## Authentication

The script reads Granola's WorkOS access token from `~/Library/Application Support/Granola/supabase.json`. If the token has expired (checked via the JWT `exp` claim), the script launches Granola automatically and waits up to 30 seconds for it to refresh the token. If the token can't be obtained, the script falls back to the local cache's `notes_markdown` field (which may be empty in newer Granola versions that store notes server-side).

## Meeting ↔ Handwritten Note Linking

This happens automatically through the existing daily notes mechanism. When a Granola meeting and a handwritten Boox note share the same `EventDate`, both get wikilinked on the same daily note in `10_DAILY`. No additional configuration is needed.

## Running

```bash
# Manual import
python3 ~/.local/bin/import-granola.py

# Preview without importing
python3 ~/.local/bin/import-granola.py --dry-run

# Re-import a specific meeting (by Granola UUID)
python3 ~/.local/bin/import-granola.py --force <granola-id>
```

## Installation

```bash
# 1. Create GranolaID (Text) and GranolaParticipants (Multi-line Text)
#    in DEVONthink → Settings → Data → Custom Metadata

# 2. Restow to install the script and launchd plist
cd ~/.dotfiles/stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" devonthink

# 3. Load the launchd job (runs every 30 minutes)
launchctl load ~/Library/LaunchAgents/com.user.granola-import.plist

# 4. (Optional) Run immediately
python3 ~/.local/bin/import-granola.py
```

To unload: `launchctl unload ~/Library/LaunchAgents/com.user.granola-import.plist`

## State

- **Imported IDs:** `~/.local/state/devonthink/granola-imported.json` — tracks which Granola meeting UUIDs have been imported. Delete this file to re-import all meetings. (Auto-migrated from the old `~/.granola-dt-imported.json` location on first run.)
- **Logs:** `~/Library/Logs/granola-import.log` (script) and `/tmp/granola-import.log` (launchd stdout/stderr).

## Notes

- **Granola must be installed** — the script reads from Granola's local cache at `~/Library/Application Support/Granola/` and authenticates to `api.granola.ai` using the locally stored token.
- **DEVONthink must be running** — the script uses AppleScript to create records.
- **Token refresh** — if the auth token is expired, the script launches Granola to refresh it. Granola does not need to be running otherwise.
- **Cache format** — auto-detects v3 through v6 cache formats for meeting metadata and transcripts.
- **Idempotency** — meetings are tracked by UUID in the state file. Running the script multiple times is safe.
- **No external dependencies** — uses only Python 3 standard library.
