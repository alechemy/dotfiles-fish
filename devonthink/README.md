# DEVONthink PKM Pipeline

Behold my needlessly complex, fully-automated document-processing pipeline. It's built on DEVONthink 4 smart rules, running on a headless Mac mini. Documents enter from multiple sources (handwritten Boox notebooks via Dropbox, web bookmarks, scans, manual imports) and flow through a series of gated processing steps: OCR/transcription, LLM-powered metadata enrichment (title, date, tags, summary), action item extraction to Things 3, and daily note aggregation. Each step is controlled by boolean metadata flags so that documents can be re-processed or debugged independently.

Pipeline scripts live in [`../stow/devonthink/`](../stow/devonthink/) (stowed to `$HOME` via GNU Stow). Standalone utilities and documentation live here.

## Custom Metadata Setup

The following custom metadata fields must be created in DEVONthink before the pipeline will work (Settings → Data → Custom Metadata):

| Field        | Type       | Purpose                                                    |
| ------------ | ---------- | ---------------------------------------------------------- |
| Handwritten  | Boolean    | Set at import by the Hazel rule for Boox handwritten notes. Gates OCR processing (Extract: Boox Handwritten) and notebook dedup (Handle Updated Notebooks) |
| NeedsProcessing | Boolean | Tracks whether a record requires processing through the pipeline |
| Recognized   | Boolean    | Tracks whether OCR/transcription has been run on a record  |
| Commented    | Boolean    | Tracks whether text has been mirrored to comment field     |
| AIEnriched  | Boolean    | Tracks whether the combined AI enrichment step (rename + tag + summarize) has been run on a record |
| NameLocked   | Boolean    | When On, prevents Enrich: AI Metadata from overwriting the document's name. Set automatically by the on-rename guard rule, by Handle Updated Notebooks for intentionally-named Boox notes, and by Enrich: AI Metadata itself after a successful AI rename |
| Summary      | Text       | Stores a brief AI-generated summary of the document's content |
| DocumentType | Text       | Stores the AI-assigned document type label (e.g. "Receipt", "Invoice", "Meeting Notes", "Manual") |
| EventDate    | Text       | Stores the event date in yyyy-mm-dd format for time-bound documents (e.g. meeting notes, calls). Extracted from content when available, otherwise from document metadata |
| LowConfidence | Boolean   | Flagged by the AI when document content is too unclear or ambiguous to produce a reliable title and summary. Use for filtering records that need manual review |
| PreviousName | Text       | Stores the document's name before the most recent AI rename, enabling a one-step revert |
| SourceFile   | Identifier | Stores the original Boox filename (without extension) as a stable dedup key |
| RecognizedAt | Date       | Timestamp set before OCR begins by Extract: Boox Handwritten. Used by Format: Boox Comments to detect timeout if `plain text` is never populated (the AI-driven "Recognize" rule action is async, unlike the builtin "OCR" action). Not used by the standard OCR path, which is synchronous. |
| EnrichStartedAt | Date    | Timestamp set by Enrich: AI Metadata on first attempt, used to enforce a 5-minute timeout so records aren't stuck retrying indefinitely |
| ErrorCount   | Integer    | Tracks the number of times a document has timed out or failed processing in a pipeline step |
| TasksExtracted | Boolean    | Tracks whether action items have been parsed and sent to Things 3 |
| PreviousTasks  | Multi-line Text | Stores a newline-separated list of tasks already sent to Things 3 to prevent duplicates on notebook updates |
| DailyNotesProcessed | Boolean | Tracks whether "Daily Notes" sections have been extracted and wikilinks have been appended |
| DailyNoteLinked | Boolean   | Tracks whether a document has been linked to its respective EventDate's daily note |
| PreviousDailyNotes | Multi-line Text | Stores a newline-separated list of extracted daily notes to prevent duplicates on notebook updates |
| WebClipSource | Item Link | Set on derived records (markdown, HTML snapshot) by Extract: Web Content; points back to the original bookmark record |
| WebClipMarkdown | Item Link | Set on the bookmark by Extract: Web Content; points to the readable markdown record |
| WebClipSnapshot | Item Link | Set on the bookmark by Extract: Web Content; points to the HTML snapshot record in 99_ARCHIVE |

