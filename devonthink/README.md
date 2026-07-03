# DEVONthink PKM Pipeline

Behold my needlessly complex, fully-automated document-processing pipeline. It's built on DEVONthink 4 smart rules. Documents enter from multiple sources (handwritten Boox notebooks via Dropbox, web bookmarks, scans, manual imports) and flow through a series of gated processing steps: OCR/transcription, LLM-powered metadata enrichment (title, date, tags, summary), action item extraction to Things 3, and daily note aggregation. Each step is controlled by boolean metadata flags so that documents can be re-processed or debugged independently.

Pipeline scripts live in [`../stow/devonthink/`](../stow/devonthink/) (stowed to `$HOME` via GNU Stow). Standalone utilities and documentation live here.

## Custom Metadata Setup

The following custom metadata fields must be created in DEVONthink before the pipeline will work (Settings → Data → Custom Metadata):

| Field               | Type            | Purpose                                                                                                                                                                                                                                                                                      |
| ------------------- | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Handwritten         | Boolean         | Set at import by the Boox import watcher for Boox handwritten notes. Gates OCR processing (Extract: Boox Handwritten) and notebook dedup (Handle Updated Notebooks)                                                                                                                          |
| NeedsProcessing     | Boolean         | Tracks whether a record requires processing through the pipeline                                                                                                                                                                                                                             |
| Recognized          | Boolean         | Tracks whether OCR/transcription has been run on a record                                                                                                                                                                                                                                    |
| Commented           | Boolean         | Tracks whether text has been mirrored to comment field                                                                                                                                                                                                                                       |
| AIEnriched          | Boolean         | Tracks whether the combined AI enrichment step (rename + tag + summarize) has been run on a record                                                                                                                                                                                           |
| NameLocked          | Boolean         | When On, prevents Enrich: AI Metadata from overwriting the document's name. Set automatically by the on-rename guard rule, by Handle Updated Notebooks for intentionally-named Boox notes, and by Enrich: AI Metadata itself after a successful AI rename                                    |
| Summary             | Text            | Stores a brief AI-generated summary of the document's content                                                                                                                                                                                                                                |
| DocumentType        | Text            | Stores the AI-assigned document type label (e.g. "Receipt", "Invoice", "Meeting Notes", "Manual")                                                                                                                                                                                            |
| EventDate           | Text            | Stores the event date in yyyy-mm-dd format for time-bound documents (e.g. meeting notes, calls). Extracted from content when available, otherwise from document metadata                                                                                                                     |
| LowConfidence       | Boolean         | Flagged by the AI when document content is too unclear or ambiguous to produce a reliable title and summary. Use for filtering records that need manual review                                                                                                                               |
| PreviousName        | Text            | Stores the document's name before the most recent AI rename, enabling a one-step revert                                                                                                                                                                                                      |
| SourceFile          | Identifier      | Stores the original Boox filename (without extension) as a stable dedup key                                                                                                                                                                                                                  |
| RecognizedAt        | Date            | Timestamp set before OCR begins by Extract: Boox Handwritten. Used by Format: Boox Comments to detect timeout if `plain text` is never populated (the AI-driven "Recognize" rule action is async, unlike the builtin "OCR" action). Not used by the standard OCR path, which is synchronous. |
| EnrichStartedAt     | Date            | Timestamp set by Enrich: AI Metadata on first attempt, used to enforce a 5-minute timeout so records aren't stuck retrying indefinitely                                                                                                                                                      |
| EnrichInputHash     | Text            | SHA-256 of the inputs the LLM saw (record name + filtered/truncated content) on the last successful enrichment. If the hash matches on a retry, Enrich: AI Metadata skips the LLM call entirely. Clear this field to force a fresh LLM call on otherwise-unchanged content                  |
| ErrorCount          | Integer         | Tracks the number of times a document has timed out or failed processing in a pipeline step                                                                                                                                                                                                  |
| PreviousTasks       | Multi-line Text | Stores a newline-separated list of tasks already sent to Things 3 to prevent duplicates on notebook updates                                                                                                                                                                                  |
| DailyNoteLinked     | Boolean         | Tracks whether a document has been linked to its respective EventDate's daily note                                                                                                                                                                                                           |
| PreviousDailyNotes  | Multi-line Text | Stores a newline-separated list of extracted daily notes to prevent duplicates on notebook updates                                                                                                                                                                                           |
| NeedsSingleFile     | Boolean         | Set on bookmarks by Extract: Web Content when the URL's hostname is NOT on the skip list. Signals to `capture-bookmarks-batch.py` that the bookmark still needs a browser-driven HTML snapshot. Cleared by `ingest-singlefile-html.py` once the bookmark has been captured                   |
| SkipSingleFile      | Boolean         | Set on bookmarks by Extract: Web Content when the URL's hostname matches `~/.config/devonthink-pipeline/singlefile-skip-domains.txt` (e.g. youtube, spotify), or manually by the user to opt a single bookmark out. The queue-drain path of `capture-bookmarks-batch.py` filters these out; the selection path (--uuid) bypasses the check so explicit user selection always captures. **Skip wins:** the hourly `Util: Metadata Cleanup` rule clears `NeedsSingleFile` whenever `SkipSingleFile` is also set, so the two flags are never simultaneously true |
| SingleFileTooLarge  | Boolean         | Set on a bookmark by `ingest-singlefile-html.py` when the captured HTML exceeds `MAX_INGEST_BYTES` (25 MB post-compression). The ingester clears `NeedsSingleFile`, deletes the staging HTML, and flags the bookmark so the user can review / re-capture manually instead of the pipeline retrying indefinitely. A desktop capture with no bookmark record (Scenario 1) is instead moved to `~/Desktop/DT_Import_Errors/` — deleting it would destroy the only copy of a deliberate capture with no trace in DT |
| WebClipSource       | Item Link       | Points back to the source bookmark from a derived record. Set on the HTML and markdown by `ingest-singlefile-html.py` during a single atomic AppleScript pass                                                                                                                                |
| WebClipMarkdown     | Item Link       | Set on the bookmark and HTML by `ingest-singlefile-html.py`; points to the readable markdown record                                                                                                                                                                                          |
| WebClipSnapshot     | Item Link       | Set on the bookmark by `ingest-singlefile-html.py`; points to the HTML snapshot record                                                                                                                                                                                                       |
| GranolaID           | Text            | Granola meeting UUID. Set by `import-granola.py` on import; used as an idempotency key to prevent duplicate imports                                                                                                                                                                          |
| GranolaParticipants | Multi-line Text | Comma-separated meeting attendee names from Granola. Set by `import-granola.py` on import                                                                                                                                                                                                    |
| SummarySource       | Item Link       | Set on summary records created by the Summarize skill; item link pointing back to the source record (bookmark, PDF, etc.) that was summarized                                                                                                                                                |
| IsJot               | Boolean         | Set by the Drafts Quick Jot action on iOS. Gates the Process: Jots smart rule, which inserts the jot into the matching daily note                                                                                                                                                            |
| AIChatTranscript    | Boolean         | Set on markdown records that came from an AI chat snapshot (claude.ai, gemini.google.com, chatgpt.com). The defuddle output is rewritten as a topic-organized writeup by `ingest-singlefile-html.py` before import (see [SingleFile Ingestion Pipeline](#singlefile-ingestion-pipeline) → "AI chat transcript rewrite"). Useful for filtering / re-running the rewrite if the prompt is tweaked |
| RewriteSource       | Item Link       | Set on rewrite records created by the prose-check skill; item link pointing back to the source record that was rewritten. The Prose Check (On Demand) rule passes the source UUID to the skill, which sets this on the output record it creates in `00_INBOX`                                |

> **Migration Note — `AI-Renamed` retired.** The earlier `AI-Renamed` boolean flag has been replaced by `NameLocked`. If any existing records still carry `AI-Renamed` metadata, you can safely ignore or batch-clear it; it is no longer referenced by any rule or script.

## Boox -> Dropbox -> Mac

- After closing a document, Boox exports a vector PDF
- Boox uploads PDF to Dropbox folder
- On Mac, Dropbox folder is mapped via Maestral to a local directory, "Notebooks"

## Boox Import Watcher

New Boox PDF exports landing in the Maestral-synced "Notebooks" folder are imported by a `launchd` + `fswatch` watcher, consistent with the rest of the pipeline (`singlefile-watcher`, `dt-daily-note`, …). It replaces a former Hazel rule, which was GUI state that had to be re-created by hand per machine.

| Component | Location | Role |
| --- | --- | --- |
| `boox-import-watcher.sh` | `~/.local/bin/` | `fswatch` loop on the Notebooks folder. On each new `.pdf` (`Created` or `Renamed` event, recursing into subfolders) it waits for the file size to settle, then invokes the importer. Untitled `Notebook-<n>` quick-note exports are deleted rather than imported. Sweeps the tree for a backlog on startup. Runs under the launchd agent. |
| `boox-import.sh` | `~/.local/bin/` | Converts one PDF to a monochrome Group4 TIFF (`magick`, 300 DPI), imports it into Lorebook's inbox via AppleScript, and sets `Handwritten=1`. Deletes the source PDF on success. |
| `com.user.boox-import-watcher.plist` | `~/Library/LaunchAgents/` | Keeps the watcher alive. `RunAtLoad=true`, `KeepAlive=true`. |

- **Watched folder:** `~/Dropbox (Maestral)/onyx/Go103/Notebooks`, including its category subfolders. Files arrive via Maestral sync; the watcher acts on both `Created` and `Renamed` fswatch events because a sync client can finalize a downloaded file by renaming it into place — `--event Created` alone would miss those.
- **Quarantine:** a PDF that fails conversion, or whose TIFF exceeds 50 MB, is moved to `~/Desktop/DT_Import_Errors/` (with a macOS notification) so a bad export isn't retried forever. A failed *import* (e.g. DEVONthink not running) leaves the PDF in place — the next watcher restart's backlog sweep retries it.
- **Untitled notes ignored:** an unnamed notebook exports as `Notebook-<n>.pdf` (the Boox's incrementing counter). These are throwaway quick notes, so the watcher deletes them instead of importing — titling a note on the device is the deliberate signal that it should enter DEVONthink. Because untitled notes never reach the database, a `SourceFile` match in [Handle Updated Notebooks](#handle-updated-notebooks-applescript) is always the same intentionally-named notebook being updated, not a name collision.
- **Ghostscript:** ImageMagick needs Ghostscript to decode PDFs. If you see a "no decode delegate" error, install it via `brew install ghostscript`.
- From here, DEVONthink smart rules take over (`Sweep: Lorebook Inbox` → `Extract: Boox Handwritten` OCR, etc.).

## DT Smart Rules

### Sweep: Global Inbox

- Search in
  - Global Inbox
- Criteria
  - Kind is Any Document
  - IsJot is Off
  - Name does not begin with "Jot "
- Trigger
  - Every Minute
  - On Import
  - On Moving
- Actions
  - Change NeedsProcessing to 1
  - Move to 00_INBOX

> **Jot exclusion.** Records created by the Drafts Quick Jot action (iOS via DTTG `x-callback-url` → `Name begins with "Jot "`, or macOS fallback → `IsJot=1`) must not be swept into `00_INBOX`. They are consumed by [Process: Jots](#process-jots), which inserts the jot into the matching daily note and trashes the record. Without this exclusion, Sweep races Process: Jots on `On Import` and the `Every Minute` poll will always eventually win — the jot lands in `00_INBOX` with `NeedsProcessing=1`, flows through enrichment, gets renamed to `YYYY-MM-DD Jot`, and triggers a DTTG sync loop.

### Sweep: Lorebook Inbox

- Search in
  - Lorebook > Inbox
- Criteria
  - Kind is Any Document
- Trigger
  - Every Minute
  - On Import
  - On Moving
- Actions
  - Run AppleScript "Handle Updated Notebooks"
  - Change NeedsProcessing to 1
  - Move to 00_INBOX

> See [Handle Updated Notebooks AppleScript](#handle-updated-notebooks-applescript). This script only runs in Sweep: Lorebook Inbox, where Boox TIFFs arrive with `Handwritten=1` already set by the Boox import watcher. If the incoming document is an update to an existing notebook, the script replaces the existing document's content, resets its pipeline state (`Recognized=0`, `Commented=0`, `AIEnriched=0`, `NeedsProcessing=1`) to generate a fresh AI summary, pins its name (`NameLocked=1`) to protect existing WikiLinks, and trashes the new import — so the remaining actions in the sweep rule are never reached. If the document is new, the script sets SourceFile metadata and allows the sweep to continue normally.

### Sweep: Lorebook Root

- Exclude Subgroups: Checked
- Search in
  - Lorebook
- Criteria
  - Kind is Any Document
- Trigger
  - Hourly
  - On Import
  - On Moving
- Actions
  - Change NeedsProcessing to 1
  - Move to 00_INBOX

### Prime: Direct 00_INBOX Arrivals

Sets `NeedsProcessing=1` on records that land in `00_INBOX` directly, bypassing the Sweep rules. Records arriving via Global Inbox or Lorebook get primed by their respective [Sweep](#sweep-global-inbox) on the move. But when a client (the Drafts "New Inbox Note" action, the Keyboard Maestro `km-new-inbox-note` macro, or any future direct-create path) drops a record straight into `00_INBOX` by UUID, no sweep fires — without this rule the record would sit untouched by the pipeline.

The embedded condition filters already-primed records out of the scripts's working set, and the script itself re-checks `NeedsProcessing` so a manually-set `NeedsProcessing=0` is not overwritten.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is empty
- Trigger
  - On Import
  - Every Minute (catches records that arrived via a path that didn't fire On Import, or that otherwise got stuck without `NeedsProcessing` set)
- Actions
  - Run AppleScript (external) — see [`mark-inbox-needs-processing.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/mark-inbox-needs-processing.applescript)

### Extract: Boox Handwritten

Runs OCR on handwritten Boox notes. A small AppleScript timestamps the record (via `RecognizedAt`) before recognition begins so that the downstream formatting rule (Format: Boox Comments) can detect if OCR stalls. The `Handwritten` flag is set at import by the Boox import watcher, so this rule only matches documents that originated from the Boox → Dropbox → import-watcher path.

> **Design Note — Async Recognition.** DEVONthink's "Recognize" action runs asynchronously: `plain text` may not be populated by the time subsequent actions in the same rule execute. For this reason, comment mirroring and formatting are handled by a separate rule (Format: Boox Comments) that polls for `plain text` availability on the next cycle.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is Off
  - Handwritten is On
- Trigger
  - Every Minute
- Actions
  - Run AppleScript (embedded)

    ```applescript
    on performSmartRule(theRecords)
        tell application id "DNtp"
            repeat with theRecord in theRecords
                add custom meta data (current date) for "RecognizedAt" to theRecord
            end repeat
        end tell
    end performSmartRule
    ```

  - Recognize - Transcribe Text & Notes
  - Change Recognized to 1

### Format: Boox Comments

Waits for the async "Recognize" action in "Extract: Boox Handwritten" to populate `plain text`, then sends the raw transcription to the LLM (via DEVONthink's `get chat response for message` command) for markdown formatting. The formatted text is written to the Finder Comment. If `plain text` is still empty, the rule skips the record and retries on the next poll. A 5-minute timeout (based on the `RecognizedAt` timestamp set by Extract: Boox Handwritten) prevents records from staying in limbo indefinitely if OCR stalls.

The `Commented` flag is flipped inside the script (not as a declarative action) so it is only set when `plain text` was actually available and processed.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is On
  - Handwritten is On
  - Commented is Off
- Trigger
  - Every Minute
- Actions
  - Run AppleScript (embedded) — see [`format-boox-comments.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/format-boox-comments.applescript)
  - _(No declarative "Change Commented" action — the script handles it conditionally)_

### Extract: Scans & Images

Documents not flagged as handwritten Boox notes but lacking a text layer (e.g., flat PDFs, screenshots, photos of receipts) are run through standard OCR. Because DT's built-in OCR is synchronous, both `Recognized` and `Commented` are set in the same rule immediately after OCR completes — no separate verification step is needed.

`Word Count is 0` operationalizes "lacking a text layer." A born-digital PDF carries selectable text (`Word Count > 0`), so it is deliberately *not* matched here — it is fast-tracked by [Extract: Native Text Bypass](#extract-native-text-bypass-true-fast-track) instead, which preserves its native text layer rather than replacing it with a lower-fidelity re-OCR.

Use "OCR - Apply" (not "OCR - To searchable PDF" or "OCR & Continue"). The latter two create a new record imported to the Global Inbox, which the Sweep rule picks up and recycles through this rule indefinitely, producing one new duplicate per minute.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Handwritten is Off
  - Recognized is Off
  - Word Count is 0
  - Any of the following are true:
    - Kind is Image
    - Kind is PDF/PS
- Trigger
  - Every Minute
- Actions
  - OCR - Apply
  - Change Recognized to 1
  - Change Commented to 1

### Extract: Web Content

Handles Bookmark records arriving in `00_INBOX` in one pass: cleans the title, flags for later SingleFile capture (if a URL is present), appends a wikilink to today's daily note, and archives directly to `99_ARCHIVE`. The actual browser-based capture happens separately via the [SingleFile Ingestion Pipeline](#singlefile-ingestion-pipeline).

The bookmark title is cleaned via [`clean-web-title`](../stow/devonthink/.local/bin/clean-web-title): fullwidth punctuation introduced by browser filename sanitizers is mapped back to ASCII (e.g. `：` → `:`, `｜` → `|`), Unicode whitespace is collapsed, and any trailing `| Site Name` brand suffix is stripped. This is the only chance to fix the bookmark name — bookmarks skip AI enrichment.

Bookmarks with no URL are archived the same way, just without the `NeedsSingleFile` flag.

**Dedup on arrival.** Before doing anything else, the rule calls `lookup records with URL` against Lorebook. If another bookmark with the same URL already exists outside `00_INBOX` (i.e. has been processed and archived), the incoming record is logged and moved to the database Trash — no title clean, no flag-setting, no daily-note entry, no archive. This prevents the downstream capture queue from re-fetching already-captured URLs when a user re-saves a bookmark they already have. Concurrent `00_INBOX` arrivals (two records with the same URL, both still in the inbox) intentionally fall through so the rule doesn't race-delete a sibling.

This rule owns the bookmark's entire journey — it does not rely on `Post-Enrich & Archive` to finish the job. Previous versions set fast-track flags (`Recognized`/`Commented`/`AIEnriched`) purely to match `Post-Enrich & Archive`'s criteria, which meant the bookmark sat in `00_INBOX` across two rule firings with ~6 metadata writes, each triggering a DT re-index + DTTG sync event. Phone-synced bookmarks were the worst case — DT's UI can transiently double-render records mid-mutation, and the effect compounded across several sync cycles. Consolidating to one rule cuts the writes to ~3 and keeps them all inside a single AppleScript `tell` block.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is Off
  - Kind is Bookmark
- Trigger
  - On Import / Every Minute
- Actions
  - Run AppleScript (external) — see [`extract-web-content.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/extract-web-content.applescript)

## SingleFile Ingestion Pipeline

Bookmark → HTML → markdown ingestion lives outside DT smart rules, in three Python/bash scripts plus a launchd watcher. DT smart rules were a poor fit: the work is a multi-step sequential operation that races against DT's own ingestion, Sweep, and every-minute ticks when expressed as several scoped rules coordinating via custom-metadata flags. The Python ingester does the whole thing in one pass and hands DT finished records.

### Two scenarios

- **Scenario 1 — desktop SingleFile save.** User hits the SingleFile hotkey in Chrome. The extension writes an HTML file to `~/Downloads/SingleFile/` (a plain folder, not symlinked to DT's inbox). `fswatch`, running under a launchd agent, notices the new file and invokes the ingester. Ingester parses the URL from the SingleFile comment header, runs `defuddle` for markdown, and creates three cross-linked records in DT: a new bookmark in `99_ARCHIVE`, the HTML snapshot in `99_ARCHIVE`, and the markdown in `00_INBOX` (which enters AI enrichment). Staging file is deleted.
- **Scenario 2 — batched capture of queued bookmarks.** User saves a bookmark directly to DEVONthink (typically from phone, via DTTG). [Extract: Web Content](#extract-web-content) flags it with `NeedsSingleFile=1` on arrival. Later, the user manually runs `capture-bookmarks-batch` (CLI or KM hotkey) to drain the queue. The batch script queries DT for pending bookmarks, drives Chromium + SingleFile one URL at a time, and hands each resulting HTML to the ingester with `--bookmark <UUID>` so the existing bookmark is reused rather than re-created. The ingester clears `NeedsSingleFile` on success.

### Components

| Component | Location | Role |
| --- | --- | --- |
| `ingest-singlefile-html.py` | `~/.local/bin/` | Shared ingester. Parses SingleFile header, runs defuddle, compresses images, creates (or reuses) bookmark + HTML snapshot + markdown in a single atomic AppleScript call. Deletes staging file on success. |
| `capture-bookmarks-batch.py` | `~/.local/bin/` | Scenario 2 runner. Finds `NeedsSingleFile=1` bookmarks, drives browser via `capture-with-singlefile`, pipes each capture to the ingester. Invoked manually (CLI or hotkey). |
| `singlefile-watcher.sh` | `~/.local/bin/` | `fswatch` loop on `~/Downloads/SingleFile/`. Fires the ingester on each new `.html`. Executes under the launchd agent. |
| `com.user.singlefile-watcher.plist` | `~/Library/LaunchAgents/` | Keeps the watcher alive. `RunAtLoad=true`, `KeepAlive=true`. |
| `capture-with-singlefile` | `~/.local/bin/` | Existing script — drives running Chromium via AppleScript, triggers SingleFile via `Cmd+D` keystroke, returns the output file path. Unchanged. |

### Records produced

Both scenarios converge on the same three-record set:

| Record | Group | Notes |
| --- | --- | --- |
| Bookmark | `99_ARCHIVE` | Lightweight live link. Fast-track flags set (`Recognized`, `Commented`, `AIEnriched` = 1). `NameLocked=1`. In Scenario 2, this is the existing bookmark the user saved. |
| HTML snapshot | `99_ARCHIVE` | Faithful SingleFile HTML. `NameLocked=1`. Bypasses AI enrichment via `AIEnriched=1` when a markdown record was produced (the markdown carries enrichment); if defuddle failed, the HTML gets `NeedsProcessing=1` instead and flows through enrichment itself as a fallback. |
| Markdown extract | `00_INBOX` | Defuddle output. `NeedsProcessing=1`, `NameLocked=1`. Enters the standard enrichment pipeline. |

Cross-links are always set: `WebClipSource` on HTML and markdown point back to the bookmark; `WebClipSnapshot` and `WebClipMarkdown` on the bookmark point forward to the HTML and markdown respectively.

### Untitled-page fallback

The `%if-empty<{page-title}|No title>` pattern in SingleFile's filename template means pages without a `<title>` tag land as `No title.html`. Without intervention, all three records (bookmark, HTML, markdown) inherit `"No title"` as their name and get `NameLocked=1`, so AI enrichment never replaces it.

`ingest-singlefile-html.py` detects the placeholder (`No title`, `Untitled`) at import time and:

1. Augments the displayed name with a URL-derived suffix (e.g. `"No title — courses.mooc.fi/.../chapter-2"`) so the three records remain visually distinguishable in DT before enrichment runs.
2. Leaves `NameLocked` unset on all three records.

`Enrich: AI Metadata` then renames the markdown using its content (the prompt explicitly recognizes `"No title"`-style names as generic). [Post-Enrich & Archive](#post-enrich--archive) walks `WebClipSource` → bookmark → `WebClipSnapshot` → HTML and propagates the AI-derived name to both siblings via `replaceIfPlaceholder`, which only replaces names still beginning with `"No title"` so any manually-edited title is preserved.

### SingleFile extension setup

1. Install Chromium and the SingleFile extension.
2. In SingleFile → Options → File name, use:

    ```
    SingleFile/%if-empty<{page-title}|No title> ({date-iso} {time-locale}).{filename-extension}
    ```

    (This matches `singlefile-extension-settings.json`, the canonical settings file. The date-time suffix keeps repeat captures of the same page from colliding; `derive_title` strips it on ingest.)

3. Bind SingleFile's keyboard shortcut to `Cmd+D` in `chrome://extensions/shortcuts`. (Used by `capture-with-singlefile` for Scenario 2 and by the user directly for Scenario 1.)
4. Ensure `~/Downloads/SingleFile/` exists as a real folder (not a symlink to DT's inbox — that's what the pre-refactor setup used, and what this architecture explicitly avoids).

### Invoking the batch from DT (optional)

Scenario 2 can be triggered from inside DEVONthink via an on-demand smart rule, rather than dropping to the shell or Keyboard Maestro. Create a rule:

- Search in: (any — criteria below make it no-op if nothing's pending)
- Criteria:
  - NeedsSingleFile is On
  - Kind is Bookmark
- Trigger: On Demand
- Actions:
  - Run AppleScript (external) — see [`capture-bookmarks-on-demand.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/capture-bookmarks-on-demand.applescript)

Fire it from Tools → Apply Rules → the rule. Two invocation modes:

- **Nothing selected (or Apply Rules from a group view):** the batch drains the full `NeedsSingleFile=1` queue.
- **One or more records selected:** the rule passes those UUIDs through and the batch captures only those. Non-bookmark or URL-less records are silently skipped; the `NeedsSingleFile` flag is not consulted in this mode, so you can force a re-capture of any bookmark on demand.

Either way, `capture-bookmarks-batch.py` launches in the background, a macOS notification confirms the launch, and progress lands in `~/Library/Logs/devonthink-pipeline.log`. Pathological pages (post-compression HTML over 25 MB) are skipped and flagged with `SingleFileTooLarge=1` on the bookmark rather than stalling the batch.

### AI chat transcript rewrite

When the SingleFile capture's source URL is hosted on `claude.ai` or `gemini.google.com`, the defuddle output is a raw turn-by-turn transcript that reads poorly as a reference document. Before import, `ingest-singlefile-html.py` calls DEVONthink's `get chat response` with a curated rewrite prompt that reorganizes the transcript by topic, drops conversational framing (greetings, "great question", model signatures, the user's questions restated), and applies the prose style rules from `~/.claude/CLAUDE.md`. The result is a topic-organized writeup, not a summary — every fact, recommendation, and caveat the assistant produced is preserved.

A provenance line is prepended to the markdown body inside the import AppleScript: `*Generated from a conversation with Claude on YYYY-MM-DD. Original capture: [title](x-devonthink-item://...).*` The link points at the HTML snapshot record so the original conversation is one click away.

The markdown record is flagged `AIChatTranscript=1`. The HTML snapshot is unchanged. If the LLM call fails or times out (240 s budget), the ingest falls through to the raw defuddle transcript and logs a warning — the pipeline does not block on the rewrite. Hosts to detect are listed in `AI_CHAT_HOSTS` at the top of `ingest-singlefile-html.py`.

### Skipping domains that don't benefit from SingleFile

Some pages either won't produce a useful SingleFile snapshot (YouTube, Spotify) or already get a clean defuddle extract without one (most static-content sites). To stop those from entering the capture queue in the first place, Extract: Web Content consults `~/.config/devonthink-pipeline/singlefile-skip-domains.txt` via `~/.local/bin/should-skip-singlefile` at ingest time. Bookmarks whose hostname matches a listed domain (suffix match, so `youtube.com` covers `m.youtube.com`) get `SkipSingleFile=1` instead of `NeedsSingleFile=1`.

The queue-drain path of `capture-bookmarks-batch.py` filters out any bookmark with `SkipSingleFile=1`, so editing the skip list retroactively stops future batch runs from touching already-flagged-for-capture records. The selection path (`--uuid`, used by the on-demand rule when invoked against a selection) bypasses the check — selecting a bookmark and running the rule is treated as explicit intent to capture, overriding the skip.

Default blocklist is `youtube.com`, `youtu.be`, `spotify.com`. Edit the file to add more.

### Logs

Most pipeline components — the ingest smart rules, Python scripts, shell watchers — write to a single central log at `~/Library/Logs/devonthink-pipeline.log`. See the [Pipeline Logging](#pipeline-logging) section for the format and how to grep it. Exceptions: `import-granola.py` and `import-github-stars.py` keep their own log files (see their integration docs), `create-daily-note.sh` writes to `~/Library/Logs/dt-daily-note.log`, and the Util/formatting rules (`Process: Jots`, `Format: Boox Comments`, `Handle Updated Notebooks`, `Lint Markdown`, H1 sync) log only to DT's Log window. The SingleFile watcher's raw stdout/stderr also lands at `/tmp/singlefile-watcher.log` (launchd's capture) in case the pipeline log itself fails to write.

`dt-watchdog.sh` (every 5 minutes) is the consumer that makes failures visible: it scans the newly-written regions of the central log, `dt-daily-note.log`, and `github-stars-import.log` for `ERROR`/`WARN`/`ALERT` lines and raises a macOS notification per new failure signature (digit-stripped, re-notified at most daily; state in `~/.local/state/devonthink/watchdog-scan/`). It also verifies the two fswatch watcher agents have live processes (kickstarting and alerting when not) and flags `.html` files stuck in `~/Downloads/SingleFile/` for more than 15 minutes.

## Pipeline Logging

Every component of the pipeline appends to a single file so a record's full journey can be traced without hunting across DT's Log window, per-script log files, and shell stderr.

**Central log:** `~/Library/Logs/devonthink-pipeline.log`

**Format:** `YYYY-MM-DDTHH:MM:SS LEVEL [Component] message (record="Name"|uuid=…)`

The `(record=…)` suffix is optional; when present, grepping by UUID surfaces every event for a single record in order.

**Example:**

```
2026-04-24T14:32:15 INFO [Extract: Web Content] archived bookmark (record="Some Article"|uuid=A1B2-…)
2026-04-24T14:32:17 INFO [singlefile-watcher] ingesting /Users/alec/Downloads/SingleFile/Some Article.html
2026-04-24T14:32:20 INFO [singlefile-ingest] ingest complete: Some Article.html (A1B2-…|C3D4-…|E5F6-…)
2026-04-24T14:33:01 INFO [Enrich: AI Metadata] enriched (type=Article) (record="Some Article"|uuid=E5F6-…)
2026-04-24T14:33:04 INFO [Post-Enrich & Archive] archived (record="Some Article"|uuid=E5F6-…)
```

**Helpers** — scripts don't write the central log directly; they go through one of two thin wrappers:

| Helper                                | Used by                                | Interface                                                                                                    |
| ------------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| [`~/.local/bin/pipeline-log`](../stow/devonthink/.local/bin/pipeline-log)         | AppleScript smart rules, bash scripts  | `pipeline-log <component> <level> <message> [<record-name> [<record-uuid>]]`                                 |
| [`~/.local/bin/pipeline_log.py`](../stow/devonthink/.local/bin/pipeline_log.py)   | Python scripts                         | `from pipeline_log import setup as setup_log; log = setup_log("component"); log.info(...)`                   |

**Adding logging to a new smart rule:** append the shared `pipelineLog` handler to the bottom of the script (see [`extract-web-content.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/extract-web-content.applescript) for the canonical copy) and call `my pipelineLog(component, level, msg, recName, recUUID)` at significant events. Existing `log message` calls can stay alongside — they feed DT's Log window for real-time in-app monitoring.

**Common greps:**

```bash
# Trace one record's journey
grep 'uuid=A1B2-ABCD' ~/Library/Logs/devonthink-pipeline.log

# Errors and warnings only
grep -E ' (WARN|ERROR) ' ~/Library/Logs/devonthink-pipeline.log

# Everything from one rule
grep '\[Enrich: AI Metadata\]' ~/Library/Logs/devonthink-pipeline.log

# Live tail during debugging
tail -f ~/Library/Logs/devonthink-pipeline.log
```

## DT Smart Rules (continued)

### Extract: Native Text Bypass (True Fast-Track)

Documents that are natively text-based (Markdown, RTF, Web Archives, HTML pages, or PDFs that already contain a text layer) bypass OCR entirely. This rule flips both flags in a single pass and advances the document straight to AI enrichment. For Markdown files, a lint step runs `markdownlint --fix` (with tab-to-space conversion) over the record's in-memory content before the flags are set, ensuring house style compliance before downstream processing. Linting reads `plain text of theRecord`, round-trips it through a temp file for `markdownlint`, and writes the result back via `set plain text` — never touching `path of theRecord` directly — so programmatically-created records (e.g. from the `summarize` and `prose-check` skills) aren't at risk of a `synchronize record` racing DT's buffered disk write and wiping the body.

Bookmarks are excluded because they are handled by [Extract: Web Content](#extract-web-content). HTML pages from SingleFile captures never reach `00_INBOX` — they're handed to DT as finished records in `99_ARCHIVE` by the [SingleFile Ingestion Pipeline](#singlefile-ingestion-pipeline). Any HTML that does land in `00_INBOX` (e.g. user-dragged, DTTG-synced) is fast-tracked by this rule.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Handwritten is Off
  - Recognized is Off
  - Word Count is greater than 0
  - Kind is not Image
  - Kind is not Bookmark
- Trigger
  - Every Minute
  - On Import
- Actions
  - Run AppleScript (embedded) — see [`lint-markdown.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/lint-markdown.applescript)
  - Change Recognized to 1
  - Change Commented to 1

> **Design note — the scan-vs-native-text split keys on `Word Count`, not `Kind`.** A born-digital PDF and a scanned PDF are both `Kind PDF/PS`; the text layer is what separates them. [Extract: Scans & Images](#extract-scans--images) takes `Word Count is 0` (no text layer → needs OCR); this rule takes `Word Count is greater than 0` (already has selectable text → skip OCR). That is why `Kind is not PDF/PS` was **removed** from this rule — a text-bearing PDF has to be allowed in. Don't re-add it or drop either `Word Count` condition: either change orphans born-digital PDFs, which then match no extraction rule and stall in `00_INBOX` with no OCR error to flag them.

### Enrich: AI Metadata

A single LLM call per document generates a title, event date, document type, tags, summary, and confidence flag in one structured JSON response. The embedded AppleScript calls `get chat response` directly with `as "JSON"` so DEVONthink returns a native AppleScript record — no `jq` parsing or markdown-fence stripping needed.

**Token-usage controls** — two guards keep spend bounded:

1. **Content cap** — Before the LLM call, the script filters out daily-notes and action-items sections (those are extracted separately by Post-Enrich & Archive and shouldn't steer the title/summary), then caps very long documents at a head+tail window (6000 words from the front + 2000 from the back + a truncation marker). Covers markdown, txt, rtf, PDFs (post-OCR), and HTML — any record with populated `plain text` goes through this path.
2. **Input-hash cache** — After a successful enrichment, the SHA-256 of `recName + filteredText` is stored in `EnrichInputHash`. If `AIEnriched` gets reset to 0 later (manual retry, `ErrorCount` cleanup, etc.) and the content hasn't changed, the rule skips the LLM call entirely — applied fields from the prior successful run stay in place, just `AIEnriched` flips back to 1. To force a fresh LLM call on otherwise-unchanged content, clear the `EnrichInputHash` field too.

The script reads each field from the record and applies it:

- **Title** — If the document already has a clear, descriptive title (from its filename or a heading within the content), the AI preserves it as-is. A new title is only generated when the existing name is generic (e.g. "Untitled", "IMG_0042"). The `NameLocked` flag prevents the rename from overwriting names that were set intentionally (see [#after-renaming-lock-name](##after-renaming-lock-name---lock-name-on-rename)).
- **Event Date** (`eventDate`) — Set if and only if the document is anchored to a single specific calendar date (e.g. a receipt, a bill, a meeting, a call, a journal entry, an appointment). Not set for documents that span a period or have no specific date (e.g. W-2, annual report, manual, walkthrough, bookmark, contract). The AI will not construct or infer a date from a referenced period. The date comes from content; the file creation/modification date is used as a fallback only when the content doesn't state it explicitly. The date is prepended to the title by the script (not the AI) and stored in the `EventDate` custom metadata field.
- **Type** — A document type label (e.g. "Receipt", "Manual", "Meeting Notes") stored in the `DocumentType` custom metadata field, separate from topical tags.
- **Tags** — 1–3 topical/thematic tags, deduplicated against existing tags before appending.
- **Summary** — A 1–2 sentence summary stored in the `Summary` custom metadata field.
- **Low Confidence** — A boolean flag stored in `LowConfidence` when the AI determines the content is too unclear for reliable extraction. Useful for filtering records that need manual review.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is On
  - Commented is On
  - AIEnriched is Off
- Trigger
  - Every Minute
- Actions
  - Execute Script (AppleScript, embedded) — see [`enrich-ai-metadata.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/enrich-ai-metadata.applescript)

### Post-Enrich & Archive

Runs after AI enrichment completes. Performs five steps in a single pass:

1. **Action Items** — Parses the document for sections titled "Action Items", "Todos", or similar, and sends any bulleted tasks to Things 3 via AppleScript. Deduplication is handled via `PreviousTasks`. Skipped for web clip records (those with `WebClipSource` set).
2. **Daily Notes** — Extracts "Daily Notes", "Today", "Journal", or "Log" sections from handwritten documents and appends them to today's daily note. Also appends a wikilink for any document with an `EventDate` to the respective date's daily note. Deduplication is handled via `PreviousDailyNotes` and `DailyNoteLinked`. Skipped for web clip records.
3. **Sync H1** — For markdown documents, ensures the first `# Heading` matches the record's filename (minus extension). If the H1 differs it's replaced; if absent it's injected after any YAML frontmatter. This guarantees the AI-enriched title is reflected in the document body.
4. **Web clip name propagation** — For markdown web clips (records with `WebClipSource` set), if the linked bookmark or HTML snapshot still carries a `"No title"` placeholder name, propagate the markdown's (post-enrichment) name to it. See [Untitled-page fallback](#untitled-page-fallback). `NameLocked` is set on the sibling _before_ the rename so `After Renaming, Lock Name` doesn't double-fire.
5. **Archive** — Moves the record to `99_ARCHIVE` and clears `NeedsProcessing`. The move happens first; the flag is only cleared on success to prevent silent data loss.

This consolidates the previous Extract: Action Items, Process: Daily Notes, and Archive: Processed Items rules into one script, eliminating two "Every Minute" polling rules.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is On
  - Commented is On
  - AIEnriched is On
- Trigger
  - Every Minute
- Actions
  - Execute Script (AppleScript, external) — see [`post-enrich-and-archive.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/post-enrich-and-archive.applescript)

### Process: Jots

Handles jot documents created from the Drafts **Quick Jot** action on iOS. Each jot arrives as a small markdown document matched by `IsJot=1` custom metadata or a `Jot ` name prefix (DTTG's `x-callback-url` scheme can't set custom metadata, so iOS jots carry the prefix instead), with the body already formatted as a timestamped bullet (e.g. `- 7:19am: Look into Fyxer AI`). The rule inserts the jot into the matching daily note's body (before `## Today's Notes`), deduplicates by content, and trashes the jot document.

On macOS the Drafts action modifies the daily note directly via AppleScript, so this rule only fires for jots created on iOS via DEVONthink To Go's `x-callback-url` scheme and synced back.

- Search in
  - Global Inbox
- Criteria
  - Any of: IsJot is On **or** Name begins with "Jot "
  - Kind is Markdown
- Trigger
  - On Import
  - Every Minute
- Actions
  - Execute Script (AppleScript, embedded) — see [`process-jots.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/process-jots.applescript)

### After Renaming, Lock Name

Automatically sets `NameLocked` whenever a document is renamed outside the pipeline (e.g. by the user in DEVONthink or Finder). This prevents Enrich: AI Metadata from overwriting an intentional name the next time the document is processed.

AI-initiated renames from Enrich: AI Metadata's own AppleScript are **not** caught by this rule because it sets `NameLocked=1` _before_ performing the rename, so the criteria below (`NameLocked is Off`) no longer match by the time the rename event fires.

- Search in
  - Lorebook (entire database)
- Criteria
  - Kind is Any Document
  - NameLocked is Off
- Trigger
  - On Renaming
- Actions
  - Change NameLocked to 1

> **Scope Note.** This rule searches the entire Lorebook database, not just `00_INBOX`. That means renaming a document in `99_ARCHIVE` (or any other group) will also lock its name — which is the desired behaviour, since it protects the name if the document is ever re-processed.

### Util: Metadata Cleanup

Hourly catch-all for trivial metadata inconsistencies that accumulate over time (manual edits, cross-device sync, retired flags, etc.). Each cleanup is a self-contained script handler that re-checks its own preconditions; the DT-level criteria are the explicit union of currently-handled cases, so DT pre-filters to just the records that need work and yields zero in normal state.

Current cleanups:

- **`SkipSingleFile` vs `NeedsSingleFile`** — if both are 1, clears `NeedsSingleFile`. Skip is the user's "off switch" and wins by design. To force-capture a previously-skipped bookmark, clear `SkipSingleFile` first, or select the record and run the on-demand rule (selection mode bypasses both flags).

- Search in
  - Lorebook (entire database)
- Criteria
  - Kind is Bookmark
  - SkipSingleFile is On
  - NeedsSingleFile is On
- Trigger
  - Hourly
- Actions
  - Execute Script (AppleScript, external) — see [`util-metadata-cleanup.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/util-metadata-cleanup.applescript)

> **Adding a new cleanup case.** Add an `OR` group to the DT criteria covering the new case's preconditions (DT 4 supports nested AND/OR groups), then add a new `my cleanupX(theRecord)` call inside `performSmartRule` plus the corresponding handler in the script. The handler should re-check preconditions defensively so it's safe even if DT yields a record that matches a different case's criteria.

### Restore Previous Name

Reverts a document's filename to the value stored in `PreviousName` (the name it had just before the most recent AI rename). After restoring, it clears `PreviousName` so the rule no longer matches the record. `NameLocked` stays **On** so Enrich: AI Metadata won't overwrite the restored name.

> **Tip — Re-running AI enrichment.** If you want the AI to take another shot at naming a document after restoring its old name, also clear `NameLocked` and `AIEnriched` on that record so it re-enters the AI pipeline.

- Search in
  - Lorebook (entire database)
- Criteria
  - Kind is Any Document
  - PreviousName is not empty
- Trigger
  - On Demand
- Actions
  - Execute Script (AppleScript, embedded) — see [`util-restore-previous-name.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/util-restore-previous-name.applescript)

> **Hardening Note — Why an AppleScript instead of declarative move actions**
>
> An earlier version used two declarative actions: `Change NeedsProcessing to 0` followed by `Move to 99_ARCHIVE`. If the move failed silently, the flag was already cleared, so the rule would never re-match. The AppleScript replacement moves **first**, then clears the flag only on success.

### Other utility rules

The remaining seeded rules are small conveniences. Most use embedded scripts or declarative actions whose definitions travel only in the seeded `SmartRules.plist`, so the notes here are intent-level — open the rule in DT for exact criteria. To refresh the seed after editing any rule, run `scripts/dump-devonthink-seed.sh` and commit.

- **Add/Update H1** (on demand) and **After Renaming Markdown, Add/Update H1** (automatic) — insert or refresh a markdown record's H1 from its name; the automatic variant fires after a rename so the body heading tracks the new name.
- **After Saving Markdown, Sync H1 and Filename** (automatic) — the bidirectional H1↔name sync: renames the record to match its first H1, or inserts/updates the H1 from the filename, skipping YAML frontmatter. External script: [`sync-h1-and-filename.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/sync-h1-and-filename.applescript).
- **After Labelling, Move to 99_ARCHIVE** (automatic) — declarative: putting a label on a record archives it.
- **Skip SingleFile (On Demand)** — flips selected bookmarks to `SkipSingleFile=1`, the manual opt-out described in the `SkipSingleFile` metadata row.
- **Prepare for Re-Enrichment** (on demand) — clears enrichment state on the selection so Enrich: AI Metadata takes a fresh pass (the workflow the `EnrichInputHash` row describes).
- **Markdownify Text** (on demand) — converts the selected records to markdown.
- **Convert User-AI Conversation to Reference** (on demand) — reshapes a captured user↔AI conversation record into a reference document.
- **Lint Markdown (On Demand)** — manual invocation of the markdown lint pass ([`lint-markdown.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/lint-markdown.applescript)).
- **Prose Check** and **Summarize** (on demand) — front doors to the Claude Code skills; they hand the selection to the skill in the background ([`prose-check-on-demand.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/prose-check-on-demand.applescript), [`summarize-on-demand.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/summarize-on-demand.applescript); see [docs/summarize.md](docs/summarize.md)).
- **Capture Bookmarks Batch (On Demand)** — documented under [Invoking the batch from DT](#invoking-the-batch-from-dt-optional).

DT-stock rules (Reminders, Filter Duplicates, Bates Numbering, chat-suggestion helpers, etc.) are not part of the pipeline and aren't documented here.

## Handle Updated Notebooks AppleScript

Runs as the first action in Sweep: Lorebook Inbox. Only processes records where `Handwritten` is already set (by the Boox import watcher), so non-Boox documents pass through untouched. Handles the "same notebook, updated content" case by matching on SourceFile metadata. If an existing document is found, its content is replaced in-place (preserving UUID, name, tags, and links). The script then resets the document's state flags (`Recognized=0`, `Commented=0`, `AIEnriched=0`, `NeedsProcessing=1`) so it runs back through the pipeline for fresh OCR, formatted comments, and a new summary. Crucially, it sets `NameLocked=1` so the AI enrichment step doesn't overwrite its filename, preserving any existing WikiLinks. Finally, the new import is moved to the database Trash (recoverable if the SourceFile match was wrong). If no match is found, the document is tagged with SourceFile metadata and the sweep continues into the normal pipeline.

See [`handle-updated-notebooks.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/handle-updated-notebooks.applescript).

## Daily Notes (Scheduled)

A daily note is automatically created in the **10_DAILY** group of the Lorebook database every morning at 6:15 AM local. The mechanism uses `launchd` (macOS's native scheduler) to run a shell script that talks to DEVONthink via AppleScript.

The schedule is set to a daytime wake-hour rather than the small hours: `StartCalendarInterval` does not fire while the Mac is asleep (and `WakeFromSleep` is not reliable when the lid is closed in standby), so an early-morning trigger that the user is consistently around for is more reliable than a 03:00 trigger that gets silently skipped. If the trigger is still missed (rare), the script's no-arg backfill path seeds today's note the next time it runs.

### How It Works

1. `launchd` fires the job at 06:15 every day.
2. The shell script computes today's date, builds the markdown content from an embedded template, and calls `osascript`.
3. The AppleScript block checks whether a note with today's filename already exists in 10_DAILY — if so it exits cleanly (idempotent). Otherwise it creates the new markdown record.
4. If a note was created, the script triggers a DEVONthink cloud sync (`synchronize database`) so the note is available on other devices immediately.
5. All activity is logged to `~/Library/Logs/dt-daily-note.log`.

### Pipeline Integration

The primary DEVONthink smart rule pipeline integrates directly with daily notes via the **Process: Daily Notes** step:

- **Extracting Daily Logs:** For handwritten notes, the pipeline searches for headers like "Daily Notes", "Today", "Journal", or "Log". If found, it extracts the content beneath them and automatically appends it to today's daily note. Deduplication ensures that repeated notebook updates don't result in duplicated entries.
- **Linking Temporal Events:** For any document processed by the pipeline, if the AI enrichment step identified a specific `EventDate` (e.g., from meeting notes), the pipeline automatically appends a wikilink to that document on the daily note corresponding to that specific date. If the target note doesn't exist yet — a past/future `EventDate`, or a morning where the 6:15 AM `create-daily-note.sh` run was missed — Post-Enrich & Archive creates it on demand (matching `create-daily-note.sh`'s heading and `Daily Note` tag) rather than dropping the link. Extract: Web Content and `ingest-singlefile-html.py` create today's note on demand the same way, so captures landing between midnight and the 06:15 seeder keep their daily-note entry.

### Template Format

Each daily note follows this structure (generated by `create-daily-note.sh`, and by the rules that create a missing note on demand):

```
# Wednesday, January 21, 2026

-
```

- **Heading** — full day-of-week, month, day, year.
- **Bullet** — empty starter bullet for quick capture.
- **Tag** — the DT tag `Daily Note` is applied to the record via AppleScript (not embedded in the document body), enabling smart groups and filtering.

### create-daily-note.sh

Source: [`stow/devonthink/.local/bin/create-daily-note.sh`](../stow/devonthink/.local/bin/create-daily-note.sh). Stowed to `~/.local/bin/create-daily-note.sh`.

### launchd Plist

Template (tracked): [`stow/devonthink/Library/LaunchAgents/com.user.dt-daily-note.plist.template`](../stow/devonthink/Library/LaunchAgents/com.user.dt-daily-note.plist.template). The generated plist (gitignored) is rendered to the same directory by `scripts/build-launchd-plists.sh`, which substitutes `__HOME__`, and is then stowed to `~/Library/LaunchAgents/com.user.dt-daily-note.plist`.

### Installation

Both the script and the launchd job are installed as part of the standard bootstrap — there is nothing to copy by hand:

```bash
./scripts/setup.sh
```

That run will (when you opt into the DEVONthink pipeline at the prompt):

1. Run `scripts/build-launchd-plists.sh` to render `*.plist.template` → `*.plist` with `__HOME__` expanded.
2. Stow `devonthink` so `~/.local/bin/create-daily-note.sh` and `~/Library/LaunchAgents/com.user.dt-daily-note.plist` become symlinks back into the repo.
3. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.dt-daily-note.plist` to load the job (idempotent; "already loaded" is tolerated).

To re-render the plist after editing the template:

```bash
./scripts/build-launchd-plists.sh
launchctl kickstart -k "gui/$(id -u)/com.user.dt-daily-note"   # pick up the new plist
```

To unload:

```bash
launchctl bootout "gui/$(id -u)/com.user.dt-daily-note"
```

To test the script directly (writes today's daily note if missing, then exits):

```bash
~/.local/bin/create-daily-note.sh
```

### Backfilling Missed Dates

If the Mac was offline for several days you can backfill:

```bash
for d in 2026-01-18 2026-01-19 2026-01-20; do
  ~/.local/bin/create-daily-note.sh "$d"
done
```

### Notes

- **Idempotency** — The script checks for an existing note with the same filename before creating. Running it twice for the same date is harmless.
- **Sleep/wake** — `StartCalendarInterval` does *not* fire while the Mac is asleep, and it does not catch up on wake unless `WakeFromSleep` is set (which is unreliable in clamshell/S5 standby). The 06:15 schedule is chosen so the user is typically around. If the trigger is still missed, the script's no-arg backfill path will create today's note the next time it runs (idempotent).
- **DEVONthink must be running** — The AppleScript targets `application id "DNtp"`. DEVONthink does not need to be frontmost, but it must be launched. The `dt-watchdog` launchd job (fires every 5 minutes) keeps DT and Maestral running, so this is generally not something to worry about.
- **Cloud sync** — After creating a note, the script calls `synchronize database` to push it to DEVONthink's configured sync store. If sync fails for any reason (e.g., no network), the note is still created locally and will sync on the next automatic or manual sync cycle.
- **Logging** — Check `~/Library/Logs/dt-daily-note.log` for creation results and `/tmp/dt-daily-note.log` for any launchd-level stdout/stderr.

## Database Backup & Recovery

Nothing in this repo backs up `~/Databases/Lorebook.dtBase2` — the repo rebuilds the *machinery* (scripts, agents, seeded rules), not the data. The database survives via two independent channels:

1. **CloudKit sync** — continuous, and the recovery path for a single-machine loss. Script-driven sync also runs after each daily-note creation.
2. **Time Machine** — the package is included in the hourly backup (verified 2026-07-03; local destination `MacBookBackup`). Caveat: TM snapshots the package while DT may be mid-write, so a restored copy should get **Tools → Verify & Repair** before trusting it. For a consistency-guaranteed archive (e.g. before risky bulk operations), use **File → Export → Database Archive**, which verifies and zips the closed database.

A *sync-store* loss plus a dead machine is the only scenario with no automated answer; the Time Machine copy is the fallback there.

## Integrations

- [Granola Integration](docs/granola.md) — automated meeting notes import from Granola
- [GitHub Stars Integration](docs/github-stars.md) — automated bookmark import for starred repos
- [Summarize Skill](docs/summarize.md) — on-demand content summarization via Claude Code
