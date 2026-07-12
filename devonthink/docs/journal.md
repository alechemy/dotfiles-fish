# Boox daily journal → DEVONthink (local-only)

The daily-journal notebook on the Boox (named `<year> Journal`, e.g.
`2026 Journal`) is the most sensitive content in the pipeline, so it is
processed entirely on-device: `boox-import-watcher.sh` routes its PDF
exports away from the cloud-backed smart-rule pipeline (OCR, comment
formatting, and enrichment all go through DEVONthink chat) and into a
local staging → vision-OCR → filing flow. The `/15_JOURNAL` group is
excluded from DT chat, same as `/10_DAILY`, which also hides it from the
DEVONthink MCP server.

## Authoring convention (on the device)

- One page per day, in order. Pages may be edited later; days may be
  skipped.
- Every page starts with a handwritten date line, alone, e.g.
  `Sat, Jul 11`. The weekday is not decoration — it is a check digit.
  Handwritten digits are the most commonly misread characters, and a
  page whose written weekday disagrees with its parsed date is parked
  for review instead of being filed under the wrong day.
- Start a new notebook each year (`2027 Journal`); the year in the
  notebook name anchors year-less date lines and rejects strays.

## Flow

1. `boox-import-watcher.sh` matches `^\d{4} Journal$` and hands the PDF
   to `journal-import.sh` instead of `boox-import.sh`.
2. `journal-import.sh` byte-hashes the export (the Boox re-emits
   unchanged notebooks on every sync), short-circuits against the done
   marker and any already-staged copy, then atomically stages it in
   `~/.local/state/devonthink/journal/staging/` and deletes the Maestral
   copy. No rendering, no OCR — it stays fast.
3. `journal-process.py` (launchd: `com.user.journal-process`, WatchPaths
   on the staging dir + 30-min interval) does the heavy work behind the
   usual gates — `pipeline-record-run` first, then battery
   (`should-run-background-job`), driver role, and the entity-layer
   idle gate (`IDLE_MINUTES`):
   - renders each page once per export (grayscale PNG, `DENSITY` dpi)
     into a per-notebook workdir and identifies pages by ImageMagick
     **pixel signature** (`%#`), so unchanged pages are never re-OCR'd
     and an edit to any old page re-enters processing automatically;
   - transcribes changed pages with the local vision model via oMLX
     (`OMLX_MODEL`, default `Qwen3-VL-32B-Instruct-4bit`), budgeted by
     `MAX_PER_RUN` per tick so a first-run backfill paces itself;
   - parses the transcription's first heading as the entry date and
     validates weekday agreement, notebook year, no future dates, and
     page-order monotonicity — failures park the page with a reason
     (`--status` lists them, `--force` re-queues);
   - upserts one markdown record per day at
     `/15_JOURNAL/<year>/YYYY-MM-DD Journal` (keyed by **date**, not
     page index, so a page inserted mid-notebook shifting later
     signatures only causes re-OCR, never duplicate records) with
     `EventDate`, `SourceFile`, `JournalEntry`, `PageIndex`,
     `PageSignature`;
   - appends an idempotent `📔 Journal` link to that day's daily note
     (created on demand for past dates, so backfill lands on the right
     historical notes);
   - when every page is filed, writes the done marker, deletes the
     staged PDF and workdir.
4. `entity-filing.py` discovers past-dated entries as kind `journal`
   (via `entity-dt-bridge.js list_sources`) and extracts facts about
   people through the normal proposal/review flow. Like `daily`, the
   `journal` kind is **local-transport only** — never DT chat, even
   under `TRANSPORT=auto`.

## RAM: one model at a time

Entity extraction (`Qwen3.5-35B-A3B-4bit`) and journal OCR
(`Qwen3-VL-32B-Instruct-4bit`) are both ~18 GB resident. oMLX loads
models on demand with LRU eviction, so nothing crashes if both are
requested — but interleaving them would thrash load/evict cycles. Both
pipelines therefore serialize local inference through a shared
non-blocking flock (`~/.local/state/devonthink/local-llm.lock`): whoever
finds it held defers to its next tick. To consolidate on a single model
instead, set `OMLX_MODEL` in `entities.conf` to the VL model — its text
extraction quality is comparable and it removes the swap entirely.

## Config

`~/.config/dt-pipeline/journal.conf` (KEY=VALUE, all optional).
`OMLX_URL` / `OMLX_API_KEY` default from `entities.conf` so the shared
server is configured once; `OMLX_MODEL` is deliberately *not* inherited
(entities.conf points at a text model).

```
OMLX_MODEL=Qwen3-VL-32B-Instruct-4bit
MAX_PER_RUN=5        # pages OCR'd per tick
IDLE_MINUTES=10      # 0 disables the idle gate
DENSITY=200          # page render dpi
```

## Debugging

```bash
# What does the worker think is pending/parked?
journal-process.py --status

# Everything logs to the central pipeline log.
rg 'journal-(import|process)' ~/Library/Logs/devonthink-pipeline.log | tail -20

# Plan without writing (renders + page diff only, no OCR, no DT writes).
journal-process.py --dry-run

# Re-queue parked pages and bypass battery/driver/idle gates.
journal-process.py --force

# State lost or the driver role moved: reseed entries from DT records.
# Page arrays/render stamps rebuild on the next staged export.
journal-process.py --rebuild-state
```

Breakage modes to know:

- **Parked pages never retry on their own.** Deterministic input at
  temperature 0 → the same misread; only a content change or `--force`
  re-queues them. The parked reason names the page and what failed.
- **A page with no parseable date parks the whole day**, not the
  notebook — later pages still process (monotonicity checks skip parked
  pages).
- **Signatures are pixel hashes of the render.** An ImageMagick major
  upgrade may change rendering subtly and re-OCR everything once;
  text-hash comparison prevents DT churn from identical re-transcripts.
- **The MCP server cannot see `/15_JOURNAL`** (chat exclusion). Verify
  records through `entity-dt-bridge.js` (`get_text` / `get_fields`) or
  the DT UI, not MCP search.
- **Unnamed notebooks are deleted by the watcher.** The journal must be
  named `<year> Journal` on the device or its exports never reach
  staging.
