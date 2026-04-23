# Pipeline Cleanup Plan

Synthesized from two independent reviews. Tasks are ordered by dependency, grouped by
theme, and rated by impact vs. effort.

---

## 1. Consolidate post-enrichment rules into a single script

**Impact: High | Effort: Medium | Risk: Low**

Currently three separate "Every Minute" smart rules run sequentially after AI enrichment,
chained by `TasksExtracted` and `DailyNotesProcessed` gate flags:

1. Extract: Action Items (sets `TasksExtracted=1`)
2. Process: Daily Notes (sets `DailyNotesProcessed=1`)
3. Archive: Processed Items (moves to `99_ARCHIVE`)

Since action items and daily notes are already idempotent (via `PreviousTasks` and
`DailyNoteLinked`), they can safely run in a single pass. The combined script checks
`WebClipSource` internally to skip action items and daily notes for web clip records
(replacing the current `WebClipSource is empty` smart rule criteria).

### What changes

- **New script:** `post-enrich-and-archive.applescript` — runs action items (if
  applicable), daily notes (if applicable), then archives. Replaces all three scripts.
- **Smart rule:** Modify Archive: Processed Items criteria to remove `TasksExtracted is On`
  and `DailyNotesProcessed is On`. Rename to "Post-Enrich & Archive". Delete the Extract:
  Action Items and Process: Daily Notes rules.
- **Flag retirement:** `TasksExtracted` and `DailyNotesProcessed` are no longer used as
  pipeline gates. Remove from all scripts that set them for fast-tracking:
  - `extract-web-content.applescript` — remove `TasksExtracted=1`
  - `process-singlefile-import.applescript` — remove `TasksExtracted=1` and
    `DailyNotesProcessed=1`
  - `handle-updated-notebooks.applescript` — remove the resets of these two flags (resetting
    `AIEnriched=0` is sufficient to re-enter the combined rule)
- **README.md / CLAUDE.md:** Update pipeline documentation, retire the two flags from the
  custom metadata table.

### Result

- 2 fewer "Every Minute" smart rules polling DEVONthink
- 2 fewer custom metadata fields to manage
- Simpler fast-tracking logic in other scripts (fewer flags to set)

### Caveats

- Things 3 failures during action item extraction should not block archiving. Wrap the
  Things 3 AppleScript call in its own `try` block so a failure logs an error but doesn't
  prevent daily notes or archiving from proceeding.
- You lose the ability to re-run _just_ action items or _just_ daily notes by clearing a
  single flag. In practice this is fine — resetting `AIEnriched=0` re-runs the full
  post-enrichment pipeline, and idempotency prevents duplicate tasks/links.

---

## 2. Extract large embedded Python to standalone scripts

**Impact: High | Effort: Low-Medium | Risk: Low**

Several AppleScripts embed 20-40+ line Python programs as escaped string literals. These
are hard to read, impossible to lint/test, and painful to modify. Only the large ones
(>15 lines) are worth extracting — the 5-10 line snippets (section regex, bullet fix)
add file management overhead for minimal gain.

### Candidates (in priority order)

#### 2a. Image compression → `compress-singlefile-images.py`

**Source:** `process-singlefile-import.applescript` lines 107-148 (~40 lines)

Decodes base64-embedded images from SingleFile HTML, recompresses via `sips`, and replaces
in-place. Currently the largest embedded Python in the pipeline.

**Install to:** `stow/devonthink/.local/bin/compress-singlefile-images.py`
**AppleScript call becomes:** `do shell script "/usr/bin/python3 ~/.local/bin/compress-singlefile-images.py " & quoted form of recPath`

#### 2b. Daily note section insertion → `insert-daily-note-section.py`

**Source:** `append-to-daily-notes.applescript` `appendToSection` handler, lines 212-242
(~30 lines)

Finds a `## Section Header` in a daily note and inserts a content block at the right
position. Handles section creation if missing. Used by both daily notes extraction and
wikilink appending.

**Install to:** `stow/devonthink/.local/bin/insert-daily-note-section.py`
**Interface:** Reads note content from stdin, takes `--header` and `--content` args, prints
updated note to stdout.

#### 2c. Notebook collision detection → `compare-tiff-pages.py`

**Source:** `handle-updated-notebooks.applescript` lines 60-87 (~25 lines)

