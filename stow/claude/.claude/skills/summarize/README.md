# summarize

Claude Code skill that summarizes any content — YouTube videos, articles, PDFs, EPUBs, podcasts — into a rich DEVONthink markdown record with section-by-section breakdowns. The summary is imported into `00_INBOX` and flows through the standard pipeline (AI enrichment → archive → wiki export).

Adapted from [reysu/ai-life-skills](https://github.com/reysu/ai-life-skills) for DEVONthink integration instead of Obsidian.

## Install

```bash
cd ~/.dotfiles/stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" devonthink
```

Then use `/summarize` in Claude Code.

## Usage

For a YouTube video or article, paste the URL:

```
/summarize https://youtube.com/watch?v=...
```

For a book or PDF, provide the file path:

```
/summarize ~/Downloads/The Singularity Is Near.epub
```

The skill summarizes content proportional to its length — a 600-page book gets chapter-by-chapter treatment, while a short article gets a concise summary. All output is imported into DEVONthink's Lorebook inbox with `NeedsProcessing=1` and `NameLocked=1`, then processed by the standard pipeline.

## Requirements

- DEVONthink 4 must be running
- CLI tools: `yt-dlp`, `defuddle`, `pdftotext`, `pandoc` (the skill checks and prompts on first run)
- The Lorebook database with `00_INBOX` group (standard pipeline setup)
