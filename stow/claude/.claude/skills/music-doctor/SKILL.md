---
name: music-doctor
description: Routine, exhaustive health checks against the music library at /Volumes/Media/Music — detects corrupt tracks, metadata drift, duplicates, empty folders, miscategorized albums, and quality regressions, with a stable dismissal mechanism so resolved-by-design issues stay quiet on future runs. Invoke when the user explicitly says `/music-doctor`, "music doctor", "music library check / scan / health", "audit my music library", or asks for music library stats.
user_invocable: true
---

# music-doctor

Library-health pass for the Music.app-shaped library at `/Volumes/Media/Music`. The skill drives `~/.local/bin/music-doctor.py` — a Python engine that walks the library, runs a battery of checks, writes findings to SQLite, and exposes triage and fix subcommands. The skill's job is to:

1. Run the scan with sensible defaults.
2. Show the user a digestible report.
3. Translate natural-language triage into the right `dismiss` / `fix` subcommand calls.
4. Preserve user judgement across runs by writing precise `dismiss --reason "..."` entries.

## Configuration

```
LIBRARY_ROOT  = /Volumes/Media/Music         # SMB-mounted NAS share
DB_PATH       = ~/.local/share/music-doctor/db.sqlite3
ALLOWED_GENRES = Ambient, Bluegrass, Classical, Country, Electronic,
                 Experimental, Folk, Hip-Hop, Jazz, Lo-Fi, Mashup, Pop,
                 R&B, Reggae, Rock, Soundtrack, Unknown
QUALITY_LOW   = <320kbps lossy           → flagged as quality_low (info)
QUALITY_UPGRADE = =320kbps lossy         → flagged as quality_upgrade_candidate
```

## Trigger

User explicitly invokes `/music-doctor`, or says:
- "run music doctor"
- "scan my music library" / "check my music library" / "audit my music library"
- "what's the state of my music library?"
- "music library stats" / "library report"
- "any broken / corrupt music files?"
- "find duplicate albums"
- "show last music-doctor run"

If the user says something narrower ("just stats", "just duplicates", "look at the Adele folder"), narrow the invocation accordingly (`stats` subcommand, `--kind duplicate_album`, scoped library root via symlink, etc).

## Step 0: Preflight

Run these before the first scan in a session. Cheap and idempotent; skip on subsequent invocations in the same session.

```bash
# Library mount alive
ls /Volumes/Media/Music >/dev/null 2>&1 || echo "MOUNT MISSING"

# Engine present
test -x ~/.local/bin/music-doctor.py || echo "ENGINE MISSING"

# Deep mode requires ffprobe (optional)
command -v ffprobe >/dev/null 2>&1 || echo "NO FFPROBE"
```

If the SMB share isn't mounted, ask the user to mount it before scanning. If the engine is missing, the dotfiles aren't fully stowed — run `cd ~/.dotfiles/stow && stow --restow --no-folding --target="$HOME" bin`.

## Step 1: Scan

Default invocation:

```bash
~/.local/bin/music-doctor.py scan --json
```

Parse the JSON object on stdout. Engine logs go to stderr; ignore them in the parse. Key fields:

```json
{
  "run_id": 7,
  "library_root": "/Volumes/Media/Music",
  "stats": { /* aggregate counts */ },
  "summary": {
    "total": 412,
    "by_severity": {"error": 3, "warning": 287, "info": 122},
    "by_kind": {"quality_upgrade_candidate": 180, "missing_cover_file": 95, ...}
  },
  "findings": [
    {
      "id": 4711,
      "hash": "ab1cd234e5f67890",
      "kind": "compilation_mismatch",
      "severity": "warning",
      "target_kind": "album",
      "targets": ["/Volumes/Media/Music/Compilations/Tropicalia 2"],
      "message": "cpil=true but artist signal says single-artist",
      "details": {"salient": "should_be_false"},
      "dismissed": false
    },
    ...
  ]
}
```

Variants:
- `--deep` runs `ffprobe` on every track. Catches truncation that mutagen reads happily. Adds significant time over SMB (~10× longer); only suggest when the user is plugged in or specifically asking for thoroughness.
- `--kind <k>` (repeatable) restricts to a subset of checks. Use when the user asks for a targeted scan ("just duplicates").
- `--workers N` (default 8). Raise to 16 on a fast machine, lower if the SMB share is sluggish.

Battery / time awareness:
- A full scan of ~10k+ tracks over SMB typically takes 2–5 minutes. Don't pre-emptively offer the gate (`~/.local/bin/should-run-background-job`); this is user-invoked work. If the user mentions being on battery and on a flight or similar, suggest deferring or using `--kind` to narrow.

## Step 2: Present the report

Lead with a one-line headline and the severity breakdown. Then list the top finding kinds, with brief example messages.

Recommended layout (terse, no decorative formatting):

