# GitHub Stars Integration

Starred GitHub repositories are automatically imported into DEVONthink as bookmark records with the repo description in the Finder comment. Stars deliberately skip the SingleFile-capture path that normal bookmarks take: most GitHub repos have a README that the bookmark itself already links to, and capturing the rendered HTML of every starred repo would be a lot of duplicate noise. AI Enrichment still runs over the description so each star gets tags, a summary, and a normalized title before landing in `99_ARCHIVE`.

## How It Works

```
GitHub API (/user/starred)
         │
         ▼
  import-github-stars.py (launchd, every 30 min)
         │
         ▼
  DEVONthink Lorebook / 00_INBOX
  (Bookmark record, comment = repo description,
   NeedsProcessing=1, Recognized=1, Commented=1)
         │
         ▼
  Skips Extract: Web Content (Recognized+Commented already set)
         │
         ▼
  Enrich: AI Metadata → AIEnriched=1
         │
         ▼
  Post-Enrich & Archive → moves to 99_ARCHIVE, exports to Wiki
```

For each newly starred repo, the script:

1. **Polls** the GitHub API via `gh` CLI, fetching stars newest-first with `starred_at` timestamps. In normal mode, stops at the first already-imported repo. On first run (no state file), only imports stars from the last 24 hours to avoid flooding the inbox — use `--backfill` to import the full history.
2. **Creates a bookmark** record in DEVONthink with the repo URL and `owner/repo` as the name.
3. **Pre-flags `Recognized=1, Commented=1`** alongside `NeedsProcessing=1`. The first two flags make the bookmark skip `Extract: Web Content`, which is what would normally schedule a SingleFile capture and download the README as markdown. For stars, that's intentional churn we don't need.
4. **Stores the repo description** in the record's Finder comment. AI Enrichment reads the comment (not `plain text`, which is empty on a bookmark) when generating tags and a summary, so even without a SingleFile capture the star still gets meaningful metadata.

If you ever want to capture a specific starred repo as a full SingleFile snapshot, do it manually from the browser via the SingleFile extension — the captured HTML will land in `~/Downloads/SingleFile/` and ingest through the same path as any other web clip.

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
