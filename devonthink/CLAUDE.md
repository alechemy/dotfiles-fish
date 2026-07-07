# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Context

This directory documents and stores scripts for a DEVONthink 4 document-processing pipeline. The pipeline runs on the user's primary Mac. Stowable config (smart rule scripts, launchd plists, helper binaries) lives in `../stow/devonthink/`.

## DEVONthink AppleScript conventions

- Always use `application id "DNtp"` (not `"DEVONthink 4"` or `"DEVONthink 3"`)
- All smart rule scripts use the `performSmartRule(theRecords)` handler
- LLM calls use DEVONthink's built-in `get chat response for message … role … mode … thinking … tool calls` command — no external API calls
- Pass `as "JSON"` (or `mode "auto"/"text"`) to control how DT returns the response
- Custom metadata is read with `get custom meta data for "FieldName" from theRecord` and written with `add custom meta data value for "FieldName" to theRecord`
- **Never compare a boolean custom-metadata read against `1` directly.** A flag set by script reads back as integer `1`, but the same flag ticked in the GUI's Info panel reads back as boolean `true`, and `true is 1` is **false** in AppleScript — an `is 1` check silently ignores GUI-set flags (DT-level rule *criteria* match both forms, so the rule fires but the script no-ops). Use the shared `flagIsSet(v)` handler (integer coercion inside a try) that every flag-reading rule script now carries.
- **Never write a Unix epoch integer into a Date-typed custom metadata field.** DEVONthink interprets a number assigned to a Date field against its 2001 Cocoa reference epoch, not Unix's 1970 — a Unix timestamp lands ~31 years in the future (a May 2026 stamp renders as May 2057). Write timestamps as a native `current date`; the read side can subtract dates directly. This bit `EnrichStartedAt` in `enrich-ai-metadata.applescript` and silently disabled the enrich timeout.
- Errors should be logged with `log message "…" info recName` (visible in DT's Log window)

## Pipeline architecture

Documents flow through DEVONthink smart rules gated by boolean custom metadata flags. The full sequence:

```
Boox device → Dropbox (via Boox export)
  → boox-import-watcher.sh (launchd + fswatch on the Maestral-synced Notebooks folder; deletes untitled `Notebook-<n>` quick notes instead of importing) → boox-import.sh: PDF → TIFF, then dedup by SourceFile at import — a new note imports to 00_INBOX (Handwritten=1, SourceFile set); a re-export replaces the matching record's file in place, resets flags, re-primes it; byte-identical re-exports are a no-op
  → Extract: Boox Handwritten (OCR) → sets Recognized=1
  → Format: Boox Comments (LLM markdown formatting → Finder Comment) → sets Commented=1
  → Extract: Scans & Images (standard OCR for non-handwritten images/PDFs with no text layer — Word Count 0) → sets Recognized=1, Commented=1
  → Extract: Web Content (bookmarks → clean title + NeedsSingleFile=1 OR SkipSingleFile=1 depending on domain (~/.config/devonthink-pipeline/singlefile-skip-domains.txt) + daily-note wikilink + archive directly to 99_ARCHIVE in one pass — does NOT flow through Post-Enrich & Archive)

SingleFile ingestion is OUT of smart rules — it's Python scripts + an fswatch launchd agent. See devonthink/README.md → "SingleFile Ingestion Pipeline".
  Scenario 1 (desktop save): Chrome SingleFile ext → ~/Downloads/SingleFile/*.html → fswatch → ingest-singlefile-html.py → creates bookmark + HTML snapshot + markdown in DT in one atomic AppleScript pass
  Scenario 2 (queued bookmark): capture-bookmarks-batch.py (manual/hotkey) → finds NeedsSingleFile=1 bookmarks → per-URL: capture-with-singlefile → ingest-singlefile-html.py --bookmark <UUID> (reuses existing bookmark, clears the flag)
  → Extract: Native Text Bypass (text-native docs — markdown/RTF/HTML and born-digital PDFs with a text layer, i.e. Word Count > 0 — skip OCR; bookmarks excluded, they go through Extract: Web Content) → sets Recognized=1, Commented=1
  → Enrich: AI Metadata (single LLM call → title, eventDate, type, tags, summary, lowConfidence) → sets AIEnriched=1
  → Post-Enrich & Archive (action items → Things 3, daily notes extraction + wikilinks, archive to 99_ARCHIVE) → move only on success
```

Smart rule scripts live in `../stow/devonthink/Library/Application Scripts/com.devon-technologies.think/Smart Rules/`. Standalone Python helpers called by those scripts live in `../stow/devonthink/.local/bin/`. Standalone AppleScript utilities live in `utils/`. Integration docs (Granola, GitHub Stars, Summarize) live in `docs/`. The canonical reference for rule criteria, triggers, and actions is `README.md`.

The **entity layer** (`/20_ENTITIES` — Person/Place/Event records, morning briefing, AI fact filing) sits outside the smart-rule state machine: two launchd-driven tier-1 Python orchestrators (`dt-morning-brief.py`, `entity-filing.py`) do all DEVONthink I/O through a single JXA gateway, `entity-dt-bridge.js`, invoked via `/usr/bin/osascript -l JavaScript` with a JSON ops file. Anything JSON-heavy that talks to DT should go through (or extend) that bridge rather than round-tripping JSON through AppleScript records. Design doc: `docs/entities.md`. JXA gotcha learned there: never probe speculative properties on a DT object specifier (`typeof rec.isNil`) — any property access fires an AppleEvent; commands return `null` for missing records, so null-check instead.

## MCP server vs the automation bridges

The DEVONthink MCP server is the **interactive** interface — use it freely from an AI session for searches, reads, and one-off record work. It is never a pipeline transport: launchd automation must not depend on a server process or session being alive, so runtime code talks to DT only via `/usr/bin/osascript` (AppleScript or `entity-dt-bridge.js`). Rules for sessions using the MCP tools:

- `/20_ENTITIES/People` and `/20_ENTITIES/_Review` are excluded from AI access; MCP tools refuse their UUIDs ("Record is excluded from AI access") and omit them from results. This is by design, not breakage — operate on entity records via osascript/the bridge instead. Same applies to `/10_DAILY`.
- Custom-metadata writes through MCP auto-create fields (typos become new fields) and can flip the flags the smart-rule state machine keys on (`NeedsProcessing`, `Recognized`, `Commented`, `AIEnriched`, …). Before setting any flag from the README's metadata table, understand which rule watches it.
- The server's privacy posture (exposed databases, private-info redaction — currently enabled) lives in DT's Settings → AI on the machine, not in this repo; see the README fresh-machine checklist.

## Key design decisions

- **AI enrichment is one LLM call** returning a JSON object with `title`, `eventDate`, `type`, `tags`, `summary`, `lowConfidence`. The script passes `as "JSON"` so DT returns a native AppleScript record — no string parsing.
- **Scan vs. native-text routing keys on `Word Count`, not `Kind` alone.** `Extract: Scans & Images` matches `(Kind Image or PDF/PS) and Word Count = 0` — only documents with no text layer go to OCR. `Extract: Native Text Bypass` matches `Word Count > 0 and Kind is not Bookmark` (markdown, RTF, HTML, **and born-digital PDFs that already carry selectable text**) and sets `Recognized=1, Commented=1` without OCR. So a born-digital PDF (`Word Count > 0`) bypasses OCR and keeps its crisp text layer instead of having it replaced by a lower-fidelity re-OCR; a scanned/image PDF (`Word Count = 0`) goes to OCR. Do **not** drop the `Word Count` conditions or re-add `Kind is not PDF/PS` to the bypass — either change re-opens a gap where born-digital PDFs match no extraction rule and stall in `00_INBOX` with no error. The original symptom was a born-digital PDF wedged at Extract: Scans & Images because OCR couldn't run (ABBYY engine not installed); the routing guard keeps such PDFs off the OCR path entirely.
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
- **`NameLocked` prevents AI rename overwrites.** The script sets `NameLocked=1` _before_ renaming so the `After Renaming, Lock Name` smart rule (which only fires when `NameLocked is Off`) doesn't catch the AI's own rename.
- **Archive uses AppleScript, not declarative actions.** Move happens first; `NeedsProcessing` is cleared only on success, preventing silent data loss if the move fails.
- **Handwritten notes use the Finder Comment** as the AI-readable text source (not `plain text`) because OCR output is formatted by the LLM before enrichment.
- **`EnrichStartedAt` timestamp** enforces a 5-minute timeout on LLM calls so records don't get stuck retrying indefinitely.

## External dependencies

- **ImageMagick + Ghostscript** (`/opt/homebrew/bin/magick`, `gtimeout`) — PDF→TIFF conversion
- **markdownlint** (`/opt/homebrew/bin/markdownlint --fix`) — applied to markdown before enrichment and to LLM-formatted comments
- **launchd** (`~/Library/LaunchAgents/com.user.dt-daily-note.plist`) — fires `create-daily-note.sh` at 05:00 daily
- **Things 3** — receives action items via AppleScript from `post-enrich-and-archive.applescript`
- **capture-with-singlefile** (`~/.local/bin/capture-with-singlefile`) — bash script that drives Chromium via AppleScript, navigates to URLs, and triggers SingleFile saves via `Cmd+D` keystroke. Called by `capture-bookmarks-batch.py` (one URL at a time)
- **defuddle** (`~/.local/share/mise/shims/defuddle`) — extracts readable article content as markdown from local HTML files for `ingest-singlefile-html.py`
- **fswatch** (`/opt/homebrew/bin/fswatch`) — folder watcher behind two launchd agents: `singlefile-watcher.sh` (`com.user.singlefile-watcher`) watches `~/Downloads/SingleFile/` for new HTML files, and `boox-import-watcher.sh` (`com.user.boox-import-watcher`) watches the Maestral-synced Boox "Notebooks" folder for new PDF exports

- **Granola** — meeting transcription app; `import-granola.py` reads its local cache and imports meeting notes into DT with pre-set metadata (`GranolaID`, `EventDate`, `NameLocked=1`)

## Custom metadata fields

See the table in `README.md` → "Custom Metadata Setup" for the full list. The pipeline-critical boolean flags are: `NeedsProcessing`, `Handwritten`, `Recognized`, `Commented`, `AIEnriched`, `NameLocked`, `NeedsSingleFile`, `SkipSingleFile`, `SingleFileTooLarge`, `AIChatTranscript`. Granola imports also use `GranolaID` (Text) and `GranolaParticipants` (Multi-line Text).