```
Run #7 — 412 findings across 1,247 albums / 15,392 tracks.

Severity: 3 error • 287 warning • 122 info.

Top kinds:
  quality_upgrade_candidate (180) — 320kbps lossy, lossless candidate
  missing_cover_file (95)         — embedded art but no cover.* file
  path_tag_mismatch (42)          — folder name doesn't match tag
  compilation_mismatch (18)       — cpil flag disagrees with artist signal
  duplicate_album (8)             — same album in 2+ locations
  unreadable (3)                  — mutagen can't parse
  ...

3 errors need attention first:
  [a1b2c3d4...] /Volumes/Media/Music/Foo/Bar/01 Track.flac — IOError: ...

Ask the user where to dig in (errors first, a specific kind, or stats).
```

For severity-relative phrasing:
- `error` — playback / indexing likely broken.
- `warning` — fixable inconsistency or organizational issue.
- `info` — improvement opportunity, no functional problem.

## Step 3: Triage with the user

The user's natural-language replies map to subcommand calls. Hold the `run_id` and the list of findings (especially their hashes) in your context for the conversation.

| User intent                                  | Action |
|---------------------------------------------|--------|
| "show me the duplicate albums"               | `report --kind duplicate_album` (or filter your in-memory findings list) |
| "show errors only"                           | `report --severity error` |
| "what about that 256kbps Tropicalia album?"  | Search findings for the path, present the hash, ask what to do |
| "fix the compilation flags"                  | Dry-run `fix --kind compilation_mismatch`, show plan, confirm, then `--apply` |
| "ignore the missing_cover_file ones — that's just legacy"   | `dismiss --kind missing_cover_file --reason "embedded-only art is fine for legacy imports"` |
| "this album's 256kbps is the highest available — don't flag again"   | `dismiss --hash <h> --reason "highest available bitrate"` |
| "undo that"                                  | `undismiss <hash>` |
| "show stats"                                 | `stats --from-db` |
| "show last scan results"                     | `report` |
| "show all past runs"                         | `history` |

When dismissing, *always* pass `--reason` capturing the user's actual rationale. The reason is persisted in the SQLite store and surfaces in future audits; future you (or the user) needs to remember *why* the dismissal exists.

## Step 4: Fix policy

Fixable kinds (engine knows how to repair them):

| Kind                  | Action                                              | Risk         |
|-----------------------|-----------------------------------------------------|--------------|
| `stale_tag`           | Clear `comment` or `copyright` tag on the file      | low — pipeline-aligned |
| `compilation_mismatch`| Flip `cpil` on every track in the album             | low — derived from artist signal |
| `empty_artist_folder` | `rmdir` the empty artist folder                     | medium — file deletion |
| `empty_album_folder`  | `rmdir` the empty album folder                      | medium — file deletion |

For everything else (`duplicate_album`, `wrong_filename`, `path_tag_mismatch`, etc.), the engine does NOT auto-fix. These need human judgment and usually map to existing tooling:
- `duplicate_album` → user decides which to keep based on quality; remove the other via the Finder or `rm -rf`.
- `quality_low` / `quality_upgrade_candidate` → re-download flow via `riptag --replaces=<path>`. The local music-redownload-queue (`~/.local/share/batch_rip/downloads.json`) is the staging point.
- `path_tag_mismatch` → fix the tag with `tagger.py`, or move the folder to match. Don't blanket-rename without showing the user.
- `wrong_filename` → fix manually or re-import via `import-album.py` to get canonical names back.

**Fix invocation pattern:**

```bash
# 1. Always dry-run first.
~/.local/bin/music-doctor.py fix --kind compilation_mismatch

# 2. Show the user the "would apply" list.

# 3. On confirmation, re-run with --apply.
~/.local/bin/music-doctor.py fix --kind compilation_mismatch --apply
```

For batch fixes (`--kind`), tell the user explicitly how many findings will be touched and across which albums. Risky kinds (`empty_artist_folder`, `empty_album_folder`) require an explicit yes per group — never blanket-delete folders without confirmation.

## Step 5: Closing the loop

When done, summarize what changed:
- Findings dismissed (with reasons).
- Fixes applied (counts by kind).
- Outstanding work (kinds with remaining findings the user wants to handle later).

Mention the relevant follow-up commands the user can run alone:
- `music-doctor.py report` — re-list findings any time.
- `music-doctor.py stats --from-db` — re-print the stats.
- `riptag --replaces=<path>` — for lossless replacements of upgrade candidates.

## Check reference

Errors:
- `unreadable` — mutagen raised; file is corrupt or in an unsupported format.
- `zero_byte_file` — empty file.
- `zero_duration` — file parses but reports zero-length audio.
- `ffprobe_error` (deep mode only) — ffprobe can't decode the stream.

