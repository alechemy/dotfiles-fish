#!/usr/bin/python3
"""Prepare an offline Navidrome run queue with recording-level reservations."""

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

TARGET = "Next Run"
COOLDOWN = 30 * 86400
DEFAULT_MINUTES = 180
MARKER = "music-next-run:"


class QueueError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configuration(path=None):
    path = path or Path.home() / ".config/navidrome/env"
    values = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.removeprefix("export ")
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key.strip() in ("NAVIDROME_URL", "NAVIDROME_USERNAME"):
                parts = shlex.split(value, comments=True)
                if len(parts) != 1:
                    raise QueueError("Invalid Navidrome env assignment.")
                values[key.strip()] = parts[0]
    except (OSError, ValueError):
        raise QueueError("Cannot read Navidrome configuration.") from None
    url = values.get("NAVIDROME_URL", "").rstrip("/")
    user = values.get("NAVIDROME_USERNAME", "")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not user or "CHANGEME" in url + user
            or any(ord(c) < 32 for c in url + user)):
        raise QueueError("Set NAVIDROME_URL and NAVIDROME_USERNAME in ~/.config/navidrome/env.")
    return url, user


class Navidrome:
    def __init__(self, url, user, password):
        self.url = url
        self.user = user
        self.salt = uuid.uuid4().hex
        self.token = hashlib.md5((password + self.salt).encode()).hexdigest()
        self.opener = urllib.request.build_opener(NoRedirect())

    @classmethod
    def from_keychain(cls, url, user):
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Navidrome", "-a", user, "-w"],
            capture_output=True, timeout=30)
        if result.returncode or not result.stdout.endswith(b"\n"):
            raise QueueError("Cannot read the Navidrome password from Keychain.")
        return cls(url, user, result.stdout[:-1].decode())

    def call(self, endpoint, **params):
        fields = dict(u=self.user, t=self.token, s=self.salt, v="1.16.1",
                      c="music-next-run", f="json", **params)
        request = urllib.request.Request(
            self.url + "/rest/" + endpoint,
            data=urllib.parse.urlencode(fields, doseq=True).encode())
        try:
            with self.opener.open(request, timeout=45) as response:
                raw = response.read(64 * 1024 * 1024 + 1)
            if len(raw) > 64 * 1024 * 1024:
                raise QueueError("Navidrome response exceeds the size limit.")
            body = json.loads(raw)["subsonic-response"]
            if not isinstance(body, dict) or body.get("status") != "ok":
                raise QueueError("Navidrome rejected " + endpoint + ".")
            return body
        except (urllib.error.URLError, TimeoutError, OSError):
            raise QueueError("Navidrome request failed: " + endpoint + ".") from None
        except (ValueError, KeyError, TypeError):
            raise QueueError("Invalid Navidrome response: " + endpoint + ".") from None

    def playlists(self):
        container = self.call("getPlaylists").get("playlists", {})
        playlists = container.get("playlist", []) if isinstance(container, dict) else None
        if not isinstance(playlists, list) or any(
                not isinstance(p, dict) or any(not isinstance(p.get(key), str) or not p[key]
                                              for key in ("id", "name", "owner")) for p in playlists):
            raise QueueError("Invalid playlist listing.")
        return playlists

    def playlist(self, playlist_id):
        playlist = self.call("getPlaylist", id=playlist_id).get("playlist")
        if not isinstance(playlist, dict) or playlist.get("id") != playlist_id:
            raise QueueError("Invalid playlist response.")
        playlist.setdefault("public", False)
        if not isinstance(playlist["public"], bool):
            raise QueueError("Invalid playlist privacy response.")
        entries = playlist.get("entry", [])
        if (not isinstance(entries, list) or len(entries) != playlist.get("songCount")
                or any(not isinstance(song, dict) or not isinstance(song.get("id"), str)
                       or not song["id"] for song in entries)):
            raise QueueError("Incomplete playlist response.")
        return playlist

    def library(self):
        songs = {}
        for page in range(1000):
            result = self.call("search3", query="", artistCount=0, albumCount=0,
                               songCount=1000, songOffset=page * 1000)
            container = result.get("searchResult3", {})
            batch = container.get("song", []) if isinstance(container, dict) else None
            if not isinstance(batch, list):
                raise QueueError("Invalid library response.")
            for song in batch:
                if not isinstance(song, dict):
                    raise QueueError("Invalid library track response.")
                song_id = song.get("id")
                if not isinstance(song_id, str) or not song_id or song_id in songs:
                    raise QueueError("Library pagination changed; retry preparation.")
                songs[song_id] = song
            if len(batch) < 1000:
                return songs
        raise QueueError("Library exceeds the pagination limit.")


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def duration(song):
    value = song.get("duration")
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise QueueError("A library track has no valid duration.")
    return value


