# Boox notebooks → DEVONthink, processed entirely on-device

Every named Boox notebook is transcribed by a local vision model — no
handwritten content ever reaches the cloud-backed smart-rule stages (DT's
AI OCR, chat formatting, chat enrichment). `boox-import-watcher.sh` routes
exports into a staging → local-OCR → filing flow; the cloud stages remain
for markdown, bookmarks, and other non-handwritten record types.

## Flow

1. `boox-import-watcher.sh` hands every named `.pdf` export to
   `boox-stage.sh` (untitled `Notebook-<n>` / `Infinite-<n>` exports are
   deleted — naming a notebook on the device is the deliberate signal that
   it enters DEVONthink).
2. `boox-stage.sh` byte-hashes the export (the Boox re-emits unchanged
   notebooks on every sync), short-circuits against the done marker and
   any already-staged copy, then atomically stages it in
   `~/.local/state/devonthink/boox/staging/` and deletes the Maestral
   copy. No rendering, no OCR — it stays fast.
3. `boox-process.py` (launchd: `com.user.boox-process`, WatchPaths on the
   staging dir + 30-min interval) does the heavy work behind the usual
   gates — `pipeline-record-run` first, then battery
   (`should-run-background-job`), driver role, and the entity-layer idle
   gate (`IDLE_MINUTES`):
   - renders each page once per export (grayscale PNG, `DENSITY` dpi)
     into a per-notebook workdir and identifies pages by ImageMagick
     **pixel signature** (`%#`), so unchanged pages are never re-OCR'd
     and an edit to any old page re-enters processing automatically;
   - transcribes changed pages with the local vision model via oMLX
     (`OMLX_MODEL`), budgeted by `MAX_PER_RUN` per tick so a first-run
     backfill paces itself; per-page transcriptions are cached in the
     state file;
   - files the notebook according to its type (below).
4. OCR holds the shared local-LLM flock
   (`~/.local/state/devonthink/local-llm.lock`) that `entity-filing.py`
   also honors, so two ~18 GB models are never resident at once. Both
   pipelines currently run the same model (`Qwen3-VL-32B-Instruct-4bit`,
   set in `entities.conf`), so the lock only serializes heavy inference.

## Regular notebooks (everything except the journal)

Filed only once **every** page has transcribed — a partially-OCR'd
comment never enters the pipeline. Then:

- Per-page transcriptions are assembled in page order; a page whose first
  line is a heading ending in `(cont.)` merges into the previous section
  (the heading is dropped). `markdownlint --fix` tidies the result.
- A **local metadata pass** (same model, text-only) supplies
  `EventDate`/tags/summary — what the cloud enrich rule used to do. The
  title stays the notebook's user-given name, prefixed with the event
  date when one is found (with `NameLocked=1`, like the old enrich
  rename). Metadata failure is non-blocking: the note files without it.
- The record keeps the **classic model**: a monochrome Group4 TIFF (the
  handwriting stays visible) in `00_INBOX`, deduplicated by `SourceFile` —
  a re-export replaces the backing file in place (stage + atomic mv),
  preserving UUID/name/tags/WikiLinks — with the transcription in the
  Finder comment and flags pre-set (`Handwritten=1, Recognized=1,
  Commented=1, AIEnriched=1, NeedsProcessing=1`).
- From there the LLM-free **Post-Enrich & Archive** rule takes over
  exactly as before: `Tasks:`/`Action Items:` sections → Things,
  `Daily Notes`/`Journal` sections → daily note, wikilink, archive to
  `99_ARCHIVE`. The cloud rules (Extract: Boox Handwritten, Format: Boox
  Comments, Enrich: AI Metadata) never match a pre-flagged record.
- Entity filing reads handwritten sources from the **Finder comment**
  (`get_text` prefers comment when `Handwritten=1`) and treats the
  `handwritten` kind as local-transport-only, same as `daily`/`journal`.

## The daily journal ("<year> Journal")

One page per day, each page starting with a handwritten date line.

