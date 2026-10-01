#!/usr/bin/env python3
"""Offline queue regressions using fictional metadata and an in-memory server."""

import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("music_next_run", ROOT / "stow/bin/.local/bin/music-next-run.py")
queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(queue)
NOW = 2_000_000_000


def song(song_id, title=None, **extra):
    return dict(id=song_id, title=title or "Track " + song_id, artist="Example Artist",
                album="Example Album", duration=60, playCount=0, **extra)


class Server:
    user = "example"

    def __init__(self, songs=None, entries=None):
        songs = songs or [song(str(n)) for n in range(20)]
        self.songs = {s["id"]: copy.deepcopy(s) for s in songs}
        self.data = {"source": dict(id="source", name="Running", owner=self.user,
                                   songCount=len(entries if entries is not None else songs),
                                   entry=copy.deepcopy(entries if entries is not None else songs))}
        self.writes = []
        self.fail = None
        self.reads = 0

    def playlists(self):
        self.reads += 1
        return copy.deepcopy(list(self.data.values()))

    def playlist(self, playlist_id):
        self.reads += 1
        return copy.deepcopy(self.data[playlist_id])

    def library(self):
        self.reads += 1
        return copy.deepcopy(self.songs)

    def call(self, endpoint, **params):
        self.writes.append((endpoint, copy.deepcopy(params)))
        if self.fail == "before":
            self.fail = None
            raise queue.QueueError("Request failed.")
        if endpoint == "createPlaylist":
            playlist_id = params.get("playlistId", "target")
            entries = [copy.deepcopy(self.songs[song_id]) for song_id in params["songId"]]
            previous = self.data.get(playlist_id, {})
            self.data[playlist_id] = dict(previous, id=playlist_id, name=params.get("name", previous.get("name")),
                                          owner=self.user, public=False, songCount=len(entries), entry=entries)
            if self.fail == "after_create":
                self.fail = None
                raise queue.QueueError("Creation response lost.")
            return {"playlist": copy.deepcopy(self.data[playlist_id])}
        if endpoint == "updatePlaylist":
            target = self.data[params["playlistId"]]
            target["public"] = params["public"] == "true"
            if "comment" in params:
                target["comment"] = params["comment"]
            if self.fail == "after_update":
                self.fail = None
                raise queue.QueueError("Update response lost.")
            return {}
        raise AssertionError(endpoint)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        queue.initialize(self.db)
        self.addCleanup(self.db.close)
        self.server = Server()

    def prepare(self, **kwargs):
        return queue.prepare(self.db, self.server, minutes=kwargs.pop("minutes", 2), now=kwargs.pop("now", NOW), **kwargs)[0]

    def ids(self, run):
        return json.loads(run["songs"])

    def test_shared_history_excludes_unplayed_compilation_copy(self):
        original = song("original", "Same Song", played=dt.datetime.fromtimestamp(NOW - 86400, dt.timezone.utc).isoformat())
        duplicate = song("compilation", "Same Song")
        duplicate["album"] = "Top 50 Hits of 2025"
        other = song("other")
        self.server = Server([original, duplicate, other], [duplicate, other])
        run = self.prepare(minutes=1)
        self.assertEqual(self.ids(run), ["other"])
        self.assertEqual(len(self.server.data["target"]["entry"]), 1)
        self.assertFalse(self.server.data["target"]["public"])

    def test_deduplicates_recordings_and_prefers_original_album(self):
        original = song("original", " Same Song ")
        duplicate = song("copy", "same song")
        duplicate["album"] = "Top 50 Hits of 2025"
        self.server = Server([duplicate, original, song("other")])
        run = self.prepare()
        self.assertEqual(set(self.ids(run)), {"original", "other"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM recording").fetchone()[0], 2)

    def test_separate_version_titles_and_durations_are_preserved(self):
        songs = [song("studio", "Same Song"), song("live", "Same Song (Live)"), song("edit", "Same Song")]
        songs[2]["duration"] = 30
        mapping = queue.sync_recordings(self.db, {s["id"]: s for s in songs}, random.Random(3))
        self.assertEqual(len(set(mapping.values())), 3)

    def test_conflicting_strong_identifiers_do_not_merge_by_metadata(self):
        songs = [song("a", "Same Song", musicBrainzId="recording-a"),
                 song("b", "Same Song", musicBrainzId="recording-b"), song("unknown", "Same Song")]
        for _ in range(2):
            mapping = queue.sync_recordings(self.db, {s["id"]: s for s in songs}, random.Random(3))
            self.assertEqual(len(set(mapping.values())), 3)

    def test_stored_authoritative_ids_survive_missing_incoming_tags(self):
        songs = [song("a", "Same Song", musicBrainzId="recording-a"),
                 song("b", "Same Song", musicBrainzId="recording-b")]
        first = queue.sync_recordings(self.db, {s["id"]: s for s in songs}, random.Random(3))
        del songs[0]["musicBrainzId"]
        second = queue.sync_recordings(self.db, {s["id"]: s for s in songs}, random.Random(3))
        self.assertNotEqual(second["a"], second["b"])
        self.assertEqual(first, second)

    def test_strong_identity_matches_despite_tag_or_duration_differences(self):
        a = song("a", "Alternate Credit", isrc=["US-ABC-25-12345"])
        b = song("b", "Different Title", isrc=["USABC2512345"])
        b["duration"] = 61
        mapping = queue.sync_recordings(self.db, {"a": a, "b": b}, random.Random(3))
        self.assertEqual(mapping["a"], mapping["b"])

    def test_new_runs_do_not_overlap_without_any_play_reports(self):
        first = self.prepare()
        second = self.prepare(new=True, now=NOW + 86400)
        self.assertTrue(set(self.ids(first)).isdisjoint(self.ids(second)))
        self.assertEqual(self.db.execute("SELECT count(*) FROM recording WHERE reserved_until > ?", (NOW,)).fetchone()[0], 4)
        self.assertEqual(json.loads(queue.latest(self.db)["songs"]), self.ids(second))

    def test_repeat_prepare_reuses_queue_and_reservations(self):
        first = self.prepare()
        before = list(self.db.execute("SELECT id, reserved_until FROM recording ORDER BY id"))
        second = self.prepare(now=NOW + 86400, minutes=10)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.ids(first), self.ids(second))
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in self.db.execute("SELECT id, reserved_until FROM recording ORDER BY id")])
        self.assertEqual(sum(endpoint == "createPlaylist" and bool(params["songId"])
                             for endpoint, params in self.server.writes), 1)

    def test_insufficient_duration_rolls_back_all_selection_changes(self):
        self.server = Server([song("a")])
        with self.assertRaisesRegex(queue.QueueError, "Too few eligible"):
            self.prepare(minutes=2)
        self.assertIsNone(queue.latest(self.db))
        self.assertEqual(self.db.execute("SELECT count(*) FROM recording").fetchone()[0], 0)
        self.assertEqual(self.server.writes, [])

    def test_publication_failure_keeps_reservations_and_blocks_new(self):
        self.server.fail = "before"
        with self.assertRaises(queue.QueueError):
            self.prepare()
        pending = queue.latest(self.db)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(self.db.execute("SELECT count(*) FROM recording WHERE reserved_until > ?", (NOW,)).fetchone()[0], 2)
        with self.assertRaisesRegex(queue.QueueError, "pending"):
            self.prepare(new=True)
        resumed = self.prepare()
        self.assertEqual(resumed["id"], pending["id"])
        self.assertEqual(queue.latest(self.db)["status"], "published")

    def test_lost_create_response_reconciles_without_duplicate_playlist(self):
        self.server.fail = "after_create"
        with self.assertRaises(queue.QueueError):
            self.prepare()
        pending = queue.latest(self.db)
        self.assertIsNone(pending["target_id"])
        self.prepare()
        self.assertEqual(sum(endpoint == "createPlaylist" and "name" in params
                             for endpoint, params in self.server.writes), 1)
        self.assertEqual(queue.latest(self.db)["target_id"], "target")

    def test_lost_metadata_update_response_retries_same_run(self):
        self.server.fail = "after_update"
        with self.assertRaises(queue.QueueError):
            self.prepare()
        pending = queue.latest(self.db)
        self.assertEqual(pending["target_id"], "target")
        resumed = self.prepare()
        self.assertEqual(resumed["id"], pending["id"])
        self.assertEqual(sum(endpoint == "createPlaylist" and bool(params["songId"])
                             for endpoint, params in self.server.writes), 1)

    def test_expired_pending_publication_renews_reservations_before_retry(self):
        self.server.fail = "before"
        with self.assertRaises(queue.QueueError):
            self.prepare()
        pending = queue.latest(self.db)
        resumed = self.prepare(now=NOW + queue.COOLDOWN + 100)
        self.assertEqual(resumed["id"], pending["id"])
        self.assertEqual(queue.latest(self.db)["status"], "published")
        rows = self.db.execute("SELECT reserved_until FROM recording WHERE consumed = 1").fetchall()
        self.assertTrue(all(row[0] == NOW + 2 * queue.COOLDOWN + 100 for row in rows))

    def test_public_playlist_is_made_private_before_replacing_contents(self):
        self.prepare()
        old_ids = [s["id"] for s in self.server.data["target"]["entry"]]
        self.server.data["target"]["public"] = True
        self.server.writes.clear()
        self.server.fail = "before"
        with self.assertRaises(queue.QueueError):
            self.prepare(new=True, now=NOW + 100)
        self.assertEqual(self.server.writes[0][0], "updatePlaylist")
        self.assertEqual([s["id"] for s in self.server.data["target"]["entry"]], old_ids)
        self.server.writes.clear()
        self.prepare(now=NOW + 100)
        self.assertEqual(self.server.writes[0], ("updatePlaylist", dict(playlistId="target", public="false")))
        self.assertFalse(self.server.data["target"]["public"])

    def test_existing_unmanaged_target_is_not_overwritten(self):
        self.server.data["unmanaged"] = dict(id="unmanaged", name="Next Run", owner=self.server.user,
                                            songCount=1, entry=[song("0")], comment="Handmade")
        with self.assertRaisesRegex(queue.QueueError, "unmanaged"):
            self.prepare()
        self.assertEqual(self.server.writes, [])
        self.assertIsNone(queue.latest(self.db))

    def test_renamed_or_replaced_managed_playlist_is_not_overwritten(self):
        self.prepare()
        self.server.data["target"]["name"] = "Another Playlist"
        self.server.writes.clear()
        with self.assertRaisesRegex(queue.QueueError, "renamed"):
            self.prepare()
        self.assertEqual(self.server.writes, [])

    def test_reservations_survive_track_id_changes(self):
        a = song("a", "Same Song")
        self.server = Server([a, song("b")])
        first = self.prepare(minutes=1)
        selected = self.server.songs[self.ids(first)[0]]
        replacement = dict(selected, id="new-id")
        self.server.songs = {"new-id": replacement, "another": song("another")}
        self.server.data["source"].update(songCount=2, entry=list(self.server.songs.values()))
        second = self.prepare(new=True, minutes=1, now=NOW + 100)
        self.assertEqual(self.ids(second), ["another"])

    def test_merging_identity_keeps_longest_reservation_and_consumption(self):
        a, b = song("a"), song("b")
        mapping = queue.sync_recordings(self.db, {"a": a, "b": b}, random.Random(3))
        self.db.execute("UPDATE recording SET reserved_until = ?, consumed = 1 WHERE id = ?", (NOW + 100, mapping["a"]))
        self.db.execute("UPDATE recording SET reserved_until = ? WHERE id = ?", (NOW + 200, mapping["b"]))
        a["musicBrainzId"] = b["musicBrainzId"] = "shared"
        mapping = queue.sync_recordings(self.db, {"a": a, "b": b}, random.Random(3))
        self.assertEqual(mapping["a"], mapping["b"])
        row = self.db.execute("SELECT * FROM recording").fetchone()
        self.assertEqual(row["reserved_until"], NOW + 200)
        self.assertEqual(row["consumed"], 1)

    def test_shuffled_deck_consumes_unplayed_before_reusing_old_tracks(self):
        self.server = Server([song(str(n)) for n in range(4)])
        first = self.prepare()
        second = self.prepare(new=True, now=NOW + queue.COOLDOWN + 1)
        self.assertTrue(set(self.ids(first)).isdisjoint(self.ids(second)))
        third = self.prepare(new=True, now=NOW + 2 * queue.COOLDOWN + 2)
        self.assertEqual(len(set(self.ids(third))), 2)

    def test_deck_exhaustion_never_bypasses_cooldown(self):
        self.server = Server([song("a"), song("b")])
        self.prepare()
        with self.assertRaisesRegex(queue.QueueError, "Too few eligible"):
            self.prepare(new=True, now=NOW + 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM run").fetchone()[0], 1)

    def test_expired_or_abandoned_queue_requires_new(self):
        self.prepare()
        with self.assertRaisesRegex(queue.QueueError, "expired"):
            self.prepare(now=NOW + queue.COOLDOWN)
        self.db.execute("UPDATE run SET status = 'abandoned'")
        with self.assertRaisesRegex(queue.QueueError, "abandoned"):
            self.prepare()
        self.prepare(new=True, now=NOW + 100)
        self.assertEqual(self.db.execute("SELECT count(*) FROM recording WHERE reserved_until > ?", (NOW,)).fetchone()[0], 4)

    def test_missing_snapshot_song_or_invalid_history_stops_without_writes(self):
        self.server.data["source"]["entry"].append(song("not-in-library"))
        with self.assertRaisesRegex(queue.QueueError, "Library changed"):
            self.prepare()
        self.assertEqual(self.server.writes, [])

    def test_fractional_dates_are_compatible_with_apple_python(self):
        expected = dt.datetime(2026, 1, 2, 10, 30, 0, tzinfo=dt.timezone.utc).timestamp()
        for fraction in ("", ".1", ".12", ".123", ".1234", ".12345", ".123456789"):
            with self.subTest(fraction=fraction):
                timestamp = queue.played_at(dict(played="2026-01-02T03:30:00" + fraction + "-07:00"))
                self.assertAlmostEqual(timestamp, expected + (float(fraction) if fraction else 0), places=5)
        self.assertEqual(queue.played_at(dict(played="2026-01-02T10:30:00Z")), expected)
        for invalid in (dict(played="invalid"), dict(played="2026-01-02T10:30:00"), dict(playCount=1)):
            with self.assertRaises(queue.QueueError):
                queue.played_at(invalid)


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        queue.initialize(self.db)
        self.addCleanup(self.db.close)
        self.server = Server()

    def ids(self, run):
        return json.loads(run["songs"])

    def daily(self, **kwargs):
        return queue.daily(self.db, self.server, minutes=2, now=kwargs.pop("now", NOW), **kwargs)[0]

    def test_daily_same_day_is_silent_without_server_access(self):
        first = self.daily()
        reads, writes = self.server.reads, len(self.server.writes)
        result = queue.daily(self.db, None, now=NOW + 60)
        self.assertEqual(result, (None, None))
        self.assertEqual(self.server.reads, reads)
        self.assertEqual(len(self.server.writes), writes)
        self.assertEqual(queue.latest(self.db)["id"], first["id"])

    def test_daily_uses_calendar_boundary_not_elapsed_24_hours(self):
        before = dt.datetime(2026, 1, 1, 23, 59).timestamp()
        after = dt.datetime(2026, 1, 2, 0, 0).timestamp()
        first = self.daily(now=before)
        second = self.daily(now=after)
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue(set(self.ids(first)).isdisjoint(self.ids(second)))
        self.assertEqual(queue.calendar_day(second["created"]), dt.date(2026, 1, 2))

    def test_daily_catches_up_without_generating_missed_days(self):
        first = self.daily()
        second = self.daily(now=NOW + 3 * 86400)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM run").fetchone()[0], 2)

    def test_daily_resumes_same_day_pending_queue_without_new_reservations(self):
        self.server.fail = "after_create"
        with self.assertRaises(queue.QueueError):
            self.daily()
        pending = queue.latest(self.db)
        resumed = self.daily(now=NOW + 60)
        self.assertEqual(resumed["id"], pending["id"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM run").fetchone()[0], 1)
        self.assertTrue(queue.daily_ready(self.db, NOW + 60))

    def test_daily_finishes_old_pending_queue_then_prepares_today(self):
        self.server.fail = "after_update"
        with self.assertRaises(queue.QueueError):
            self.daily()
        pending = queue.latest(self.db)
        today = self.daily(now=NOW + 86400)
        self.assertNotEqual(today["id"], pending["id"])
        self.assertTrue(set(self.ids(today)).isdisjoint(self.ids(pending)))
        self.assertEqual(self.db.execute("SELECT count(*) FROM run WHERE status = 'published'").fetchone()[0], 2)

    def test_daily_retries_failed_new_day_publication_without_reshuffling(self):
        self.daily()
        self.server.fail = "before"
        with self.assertRaises(queue.QueueError):
            self.daily(now=NOW + 86400)
        pending = queue.latest(self.db)
        resumed = self.daily(now=NOW + 86400 + 1800)
        self.assertEqual(resumed["id"], pending["id"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM run").fetchone()[0], 2)

    def test_daily_expired_pending_retry_still_generates_a_new_day(self):
        self.server.fail = "before"
        with self.assertRaises(queue.QueueError):
            self.daily()
        pending = queue.latest(self.db)
        today = self.daily(now=NOW + queue.COOLDOWN + 86400)
        self.assertNotEqual(today["id"], pending["id"])
        self.assertTrue(set(self.ids(today)).isdisjoint(self.ids(pending)))

    def test_expired_pending_recovery_does_not_mark_old_queue_as_today(self):
        self.server.fail = "before"
        with self.assertRaises(queue.QueueError):
            self.daily()
        old = queue.latest(self.db)
        now = NOW + queue.COOLDOWN + 86400
        with patch.object(self.server, "library", side_effect=queue.QueueError("Library unavailable.")):
            with self.assertRaises(queue.QueueError):
                self.daily(now=now)
        self.assertFalse(queue.daily_ready(self.db, now))
        self.assertEqual(queue.latest(self.db)["created"], old["created"])
        today = self.daily(now=now + 1800)
        self.assertNotEqual(today["id"], old["id"])
        self.assertTrue(set(self.ids(today)).isdisjoint(self.ids(old)))

    def test_daily_dry_run_does_not_publish_or_reserve(self):
        self.daily()
        before = list(self.db.execute("SELECT * FROM recording"))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "queue.sqlite3"
            with contextlib.closing(sqlite3.connect(path)) as copy_db:
                self.db.backup(copy_db)
            self.server.writes.clear()
            with queue.state(path, dry_run=True) as db:
                preview, _ = queue.daily(db, self.server, minutes=2, now=NOW + 86400, dry_run=True)
                self.assertEqual(queue.calendar_day(preview["created"]), queue.calendar_day(NOW + 86400))
            with contextlib.closing(sqlite3.connect(path)) as existing:
                self.assertEqual(existing.execute("SELECT count(*) FROM run").fetchone()[0], 1)
        self.assertEqual(self.server.writes, [])
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in self.db.execute("SELECT * FROM recording")])

    def test_previous_day_pending_dry_run_keeps_real_pending_state(self):
        self.server.fail = "after_create"
        with self.assertRaises(queue.QueueError):
            self.daily()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "queue.sqlite3"
            with contextlib.closing(sqlite3.connect(path)) as copy_db:
                self.db.backup(copy_db)
            before = path.read_bytes()
            self.server.writes.clear()
            with queue.state(path, dry_run=True) as db:
                preview, _ = queue.daily(db, self.server, minutes=2, now=NOW + 86400, dry_run=True)
                self.assertEqual(queue.calendar_day(preview["created"]), queue.calendar_day(NOW + 86400))
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.server.writes, [])
        self.assertEqual(queue.latest(self.db)["status"], "pending")

    def test_thirty_daily_queues_have_no_overlapping_recordings(self):
        self.server = Server([song(str(n)) for n in range(90)])
        heard = set()
        for day in range(30):
            run = self.daily(now=NOW + day * 86400)
            ids = set(self.ids(run))
            self.assertTrue(heard.isdisjoint(ids))
            heard.update(ids)
        self.assertEqual(len(heard), 60)

    def test_main_daily_ready_skips_keychain_and_power_gate(self):
        self.daily()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "queue.sqlite3"
            with contextlib.closing(sqlite3.connect(path)) as copy_db:
                self.db.backup(copy_db)
            before = path.read_bytes()
            with patch.object(queue, "configuration", return_value=("https://example.invalid", "example")), \
                    patch.object(queue, "state_path", return_value=path), \
                    patch.object(queue.time, "time", return_value=NOW), \
                    patch.object(queue.Navidrome, "from_keychain", side_effect=AssertionError("Keychain accessed")), \
                    patch.object(queue.subprocess, "run", side_effect=AssertionError("Power gate invoked")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(queue.main(["daily"]), 0)
                self.assertEqual(output.getvalue(), "")
            self.assertEqual(path.read_bytes(), before)

    def test_main_daily_work_uses_deadline_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "queue.sqlite3"
            with patch.object(queue, "configuration", return_value=("https://example.invalid", "example")), \
                    patch.object(queue, "state_path", return_value=path), \
                    patch.object(queue.time, "time", return_value=NOW), \
                    patch.object(queue.Navidrome, "from_keychain", return_value=self.server), \
                    patch.object(queue.subprocess, "run", return_value=Mock(returncode=0)) as gate, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(queue.main(["daily", "--minutes", "2"]), 0)
                self.assertEqual(gate.call_args.args[0][-1], "--urgent")

    def test_agent_template_and_setup_use_daily_entrypoint(self):
        template = ROOT / "stow/bin/Library/LaunchAgents/com.user.music-next-run.plist.template"
        config = plistlib.loads(template.read_bytes())
        self.assertEqual(config["Label"], "com.user.music-next-run")
        self.assertEqual(config["ProgramArguments"], ["/usr/bin/python3", "__HOME__/.local/bin/music-next-run.py", "daily"])
        self.assertEqual(config["StartCalendarInterval"], [{"Minute": 0}, {"Minute": 30}])
        self.assertTrue(config["RunAtLoad"])
        self.assertEqual(config["Umask"], 0o77)
        self.assertTrue(SPEC.origin and Path(SPEC.origin).read_text().startswith("#!/usr/bin/python3\n"))
        self.assertIn('load_launch_agent "$HOME/Library/LaunchAgents/com.user.music-next-run.plist"', (ROOT / "scripts/setup.sh").read_text())


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private/queue.sqlite3"

    def test_private_state_lock_and_persistence(self):
        with queue.state(self.path) as db:
            queue.prepare(db, Server(), minutes=2, now=NOW)
            with self.assertRaisesRegex(queue.QueueError, "in progress"):
                with queue.state(self.path):
                    pass
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        with queue.state(self.path) as db:
            self.assertEqual(queue.latest(db)["status"], "published")

    def test_dry_run_does_not_create_or_change_state_or_server(self):
        server = Server()
        with queue.state(self.path, dry_run=True) as db:
            queue.prepare(db, server, minutes=2, now=NOW, dry_run=True)
        self.assertFalse(self.path.parent.exists())
        self.assertEqual(server.writes, [])
        with queue.state(self.path) as db:
            queue.prepare(db, server, minutes=2, now=NOW)
        before = self.path.read_bytes()
        server.writes.clear()
        with queue.state(self.path, dry_run=True) as db:
            queue.prepare(db, server, minutes=2, now=NOW + 100, new=True, dry_run=True)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(server.writes, [])

    def test_state_symlinks_are_rejected(self):
        self.path.parent.mkdir()
        real = self.path.parent / "real"
        real.write_text("canary")
        self.path.symlink_to(real)
        for dry_run in (True, False):
            with self.assertRaises((queue.QueueError, OSError)):
                with queue.state(self.path, dry_run):
                    pass
        self.assertEqual(real.read_text(), "canary")

    def test_scope_separates_server_and_account(self):
        self.assertNotEqual(queue.state_path("https://a.invalid", "a"), queue.state_path("https://b.invalid", "a"))
        self.assertNotEqual(queue.state_path("https://a.invalid", "a"), queue.state_path("https://a.invalid", "b"))


class ApiTests(unittest.TestCase):
    def test_auth_stays_in_post_body_and_keychain_newline_is_preserved(self):
        with patch.object(queue.subprocess, "run", return_value=Mock(returncode=0, stdout=b" lead\ntrail \n")) as security:
            api = queue.Navidrome.from_keychain("https://music.example.invalid", "example")
        self.assertNotIn(" lead", repr(security.call_args))
        self.assertEqual(api.token, queue.hashlib.md5((" lead\ntrail " + api.salt).encode()).hexdigest())
        response = io.BytesIO(json.dumps({"subsonic-response": {"status": "ok"}}).encode())
        api.opener = Mock()
        api.opener.open.return_value = response
        api.call("createPlaylist", songId=["one", "two"])
        request = api.opener.open.call_args[0][0]
        self.assertEqual(request.full_url, "https://music.example.invalid/rest/createPlaylist")
        fields = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(fields["songId"], ["one", "two"])
        self.assertEqual(fields["t"], [api.token])
        self.assertNotIn(b"trail", request.data)

    def test_remote_error_text_and_urls_are_not_exposed(self):
        api = queue.Navidrome("https://music.example.invalid", "example", "test-only")
        api.opener = Mock()
        api.opener.open.side_effect = urllib.error.URLError("private-url-canary")
        with self.assertRaises(queue.QueueError) as caught:
            api.call("ping")
        self.assertNotIn("canary", str(caught.exception))
        api.opener.open.side_effect = None
        api.opener.open.return_value = io.BytesIO(json.dumps({"subsonic-response": {"status": "failed", "error": {"message": "private-canary"}}}).encode())
        with self.assertRaises(queue.QueueError) as caught:
            api.call("ping")
        self.assertNotIn("canary", str(caught.exception))
        self.assertIsNone(queue.NoRedirect().redirect_request(None, None, 302, "", {}, "https://another.invalid"))

    def test_navidrome_omits_false_public_field(self):
        api = queue.Navidrome("https://music.example.invalid", "example", "test-only")
        api.call = Mock(return_value={"playlist": dict(id="target", owner="example", songCount=0)})
        self.assertFalse(api.playlist("target")["public"])
        for invalid in (None, 0, "false"):
            api.call = Mock(return_value={"playlist": dict(id="target", owner="example", songCount=0, public=invalid)})
            with self.assertRaisesRegex(queue.QueueError, "privacy"):
                api.playlist("target")

    def test_pagination_and_duplicate_page_detection(self):
        api = queue.Navidrome("https://music.example.invalid", "example", "test-only")
        batch = [song(str(n)) for n in range(1000)]
        api.call = Mock(side_effect=[{"searchResult3": {"song": batch}}, {"searchResult3": {"song": [song("last")]}}])
        self.assertEqual(len(api.library()), 1001)
        self.assertEqual(api.call.call_args_list[1].kwargs["songOffset"], 1000)
        api.call = Mock(side_effect=[{"searchResult3": {"song": batch}}, {"searchResult3": {"song": batch}}])
        with self.assertRaisesRegex(queue.QueueError, "pagination"):
            api.library()

    def test_config_is_parsed_as_data_and_unsafe_urls_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "env"
            path.write_text('NAVIDROME_URL="https://music.example.invalid/prefix/"\nexport NAVIDROME_USERNAME="example"\nOTHER="$(do-not-run)"\n')
            self.assertEqual(queue.configuration(path), ("https://music.example.invalid/prefix", "example"))
            for url in ("file:///tmp/example", "https://user:password@example.invalid", "https://example.invalid/?token=bad", "http://CHANGEME:4533"):
                path.write_text('NAVIDROME_URL="' + url + '"\nNAVIDROME_USERNAME="example"\n')
                with self.assertRaises(queue.QueueError):
                    queue.configuration(path)


if __name__ == "__main__":
    unittest.main()
