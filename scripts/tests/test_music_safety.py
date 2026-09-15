#!/usr/bin/env python3
"""Offline regression fixtures. Mutagen is stubbed; no personal audio is read."""
import importlib.util
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
        "": [], "flac": ["FLAC"], "mp3": ["MP3"], "mp4": ["MP4"],
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
             "qobuz": {"title": f"Song {rank}", "performer": "Example Artist", "duration": 100}}
            for rank in (1, 2)]}
        self.top.write_json(self.top.state_path("manifests", "2000.json"), self.manifest)

    def test_rank_only_initial_resolution_has_no_network_or_manifest_write(self):
        Path(self.top.state_path("manifests", "2000.json")).unlink()
        self.top.write_json(self.top.state_path("charts", "2000.json"), {"entries": [{"rank": 1}]})
        with patch.object(self.top, "Qobuz") as qb, self.assertRaises(SystemExit):
            self.top.cmd_resolve(types.SimpleNamespace(year=2000, rank=[1]))
        qb.assert_not_called()
        self.assertFalse(Path(self.top.state_path("manifests", "2000.json")).exists())

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
