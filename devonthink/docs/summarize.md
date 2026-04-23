# Summarize Skill (On-Demand)

The [`stow/claude/.claude/skills/summarize/`](../../stow/claude/.claude/skills/summarize/) directory contains a Claude Code skill that summarizes any content — YouTube videos, articles, PDFs, EPUBs, podcasts — into a rich DEVONthink markdown record with section-by-section breakdowns. The summary is imported into Lorebook's `00_INBOX` with `NeedsProcessing=1` and `NameLocked=1` and flows through the standard pipeline.

## Install

The skill lives in the `claude` stow package (auto-stowed by `setup.sh`); the smart rule AppleScript lives in the `devonthink` package:

```bash
cd ~/.dotfiles/stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" claude devonthink
```

## Smart Rule Setup

Create an on-demand smart rule in DEVONthink that triggers the skill on the selected record:

- **Search in**: Lorebook (entire database)
- **Criteria**: Any of the following are true:
  - Kind is Bookmark
  - Kind is PDF/PS
- **Trigger**: On Demand
- **Actions**: Run AppleScript (external) — [`summarize-on-demand.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/summarize-on-demand.applescript)

Select a bookmark or PDF in DEVONthink and run the rule. The script reads the URL (for bookmarks) or file path (for PDFs), then invokes `claude -p "/summarize <source>"` in the background. Progress is logged to `~/Library/Logs/summarize.log`. The skill imports all output back into `00_INBOX` and the pipeline picks it up from there.

You can also invoke directly from Claude Code:

```
/summarize https://youtube.com/watch?v=...
/summarize https://example.com/article
/summarize ~/Downloads/book.epub
```

## Requirements

- DEVONthink 4 must be running with Lorebook open
- `claude` CLI available at `~/.local/bin/claude`
- CLI tools (the skill checks on first run and prompts before installing):
  - `yt-dlp` — YouTube and podcast extraction (`brew install yt-dlp`)
  - `defuddle` — web article extraction (`npm install -g defuddle`)
  - `pdftotext` — PDF text extraction (`brew install poppler`)
  - `pandoc` — EPUB/DOCX conversion (`brew install pandoc`)
- Optional: `mlx_whisper` (local audio transcription) or `ELEVENLABS_API_KEY` (ElevenLabs Scribe) for audio content without subtitles