OCR-based text containment check between two TIFF files. Layer 2 of the 3-layer collision
detection. Currently uses ImageMagick + Tesseract + Python difflib all inside one escaped
string.

**Install to:** `stow/devonthink/.local/bin/compare-tiff-pages.py`
**Interface:** Takes two file paths as args, prints a similarity score or `USE_RMSE`.

#### 2d. Filename sanitization in enrich → Python replacement

**Source:** `enrich-ai-metadata.applescript` lines 218-250 (~30 lines of AppleScript)

Replaces `/` and `:` with `-`, collapses duplicate separators, trims whitespace. The
existing date-stripping code just above it already uses `do shell script` + Python, so this
is a natural extension.

**Approach:** Replace the 30 lines of AppleScript character-replacement loops with:

```applescript
set sanitized to do shell script "export THE_TITLE=" & quoted form of theTitle & ¬
    " && /usr/bin/python3 -c \"import os,re; t=os.environ['THE_TITLE']; " & ¬
    "t=re.sub(r'[/:]',' - ',t); t=re.sub(r'( - ){2,}',' - ',t); print(t.strip(),end='')\""
```

This doesn't need a standalone file — a one-liner in-place is fine.

### What NOT to extract

- Section-header regex patterns (5-10 lines each, slight variations per script)
- Circled number / bullet marker fixes in format-boox-comments (5 lines each)
- YAML escaping in export-wiki-raw (5 lines)
- Text filtering in enrich-ai-metadata (15 lines but tightly coupled to the enrichment
  prompt logic)

These are short, self-contained, and readable enough inline.

---

## 3. Fix wiki export for defuddle fallback path

**Impact: Medium | Effort: Low | Risk: Low**

After the defuddle-fallback change (where HTML stays in `00_INBOX` for direct enrichment),
the wiki export skip logic in `export-wiki-raw.applescript` needs a small update.

### Problem

When defuddle fails:

- The HTML gets enriched directly (`AIEnriched=1`) and has `WebClipSource` set
- The bookmark has `WebClipSnapshot` but no `WebClipMarkdown` (no markdown was created)

Current skip logic:

- Line 48: Skip bookmarks that have `WebClipMarkdown` → bookmark passes through (no
  markdown exists)
- Line 64: Skip HTML if `WebClipSource` is set AND `AIEnriched` is not 1 → HTML passes
  through (it IS enriched)

Both get exported, creating a duplicate wiki source for the same URL.

### Fix

Add a guard: if a bookmark has `WebClipSnapshot` (meaning an HTML archive exists that could
carry enrichment), skip the bookmark from wiki export. The HTML record has the actual
content; the bookmark is just a URL stub.

```applescript
-- Existing guard: skip bookmarks with a markdown version
if clipMd is not "" then
    add custom meta data 1 for "WikiExported" to theRecord
else
    -- NEW: skip bookmarks with an HTML snapshot (which may carry enrichment when defuddle failed)
    set clipSnapshot to ""
    try
        set clipSnapshot to (get custom meta data for "WebClipSnapshot" from theRecord) as text
        if clipSnapshot is "missing value" then set clipSnapshot to ""
    end try
    if clipSnapshot is not "" then
        add custom meta data 1 for "WikiExported" to theRecord
    else
        -- ... existing HTML snapshot guard and export logic ...
    end if
end if
```

**Depends on:** Nothing (standalone fix)

---

## 4. Fix DailyNotesProcessed in extract-web-content

**Impact: Low | Effort: Trivial | Risk: None**

`extract-web-content.applescript` intentionally doesn't set `DailyNotesProcessed` so
bookmarks can appear in daily notes. But bookmarks are fast-tracked past AI enrichment
(`AIEnriched=1`), so they never get an `EventDate`. The daily notes rule runs on them,
finds nothing to do, and sets `DailyNotesProcessed=1` — a wasted cycle every time.

### Fix

Set `DailyNotesProcessed=1` in `extract-web-content.applescript`. If the user later wants
bookmarks to appear in daily notes, the enrichment on the markdown record (which does get an
EventDate) already handles daily note linking.

**Note:** This becomes moot if task 1 is completed (the combined script checks
`WebClipSource` internally). Include here for completeness in case task 1 is deferred.

---