> **Migration Note — `AI-Renamed` retired.** The earlier `AI-Renamed` boolean flag has been replaced by `NameLocked`. If any existing records still carry `AI-Renamed` metadata, you can safely ignore or batch-clear it; it is no longer referenced by any rule or script.

## Boox -> Dropbox -> Mac

- After closing a document, Boox exports a vector PDF
- Boox uploads PDF to Dropbox folder
- On Mac, Dropbox folder is mapped via Maestral to a local directory, "Notebooks"

## Hazel

- "Notebooks" (Dropbox destination for Boox PDF exports)
  - Rule: Convert Boox PDFs to TIFFs and Import to DEVONthink
    - Conditions
      - Kind is PDF
    - Actions
      - Run shell script — see [`utils/hazel-boox-import.sh`](utils/hazel-boox-import.sh)
      - Note: ImageMagick requires Ghostscript to decode PDFs. If you get a "no decode delegate" error, install it via `brew install ghostscript`.

## DT Smart Rules

### Sweep: Global Inbox

- Search in
  - Global Inbox
- Criteria
  - Kind is Any Document
- Trigger
  - Every Minute
  - On Import
  - On Moving
- Actions
  - Change NeedsProcessing to 1
  - Move to 00_INBOX

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

> See [Handle Updated Notebooks AppleScript](#handle-updated-notebooks-applescript). This script only runs in Sweep: Lorebook Inbox, where Boox TIFFs arrive with `Handwritten=1` already set by the Hazel import. If the incoming document is an update to an existing notebook, the script replaces the existing document's content, resets its pipeline state (`Recognized=0`, `Commented=0`, `AIEnriched=0`, `TasksExtracted=0`, `DailyNotesProcessed=0`, `NeedsProcessing=1`) to generate a fresh AI summary, pins its name (`NameLocked=1`) to protect existing WikiLinks, and deletes the new import — so the remaining actions in the sweep rule are never reached. If the document is new, the script sets SourceFile metadata and allows the sweep to continue normally.

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

### Extract: Boox Handwritten

Runs OCR on handwritten Boox notes. A small AppleScript timestamps the record (via `RecognizedAt`) before recognition begins so that the downstream formatting rule (Format: Boox Comments) can detect if OCR stalls. The `Handwritten` flag is set at import by the Hazel rule, so this rule only matches documents that originated from the Boox → Dropbox → Hazel path.

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
  - *(No declarative "Change Commented" action — the script handles it conditionally)*

### Extract: Scans & Images

Documents not flagged as handwritten Boox notes but lacking a text layer (e.g., flat PDFs, screenshots, photos of receipts) are run through standard OCR. Because DT's built-in OCR is synchronous, both `Recognized` and `Commented` are set in the same rule immediately after OCR completes — no separate verification step is needed.

Use "OCR - Apply" (not "OCR - To searchable PDF" or "OCR & Continue"). The latter two create a new record imported to the Global Inbox, which the Sweep rule picks up and recycles through this rule indefinitely, producing one new duplicate per minute.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Handwritten is Off
  - Recognized is Off
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

Intercepts Bookmark records and downloads the page content in two formats using external CLI tools:

1. **Faithful HTML snapshot** (`monolith`) — single-file archive with all CSS, images, and fonts inlined as `data:` URIs. JavaScript is stripped (`-j`) and the document is isolated from the network (`-I`).
2. **Readable markdown** (`defuddle`) — clean extracted article content, ideal for AI enrichment and search.

The **markdown** is imported to `00_INBOX` with `NeedsProcessing=1` and flows through the full pipeline (lint → AI enrichment → archive). It is the only record that incurs an AI enrichment call. The **HTML snapshot** is imported directly to `99_ARCHIVE` — it's an archival backup and doesn't need AI processing. The **original bookmark** is kept as a lightweight live link; all its pipeline flags are set so it archives alongside the derived records. All three records share the same URL metadata, which serves as the natural join key.

If both downloads fail (e.g. the page requires JavaScript rendering), the bookmark is passed through as-is by setting `Recognized=1` and `Commented=1`, so it reaches AI enrichment with minimal content rather than getting stuck.

> **SPA Caveat.** `monolith` fetches server-rendered HTML. Pages that render entirely via client-side JavaScript (e.g. Twitter/X, some SPAs) will produce empty or skeleton content. For these, the bookmark fallthrough ensures the URL is still captured and enriched.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is Off
  - Kind is Bookmark
- Trigger
  - Every Minute
- Actions
  - Run AppleScript (external) — see [`extract-web-content.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/extract-web-content.applescript)

> **Dependencies.** `monolith` (Homebrew: `brew install monolith`) and `defuddle` (npm: `npm install -g defuddle`, resolved via mise shims). Both must be accessible from AppleScript's `do shell script` — the script sets `PATH` to include `/opt/homebrew/bin` and `$HOME/.local/share/mise/shims`.

### Extract: Native Text Bypass (True Fast-Track)

Documents that are natively text-based (Markdown, RTF, Web Archives, or PDFs that already contain a text layer) bypass OCR entirely. This rule flips both flags in a single pass and advances the document straight to AI enrichment. For Markdown files, a lint step runs `markdownlint --fix` (with tab-to-space conversion) on the backing file before the flags are set, ensuring house style compliance before downstream processing.

Bookmarks are excluded because they are handled by the preceding Extract: Web Content rule, which downloads page content and replaces the bookmark with richer document types.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Handwritten is Off
  - Recognized is Off
  - Kind is not Image
  - Kind is not PDF/PS
  - Kind is not Bookmark
- Trigger
  - Every Minute
  - On Import
- Actions
  - Run AppleScript (embedded) — see [`lint-markdown.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/lint-markdown.applescript)
  - Change Recognized to 1
  - Change Commented to 1

### Enrich: AI Metadata

A single LLM call per document generates a title, event date, document type, tags, summary, and confidence flag in one structured JSON response. The embedded AppleScript calls `get chat response` directly with `as "JSON"` so DEVONthink returns a native AppleScript record — no `jq` parsing or markdown-fence stripping needed. The script reads each field from the record and applies it:

- **Title** — If the document already has a clear, descriptive title (from its filename or a heading within the content), the AI preserves it as-is. A new title is only generated when the existing name is generic (e.g. "Untitled", "IMG_0042", "Notebook-7"). The `NameLocked` flag prevents the rename from overwriting names that were set intentionally (see [#util-lock-name-on-rename](##util-lock-name-on-rename---lock-name-on-rename)).
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
  - AI-Enriched is Off
- Trigger
  - Every Minute
- Actions
  - Execute Script (AppleScript, embedded) — see [`enrich-ai-metadata.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/enrich-ai-metadata.applescript)

### Extract: Action Items

Parses the document's recognized text (or comments, for handwritten notes) using a Python regex script. Searches for sections titled "Action Items", "Todos", or similar, and sends any bulleted tasks directly to Things 3 via AppleScript. Deduplication is handled via `PreviousTasks`.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Recognized is On
  - Commented is On
  - AI-Enriched is On
  - TasksExtracted is Off
  - WebClipSource is empty
- Trigger
  - Every Minute
- Actions
  - Execute Script (AppleScript, embedded) — see [`extract-action-items.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/extract-action-items.applescript)

### Process: Daily Notes

Extracts "Daily Notes", "Today", "Journal", or "Log" sections from handwritten documents and appends them to today's daily note. Also checks all documents for an `EventDate` (assigned during AI enrichment); if present, it appends a wikilink to this document on the respective date's daily note. Deduplication is handled via `PreviousDailyNotes` and `DailyNoteLinked`.

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - AI-Enriched is On
  - DailyNotesProcessed is Off
  - WebClipSource is empty
- Trigger
  - Every Minute
- Actions
  - Execute Script (AppleScript, embedded) — see [`append-to-daily-notes.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/append-to-daily-notes.applescript)

### Util: Lock Name on Rename

Automatically sets `NameLocked` whenever a document is renamed outside the pipeline (e.g. by the user in DEVONthink or Finder). This prevents Enrich: AI Metadata from overwriting an intentional name the next time the document is processed.

AI-initiated renames from Enrich: AI Metadata's own AppleScript are **not** caught by this rule because it sets `NameLocked=1` *before* performing the rename, so the criteria below (`NameLocked is Off`) no longer match by the time the rename event fires.

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

### Util: Restore Previous Name

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
  - Execute Script (AppleScript, embedded) — see [`utils/util-restore-previous-name.applescript`](utils/util-restore-previous-name.applescript)

### Archive: Processed Items

- Search in
  - 00_INBOX
- Criteria
  - NeedsProcessing is On
  - Label is not "Needs Review"
  - Recognized is On
  - Commented is On
  - AI-Enriched is On
  - TasksExtracted is On
  - DailyNotesProcessed is On
- Trigger
  - Every Minute
- Actions
  - Execute Script (AppleScript, embedded) — see [`archive-processed-items.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/archive-processed-items.applescript)

> **Hardening Note — Why an AppleScript instead of declarative actions**
>
> The previous version used two declarative actions: `Change NeedsProcessing to 0` followed by `Move to 99_ARCHIVE`. If the move failed silently (which DEVONthink's declarative actions can do — there is no error propagation), the flag was already cleared, so Archive: Processed Items would never re-match the record. Combined with the fact that AI tag application creates replicants in tag groups, a failed move could remove the record from `00_INBOX` without placing it in `99_ARCHIVE`, leaving it as a "tag-only item" — visible only by browsing to its tag(s).
>
> The AppleScript replacement moves **first**, then clears the flag only on success. If the move throws, `NeedsProcessing` stays `On` and Archive: Processed Items will retry on the next poll. Failures are logged to DEVONthink's Log window for visibility.

> **Design Note — Optional Review Gate**
>
> Previously, this rule set the label to "Needs Review" and left documents in `00_INBOX` until a separate rule (`03 - After Review, Move to 99_ARCHIVE`) detected the label had been cleared. In practice the auto-ingestion and classification steps are reliable enough that manual review is unnecessary for the vast majority of documents.
>
> If you later want to re-introduce a review gate for **low-confidence** classifications, you could:
> 1. Add a confidence-check step before archiving that sets Label to "Needs Review" when the AI enrichment response is malformed, empty, or suspiciously generic (e.g., title is "Document", tags are empty).
> 2. Re-add a `Sweep` rule that watches `00_INBOX` for documents whose label is *not* "Needs Review" and moves them to `99_ARCHIVE` on labelling.
> 3. Have the archive rule skip the move for any document already labelled "Needs Review", leaving it in the inbox for manual triage.

## Handle Updated Notebooks AppleScript

Runs as the first action in each sweep rule. Only processes records where `Handwritten` is already set (by the Hazel import rule), so non-Boox documents pass through untouched. Handles the "same notebook, updated content" case by matching on SourceFile metadata. If an existing document is found, its content is replaced in-place (preserving UUID, name, tags, and links). The script then resets the document's state flags (`Recognized=0`, `Commented=0`, `AIEnriched=0`, `TasksExtracted=0`, `DailyNotesProcessed=0`, `NeedsProcessing=1`) so it runs back through the pipeline for fresh OCR, formatted comments, and a new summary. Crucially, it sets `NameLocked=1` so the AI enrichment step doesn't overwrite its filename, preserving any existing WikiLinks. Finally, the new import is deleted. If no match is found, the document is tagged with SourceFile metadata and the sweep continues into the normal pipeline.

See [`handle-updated-notebooks.applescript`](../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/handle-updated-notebooks.applescript).

## Daily Notes (Scheduled)

A daily note is automatically created in the **10_DAILY** group of the Lorebook database every morning at 3:00 AM. The mechanism uses `launchd` (macOS's native scheduler) to run a shell script that talks to DEVONthink via AppleScript.

### How It Works

1. `launchd` fires the job at 03:00 every day (or on next wake if the Mac was asleep).
2. The shell script computes today's date, builds the markdown content from an embedded template, and calls `osascript`.
3. The AppleScript block checks whether a note with today's filename already exists in 10_DAILY — if so it exits cleanly (idempotent). Otherwise it creates the new markdown record.
4. If a note was created, the script triggers a DEVONthink cloud sync (`synchronize database`) so the note is available on other devices immediately.
5. All activity is logged to `~/Library/Logs/dt-daily-note.log`.

### Pipeline Integration

The primary DEVONthink smart rule pipeline integrates directly with daily notes via the **Process: Daily Notes** step:

- **Extracting Daily Logs:** For handwritten notes, the pipeline searches for headers like "Daily Notes", "Today", "Journal", or "Log". If found, it extracts the content beneath them and automatically appends it to today's daily note. Deduplication ensures that repeated notebook updates don't result in duplicated entries.
- **Linking Temporal Events:** For any document processed by the pipeline, if the AI enrichment step identified a specific `EventDate` (e.g., from meeting notes), the pipeline automatically appends a wikilink to that document on the daily note corresponding to that specific date.

### Template Format

Each daily note follows this structure (see `Daily Note.md` for a standalone DEVONthink template):

```
# Wednesday, January 21, 2026

-
```

- **Heading** — full day-of-week, month, day, year.
- **Bullet** — empty starter bullet for quick capture.
- **Tag** — the DT tag `type/daily` is applied to the record via AppleScript (not embedded in the document body), enabling smart groups and filtering.

### create-daily-note.sh

Install location: `~/.local/bin/create-daily-note.sh` (must be `chmod +x`). See [`create-daily-note.sh`](../stow/devonthink/.local/bin/create-daily-note.sh).

### launchd Plist

File: `~/Library/LaunchAgents/com.user.dt-daily-note.plist`. See [`com.user.dt-daily-note.plist`](../stow/devonthink/Library/LaunchAgents/com.user.dt-daily-note.plist).

### Installation

```bash
# 1. Install the script
sudo cp create-daily-note.sh ~/.local/bin/create-daily-note.sh
sudo chmod +x ~/.local/bin/create-daily-note.sh

# 2. Install the launchd plist
cp com.user.dt-daily-note.plist ~/Library/LaunchAgents/

# 3. Load the job (takes effect immediately; first run at next 03:00)
launchctl load ~/Library/LaunchAgents/com.user.dt-daily-note.plist

# 4. (Optional) Test it right now
~/.local/bin/create-daily-note.sh
```

To unload: `launchctl unload ~/Library/LaunchAgents/com.user.dt-daily-note.plist`

### Backfilling Missed Dates

If the Mac was offline for several days you can backfill:

```bash
for d in 2026-01-18 2026-01-19 2026-01-20; do
  ~/.local/bin/create-daily-note.sh "$d"
done
```

### Notes

- **Idempotency** — The script checks for an existing note with the same filename before creating. Running it twice for the same date is harmless.
- **Sleep/wake** — `launchd` with `StartCalendarInterval` will fire the job on the next wake after a missed interval, so the note will still be created even if the Mac was asleep at 03:00.
- **DEVONthink must be running** — The AppleScript targets `application id "DNtp"`. DEVONthink does not need to be frontmost, but it must be launched. On the Mac mini server this is already the case since the rest of the pipeline depends on it.
- **Cloud sync** — After creating a note, the script calls `synchronize database` to push it to DEVONthink's configured sync store. If sync fails for any reason (e.g., no network), the note is still created locally and will sync on the next automatic or manual sync cycle.
- **Logging** — Check `~/Library/Logs/dt-daily-note.log` for creation results and `/tmp/dt-daily-note.log` for any launchd-level stdout/stderr.
