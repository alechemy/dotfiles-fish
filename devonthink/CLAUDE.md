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
  → Extract: Web Content (bookmarks → clean title + NeedsSingleFile=1 + daily-note wikilink + archive directly to 99_ARCHIVE in one pass — does NOT flow through Post-Enrich & Archive)

SingleFile ingestion is OUT of smart rules — it's Python scripts + an fswatch launchd agent. See devonthink/README.md → "SingleFile Ingestion Pipeline".
  Scenario 1 (desktop save): Chrome SingleFile ext → ~/Downloads/SingleFile/*.html → fswatch → ingest-singlefile-html.py → creates bookmark + HTML snapshot + markdown in DT in one atomic AppleScript pass
  Scenario 2 (queued bookmark): capture-bookmarks-batch.py (manual/hotkey) → finds NeedsSingleFile=1 bookmarks → per-URL: capture-with-singlefile → ingest-singlefile-html.py --bookmark <UUID> (reuses existing bookmark, clears the flag)
  → Extract: Native Text Bypass (text-native docs skip OCR, excludes bookmarks and HTML) → sets Recognized=1, Commented=1
  → Enrich: AI Metadata (single LLM call → title, eventDate, type, tags, summary, lowConfidence) → sets AIEnriched=1
  → Post-Enrich & Archive (action items → Things 3, daily notes extraction + wikilinks, archive to 99_ARCHIVE) → move only on success
  → Export: Wiki Raw (post-archive, writes metadata + content to ~/Wiki/raw/) → sets WikiExported=1
```

Smart rule scripts live in `../stow/devonthink/Library/Application Scripts/com.devon-technologies.think/Smart Rules/`. Standalone Python helpers called by those scripts live in `../stow/devonthink/.local/bin/`. Hazel/launchd utilities live in `utils/`. Integration docs (Wiki, Granola, GitHub Stars, Summarize) live in `docs/`. The canonical reference for rule criteria, triggers, and actions is `README.md`.

## Key design decisions

- **AI enrichment is one LLM call** returning a JSON object with `title`, `eventDate`, `type`, `tags`, `summary`, `lowConfidence`. The script passes `as "JSON"` so DT returns a native AppleScript record — no string parsing.
- **Programmatic record creators must pre-do early-pipeline work and pre-set the flags that would have been set by it.** Every metadata or content mutation on a just-created record triggers a DT index update, a DTTG sync event, and a UI re-render. When several smart rules fire `On Import` on the same fresh record in rapid succession, DT's UI can transiently double or triple-render it (phantom rows in rule filter views) — observed historically with phone-synced bookmarks and with SingleFile-ingested markdown. The fix is not "reduce the number of rules" but "do the work upstream so the rules don't match in the first place":
  - For markdown records landing in `00_INBOX`, call `~/.local/bin/lint-markdown-file` on the file before import and set `Recognized=1, Commented=1` at creation — this keeps `Extract: Native Text Bypass` from matching.
  - For bookmark records landing in `00_INBOX`, set `Recognized=1, Commented=1, AIEnriched=1` (or own the bookmark's journey entirely, as `Extract: Web Content` now does) to keep `Post-Enrich & Archive` from matching.
  - For records that should skip the pipeline entirely (rewrite/companion records like prose-check output), set `NeedsProcessing=0` explicitly — not empty — to block `mark-inbox-needs-processing` from flipping it back on.
  - Current pre-flagging callers: `ingest-singlefile-html.py`, `summarize` skill, `import-granola.py`, `import-github-stars.py`, `km-new-inbox-note.applescript`, `prose-check` skill. New record-creators in any part of the pipeline must follow the same pattern.
- **Web clip ingestion is Python, not smart rules.** Scenario 1 (desktop SingleFile save) is driven by an fswatch launchd agent on `~/Downloads/SingleFile/`. Scenario 2 (scheduled/manual batch capture of `NeedsSingleFile=1` bookmarks) is `capture-bookmarks-batch.py`. Both funnel through `ingest-singlefile-html.py`, which creates bookmark + HTML snapshot + markdown in a single atomic AppleScript call — DT never sees the staging file, and no Sweep / Every-Minute / `synchronize record` can race the ingestion. Previous smart-rule-based implementation (Capture: SingleFile Batch + Process: SingleFile Import) had three known race classes around URL matching, HTML filename lookups, and DT's buffered disk writes; moving the work out of smart rules eliminated all of them.
- **All pipeline components log to `~/Library/Logs/devonthink-pipeline.log`** via two helpers:
  - `~/.local/bin/pipeline-log <component> <level> <message> [<record-name> [<record-uuid>]]` — bash, called from AppleScript via `do shell script`. Each smart-rule script includes a short `pipelineLog(component, level, msg, recName, recUUID)` handler that wraps this.
  - `~/.local/bin/pipeline_log.py` — Python module. Add `sys.path.insert(0, str(Path.home()/".local"/"bin"))` then `from pipeline_log import setup as setup_log; log = setup_log("component-name")`. Returns a `logging.Logger`. Accepts `extra={"record_name": ..., "record_uuid": ...}` for record context.
  - Format: `YYYY-MM-DDTHH:MM:SS LEVEL [Component] message (record="Name"|uuid=…)`. Grep by UUID to trace one record's full journey across rules. Existing `log message` calls in AppleScripts remain alongside the central log for real-time monitoring in DT's Log window.
- **Markdown transforms operate on in-memory `plain text`, never on `path of theRecord`.** The earlier `lint-markdown` rule ran `sed -i` + `markdownlint --fix` directly on the backing file and then called `synchronize record`, which races with DT's buffered write of `set plain text` for programmatically-created records (from the `summarize` and `prose-check` skills): if the rule fires before DT flushes, `synchronize record` overwrites DT's in-memory content with the stale/empty disk state and silently wipes the record. Any transform that mutates a markdown record should read `plain text`, transform, and write back via `set plain text` — keeping DT as the source of truth.
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
- **Things 3** — receives action items via AppleScript from `post-enrich-and-archive.applescript`
- **capture-with-singlefile** (`~/.local/bin/capture-with-singlefile`) — bash script that drives Chromium via AppleScript, navigates to URLs, and triggers SingleFile saves via `Cmd+D` keystroke. Called by `capture-bookmarks-batch.py` (one URL at a time)
- **defuddle** (`~/.local/share/mise/shims/defuddle`) — extracts readable article content as markdown from local HTML files for `ingest-singlefile-html.py`
- **fswatch** (`/opt/homebrew/bin/fswatch`) — watches `~/Downloads/SingleFile/` for new HTML files; invoked by `singlefile-watcher.sh` under the `com.user.singlefile-watcher` launchd agent

- **Granola** — meeting transcription app; `import-granola.py` reads its local cache and imports meeting notes into DT with pre-set metadata (`GranolaID`, `EventDate`, `NameLocked=1`)

## Custom metadata fields

See the table in `README.md` → "Custom Metadata Setup" for the full list. The pipeline-critical boolean flags are: `NeedsProcessing`, `Handwritten`, `Recognized`, `Commented`, `AIEnriched`, `NameLocked`, `WikiExported`, `NeedsSingleFile`. Granola imports also use `GranolaID` (Text) and `GranolaParticipants` (Multi-line Text).
