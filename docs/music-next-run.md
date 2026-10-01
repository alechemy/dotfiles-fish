# Offline running playlists

`music-next-run.py` prepares a private, static `Next Run` playlist on Navidrome for
Arpeggi downloads. It uses `Running` as the eligibility source, preserving its
runnability, genre, rating, and Rock Rotation rules. It never edits that playlist
or removes library files.

## Before a run

Connect the Mac and Arpeggi to Navidrome. Let Arpeggi upload its previous offline
listening history, then prepare the first queue:

```sh
music-next-run.py prepare
```

The default queue covers at least 90 minutes. Include a margin for your run when
choosing a different duration:

```sh
music-next-run.py prepare --minutes 120
```

In Arpeggi, open `Next Run`, pull to refresh, and download its current tracks.
Wait for downloads to finish before leaving. Start with the first track and keep
shuffle, repeat, and continuous playback off. Avoid starting playback from the
whole downloads collection, which can include tracks from previous queues.

Before each subsequent run, explicitly request a different queue:

```sh
music-next-run.py prepare --new
```

Without `--new`, the command reuses the prepared queue, including its original
duration and order. This makes retries safe but does not make replaying that queue
a different run. The phone must refresh and download after every new preparation.

Preparation and downloading are separate steps. The command cannot verify the
phone's cached playlist or control Arpeggi's download completion.

## Selection and cooldowns

The command enumerates the account's library through Navidrome's Subsonic API and
fetches the current source playlist. It groups library copies by MusicBrainz
recording ID, ISRC, or normalized artist/title and duration rounded to one second.
Different version titles and durations remain separate unless a shared recording
identifier links them. Conflicting known recording identifiers prevent a
metadata-only merge. Ambiguous metadata matches remain separate.

Metadata matching is not audio fingerprinting. Incorrect or incomplete tags can
leave an unrecognized duplicate, or conflate versions with indistinguishable
metadata. The three album copies that prompted this workflow share the metadata
fingerprint and therefore share one recording identity.

Each recording gets one persistent random position, regardless of its number of
copies. Selection consumes unselected eligible recordings first. After that pool
is exhausted, previously selected eligible recordings get new random positions.
Neither exhaustion nor an undersized pool relaxes the cooldown.

A recording is eligible only when both conditions hold:

- No library copy has a recorded play within the last 30 days.
- No prepared queue has reserved it within the last 30 days.

Reservations commit locally before publication. They apply to the entire matched
recording, even if the run has not happened or Arpeggi has not uploaded its offline
history. Unplayed margin tracks also remain reserved. Selection stops once the
queue reaches the requested duration, without repeating a recording in that queue.
An original album copy is preferred over a `Top 50 Hits of ...` compilation copy
when both are eligible.

The guarantee applies to recognized recordings in newly generated queues, using
history available at preparation time. Replaying an old downloaded queue, allowing
continuous playback to leave the queue, another player listening after preparation,
or losing the local reservation database falls outside that guarantee.

## Inspection and recovery

Preview a new queue without local or server writes:

```sh
music-next-run.py prepare --new --dry-run
```

Inspect local publication status without contacting Navidrome or Keychain:

```sh
music-next-run.py status
```

If publication fails, reservations remain and the queue is pending. Rerun
`prepare` without `--new` to reconcile or finish publication of the same queue.
An expired pending queue renews its reservations before retrying. An expired
published queue requires `--new`.

If a pending queue cannot be published, for example because a selected file was
removed, abandon it locally and prepare a replacement:

```sh
music-next-run.py abandon
music-next-run.py prepare --new
```

Abandoning keeps every reservation and does not alter the server playlist. Do not
play the abandoned queue. If the managed server playlist was renamed or deleted,
restore its original name and identity before retrying. The command refuses to
replace an unrelated existing playlist called `Next Run`.

The source can be another owned playlist:

```sh
music-next-run.py prepare --new --source "Running: Hard" --minutes 90
```

All sources share the account's deck and reservations. The recording-level
cooldown still applies even if the chosen source has no cooldown rule of its own.

## Configuration and state

The command uses the existing `~/.config/navidrome/env` assignments
`NAVIDROME_URL` and `NAVIDROME_USERNAME`, plus the existing macOS Keychain password
under service `Navidrome` and that account name. It parses configuration as data,
never shell code. Authentication tokens travel in POST bodies; redirects are
refused. Use HTTPS outside a trusted network.

The implementation uses only Python's standard library and supports Apple's
`/usr/bin/python3`. It lives at `stow/bin/.local/bin/music-next-run.py` and is
installed through the existing `bin` Stow package.

Private state lives under `~/.local/state/music-next-run/<scope>/queue.sqlite3`.
The scope is a hash of server URL and account. The profile directory is mode 700;
the database and lock file are mode 600. Concurrent preparations are refused.
Credentials are not stored in this database, and errors do not print remote
messages, authenticated URLs, or private topology.

Preserve this database across reinstalls. Deleting it, moving it, or changing the
server URL/account starts a separate reservation history and loses offline-repeat
protection. The database records prepared song IDs, identity aliases, deck order,
and reservation dates. Keep it out of Git and synchronize neither active copies
nor concurrent writers across machines.

Run the focused regressions with:

```sh
/usr/bin/python3 -m unittest discover -s scripts/tests -p test_music_next_run.py
```
