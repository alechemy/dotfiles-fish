# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Context

This directory documents and stores scripts for a DEVONthink 4 document-processing pipeline. The pipeline runs on a Mac mini server. Stowable config (smart rule scripts, launchd plists, helper binaries) lives in `../stow/devonthink/`.

## DEVONthink AppleScript conventions

- Always use `application id "DNtp"` (not `"DEVONthink 4"` or `"DEVONthink 3"`)
- All smart rule scripts use the `performSmartRule(theRecords)` handler
- LLM calls use DEVONthink's built-in `get chat response for message … role … mode … thinking … tool calls` command — no external API calls
- Pass `as "JSON"` (or `mode "auto"/"text"`) to control how DT returns the response
- Custom metadata is read with `get custom meta data for "FieldName" from theRecord` and written with `add custom meta data value for "FieldName" to theRecord`
- Errors should be logged with `log message "…" info recName` (visible in DT's Log window)

## Pipeline architecture

Documents flow through DEVONthink smart rules gated by boolean custom metadata flags. The full sequence:

```
Boox device → Dropbox (via Boox export)
  → Hazel (hazel-boox-import.sh): PDF → TIFF, import to Lorebook inbox, set Handwritten=1
  → Sweep rules: set NeedsProcessing=1, move to 00_INBOX
  → Handle Updated Notebooks (for Boox re-imports): replace content in-place, reset flags, delete duplicate
  → Extract: Boox Handwritten (OCR) → sets Recognized=1
  → Format: Boox Comments (LLM markdown formatting → Finder Comment) → sets Commented=1
  → Extract: Scans & Images (standard OCR for non-handwritten images/PDFs) → sets Recognized=1, Commented=1
  → Extract: Web Content (bookmarks → monolith HTML + defuddle markdown, deletes bookmark) → new records re-enter pipeline
  → Extract: Native Text Bypass (text-native docs skip OCR, excludes bookmarks) → sets Recognized=1, Commented=1
  → Enrich: AI Metadata (single LLM call → title, eventDate, type, tags, summary, lowConfidence) → sets AIEnriched=1
  → Extract: Action Items (regex parse → Things 3 via AppleScript) → sets TasksExtracted=1
  → Process: Daily Notes (extract journal sections, append EventDate wikilinks) → sets DailyNotesProcessed=1
  → Archive: Processed Items (move to 99_ARCHIVE, clear NeedsProcessing) → move only on success
  → Export: Wiki Raw (post-archive, writes metadata + content to ~/Wiki/raw/) → sets WikiExported=1
```

Smart rule scripts live in `../stow/devonthink/Library/Application Scripts/com.devon-technologies.think/Smart Rules/`. Standalone utilities live in `utils/`. The canonical reference for rule criteria, triggers, and actions is `README.md`.

## Key design decisions

- **AI enrichment is one LLM call** returning a JSON object with `title`, `eventDate`, `type`, `tags`, `summary`, `lowConfidence`. The script passes `as "JSON"` so DT returns a native AppleScript record — no string parsing.
- **`NameLocked` prevents AI rename overwrites.** The script sets `NameLocked=1` _before_ renaming so the `Util: Lock Name on Rename` smart rule (which only fires when `NameLocked is Off`) doesn't catch the AI's own rename.
- **Archive uses AppleScript, not declarative actions.** Move happens first; `NeedsProcessing` is cleared only on success, preventing silent data loss if the move fails.
- **Handwritten notes use the Finder Comment** as the AI-readable text source (not `plain text`) because OCR output is formatted by the LLM before enrichment.
- **`EnrichStartedAt` timestamp** enforces a 5-minute timeout on LLM calls so records don't get stuck retrying indefinitely.
- **Wiki is indexed into Lorebook, not a separate database.** The `~/Wiki/wiki/` directory is indexed into a `20_WIKI` group in Lorebook so that `[[wikilinks]]` resolve between wiki pages and archived documents, and See Also works cross-collection. All pipeline smart rules are scoped to `00_INBOX` so they don't touch indexed wiki files.

## External dependencies

- **ImageMagick + Ghostscript** (`/opt/homebrew/bin/magick`, `gtimeout`) — PDF→TIFF conversion
- **markdownlint** (`/opt/homebrew/bin/markdownlint --fix`) — applied to markdown before enrichment and to LLM-formatted comments
- **Hazel** — watches the Maestral-synced Dropbox "Notebooks" folder
- **launchd** (`~/Library/LaunchAgents/com.user.dt-daily-note.plist`) — fires `create-daily-note.sh` at 03:00 daily
- **Things 3** — receives action items via AppleScript from `extract-action-items.applescript`
- **monolith** (`/opt/homebrew/bin/monolith`) — saves web pages as single self-contained HTML files for Extract: Web Content
- **defuddle** (`~/.local/share/mise/shims/defuddle`) — extracts readable article content as markdown for Extract: Web Content

- **Granola** — meeting transcription app; `import-granola.py` reads its local cache and imports meeting notes into DT with pre-set metadata (`GranolaID`, `EventDate`, `NameLocked=1`)

## Custom metadata fields

See the table in `README.md` → "Custom Metadata Setup" for the full list. The pipeline-critical boolean flags are: `NeedsProcessing`, `Handwritten`, `Recognized`, `Commented`, `AIEnriched`, `NameLocked`, `TasksExtracted`, `DailyNotesProcessed`, `WikiExported`. Granola imports also use `GranolaID` (Text) and `GranolaParticipants` (Multi-line Text).