Warnings:
- `empty_field` — missing title / album / artist / albumartist tag.
- `stale_tag` — `comment` or `copyright` tag set (the pipeline strips these).
- `disallowed_genre` — genre tag not in the project allowlist.
- `path_tag_mismatch` — artist or album folder name doesn't match the tag (with Compilations folder rules).
- `inconsistent_album_tag` / `inconsistent_albumartist` — tracks in one album folder claim different album/albumartist values.
- `inconsistent_compilation` — `cpil` flag differs across tracks in the same album.
- `compilation_mismatch` — `cpil` value disagrees with the actual artist signal (via `compilation_signal` from `_music_tags.py`).
- `track_gap` — track numbers within a disc skip a value.
- `duplicate_track_number` — two tracks share the same disc+track tuple.
- `duplicate_track` — two files in the same album share the same title.
- `duplicate_album` — same `(normalized albumartist, album)` appears in 2+ folders.
- `wrong_filename` — filename doesn't match the canonical `[D-]NN Title.ext` pattern.
- `missing_cover` — no cover file and no embedded art.
- `empty_artist_folder` / `empty_album_folder` — directory contains no audio.
- `stray_file_at_root` — non-hidden file directly under the library root.
- `artist_name_variant` — two artist folders normalize to the same name (e.g. "MF DOOM" vs "MF Doom").
- `nested_subdir_in_album` — album folder contains a sub-folder (canonical library is flat).

Info:
- `quality_low` — lossy file below 320kbps.
- `quality_upgrade_candidate` — lossy file at or above 320kbps (lossless replacement candidate).
- `quality_mixed` — album mixes lossless and lossy tracks.
- `unusual_sample_rate` — sample rate below 44.1kHz.
- `missing_year` / `unusual_year` — year tag missing or outside [1900, current+1].
- `single_track_album` — album folder with one track (often a misfile).
- `missing_cover_file` — embedded cover art but no `cover.*` at the album root.
- `multiple_covers` — more than one cover candidate in the album folder.
- `stray_file` — non-audio, non-cover file in an album folder.

## Dismissal mechanics

Each finding has a stable `hash` = `sha1(kind + sorted_targets + salient)[:16]`. The hash survives across runs as long as the targets and the salient detail don't change. Dismissing a hash inserts a row into `dismissals`. Report and `scan --json` honor the table automatically (skip dismissed). `--include-dismissed` shows them.

When the user dismisses by selector (`--kind`, `--target`), the engine expands the selector to the matching hashes in the latest run and inserts one row per finding. Future findings with the same hash stay suppressed.

If a finding's underlying data changes (e.g. a low-bitrate album is replaced with a lossless rip), the kind no longer fires; the dismissal sits inert in the table but doesn't apply. No cleanup needed.

## Engine subcommand cheatsheet

```bash
# Full scan, structured output
music-doctor.py scan --json

# Quick stats only, no checks
music-doctor.py stats              # rescan
music-doctor.py stats --from-db    # last run

# Last run's findings
music-doctor.py report                              # default human output
music-doctor.py report --json                       # structured
music-doctor.py report --severity error             # filter by severity
music-doctor.py report --kind duplicate_album       # filter by kind
music-doctor.py report --include-dismissed          # show dismissed

# Dismissal
music-doctor.py dismiss --hash <h> --reason "..."
music-doctor.py dismiss --kind quality_low --target "Adele/21" --reason "highest available"
music-doctor.py undismiss <h>

# Fix (dry-run by default; add --apply to commit)
music-doctor.py fix --kind stale_tag
music-doctor.py fix --kind compilation_mismatch --apply
music-doctor.py fix --hash <h> --apply

# History
music-doctor.py history --limit 20
```

## Key rules

1. **Always dry-run fixes first.** Show the planned changes, confirm, then `--apply`.
2. **Capture user judgment as `--reason`.** Every dismissal needs a real reason; "OK" is not sufficient.
3. **Never delete files without explicit confirmation per group.** `empty_*_folder` fixes require a yes for each artist/album.
4. **Don't auto-fix kinds outside the FIX_HANDLERS table.** Re-tagging, re-downloading, and folder moves belong to `tagger.py`, `import-album.py`, `riptag`, or the Finder — not to music-doctor.
5. **Surface the hash.** When the user might want to dismiss or fix later, show the 16-char hash. They reference it by hash, not by ID.
6. **Use the existing pipeline for lossy→lossless upgrades.** `riptag --replaces=<library-relative-path> ...` runs through the guarded re-download flow.

## Related tooling

- `~/.local/bin/music-organize.py` — files audio into Artist/Album tree per tag. Where the canonical filename and layout come from.
- `~/.local/bin/tagger.py` — m4a genre / cpil / cover unifier. Use for one-off retag fixes.
- `~/.local/bin/import-album.py` — bridges external folders into the library; auto-detects compilation status.
- `riptag` (fish function) — Qobuz download + tag + organize. `--replaces=PATH` enables the guarded re-download path used to upgrade quality.
- `batch_rip` (fish function) — runs the re-download queue at `~/.local/share/batch_rip/downloads.json`.