def recording_aliases(song):
    artist, title = song.get("artist"), song.get("title")
    if not isinstance(song.get("album", ""), str):
        raise QueueError("A library track has invalid album metadata.")
    if not isinstance(artist, str) or not artist.strip() or not isinstance(title, str) or not title.strip():
        raise QueueError("A library track has incomplete artist/title metadata.")
    identity = json.dumps([normalized(artist), normalized(title), round(duration(song))],
                          ensure_ascii=False, separators=(",", ":"))
    aliases = {"song:" + song["id"], "meta:" + hashlib.sha256(identity.encode()).hexdigest()}
    mbid = song.get("musicBrainzId")
    if isinstance(mbid, str) and mbid:
        aliases.add("mbid:" + mbid.lower())
    isrcs = song.get("isrc", [])
    if not isinstance(isrcs, list):
        raise QueueError("A library track has invalid ISRC metadata.")
    for isrc in isrcs:
        if isinstance(isrc, str):
            isrc = re.sub(r"[-\s]", "", isrc.upper())
            if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", isrc):
                aliases.add("isrc:" + isrc)
    return aliases


def played_at(song):
    value = song.get("played")
    if not value:
        if song.get("playCount", 0):
            raise QueueError("A played track has no last-played date.")
        return 0
    try:
        value = re.sub(r"\.(\d+)", lambda match: "." + (match[1] + "000000")[:6], value).replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        raise QueueError("A library track has an invalid last-played date.") from None


