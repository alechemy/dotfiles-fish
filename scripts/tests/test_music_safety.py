#!/usr/bin/env python3
"""Offline regression fixtures. Mutagen is stubbed; no personal audio is read."""
import importlib.util
import json
import shlex
import subprocess
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BIN = Path(__file__).resolve().parents[2] / "stow/bin/.local/bin"


def load(name):
    mocks = {}
    for suffix, names in {
        "": [], "flac": ["FLAC"], "mp3": ["MP3"], "mp4": ["MP4", "MP4Cover", "MP4FreeForm"],
        "id3": ["COMM", "TALB", "TCMP", "TCON", "TCOP", "TDRC", "TIT2", "TPE1", "TPE2", "TPOS", "TRCK"],
    }.items():
        key = "mutagen" + ("." + suffix if suffix else "")
        module = types.ModuleType(key)
        for attr in names:
            setattr(module, attr, Mock(side_effect=AssertionError("unexpected audio read")))
        mocks[key] = module
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), BIN / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, mocks):
        spec.loader.exec_module(module)
    module._mutagen = mocks
    return module


class MusicSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root / "library"
        self.album = self.library / "Artist" / "Album"
        self.album.mkdir(parents=True)
        self.org = load("music-organize")
        self.tags = dict(albumartist="Artist", artist="Artist", album="Album", title="Song",
                         track=1, disc=1, disctotal=1, compilation=False)

    def organize(self, source, **kwargs):
        stats = {"moved": 0, "failed": [], "kept": []}
        with patch.object(self.org, "read_tags", return_value=self.tags):
            self.org.organize_source(str(source), str(self.library), "replace", False, set(), stats, **kwargs)
        return stats

    def test_in_place_file_and_guarded_source_survive(self):
        song = self.album / "01 Song.m4a"
        song.write_bytes(b"original")
        for kwargs in ({}, {"replaces": "Artist/Album", "archive_root": str(self.root / "archive")}):
            stats = self.organize(song, **kwargs)
            self.assertTrue(stats["failed"])
            self.assertEqual(song.read_bytes(), b"original")

    def test_other_source_under_destination_is_protected(self):
        source = self.root / "new.m4a"
        source.write_bytes(b"new")
        other = self.album / "01 Song.m4a"
        other.write_bytes(b"old")
        stats = self.organize(source, protected_sources=[str(other)])
        self.assertTrue(stats["failed"])
        self.assertEqual(other.read_bytes(), b"old")
        self.assertEqual(source.read_bytes(), b"new")

    def test_archive_artist_symlink_into_source_is_rejected_before_mutation(self):
        source = self.root / "source"
        source.mkdir()
        incoming = source / "01 Song.m4a"
        incoming.write_bytes(b"new")
        old = self.album / "01 Song.m4a"
        old.write_bytes(b"old")
        archive = self.root / "archive"
        dated = archive / self.org.datetime.now().strftime("%Y-%m-%d")
        dated.mkdir(parents=True)
        (dated / "Artist").symlink_to(source)
        stats = self.organize(source, archive_root=str(archive))
        self.assertTrue(stats["failed"])
        self.assertEqual(old.read_bytes(), b"old")
        self.assertEqual(incoming.read_bytes(), b"new")
        self.assertFalse((source / "Album").exists())

    def test_replacement_paths_reject_absolute_traversal_root_and_symlink(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.library / "escape").symlink_to(outside, target_is_directory=True)
        for name in (str(outside), "../outside", ".", "Artist/../../outside", "escape"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.org.replacement_path(str(self.library), name)
        self.assertTrue(outside.is_dir())

    def test_unknown_quality_refuses_replacement(self):
        (self.album / "old.m4a").write_bytes(b"old")
        for qualities in ([None, (1, 16, 44100, 0)], [(1, 16, 44100, 0), None], [(1, 0, 44100, 0), (1, 16, 44100, 0)]):
            with patch.object(self.org, "track_quality", side_effect=qualities):
                self.assertFalse(self.org.evaluate_replacement(["new.m4a"], str(self.album))[0])

    def test_recase_failure_prevents_filing_and_deletion(self):
        source = self.root / "source.m4a"
        source.write_bytes(b"new")
        self.tags.update(artist="artist", albumartist="artist")
        old = self.album / "01 Song.m4a"
        old.write_bytes(b"old")
        with patch.object(self.org, "write_artist_case", side_effect=OSError("save failed")):
            stats = self.organize(source)
        self.assertTrue(stats["failed"])
        self.assertEqual(source.read_bytes(), b"new")
        self.assertEqual(old.read_bytes(), b"old")

    def test_extras_tags_are_safe_components(self):
        importer = load("import-album")
        for artist, album in (("/absolute", "../../album"), ("..", "/")):
            folder = self.root / "source"
            (folder / "Disc 1").mkdir(parents=True, exist_ok=True)
            extra = folder / "Disc 1" / "notes.txt"
            extra.write_text("fictional notes")
            archive = self.root / "imports"
            dest = Path(importer.preserve_extras(str(folder), str(archive), artist, album, False))
            self.assertTrue(dest.resolve().is_relative_to(archive.resolve()))
            self.assertEqual((dest / "Disc 1/notes.txt").read_text(), "fictional notes")

    def test_extras_escaping_destination_symlink_rejected_before_moves(self):
        importer = load("import-album")
        source = self.root / "source"
        source.mkdir()
        (source / "notes.txt").write_text("notes")
        archive = self.root / "imports"
        archive.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (archive / importer.datetime.now().strftime("%Y-%m-%d")).symlink_to(outside)
        with self.assertRaises(ValueError):
            importer.preserve_extras(str(source), str(archive), "Artist", "Album", False)
        self.assertTrue((source / "notes.txt").exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_empty_folder_fixer_preserves_every_nonempty_target(self):
        doctor = load("music-doctor")
        for filename in ("archive.zip", "sheet.cue", "notes.txt", "nested/file", ".hidden/audio.m4a", "cover.jpg"):
            folder = self.root / filename.replace("/", "_")
            path = folder / filename
            path.parent.mkdir(parents=True)
            path.write_bytes(b"keep")
            ctx = types.SimpleNamespace(apply=True, changed=[], skipped=[])
            doctor.fix_empty_folder({"targets": [str(folder)], "hash": "fixture"}, ctx)
            self.assertEqual(path.read_bytes(), b"keep")
            self.assertTrue(ctx.skipped)
        empty = self.root / "empty"
        empty.mkdir()
        doctor.fix_empty_folder({"targets": [str(empty)], "hash": "fixture"}, ctx)
        self.assertFalse(empty.exists())


class MetadataSafety(unittest.TestCase):
    def test_artist_whitespace_collapses(self):
        helpers = load("_music_tags")
        self.assertEqual(helpers.norm_artist("  Example   Artist\t feat. Guest  "), "example artist")

    def test_import_numbering_and_declared_totals(self):
        importer = load("import-album")
        args = types.SimpleNamespace(album="Album", year="2000", artist="Artist", albumartist=None, genre="Rock")
        files = ["/fictional/one.m4a", "/fictional/three.m4a"]
        for numbers, totals, expected, invalid in (([1, 3], [0, 0], 3, False), ([1, 3], [5, 5], 5, False),
                                                    ([1, 3], [2, 2], None, True), ([1, 1], [2, 2], None, True),
                                                    ([1, 3], [3, 4], None, True)):
            tags = {f: {"title": "Song", "track": n, "track_total": t} for f, n, t in zip(files, numbers, totals)}
            plans, errors = importer.build_plan(files, tags, None, args, "/fictional")
            self.assertEqual(bool(errors), invalid)
            if not invalid:
                self.assertEqual([p["track_total"] for p in plans], [expected, expected])

    def test_doctor_detects_missing_trailing_track(self):
        doctor = load("music-doctor")
        album = doctor.AlbumInfo("/fictional/Artist/Album", "Artist", "Album", audio=["one", "two"])
        files = {name: doctor.FileInfo(path=name, size=1000, ext=".m4a", tags={"track": n, "track_total": 3},
                                       lossless=True, bitrate=1000, sample_rate=44100, duration=100, codec="alac")
                 for name, n in (("one", 1), ("two", 2))}
        findings = doctor.check_files([album], files, False)
        gaps = [f for f in findings if f.kind == "track_gap"]
        self.assertEqual(len(gaps), 1)
        self.assertIn("missing [3]", gaps[0].message)

    def test_tagger_file_failure_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.m4a"
            path.write_bytes(b"fictional audio")
            with patch.object(sys, "argv", ["tagger.py", "--genre", "Rock", str(path)]), self.assertRaises(SystemExit) as exit:
                load("tagger")
            self.assertEqual(exit.exception.code, 1)
            self.assertEqual(path.read_bytes(), b"fictional audio")

    def test_batch_failure_returns_nonzero_and_preserves_retry(self):
        # Resolve from the Stow root, not a live HOME link.
        batch = BIN.parents[2] / "fish/.config/fish/functions/batch_rip.fish"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "batch.fish"
            source.write_text(batch.read_text().replace("/tmp/riptag-resume-id", str(root / "resume")))
            entry = {"url": "https://example.com/album/fiction", "genre": "Rock"}
            queue = root / "queue.json"
            queue.write_text(json.dumps([entry]))
            command = f"source {shlex.quote(str(source))}; function riptag; return 1; end; batch_rip {shlex.quote(str(queue))}"
            result = subprocess.run(["fish", "--no-config", "--private", "-c", command],
                                    env=dict(os.environ, HOME=tmp), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(json.loads((root / "needs-retry.json").read_text()), [entry])
            self.assertEqual(json.loads(queue.read_text()), [])
            retry_command = f"source {shlex.quote(str(source))}; function riptag; return 1; end; batch_rip {shlex.quote(str(root / 'needs-retry.json'))}"
            result = subprocess.run(["fish", "--no-config", "--private", "-c", retry_command],
                                    env=dict(os.environ, HOME=tmp), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(json.loads((root / "needs-retry.json").read_text()), [entry])
            # Invalid retry state must not remove the input entry.
            queue.write_text(json.dumps([entry]))
            (root / "needs-retry.json").write_text("invalid")
            result = subprocess.run(["fish", "--no-config", "--private", "-c", command],
                                    env=dict(os.environ, HOME=tmp), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(queue.read_text()), [entry])

    def test_runnability_rejects_replaced_file_even_with_same_size_and_mtime(self):
        runn = load("runnability")
        with tempfile.TemporaryDirectory() as tmp:
            runn.LIBRARY_ROOT = Path(tmp)
            path = Path(tmp) / "song.m4a"
            path.write_bytes(b"old")
            identity = runn.file_identity(path)
            st = path.stat()
            other = Path(tmp) / "replacement"
            other.write_bytes(b"new")
            os.utime(other, ns=(st.st_atime_ns, st.st_mtime_ns))
            other.replace(path)
            with patch.object(runn, "_write_mp4") as writer:
                result = runn._write_one("song.m4a", 80, None, None, identity, False)
            self.assertEqual(result[1], "stale")
            writer.assert_not_called()
            self.assertEqual(path.read_bytes(), b"new")

    def test_runnability_write_refreshes_identity_only_after_current_file_write(self):
        runn = load("runnability")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runn.LIBRARY_ROOT = root
            runn.DB_PATH = root / "features.db"
            path = root / "song.m4a"
            path.write_bytes(b"old")
            identity = runn.file_identity(path)
            conn = runn.open_db()
            conn.execute("INSERT INTO features (relpath, size, mtime, analyzed_at, file_identity) VALUES (?, ?, ?, ?, ?)",
                         ("song.m4a", 3, path.stat().st_mtime, "fixture", identity))
            conn.commit()
            conn.close()
            def writer(path, *args):
                path.write_bytes(b"tagged")
                return True
            args = types.SimpleNamespace(dry_run=False, force=True, paths=[], workers=1)
            with patch.object(runn, "load_config", return_value={"cadence": {"target_spm": 170}}), patch.object(runn, "score_row", return_value=(80, {})), patch.object(runn, "_write_mp4", side_effect=writer):
                self.assertEqual(runn.cmd_write(args), 0)
            conn = runn.open_db()
            row = conn.execute("SELECT size, file_identity FROM features").fetchone()
            self.assertEqual(row, (6, runn.file_identity(path)))
            conn.close()

    def test_runnability_requires_legacy_rows_to_be_reanalyzed(self):
        runn = load("runnability")
        with tempfile.TemporaryDirectory() as tmp:
            runn.DB_PATH = Path(tmp) / "features.db"
            runn.LIBRARY_ROOT = Path(tmp)
            (Path(tmp) / "song.m4a").write_bytes(b"music")
            conn = runn.open_db()
            self.assertIn("file_identity", {r[1] for r in conn.execute("PRAGMA table_info(features)")})
            conn.close()
            self.assertEqual(runn._write_one("song.m4a", 80, None, None, None, False)[1], "stale")


class TopHitsSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.top = load("top-hits")
        self.top.STATE_DIR = str(self.root / "state")
        self.top.DOWNLOADS_DIR = str(self.root / "downloads")
        self.top.LIBRARY_ROOT = str(self.root / "library")
        self.top.TOP_N = 2
        self.manifest = {"year": 2000, "album": "Fictional Hits", "entries": [
            {"rank": rank, "status": "manual", "chart_artist": "Example Artist", "chart_title": f"Song {rank}",
             "qobuz": {"id": str(rank), "title": f"Song {rank}", "performer": "Example Artist", "duration": 100}}
            for rank in (1, 2)]}
        self.top.write_json(self.top.state_path("manifests", "2000.json"), self.manifest)

    def test_rank_only_initial_resolution_has_no_network_or_manifest_write(self):
        Path(self.top.state_path("manifests", "2000.json")).unlink()
        self.top.write_json(self.top.state_path("charts", "2000.json"), {"entries": [{"rank": 1}]})
        with patch.object(self.top, "Qobuz") as qb, self.assertRaises(SystemExit):
            self.top.cmd_resolve(types.SimpleNamespace(year=2000, rank=[1]))
        qb.assert_not_called()
        self.assertFalse(Path(self.top.state_path("manifests", "2000.json")).exists())

    def test_resolve_preserves_provenance_and_invalidates_changed_pick(self):
        import copy
        for row in self.manifest["entries"]:
            row["tag_identity"] = self.top.recording_identity(row, "Example Artist feat. Guest")
        self.top.write_json(self.top.state_path("manifests", "2000.json"), self.manifest)
        self.top.write_json(self.top.state_path("charts", "2000.json"),
                            {"entries": [{"rank": r, "title": f"Song {r}", "artist": "Example Artist"} for r in (1, 2)], "source_url": "https://example.com/chart", "retrieved_at": "fixture"})
        def resolved(entry, year, items):
            row = copy.deepcopy(self.manifest["entries"][entry["rank"] - 1])
            row.pop("tag_identity")
            row.update(flags=[], skip_reason=None)
            row["qobuz"].update(album="Album", released="2000")
            if row["rank"] == 1:
                row["qobuz"]["id"] = "changed"
            return row
        with patch.object(self.top, "Qobuz", return_value=types.SimpleNamespace(calls=0)), patch.object(self.top, "search_entry", return_value=("fixture", [])), patch.object(self.top, "resolve_entry", side_effect=resolved), patch.object(self.top, "enrich_from_track_get"), patch.object(self.top, "write_review", return_value="fixture"):
            self.top.cmd_resolve(types.SimpleNamespace(year=2000, rank=None))
        reloaded = self.top.load_manifest(2000)
        self.assertEqual(reloaded["entries"][1]["tag_identity"], self.manifest["entries"][1]["tag_identity"])
        with self.assertRaises(ValueError):
            self.top.expected_recording_identity({}, reloaded["entries"][0])

    def test_manifest_requires_exact_unique_ranks(self):
        for ranks in ((1,), (1, 1), (1, 3)):
            with self.subTest(ranks=ranks), self.assertRaises(SystemExit):
                self.top.not_ready_rows({"entries": [{"rank": r, "status": "manual"} for r in ranks]})

    def test_adoption_mismatch_preserves_staging_and_progress(self):
        staging = Path(self.top.DOWNLOADS_DIR) / "2000"
        staging.mkdir(parents=True)
        sentinel = staging / "retry.m4a"
        sentinel.write_bytes(b"retry")
        class Audio(dict):
            info = types.SimpleNamespace(length=100)
        for ranks, wrong in (((1, 1, 2), False), ((1, 2, 3), False), ((1,), False), ((1, 2), True)):
            files = [str(i) for i in range(len(ranks))]
            audios = [Audio(trkn=[(r, 2)], **{"\xa9nam": ["Wrong" if wrong else f"Song {r}"], "\xa9ART": ["Example Artist"]}) for r in ranks]
            self.top._mutagen["mutagen.mp4"].MP4 = Mock(side_effect=audios)
            with patch.dict(sys.modules, self.top._mutagen), patch.object(self.top, "find_audio", return_value=files), patch.object(self.top, "nas_chmod") as chmod, self.assertRaises(SystemExit):
                self.top.cmd_adopt(types.SimpleNamespace(year=2000))
            chmod.assert_not_called()
            self.assertEqual(sentinel.read_bytes(), b"retry")
            self.assertFalse(Path(self.top.state_path("progress", "2000.json")).exists())

    def test_adoption_holds_lock_and_accepts_complete_identity(self):
        class Audio(dict):
            info = types.SimpleNamespace(length=100)
        self.top._mutagen["mutagen.mp4"].MP4 = Mock(side_effect=[
            Audio(trkn=[(r, 2)], **{"\xa9nam": [f"Song {r}"], "\xa9ART": ["Example Artist"]})
            for r in (1, 2)])
        with self.top.year_lock(2000, "test"):
            with self.assertRaises(SystemExit):
                self.top.cmd_adopt(types.SimpleNamespace(year=2000))
        with patch.dict(sys.modules, self.top._mutagen), patch.object(self.top, "find_audio", return_value=["one", "two"]), patch.object(self.top, "nas_chmod", return_value=[]), patch.object(self.top, "score_runnability", return_value=(True, "")):
            self.top.cmd_adopt(types.SimpleNamespace(year=2000))
        self.assertTrue(self.top.load_progress(2000)["assembled_at"])

    def test_completion_uses_current_files_and_never_redownloads_recorded_loss(self):
        self.top.save_progress({"year": 2000, "ranks": {"1": {"verified": True}, "2": {"verified": True}},
                                "assembled_at": "2000-01-01T00:00:00Z", "library_dir": "old"})
        for available in (False, True):
            with patch.dict(sys.modules, self.top._mutagen), patch.object(self.top, "library_available", return_value=available), patch.object(self.top, "_run_stage") as stage:
                info = self.top.year_status(2000)
                self.assertFalse(info["assembled"])
                self.assertFalse(info["downloaded"])
                self.assertTrue(info["recorded_assembled"])
                with self.assertRaises(SystemExit):
                    self.top.cmd_run(types.SimpleNamespace(years="2000", max_attempts=1, retry_wait=0, cooldown=0))
                stage.assert_not_called()

    def test_tag_retag_and_status_share_persisted_feature_credit(self):
        class Audio(dict):
            info = types.SimpleNamespace(length=100)
            def save(self):
                pass
        mp4 = self.top._mutagen["mutagen.mp4"]
        mp4.MP4FreeForm = bytes
        mp4.MP4Cover = Mock(return_value=b"cover")
        for performer in ("Example Artist", "Example Artist feat. Guest"):
            manifest = json.loads(json.dumps(self.manifest))
            manifest["album_artist"] = "Various Artists"
            audios = {}
            for row in manifest["entries"]:
                row["genre"] = "Pop"
                row["qobuz"]["performer"] = performer
                row["chart_artist"] = "Example Artist feat. Guest"
                audios[str(row["rank"])] = Audio(**{"\xa9ART": ["Example Artist feat. Guest"], "\xa9nam": [row["qobuz"]["title"]]})
            mp4.MP4 = lambda path: audios[path]
            with patch.dict(sys.modules, self.top._mutagen), patch.object(self.top, "find_audio", return_value=list(audios)):
                for row in manifest["entries"]:
                    path = str(row["rank"])
                    self.top.tag_file(path, row, manifest, b"cover")
                    self.assertEqual(audios[path]["\xa9nam"], [f"Song {row['rank']} (feat. Guest)"])
                    first = dict(audios[path])
                    reloaded = self.top.load_manifest(2000)
                    persisted_row = next(r for r in reloaded["entries"] if r["rank"] == row["rank"])
                    self.assertEqual(persisted_row["tag_identity"]["source_artist"], "Example Artist feat. Guest")
                    self.top.tag_file(path, persisted_row, reloaded, b"cover", new_recording=False)
                    self.assertEqual(dict(audios[path]), first)
                self.assertEqual(len(self.top.verified_library_files(manifest)), 2)
                self.top.write_json(self.top.state_path("manifests", "2000.json"), manifest)
                self.top.save_progress({"year": 2000, "ranks": {}, "assembled_at": "fixture"})
                with patch.object(self.top, "library_available", return_value=True):
                    self.assertTrue(self.top.year_status(2000)["assembled"])
                for key, value in (("\xa9ART", "Wrong Artist"), ("\xa9nam", "Song 1 (feat. Wrong Guest)"),
                                   ("\xa9nam", "Song 1 (Remix)")):
                    original = audios["1"][key]
                    audios["1"][key] = [value]
                    with self.assertRaises(SystemExit):
                        self.top.verified_library_files(manifest)
                    audios["1"][key] = original
                original_audio = dict(audios["1"])
                forged = self.top.recording_identity(manifest["entries"][0], "Example Artist feat. Wrong Guest")
                audios["1"][self.top.IDENTITY_TAG] = [json.dumps(forged).encode()]
                audios["1"]["\xa9nam"] = [forged["title"]]
                with self.assertRaises(SystemExit):
                    self.top.verified_library_files(self.top.load_manifest(2000))
                audios["1"].clear()
                audios["1"].update(original_audio)
                changed = self.top.load_manifest(2000)
                changed["entries"][0]["qobuz"]["id"] = "replacement-id"
                with self.assertRaises(SystemExit):
                    self.top.verified_library_files(changed)

    def test_full_assemble_retag_status_identity_parity(self):
        class Audio(dict):
            info = types.SimpleNamespace(length=100)
            def save(self):
                pass
        manifest = self.manifest
        manifest["album_artist"] = "Various Artists"
        audios, records = {}, {}
        for row in manifest["entries"]:
            rank = str(row["rank"])
            row["genre"] = "Pop"
            row["qobuz"]["performer"] = "Example Artist feat. Guest"
            row["chart_artist"] = "Example Artist feat. Guest"
            path = Path(self.top.DOWNLOADS_DIR) / "2000" / rank / "source.m4a"
            path.parent.mkdir(parents=True)
            path.write_text(rank)
            records[rank] = {"verified": True, "path": str(path)}
            audios[rank] = Audio(**{"\xa9ART": ["Example Artist feat. Guest"], "\xa9nam": [row["qobuz"]["title"]]})
        self.top.write_json(self.top.state_path("manifests", "2000.json"), manifest)
        self.top.save_progress({"year": 2000, "ranks": records})
        (Path(self.top.LIBRARY_ROOT) / "Compilations").mkdir(parents=True)
        mp4 = self.top._mutagen["mutagen.mp4"]
        mp4.MP4 = lambda path: audios[Path(path).read_text()]
        mp4.MP4FreeForm = bytes
        mp4.MP4Cover = Mock(return_value=b"cover")
        def cover(year, path):
            Path(path).write_bytes(b"cover")
            return b"cover"
        def organizer(cmd, **kwargs):
            self.top.shutil.move(cmd[-1], self.top.library_album_dir(manifest))
            Path(cmd[cmd.index("--manifest") + 1]).write_text(self.top.library_album_dir(manifest) + "\n")
            return types.SimpleNamespace(returncode=0)
        with patch.dict(sys.modules, self.top._mutagen), patch.object(self.top, "make_cover", side_effect=cover), patch.object(self.top.subprocess, "run", side_effect=organizer), patch.object(self.top, "nas_chmod", return_value=[]), patch.object(self.top, "score_runnability", return_value=(True, "")), patch.object(self.top, "library_available", return_value=True):
            self.top.cmd_assemble(types.SimpleNamespace(year=2000, force=False))
            before = {rank: dict(audio) for rank, audio in audios.items()}
            self.assertTrue(self.top.year_status(2000)["assembled"])
            self.top.cmd_retag(types.SimpleNamespace(year=2000))
            self.assertEqual(before, {rank: dict(audio) for rank, audio in audios.items()})
            self.assertTrue(self.top.year_status(2000)["assembled"])

    def test_legacy_unknown_feature_credit_is_not_inferred_from_title(self):
        row = self.manifest["entries"][0]
        audio = {"\xa9ART": ["Example Artist"], "\xa9nam": ["Song 1 (feat. Unproven Guest)"]}
        expected = self.top.expected_recording_identity(audio, row)
        self.assertEqual(expected["title"], "Song 1")
        self.assertNotEqual(expected["title"], audio["\xa9nam"][0])
        forged = self.top.recording_identity(row, "Example Artist feat. Unproven Guest")
        audio[self.top.IDENTITY_TAG] = [json.dumps(forged).encode()]
        self.assertEqual(self.top.expected_recording_identity(audio, row), expected)
        row["chart_artist"] = "Example Artist feat. Known Guest"
        audio["\xa9nam"] = ["Song 1 (feat. Known Guest)"]
        self.assertEqual(self.top.expected_recording_identity(audio, row)["title"], audio["\xa9nam"][0])
        audio["\xa9nam"] = ["Song 1 (feat. Wrong Guest)"]
        self.assertNotEqual(self.top.expected_recording_identity(audio, row)["title"], audio["\xa9nam"][0])

    def test_download_reverifies_recorded_files_before_skipping(self):
        for row in self.manifest["entries"]:
            row["qobuz"]["url"] = "https://example.com/track/fixture"
        self.top.write_json(self.top.state_path("manifests", "2000.json"), self.manifest)
        staging = Path(self.top.DOWNLOADS_DIR) / "2000"
        records = {}
        for rank in (1, 2):
            path = staging / f"{rank:02d}" / "song.m4a"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"replaced wrong recording")
            records[str(rank)] = {"verified": True, "path": str(path)}
        self.top.save_progress({"year": 2000, "ranks": records})
        with patch.object(self.top, "verify_file", return_value=(False, "wrong recording")), patch.object(self.top, "rip_track", return_value=(1, "failed")) as rip, patch.object(self.top.time, "sleep"), self.assertRaises(SystemExit):
            self.top.cmd_download(types.SimpleNamespace(year=2000))
        self.assertEqual(rip.call_count, 2)
        self.assertTrue(all(not rec["verified"] for rec in self.top.load_progress(2000)["ranks"].values()))

    def test_assembly_reverifies_recorded_files_before_any_tag_or_move(self):
        staging = Path(self.top.DOWNLOADS_DIR) / "2000"
        records = {}
        for rank in (1, 2):
            path = staging / f"{rank:02d}" / "song.m4a"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"replaced wrong recording")
            records[str(rank)] = {"verified": True, "path": str(path)}
        self.top.save_progress({"year": 2000, "ranks": records})
        Path(self.top.LIBRARY_ROOT).mkdir()
        (Path(self.top.LIBRARY_ROOT) / "placeholder").mkdir()
        with patch.object(self.top, "verify_file", return_value=(False, "wrong recording")), patch.object(self.top, "tag_file") as tag, patch.object(self.top, "make_cover") as cover, patch.object(self.top.shutil, "move") as move, self.assertRaises(SystemExit):
            self.top.cmd_assemble(types.SimpleNamespace(year=2000, force=False))
        tag.assert_not_called()
        cover.assert_not_called()
        move.assert_not_called()
        self.assertTrue(all(Path(rec["path"]).exists() for rec in records.values()))

    def test_downloaded_status_requires_existing_verified_audio(self):
        self.top.save_progress({"year": 2000, "ranks": {"1": {"verified": True, "path": str(self.root / "gone")}}})
        self.assertEqual(self.top.year_status(2000)["verified"], 0)

    def test_failed_publication_preserves_old_and_download(self):
        old, new, target = [self.root / n for n in ("old.m4a", "new.m4a", "target.m4a")]
        old.write_bytes(b"old")
        new.write_bytes(b"new")
        for operation in ("copy2", "replace"):
            obj = self.top.shutil if operation == "copy2" else self.top.os
            with patch.object(obj, operation, side_effect=OSError("offline")), self.assertRaises(OSError):
                self.top.replace_recording(str(new), str(target), [str(old)])
            self.assertEqual(old.read_bytes(), b"old")
            self.assertEqual(new.read_bytes(), b"new")
        self.top.replace_recording(str(new), str(target), [str(old)])
        self.assertEqual(target.read_bytes(), b"new")
        self.assertFalse(old.exists())
        self.assertFalse(new.exists())


if __name__ == "__main__":
    unittest.main()
