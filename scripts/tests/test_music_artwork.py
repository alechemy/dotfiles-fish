#!/usr/bin/env python3
"""Offline artwork fixtures with synthetic images and stubbed audio tags."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from test_music_safety import load


class MusicArtworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.song = self.source / "song.m4a"
        self.song.write_bytes(b"fictional audio")
        self.library = self.root / "library"
        self.album = self.library / "Artist" / "Album"
        self.album.mkdir(parents=True)
        self.org = load("music-organize")
        self.assertEqual(self.org.NAVIDROME_MAX_IMAGE_BYTES, 20_000_000)
        self.tags = dict(albumartist="Artist", artist="Artist", album="Album", title="Song",
                         track=1, disc=1, disctotal=1, compilation=False)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, HOME=str(self.root / "home")).start()
        patch.object(self.org, "NAVIDROME_MAX_IMAGE_BYTES", 20_000).start()

    def cover(self, ext="jpg", oversized=True):
        image = Image.new("RGBA" if ext == "png" else "RGB", (128, 128),
                          (12, 80, 160, 100) if ext == "png" else (12, 80, 160))
        output = io.BytesIO()
        image.save(output, format={"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[ext])
        data = output.getvalue()
        if oversized:
            data += b"\0" * (20_001 - len(data))
        path = self.source / ("cover." + ext)
        path.write_bytes(data)
        return path, data

    def organize(self, dry_run=False):
        stats = {"moved": 0, "failed": [], "kept": []}
        with patch.object(self.org, "read_tags", return_value=self.tags):
            self.org.organize_source(str(self.source), str(self.library), "replace",
                                     dry_run, set(), stats)
        return stats

    def test_oversized_cover_is_compressed_and_original_is_archived(self):
        cover, original = self.cover()
        stats = self.organize()
        self.assertFalse(stats["failed"])
        dest = self.album / cover.name
        self.assertLessEqual(dest.stat().st_size, 20_000)
        with Image.open(dest) as image:
            self.assertEqual(image.size, (128, 128))
        backups = list((self.root / "home/.local/state/music/artwork-originals").glob("*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.source.exists())

    def test_small_cover_is_copied_without_changes_or_backup(self):
        cover, original = self.cover(oversized=False)
        stats = self.organize()
        self.assertFalse(stats["failed"])
        self.assertEqual((self.album / cover.name).read_bytes(), original)
        self.assertFalse((self.root / "home").exists())

    def test_invalid_oversized_cover_preserves_source_and_existing_album(self):
        (self.source / "cover.jpg").write_bytes(b"invalid" * 4000)
        old = self.album / "01 Song.m4a"
        old.write_bytes(b"existing audio")
        stats = self.organize()
        self.assertTrue(stats["failed"])
        self.assertEqual(old.read_bytes(), b"existing audio")
        self.assertEqual(self.song.read_bytes(), b"fictional audio")

    def test_dry_run_writes_nothing(self):
        cover, original = self.cover()
        stats = self.organize(dry_run=True)
        self.assertFalse(stats["failed"])
        self.assertEqual(cover.read_bytes(), original)
        self.assertTrue(self.song.exists())
        self.assertEqual(list(self.album.iterdir()), [])
        self.assertFalse((self.root / "home").exists())

    def test_png_transparency_and_webp_format_are_preserved(self):
        for ext in ("png", "webp"):
            with self.subTest(ext=ext):
                cover, _ = self.cover(ext)
                data = self.org.prepare_cover(str(cover))
                self.assertLessEqual(len(data), 20_000)
                with Image.open(io.BytesIO(data)) as image:
                    self.assertEqual(image.format, "PNG" if ext == "png" else "WEBP")
                    self.assertEqual(image.size, (128, 128))
                    if ext == "png":
                        self.assertEqual(image.getpixel((0, 0))[3], 100)

    def test_jpeg_uses_lower_quality_before_reducing_dimensions(self):
        cover = self.source / "cover.jpg"
        Image.effect_noise((256, 256), 100).convert("RGB").save(cover, quality=100)
        sizes = []
        with Image.open(cover) as image:
            for quality in (95, 90):
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True, subsampling=0)
                sizes.append(len(output.getvalue()))
        limit = sum(sizes) // 2
        self.assertGreater(sizes[0], limit)
        self.assertLessEqual(sizes[1], limit)
        with patch.object(self.org, "NAVIDROME_MAX_IMAGE_BYTES", limit):
            data = self.org.prepare_cover(str(cover))
        self.assertLessEqual(len(data), limit)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (256, 256))

    def test_large_encoding_is_resized_until_it_fits(self):
        cover = self.source / "cover.png"
        Image.effect_noise((128, 128), 100).save(cover)
        with patch.object(self.org, "NAVIDROME_MAX_IMAGE_BYTES", 2_000):
            data = self.org.prepare_cover(str(cover))
        self.assertLessEqual(len(data), 2_000)
        with Image.open(io.BytesIO(data)) as image:
            self.assertLess(image.width, 128)
            self.assertEqual(image.width, image.height)

    def test_limit_boundary_is_unchanged(self):
        cover, data = self.cover()
        cover.write_bytes(data[:20_000])
        self.assertIsNone(self.org.prepare_cover(str(cover)))

    def test_archive_reuses_identical_originals_and_rejects_damaged_entries(self):
        cover, original = self.cover()
        first = self.org.archive_original_cover(str(cover))
        self.assertEqual(self.org.archive_original_cover(str(cover)), first)
        self.assertEqual(Path(first).read_bytes(), original)
        Path(first).write_bytes(b"damaged backup")
        with self.assertRaises(ValueError):
            self.org.archive_original_cover(str(cover))
        self.assertEqual(cover.read_bytes(), original)

    def test_animated_oversized_cover_is_rejected_without_mutation(self):
        cover = self.source / "cover.webp"
        frames = [Image.new("RGB", (32, 32), color) for color in ("red", "blue")]
        frames[0].save(cover, save_all=True, append_images=frames[1:])
        original = cover.read_bytes()
        with patch.object(self.org, "NAVIDROME_MAX_IMAGE_BYTES", 1):
            stats = self.organize()
        self.assertTrue(stats["failed"])
        self.assertEqual(cover.read_bytes(), original)
        self.assertTrue(self.song.exists())
        self.assertEqual(list(self.album.iterdir()), [])

    def test_oversized_pixel_canvas_is_rejected_before_decoding(self):
        cover, _ = self.cover()
        with patch("PIL.Image.open") as opened:
            image = opened.return_value.__enter__.return_value
            image.is_animated = False
            image.width, image.height = 64 << 20, 2
            with self.assertRaises(ValueError):
                self.org.prepare_cover(str(cover))
            image.copy.assert_not_called()
            image.convert.assert_not_called()
        self.assertTrue(self.song.exists())

    def test_atomic_cover_failure_keeps_destination_and_cleans_temporary_file(self):
        cover, original = self.cover(oversized=False)
        dest = self.album / "cover.jpg"
        dest.write_bytes(b"existing cover")
        with patch.object(self.org.os, "replace", side_effect=OSError("rename failed")):
            with self.assertRaises(OSError):
                self.org.copy_cover(str(cover), str(dest), original)
        self.assertEqual(dest.read_bytes(), b"existing cover")
        self.assertEqual(list(self.album.iterdir()), [dest])

    def test_backup_failure_blocks_import_before_library_changes(self):
        self.cover()
        old = self.album / "01 Song.m4a"
        old.write_bytes(b"existing audio")
        with patch.object(self.org, "archive_original_cover", side_effect=OSError("backup failed")):
            stats = self.organize()
        self.assertTrue(stats["failed"])
        self.assertEqual(old.read_bytes(), b"existing audio")
        self.assertTrue(self.song.exists())

    def test_cover_write_failure_retains_source_and_reports_failure(self):
        cover, original = self.cover()
        with patch.object(self.org, "copy_cover", side_effect=OSError("write failed")):
            stats = self.organize()
        self.assertTrue(stats["failed"])
        self.assertEqual(cover.read_bytes(), original)
        self.assertTrue(self.source.exists())


if __name__ == "__main__":
    unittest.main()