def initialize(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1):
        raise QueueError("Unsupported queue state version.")
    if version == 0:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS recording (
            id TEXT PRIMARY KEY, position TEXT NOT NULL,
            consumed INTEGER NOT NULL DEFAULT 0, reserved_until REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS alias (
            name TEXT NOT NULL, recording_id TEXT NOT NULL REFERENCES recording(id),
            PRIMARY KEY (name, recording_id)
        );
        CREATE TABLE IF NOT EXISTS run (
            id TEXT PRIMARY KEY, created REAL NOT NULL, source TEXT NOT NULL,
            duration REAL NOT NULL, songs TEXT NOT NULL, target_id TEXT,
            status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'abandoned'))
        );
        PRAGMA user_version = 1;
        """)
    db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = sqlite3.Row


def state_path(url, user):
    scope = hashlib.sha256(json.dumps([url, user]).encode()).hexdigest()
    return Path.home() / ".local/state/music-next-run" / scope / "queue.sqlite3"


@contextlib.contextmanager
def state(path, dry_run=False):
    if dry_run:
        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            if path.exists():
                if path.is_symlink():
                    raise QueueError("Queue state must not be a symlink.")
                with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as existing:
                    existing.backup(db)
            initialize(db)
            yield db
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    with contextlib.ExitStack() as stack:
        fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        lock = stack.enter_context(os.fdopen(fd, "w"))
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise QueueError("Another run preparation is in progress.") from None
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = stack.enter_context(contextlib.closing(sqlite3.connect(path)))
        initialize(db)
        yield db


def latest(db):
    return db.execute("SELECT * FROM run ORDER BY rowid DESC LIMIT 1").fetchone()


def sync_recordings(db, songs, rng):
    identifiers = {}
    redirects = {}
    for song in songs.values():
        aliases = recording_aliases(song)
        matches = {row[0] for alias in aliases if not alias.startswith("meta:") for row in db.execute(
            "SELECT recording_id FROM alias WHERE name = ?", (alias,))}
        fallback = {row[0] for alias in aliases if alias.startswith("meta:") for row in db.execute(
            "SELECT recording_id FROM alias WHERE name = ?", (alias,))}
        authoritative = set(aliases)
        for matched in matches:
            authoritative.update(row[0] for row in db.execute(
                "SELECT name FROM alias WHERE recording_id = ?", (matched,)))
        compatible = set()
        for candidate in fallback - matches:
            known = {row[0] for row in db.execute("SELECT name FROM alias WHERE recording_id = ?", (candidate,))}
            conflicts = False
            for namespace in ("mbid:", "isrc:"):
                incoming = {alias for alias in authoritative if alias.startswith(namespace)}
                existing = {alias for alias in known if alias.startswith(namespace)}
                if incoming and existing and incoming.isdisjoint(existing):
                    conflicts = True
            if not conflicts:
                compatible.add(candidate)
        if len(fallback - matches) == 1 and len(compatible) == 1:
            matches.update(compatible)
        if not matches:
            recording = uuid.uuid4().hex
            db.execute("INSERT INTO recording(id, position) VALUES (?, ?)",
                       (recording, "%032x" % rng.getrandbits(128)))
        else:
            recording = min(matches)
            for other in matches - {recording}:
                db.execute("""UPDATE recording SET
                    reserved_until = max(reserved_until, (SELECT reserved_until FROM recording WHERE id = ?)),
                    consumed = max(consumed, (SELECT consumed FROM recording WHERE id = ?)) WHERE id = ?""",
                           (other, other, recording))
                db.execute("INSERT OR IGNORE INTO alias SELECT name, ? FROM alias WHERE recording_id = ?",
                           (recording, other))
                db.execute("DELETE FROM alias WHERE recording_id = ?", (other,))
                db.execute("DELETE FROM recording WHERE id = ?", (other,))
                redirects[other] = recording
        for alias in aliases:
            db.execute("INSERT OR IGNORE INTO alias VALUES (?, ?)", (alias, recording))
        identifiers[song["id"]] = recording
    for song_id, recording in identifiers.items():
        while recording in redirects:
            recording = redirects[recording]
        identifiers[song_id] = recording
    return identifiers


def plan(db, library, source, minutes, now, rng=None):
    rng = rng or random.SystemRandom()
    entries = source.get("entry", [])
    if not entries:
        raise QueueError("The source playlist is empty.")
    if any(song["id"] not in library for song in entries):
        raise QueueError("Library changed while preparing; retry preparation.")
    library = dict(library)
    library.update((song["id"], song) for song in entries)
    mapping = sync_recordings(db, library, rng)
    last_played = {}
    for song in library.values():
        recording = mapping[song["id"]]
        last_played[recording] = max(last_played.get(recording, 0), played_at(song))
    candidates = {}
    for song in entries:
        recording = mapping[song["id"]]
        previous = candidates.get(recording)
        if previous is None or (song.get("album", "").startswith("Top 50 Hits of "), song["id"]) < (
                previous.get("album", "").startswith("Top 50 Hits of "), previous["id"]):
            candidates[recording] = song
    eligible = {}
    for recording, song in candidates.items():
        row = db.execute("SELECT * FROM recording WHERE id = ?", (recording,)).fetchone()
        if row["reserved_until"] <= now and last_played[recording] <= now - COOLDOWN:
            eligible[recording] = (row, song)
    if sum(duration(song) for _, song in eligible.values()) < minutes * 60:
        raise QueueError("Too few eligible recordings to cover the requested run; cooldown was not relaxed.")
    selected = []
    seconds = 0
    for consumed in (0, 1):
        available = [(recording, row, song) for recording, (row, song) in eligible.items()
                     if row["consumed"] == consumed]
        if consumed:
            for recording, _, _ in available:
                db.execute("UPDATE recording SET position = ?, consumed = 0 WHERE id = ?",
                           ("%032x" % rng.getrandbits(128), recording))
            available = [(recording, db.execute("SELECT * FROM recording WHERE id = ?", (recording,)).fetchone(), song)
                         for recording, _, song in available]
        for recording, _, song in sorted(available, key=lambda item: (item[1]["position"], item[0])):
            selected.append(song["id"])
            seconds += duration(song)
            db.execute("UPDATE recording SET consumed = 1, reserved_until = ? WHERE id = ?",
                       (now + COOLDOWN, recording))
            if seconds >= minutes * 60:
                break
        if seconds >= minutes * 60:
            break
    previous = latest(db)
    run_id = uuid.uuid4().hex
    db.execute("INSERT INTO run VALUES (?, ?, ?, ?, ?, ?, 'pending')",
               (run_id, now, source["name"], seconds, json.dumps(selected),
                previous["target_id"] if previous else None))
    return latest(db), len(candidates), len(eligible)


def owned_playlist(playlists, name, user):
    matches = [p for p in playlists if p.get("name") == name and p.get("owner") == user]
    if len(matches) > 1:
        raise QueueError("Multiple owned playlists have the requested name.")
    return matches[0] if matches else None


def publish(db, api, run):
    song_ids = json.loads(run["songs"])
    target_id = run["target_id"]
    target = owned_playlist(api.playlists(), TARGET, api.user)
    if target_id:
        if not target or target.get("id") != target_id:
            raise QueueError("The managed Next Run playlist was renamed, removed, or replaced.")
    elif target:
        actual = api.playlist(target["id"])
        if actual.get("entry", []) and [song["id"] for song in actual["entry"]] != song_ids:
            raise QueueError("An unmanaged Next Run playlist already exists; it was not overwritten.")
        target_id = target["id"]
    if not target_id:
        created = api.call("createPlaylist", name=TARGET, songId=[]).get("playlist", {})
        target_id = created.get("id")
        if not isinstance(target_id, str) or not target_id:
            raise QueueError("Playlist creation did not return an ID; rerun prepare to reconcile.")
    db.execute("UPDATE run SET target_id = ? WHERE id = ?", (target_id, run["id"]))
    db.commit()
    actual = api.playlist(target_id)
    if actual.get("public") is not False:
        api.call("updatePlaylist", playlistId=target_id, public="false")
        actual = api.playlist(target_id)
    if actual.get("public") is not False or actual.get("owner") != api.user:
        raise QueueError("Could not verify playlist privacy and ownership; contents were not replaced.")
    if [song["id"] for song in actual.get("entry", [])] != song_ids:
        api.call("createPlaylist", playlistId=target_id, songId=song_ids)
    comment = MARKER + run["id"] + ". Play in order with shuffle, repeat, and continuous playback off."
    api.call("updatePlaylist", playlistId=target_id, public="false", comment=comment)
    actual = api.playlist(target_id)
    if ([song["id"] for song in actual.get("entry", [])] != song_ids
            or actual.get("public") is not False or actual.get("owner") != api.user
            or actual.get("comment") != comment):
        raise QueueError("Published playlist verification failed; reservations remain. Rerun prepare.")
    db.execute("UPDATE run SET status = 'published' WHERE id = ?", (run["id"],))
    db.commit()


def prepare(db, api, minutes=DEFAULT_MINUTES, source_name="Running", new=False, dry_run=False, now=None):
    now = time.time() if now is None else now
    run = latest(db)
    if run and new and run["status"] == "pending":
        raise QueueError("A publication is pending. Rerun prepare without --new to finish it first.")
    if run and not new:
        if run["status"] == "abandoned":
            raise QueueError("The previous queue was abandoned; use prepare --new.")
        if now >= run["created"] + COOLDOWN:
            if run["status"] != "pending":
                raise QueueError("The prepared run has expired; use prepare --new.")
            with db:
                for song_id in json.loads(run["songs"]):
                    matches = list(db.execute("SELECT recording_id FROM alias WHERE name = ?", ("song:" + song_id,)))
                    if len(matches) != 1:
                        raise QueueError("Pending recording identity is missing; abandon this queue before using --new.")
                    db.execute("UPDATE recording SET reserved_until = max(reserved_until, ?) WHERE id = ?",
                               (now + COOLDOWN, matches[0][0]))
        if source_name != run["source"]:
            raise QueueError("Use --new to change the source playlist.")
        counts = None
    else:
        playlists = api.playlists()
        if run is None and owned_playlist(playlists, TARGET, api.user):
            raise QueueError("An unmanaged Next Run playlist already exists; it was not overwritten.")
        source = owned_playlist(playlists, source_name, api.user)
        if not source or source_name == TARGET:
            raise QueueError("No unambiguous owned source playlist was found.")
        library = api.library()
        source = api.playlist(source["id"])
        if source.get("owner") != api.user or source.get("name") != source_name:
            raise QueueError("Source playlist ownership or name changed; retry preparation.")
        with db:
            run, candidates, eligible = plan(db, library, source, minutes, now)
        counts = (candidates, eligible)
    if not dry_run:
        publish(db, api, run)
    return run, counts


def calendar_day(timestamp):
    return dt.datetime.fromtimestamp(timestamp).date()


def daily_ready(db, now):
    run = latest(db)
    return bool(run and run["status"] == "published" and calendar_day(run["created"]) == calendar_day(now))


def daily(db, api, minutes=DEFAULT_MINUTES, source_name="Running", dry_run=False, now=None):
    now = time.time() if now is None else now
    if daily_ready(db, now):
        return None, None
    run = latest(db)
    if run and run["status"] == "pending":
        previous_day = calendar_day(run["created"])
        resumed, counts = prepare(db, api, minutes, run["source"], dry_run=dry_run, now=now)
        if previous_day == calendar_day(now):
            return resumed, counts
        if dry_run:
            db.execute("UPDATE run SET status = 'published' WHERE id = ?", (run["id"],))
            db.commit()
    return prepare(db, api, minutes, source_name, new=True, dry_run=dry_run, now=now)


def describe(run, dry_run=False):
    count = len(json.loads(run["songs"]))
    prefix = "Would prepare" if dry_run else "Prepared"
    return "%s Next Run for %s with %d tracks covering %.1f minutes." % (
        prefix, calendar_day(run["created"]), count, run["duration"] / 60)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="Publish or reuse the prepared run")
    prep.add_argument("--new", action="store_true", help="Reserve a different queue for the next run")
    scheduled = sub.add_parser("daily", help="Ensure one published queue for the local calendar day")
    for command in (prep, scheduled):
        command.add_argument("--minutes", type=int, default=DEFAULT_MINUTES, help="Minimum queue duration, including your margin")
        command.add_argument("--source", default="Running", help="Owned source playlist name")
        command.add_argument("--dry-run", action="store_true", help="Preview without local or server writes")
    sub.add_parser("status", help="Show local queue status without contacting Navidrome")
    sub.add_parser("abandon", help="Abandon the active queue locally, keeping all reservations")
    args = parser.parse_args(argv)
    if args.command in ("prepare", "daily") and not 1 <= args.minutes <= 1440:
        parser.error("--minutes must be between 1 and 1440")
    try:
        url, user = configuration()
        path = state_path(url, user)
        if args.command == "status":
            with state(path, dry_run=True) as db:
                run = latest(db)
                print(describe(run) + " Publication is " + run["status"] + "." if run else "No run has been prepared.")
            return 0
        if args.command == "abandon":
            with state(path) as db:
                run = latest(db)
                if run:
                    db.execute("UPDATE run SET status = 'abandoned' WHERE id = ?", (run["id"],))
                    db.commit()
            print("The queue was abandoned locally. Reservations remain; use prepare --new.")
            return 0
        with state(path, args.dry_run) as db:
            now = time.time()
            if args.command == "daily":
                if daily_ready(db, now):
                    return 0
                gate = Path.home() / ".local/bin/should-run-background-job"
                if subprocess.run([str(gate), "--urgent"], capture_output=True, timeout=10).returncode:
                    raise QueueError("Daily queue preparation was blocked by the background-job gate.")
            api = Navidrome.from_keychain(url, user)
            if args.command == "daily":
                run, counts = daily(db, api, args.minutes, args.source, args.dry_run, now)
            else:
                run, counts = prepare(db, api, args.minutes, args.source, args.new, args.dry_run, now)
        print(describe(run, args.dry_run))
        if counts:
            print("Considered %d recordings; %d passed shared history and reservation cooldowns." % counts)
        if not args.dry_run:
            print("In Arpeggi, refresh and download Next Run. Play it in order with shuffle, repeat, and continuous playback off.")
        return 0
    except QueueError as error:
        print("ERROR: " + str(error), file=sys.stderr)
    except (OSError, sqlite3.Error, subprocess.SubprocessError, UnicodeError, ValueError):
        print("ERROR: Local configuration, Keychain, or queue state could not be read safely.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
