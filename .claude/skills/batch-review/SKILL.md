---
name: batch-review
description: Review albums from the Qobuz batch-rip queue and build a corrected downloads queue. Reads raw input.json, verifies URLs and explicit status, appends validated entries to downloads.json.
argument-hint: [count=10]
---

# Batch Review

You are processing albums from `batch-rip-work/input.json` into a corrected download queue at `batch-rip-work/downloads.json`.

Parse arguments from: **$ARGUMENTS** (default count is 10 if not specified).

## Context

- **Raw input (read-only):** `batch-rip-work/input.json` — JSON array of `{url, genre, artist, title, compilation?}` objects. Do NOT modify this file.
- **Output queue:** `batch-rip-work/downloads.json` — validated/corrected entries ready for `batch_rip`. You append to this file. Create it as `[]` if it doesn't exist.
- **Reviewed log:** `batch-rip-work/reviewed.json` — flat JSON array of album IDs the skill has already processed (any outcome). You append to this file. Create it as `[]` if it doesn't exist.
- **Rejection log:** `batch-rip-work/not_found_library.md` — human-readable log of albums that can't be downloaded. You append to this file.
- **Search command:** `fish -c 'rip search -o <output.json> -n <count> qobuz album "<query>"'` (must use `-o` flag; must be invoked via `fish -c` as `rip` is a fish function)

## Determining what to process

Skip entries in the raw file if any of the following are true:

- They have `"skip": true`
- Their album ID is in `reviewed.json`

Extract album ID from URL: `https://play.qobuz.com/album/<id>` → `<id>`

Work through the raw file in order, collecting the first N unprocessed entries.

## Qobuz API

Read `app_id` and auth token from `~/Library/Application Support/streamrip/config.toml` (under `[qobuz]`). Cache for the duration of the batch.

### Album metadata

```bash
curl -s "https://www.qobuz.com/api.json/0.2/album/get?album_id=<ID>&app_id=<APP_ID>" \
  -H "x-user-auth-token: <TOKEN>" \
  | jq '{id, title, parental_warning, artist: .artist.name, tracks_count, maximum_bit_depth, maximum_sampling_rate, tracks: [.tracks.items[] | {title, parental_warning, streamable}]}'
```

### Album search

```bash
curl -s "https://www.qobuz.com/api.json/0.2/album/search?query=<QUERY>&limit=5&app_id=<APP_ID>" \
  -H "x-user-auth-token: <TOKEN>" \
  | jq '[.albums.items[] | {id, title, parental_warning, artist: .artist.name, tracks_count}]'
```

The Qobuz search API (`/album/search`) is unreliable and frequently returns 500 errors — prefer `rip search` for finding albums. The `album/get` endpoint is reliable for known IDs. If an API call fails, fall back to `rip search`, then WebFetch as last resort. If all fail, note "fetch failed" and move on.

## Review phase

Fetch all N albums in parallel (batches of 5–10 curl calls). Then for each:

### Step 1: Fetch metadata

- Extract the album ID from the URL.
- Hit the `album/get` API endpoint.
- Extract: title, artist, `parental_warning`, track count, quality.

### Step 2: Verify identity

- Does the response match the expected artist and title?
- If **not a match** (wrong album, 404, region-locked, etc.):
  - Search for the correct album using `rip search` or the search API.
  - If found → **action:** use the corrected URL in the output.
  - If not found → **action:** log to `not_found_library.md`, omit from output.

### Step 3: Check streamability

- The album-level `streamable` flag is unreliable — check `tracks[].streamable` for each track.
- If **any tracks are not streamable** → **action:** log to `not_found_library.md` under "## Partially streamable", omit from output.
- Do not proceed to the explicit/clean check for partial albums.

### Step 4: Check explicit/clean status

Qobuz search results frequently surface the clean version of an album first, so URLs copied from search results are often accidentally the censored release. This step catches that.

- If `parental_warning: true` → explicit. Move on.
- If `parental_warning: false`:
  1. Always search for the same artist + album title. Do not skip this based on genre or any other heuristic.
  2. Check results for a different release with `parental_warning: true`.
  3. If found → **action:** swap to the explicit URL in the output.
  4. If no explicit version found → the album is simply a clean release. Treat as `ready` and note "no explicit version" in the Notes column. Do NOT log to `not_found_library.md`.

### Step 5: Present summary table

| #   | Artist | Title | Status | Action | Qobuz URL | Notes |
| --- | ------ | ----- | ------ | ------ | --------- | ----- |

Status values:

- `ready` — URL verified, fully streamable, no changes needed (includes albums with no explicit version)
- `url-swap` — replaced with a better or explicit URL
- `partial` — some tracks not streamable; omitted
- `skip` — not found on Qobuz; omitted

Action column: `none`, `swap URL → <new_id>`, `omit (not found)`, etc.

Qobuz URL column: always include the full bare URL (e.g. `https://play.qobuz.com/album/om4crac34f3ya`). Do NOT use markdown link syntax. For omitted entries, use `—`. For wrong-URL entries, include the original URL in Notes.

## Apply phase

After showing the table, immediately apply changes without prompting:

1. **Append validated entries to `downloads.json`**: for each album with status `ready` or `url-swap`, append `{url, genre, artist, title}` (include `compilation: true` if set) to the array. Use the corrected URL where applicable.

2. **Append to `not_found_library.md`**: log any `skip` or `partial` albums under the appropriate heading.

3. **Append all reviewed album IDs to `reviewed.json`**: add the original album ID (from `input.json`) for every album processed this run, regardless of outcome.

Then report how many entries were added to `downloads.json` and how many were logged to `not_found_library.md`.

## not_found_library.md format

```
## Not on Qobuz
# Artist / Title / Genre — reason (e.g., "no results", "region-locked", "only on Tidal")

## Wrong URL / Broken link
# Artist / Title / Genre — what happened (e.g., "404", "points to different album")

## Partially streamable
# Artist / Title / Genre — streamable track count (e.g., "3/14 tracks streamable") (<album_id>)

## Download failures
# Artist / Title / Genre — error details
```

When appending entries, place them under the appropriate heading. Create headings if they don't exist yet. Preserve all existing entries — only restructure if the file doesn't yet use this format.

## Important notes

- Do NOT modify `input.json`.
- `rip search` is interactive — always pass `-o <file>` to write results to a file.
- Fetch albums in parallel to minimize latency.
- If a Qobuz API call fails, try `rip search` as fallback, then WebFetch, then web search.