- Every page begins with a date heading, e.g. `Sat, Jul 11`. The weekday
  is a **check digit**: handwritten digits are the most commonly misread
  characters, and a page whose written weekday disagrees with its parsed
  date is parked for review instead of being filed under the wrong day.
  Accepted forms: month-name (`Jul 4`, `July 4th, 2026`), ISO
  (`2026-07-04`), US numeric (`7/4`, `7/4/26`); weekday optional,
  anywhere in the line; extra words tolerated. Not accepted: day-first
  (`4 July`), spelled-out days, dates outside the notebook year, future
  dates, or dates that don't increase with page order.
- Each page files as `/15_JOURNAL/<year>/YYYY-MM-DD Journal` (markdown,
  keyed by **date**, not page index, so an inserted page shifting later
  signatures only causes re-OCR, never duplicate records) with
  `EventDate`, `SourceFile`, `JournalEntry`, `PageIndex`,
  `PageSignature`, and an idempotent `📔 Journal` link on that day's
  daily note (created on demand for past dates, so backfill lands on the
  right historical notes).
- `/15_JOURNAL` is excluded from DT chat, same as `/10_DAILY`, which
  also hides it from the DEVONthink MCP server. Start a new notebook
  each year (`2027 Journal`).

## Downstream surfaces

- **Morning brief `## Journal` line** (`dt-morning-brief.py`): warns when
  yesterday's journal entry never arrived, distinguishing "staged/pending
  OCR" and "parked" from "nothing synced — check the Boox's Dropbox
  sync". Dormant until the first entry files, quiet again when the
  newest entry is older than a week, and scoped to `<year> Journal`
  notebooks only.
- **`## On This Day`** picks up journal entries automatically — they
  carry `EventDate`, which is exactly what that section queries (bridge
  search is not filtered by chat exclusion; verified).
- **Things**: regular notebooks get task extraction from Post-Enrich &
  Archive as always. For the journal it is opt-in (`THINGS_TASKS=on`):
  bullets under a `Tasks:`/`Action Items:` header become to-dos via the
  Things URL scheme (`open -g`, no AppleEvents), deduplicated per entry
  across re-OCRs.

## Config

`~/.config/dt-pipeline/journal.conf` (KEY=VALUE, all optional).
`OMLX_URL` / `OMLX_API_KEY` default from `entities.conf` so the shared
server is configured once; `OMLX_MODEL` is deliberately *not* inherited.

```
OMLX_MODEL=Qwen3-VL-32B-Instruct-4bit
MAX_PER_RUN=5        # pages OCR'd per tick
IDLE_MINUTES=10      # 0 disables the idle gate
DENSITY=200          # page render dpi
THINGS_TASKS=off     # journal-only Things extraction (see above)
```

## Debugging

```bash
# What does the worker think is pending/parked/filed?
boox-process.py --status

# Everything logs to the central pipeline log.
rg 'boox-(stage|process)' ~/Library/Logs/devonthink-pipeline.log | tail -20

# Plan without writing (renders + page diff only, no OCR, no DT writes).
boox-process.py --dry-run

# Re-queue parked pages and bypass battery/driver/idle gates.
boox-process.py --force

# State lost or the driver role moved: reseed journal entries from DT.
# Page arrays/render stamps rebuild on the next staged export; regular
# notebooks re-find their record by SourceFile at filing time anyway.
boox-process.py --rebuild-state
```

Breakage modes to know:

- **Parked pages never retry on their own.** Deterministic input at
  temperature 0 → the same misread; only a content change or `--force`
  re-queues them. The parked reason names the page and what failed.
- **Do not manually reset `Recognized`/`Commented` on a handwritten
  record** — that re-arms the vestigial cloud rules. To re-process a
  handwritten note locally, re-export it from the device (the page-diff
  picks up changes) or run `boox-process.py --force`.
- **Signatures are pixel hashes of the render.** An ImageMagick major
  upgrade may change rendering subtly and re-OCR everything once;
  cached-text and comment comparison prevent DT churn from identical
  re-transcripts.
- **The MCP server cannot see `/15_JOURNAL`** (chat exclusion). Verify
  journal records through `entity-dt-bridge.js` (`get_text` /
  `get_fields`) or the DT UI, not MCP search.
- **Unnamed notebooks are deleted by the watcher.** A notebook must be
  named on the device or its exports never reach staging.
