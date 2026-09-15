---
name: playlist-import
description: Convert a public Spotify playlist URL into individually reviewed Qobuz matches, then download it through riptag as one Navidrome compilation album. Use for soundtrack reconstruction, Spotify-to-Qobuz imports, or replacing a wrongly matched playlist album.
---

# Playlist import

Read the source playlist, match recordings rather than album names, and verify the resulting files. Never accept Qobuz's first search result without reviewing it. Qobuz search can return unrelated recordings when the requested song is absent.

## Scope and prerequisites

This workflow uses public Spotify metadata, the existing authenticated streamrip installation, and the existing riptag pipeline. It does not download audio from Spotify or use browser cookies. Screenshot extraction is outside this skill.

The bundled Qobuz helpers run with `~/Developer/streamrip/.venv/bin/python3`, which supplies streamrip and mutagen. Run required tools directly. If that environment, Fish, riptag, or NAS access is missing, stop and report the missing prerequisite. Do not install another downloader or expose generated config.

Read `~/.dotfiles/docs/dotfiles-reference.md` sections on shared skills, music library identity, and audio tagging before changing library metadata. Read the installed `riptag.fish`, `riptag-worker.sh`, and `tagger.py` before relying on their options. NAS mode is the default; use `--local` only deliberately.

A request to inspect or match a playlist is read-only. Creating the private Qobuz playlist, downloading, or replacing library files requires a current-turn instruction authorizing that operation. Never modify an existing Qobuz playlist or publish a public one as a side effect. If tracks are missing, report the limitation before proceeding. Ask whether to import the available subset unless the user has already authorized a partial approximation.

Keep source metadata, matching manifests, receipts, cover files, and logs under a private job directory in `~/.local/state/playlist-import/`, never in git. Use one job directory and one stable creation receipt per import. Set `umask 077` before creating files. Do not run concurrent riptag jobs: its worker uses shared `/tmp` logs and manifests.

## 1. Read the Spotify URL

Resolve all relative script paths below against this skill directory. Example shell variables use Bash.

```bash
umask 077
SKILL="$HOME/.agents/skills/playlist-import"
PY="$HOME/Developer/streamrip/.venv/bin/python3"
JOB="$HOME/.local/state/playlist-import/<job-name>"
mkdir -p "$JOB"
python3 "$SKILL/scripts/spotify_playlist.py" '<spotify-playlist-url>' \
  --expected-count <confirmed-total> --output "$JOB/source.json"
```

Confirm the total independently from Spotify's playlist page or the user. Do not copy the extracted row count back into `--expected-count`: public embeds can truncate long playlists. The helper refuses a count mismatch, a different playlist, or missing/non-track rows. It preserves order and duplicates.

The public embed exposes titles, artist credit strings, durations, explicit flags, and Spotify track URIs. It does not provide structured artist arrays, album names, or ISRCs. Do not infer artist boundaries by splitting every comma. Enrich ambiguous matches from the public track or album page, or a legitimately configured Spotify Web API client. Follow every pagination link when using that API.

If Spotify blocks the embed or its total cannot be confirmed, stop and request a complete text/CSV export or the total. Do not silently treat an embed preview as a complete playlist. URL extraction worked for a 23-track playlist during implementation; that is not a guarantee for every playlist.

Never print or retain raw embed HTML or its full `__NEXT_DATA__` object. The state includes anonymous session access tokens. The helper projects music metadata only.

## 2. Review Qobuz recordings

```bash
"$PY" "$SKILL/scripts/qobuz_playlist.py" search track '<title> <primary artist>'
"$PY" "$SKILL/scripts/qobuz_playlist.py" search album '<artist> <source album>'
"$PY" "$SKILL/scripts/qobuz_playlist.py" inspect album '<qobuz-album-id>'
"$PY" "$SKILL/scripts/qobuz_playlist.py" inspect track '<qobuz-track-id>'
```

These commands reuse streamrip's existing credentials, suppress backend error bodies, and print catalog fields only. `--config <path>` before the subcommand selects an existing config without displaying it. Keep TLS verification enabled.

For each source position, compare the title, credited artist, version, duration, explicit status, and source release where available. Prefer an ISRC match when both sources actually supply one. An ISRC alone does not prove identical mastering or edits. Check duration even when ISRCs match. Prefer the original album or single over an unrelated compilation; prefer higher lossless quality only after recording identity is settled.

Treat remixes, live versions, acoustic recordings, re-recordings, clean edits, karaoke, and cover artists as different recordings. Do not substitute one without approval. A featured credit missing from Qobuz's primary artist field needs corroboration, not automatic rejection or an invented artist tag.

If track search fails, try the source album and inspect its track list. Also try narrower artist/title queries and the artist's available catalog. Public web search can corroborate release availability. Record an unsuccessful search as "not found in the accessible catalog", not proof that the recording has never existed on Qobuz.

Copy `source.json` to `manifest.json`. Retain every source row and position. Add top-level `album`, `genre`, and `year` strings. Label a homemade film compilation as an unofficial soundtrack, not an official release. Use the film year when that is the intended album identity, rather than riptag's current-year playlist default.

Each reviewed row receives a `qobuz` object containing the selected catalog fields from `inspect track`, plus `reviewed: true` and a specific `reason`. Keep `id` as a numeric string. Store source release evidence and duration differences when useful. Set an unmatched row's `qobuz` to `null` and add `missing_reason`. Do not delete unmatched source rows or renumber the source manifest.