## 5. Split README integration sections into separate files

**Impact: Medium | Effort: Low | Risk: None**

The README is ~800 lines. Three self-contained integration sections account for ~250 lines
and are independently readable:

- **Granola Integration** (lines 598-685) → `docs/granola.md`
- **GitHub Stars Integration** (lines 686-762) → `docs/github-stars.md`
- **Wiki Integration** (lines 533-596) → `docs/wiki.md`

Replace each section in README.md with a one-line link:
`See [Granola Integration](docs/granola.md).`

The core README then focuses on the document pipeline: custom metadata, Hazel, smart rules,
daily notes, and capture setup. This is the part you actually reference when debugging or
modifying the pipeline.

### What NOT to change

- Keep per-rule criteria/trigger/action documentation in the README — this is essential for
  setup and debugging. Gemini suggested replacing this with "pipeline phases" documentation,
  but the rules ARE the phases and their criteria are the contract.
- Keep the `devonthink/CLAUDE.md` pipeline overview as-is — it serves a different purpose
  (quick orientation for the AI) vs. the README's detailed per-rule docs.

---

## 6. Move integration state files to `~/.local/state/`

**Impact: Low | Effort: Trivial | Risk: Low**

Two JSON state files sit in `$HOME`:

- `~/.granola-dt-imported.json`
- `~/.github-stars-dt-imported.json`

Move to `~/.local/state/devonthink/` (XDG-aligned). Update the two Python scripts
(`import-granola.py`, `import-github-stars.py`) to use the new paths, with a migration
check that moves the old file on first run.

---

## 7. Investigate synchronous handwriting recognition

**Impact: Potentially High | Effort: Investigation only | Risk: N/A**

Gemini suggested using `ocr theRecord` synchronously instead of the async "Recognize -
Transcribe Text & Notes" declarative action, which would collapse the two-rule Boox
extraction dance (Extract: Boox Handwritten + Format: Boox Comments) into a single script
and eliminate the `RecognizedAt` timestamp / polling / timeout machinery.

### Why this needs investigation

DEVONthink's `ocr` AppleScript command does standard printed-text OCR. The "Recognize -
Transcribe Text & Notes" action uses ML-based handwriting transcription — a different
engine. It's unclear whether the handwriting recognizer is accessible via AppleScript at all.

### How to verify

1. Check DEVONthink 4's AppleScript dictionary for a synchronous handwriting recognition
   command (look for `recognize`, `transcribe`, or similar)
2. Test on a sample Boox TIFF: `tell application id "DNtp" to ocr <record>` — does it
   produce handwriting transcription or only printed-text OCR?
3. Check the DEVONthink forums for AppleScript-based handwriting recognition

If synchronous handwriting recognition IS available, this is a high-value simplification.
If not, the current two-rule polling design is the correct approach and should stay as-is.

---

## Execution order

Tasks are independent unless noted. Suggested sequence based on impact and
dependency:

```
1. Consolidate post-enrichment rules  ← highest impact, most files touched
2. Extract large embedded Python       ← can be done incrementally (2a, 2b, 2c, 2d)
3. Fix wiki export for defuddle fallback  ← small, standalone
4. Fix DailyNotesProcessed             ← trivial, moot after task 1
5. Split README integrations           ← documentation only, no code risk
6. Move state files                    ← trivial, low priority
7. Investigate sync OCR                ← research only, no code changes yet
```

Tasks 2a-2d are independent of each other and of task 1. Task 3 is independent of
everything. Tasks 5-7 can be done in any order.

---

## What both reviews agreed to skip

- **Shared AppleScript handler library** — DT smart rules execute scripts in isolation;
  `load script` from arbitrary paths is fragile and adds a hidden dependency. Each script
  should remain self-contained.
- **Shared Python import module** (`dt_import_utils.py`) — two independent scripts sharing
  a logging function don't warrant coupling via a shared dependency.
- **Small inline Python extraction** (5-10 line snippets) — the file management overhead
  exceeds the readability gain.
- **Generic metadata read/write helpers** — `get custom meta data for "X" from theRecord`
  is already a one-liner. Wrapping it adds indirection without clarity.
- **Replacing ProseMirror converter with npm package** — the custom code in
  import-granola.py works, has no maintenance burden, and avoids adding a Node dependency
  to a Python script.
