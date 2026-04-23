# GitHub Stars Integration

Starred GitHub repositories are automatically imported into DEVONthink as bookmarks. The existing [Extract: Web Content](../README.md#extract-web-content) smart rule then downloads the README as readable markdown and archives an HTML snapshot of the repo page, and the standard pipeline handles AI enrichment (tags, summary, etc.).

## How It Works

```
GitHub API (/user/starred)
         │
         ▼
  import-github-stars.py (launchd, every 30 min)
         │
         ▼
  DEVONthink Lorebook / 00_INBOX
  (Bookmark record, NeedsProcessing=1)
         │
         ▼
  Extract: Web Content → sets NeedsSingleFile=1, fast-tracks
         │
         ▼
  Capture: SingleFile Batch → browser-based HTML capture
         │
         ▼
  Standard pipeline: AI Enrichment → Post-Enrich & Archive → Wiki Export
```

For each newly starred repo, the script:

1. **Polls** the GitHub API via `gh` CLI, fetching stars newest-first with `starred_at` timestamps. In normal mode, stops at the first already-imported repo. On first run (no state file), only imports stars from the last 24 hours to avoid flooding the inbox — use `--backfill` to import the full history.
2. **Creates a bookmark** record in DEVONthink with the repo URL and `owner/repo` as the name.
3. Sets `NeedsProcessing=1` so the bookmark enters the standard pipeline. Extract: Web Content sets `NeedsSingleFile=1` and fast-tracks it. Capture: SingleFile Batch captures the page via browser, then Process: SingleFile Import extracts readable content for AI enrichment.

## Running

```bash
# Manual import (new stars only)
python3 ~/.local/bin/import-github-stars.py

# Preview without importing
python3 ~/.local/bin/import-github-stars.py --dry-run

# Import entire star history
python3 ~/.local/bin/import-github-stars.py --backfill

# Re-import a specific repo
python3 ~/.local/bin/import-github-stars.py --force owner/repo
```

## Installation

```bash
# 1. Restow to install the script and launchd plist
cd ~/.dotfiles/stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" devonthink

# 2. Ensure gh CLI is authenticated
gh auth status

# 3. Load the launchd job (runs every 30 minutes)
launchctl load ~/Library/LaunchAgents/com.user.github-stars-import.plist

# 4. (Optional) Run immediately
python3 ~/.local/bin/import-github-stars.py
```

To unload: `launchctl unload ~/Library/LaunchAgents/com.user.github-stars-import.plist`

## State

- **Imported repos:** `~/.local/state/devonthink/github-stars-imported.json` — tracks which `owner/repo` names have been imported. Delete this file to re-import all stars. (Auto-migrated from the old `~/.github-stars-dt-imported.json` location on first run.)
- **Logs:** `~/Library/Logs/github-stars-import.log` (script) and `/tmp/github-stars-import.log` (launchd stdout/stderr).

## Notes

- **`gh` CLI must be installed and authenticated** — the script calls `/opt/homebrew/bin/gh` (Homebrew install path). Run `gh auth login` if not yet configured.
- **DEVONthink must be running** — the script uses AppleScript to create bookmark records.
- **Idempotency** — repos are tracked by `full_name` in the state file. State is saved after each successful import, so a crash mid-run won't cause duplicates.
- **No external dependencies** — uses only Python 3 standard library and the `gh` CLI.