The helper validates review attestations and availability; it does not decide whether two recordings match. That decision remains the agent's responsibility.

## 3. Create and verify the private Qobuz playlist

After matching and obtaining the required authorization:

```bash
"$PY" "$SKILL/scripts/qobuz_playlist.py" create "$JOB/manifest.json" \
  --receipt "$JOB/receipt.json" --apply
```

Only add `--allow-missing` for an explicitly accepted partial import. The helper retains matched tracks in source order, records omitted source positions, creates a private non-collaborative playlist, and reads it back to check name, count, privacy, and exact ID order. Duplicates remain duplicates; if Qobuz drops one, verification fails. Manifests are limited to 500 source rows because this workflow does not implement large-playlist batching.

Keep the receipt. Reusing it with the same manifest verifies the existing playlist instead of creating another. A changed manifest is rejected. If a request times out after sending the remote write, the receipt remains in an uncertain state. Inspect the account for the created playlist, verify its exact contents, and recover its ID into the receipt before retrying. Never delete the receipt or choose a fresh receipt merely to retry an uncertain creation. Do not blindly repeat a remote write.

The receipt's `url` is the input to riptag. Do not pass the Spotify URL to riptag or search Qobuz for an album named after the soundtrack.

## 4. Download through riptag

```bash
fish -c 'riptag --compilation --year=<year> <verified-qobuz-playlist-url> <genre>'
```

Quote arguments properly when generating the real command, especially names containing apostrophes. Use argument arrays or shell escaping rather than interpolation of untrusted playlist text.

Riptag detects `/playlist/`, preserves playlist order, applies `Various Artists`, and unifies embedded artwork. Its existing configuration must set `metadata.set_playlist_to_album` and `metadata.renumber_playlist_tracks`; verify those two fields with a redacting parser if the resulting tags disagree. Never print the full generated streamrip config.

The default unified artwork is the first song's original cover, not necessarily the playlist cover. Inspect it. For a film compilation, use suitable film or playlist artwork after checking the image. Public Spotify entity `coverArt.sources` can supply an image URL; project only that field without retaining the full state. Download only a reviewed public image URL. If the image is WebP, convert it to real JPEG or PNG with installed `ffmpeg`, not a filename change.

For partial failures, retain the job and use the exact resume instructions from riptag. Do not treat a zero streamrip exit status or the existence of an album folder as proof that all tracks arrived. Do not remove an old album yet.

## 5. Verify the album, then replace the wrong one

Find the local library root with `/usr/bin/python3 ~/.local/bin/_music_nas.py get local_library_root`. For a remote operation use the `remote.library_root` getter. Both read private `~/.config/music/nas.json`, or `MUSIC_NAS_CONFIG`. Do not dump that file or copy its topology into task records.

```bash
"$PY" "$SKILL/scripts/verify_album.py" "$JOB/manifest.json" '<album-folder>'
"$PY" "$SKILL/scripts/verify_album.py" "$JOB/manifest.json" '<album-folder>' \
  --cover "$JOB/cover.jpg" --apply
```

Use `--allow-missing` consistently for an accepted partial import. The second command is optional. It audits all tracks before writing, sets consecutive track totals and disc 1 of 1, optionally embeds the reviewed cover, and rereads the files. Saves are per-file, not transactional. If a save fails, verify and repair before replacing anything.

The audit checks file count, source order, reviewed title and primary artist, duration within two seconds, album identity, genre, year, compilation flag, ALAC codec, and unified artwork. Artist recasing is tolerated because the organizer adopts existing library spelling. Other discrepancies stop the import. A longer legitimate duration difference needs explicit investigation rather than loosening the check for every track. For additional confidence, decode the files with installed `ffmpeg` before removing the old album.

For an unrelated or wrongly matched old album, `riptag --replaces` is not an identity test: its guard compares track count and quality. Keep that guard for genuine re-downloads. Instead, after verifying the new compilation, move the exact old directory to a uniquely named quarantine outside the scanned library, preferably by an NAS-side rename on the same filesystem. Confirm both paths and expected counts first, refuse existing quarantine destinations, and leave unrelated folders alone. Record the recovery path in the private job receipt or notes.

NAS permissions matter. Riptag fixes them during import; after any SMB-side retagging, check the album's readability and restore permissions on the exact NAS album directory if necessary. Follow the worker's existing permission convention, not a library-wide chmod.

Trigger the existing Navidrome scan integration, then verify the new album's title and track count and the old album's absence. If using its API, reuse the existing local auth integration without printing cache contents, credentials, or authenticated URLs. A scan request is asynchronous; confirm completion before claiming the library is updated.

Report the imported count versus the source count, any missing tracks or approved substitutions, the album name, and the old album's recovery location. Keep the full mapping and receipt for future repair.

## Maintenance

The Qobuz helper uses the installed streamrip client for authentication and catalog reads. Private playlist creation uses Qobuz's `playlist/create` endpoint with initial `track_ids`, followed by `playlist/get` verification. This endpoint is documented by the existing [clj-qobuz client](https://cljdoc.org/d/audiogum/clj-qobuz/0.1.15/api/clj-qobuz.playlist). Qobuz's partner API documentation is not reliably public. Check current provider and client documentation before changing this integration.

Run the synthetic helper tests from the dotfiles repository:

```bash
python3 -m unittest discover -s scripts/tests -p test_playlist_import.py
```

Restow the `agents` package after adding or removing skill files. Keep real manifests, account state, and downloaded artwork outside the repository.
