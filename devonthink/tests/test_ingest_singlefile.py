import io
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path

from helpers import load

sf = load("ingest-singlefile-html.py", "ingest_singlefile_html")


class TruncateAtWord(unittest.TestCase):
    def test_returns_input_unchanged_when_within_limit(self):
        self.assertEqual(sf.truncate_at_word("short title", 120), "short title")

    def test_truncates_at_a_word_boundary_with_an_ellipsis(self):
        out = sf.truncate_at_word("alpha beta gamma delta epsilon", 18)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 18)
        self.assertNotIn(" …", out)

    def test_hard_cuts_a_string_with_no_usable_space(self):
        out = sf.truncate_at_word("a" * 200, 20)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 20)


class NormalizeTitle(unittest.TestCase):
    def test_folds_fullwidth_punctuation_and_collapses_whitespace(self):
        self.assertEqual(sf.normalize_title("A：  B\tc"), "A: B c")


class IsRedditUrl(unittest.TestCase):
    def test_true_only_for_known_reddit_hosts(self):
        self.assertTrue(sf.is_reddit_url("https://www.reddit.com/r/x/comments/a/t/"))
        self.assertTrue(sf.is_reddit_url("https://old.reddit.com/r/x"))
        self.assertFalse(sf.is_reddit_url("https://example.com/r/x"))


class AugmentGenericTitle(unittest.TestCase):
    def test_appends_host_and_path_dropping_www(self):
        self.assertEqual(
            sf.augment_generic_title("No title", "https://www.example.com/a/b/"),
            "No title — example.com/a/b",
        )

    def test_missing_host_returns_base_unchanged(self):
        self.assertEqual(sf.augment_generic_title("No title", "not-a-url"), "No title")


class IsAiChatUrl(unittest.TestCase):
    def test_returns_platform_label_for_known_hosts(self):
        self.assertEqual(sf.is_ai_chat_url("https://claude.ai/chat/1"), "Claude")
        self.assertEqual(sf.is_ai_chat_url("https://chatgpt.com/c/1"), "ChatGPT")

    def test_strips_port_and_rejects_unknown_hosts(self):
        self.assertEqual(sf.is_ai_chat_url("https://claude.ai:443/x"), "Claude")
        self.assertEqual(sf.is_ai_chat_url("https://example.com/x"), "")


class ParseSourceUrl(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def _html(self, body):
        p = Path(self.dir.name) / "cap.html"
        p.write_text(body)
        return p

    def test_extracts_url_from_singlefile_comment(self):
        p = self._html("<!-- Page saved with SingleFile\n url: https://ex.com/a -->\n")
        self.assertEqual(sf.parse_source_url(p), "https://ex.com/a")

    def test_extracts_url_after_large_opening_tag_data(self):
        p = self._html(
            '<html style="background:url(data:image/png;base64,'
            + "A" * 200_000
            + ')"><!-- Page saved with SingleFile\n url: https://ex.com/large -->'
        )
        self.assertEqual(sf.parse_source_url(p), "https://ex.com/large")

    def test_returns_none_without_the_singlefile_marker(self):
        p = self._html("<html>url: https://ex.com/a</html>")
        self.assertIsNone(sf.parse_source_url(p))

    def test_returns_none_when_marker_present_but_no_url(self):
        p = self._html("<!-- Page saved with SingleFile, no url here -->")
        self.assertIsNone(sf.parse_source_url(p))


class _Echo:
    def __init__(self, stdout):
        self.stdout = stdout


class DeriveTitle(unittest.TestCase):
    """derive_title with the Reddit fetch and clean-web-title subprocess stubbed
    so no network or external binary is touched; clean-web-title's pass-through
    on non-brand titles is faithfully modeled by echoing its stdin."""

    def setUp(self):
        self.orig_run = sf.subprocess.run
        self.orig_fetch = sf.fetch_reddit_post_title
        sf.subprocess.run = lambda *a, **k: _Echo(k.get("input", ""))
        sf.fetch_reddit_post_title = lambda url: None
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        sf.subprocess.run = self.orig_run
        sf.fetch_reddit_post_title = self.orig_fetch
        self.dir.cleanup()

    def _path(self, stem):
        return Path(self.dir.name) / f"{stem}.html"

    def test_placeholder_stem_is_generic_and_augmented(self):
        title, is_generic, override = sf.derive_title(
            self._path("No title (07-09-2026 10-30-00)"), "https://ex.com/foo"
        )
        self.assertTrue(is_generic)
        self.assertFalse(override)
        self.assertTrue(title.startswith("No title — "))

    def test_real_title_is_kept_and_not_generic(self):
        title, is_generic, override = sf.derive_title(
            self._path("My Real Article (01-02-2026 00-00-00)"), "https://ex.com/foo"
        )
        self.assertEqual(title, "My Real Article")
        self.assertFalse(is_generic)
        self.assertFalse(override)

    def test_reddit_boilerplate_title_falls_back_to_generic(self):
        title, is_generic, override = sf.derive_title(
            self._path("From the pics community on Reddit"),
            "https://www.reddit.com/r/pics/comments/abc/t/",
        )
        self.assertTrue(is_generic)

    def test_reddit_api_title_overrides_existing_name(self):
        sf.fetch_reddit_post_title = lambda url: "The Verbatim Post Title"
        title, is_generic, override = sf.derive_title(
            self._path("From the pics community on Reddit"),
            "https://www.reddit.com/r/pics/comments/abc/t/",
        )
        self.assertEqual(title, "The Verbatim Post Title")
        self.assertFalse(is_generic)
        self.assertTrue(override)


class CaptureTimestamp(unittest.TestCase):
    def _stamp(self, *args):
        return sf.capture_timestamp(datetime(*args).timestamp())

    def test_afternoon(self):
        self.assertEqual(self._stamp(2026, 7, 23, 15, 7), ("2026-07-23", "3:07pm"))

    def test_morning_hour_has_no_leading_zero(self):
        self.assertEqual(self._stamp(2026, 7, 23, 9, 5), ("2026-07-23", "9:05am"))

    def test_midnight_hour_is_12am(self):
        self.assertEqual(self._stamp(2026, 7, 23, 0, 42), ("2026-07-23", "12:42am"))

    def test_noon_hour_is_12pm(self):
        self.assertEqual(self._stamp(2026, 7, 23, 12, 0), ("2026-07-23", "12:00pm"))

    def test_past_day_keeps_its_date(self):
        self.assertEqual(self._stamp(2026, 7, 21, 23, 59), ("2026-07-21", "11:59pm"))


class CompressionSafety(unittest.TestCase):
    def test_failed_or_timed_out_compression_never_imports_partial_bytes(self):
        for timeout in (False, True):
            for import_fails in (False, True):
                with self.subTest(timeout=timeout, import_fails=import_fails), tempfile.TemporaryDirectory() as tmp:
                    original = Path(tmp) / "capture.html"
                    original.write_bytes(b"original html")

                    def compressor(argv, **kwargs):
                        working = Path(argv[-1])
                        self.assertNotEqual(working, original)
                        working.write_bytes(b"partial write")
                        if timeout:
                            raise subprocess.TimeoutExpired(argv, 120)
                        return subprocess.CompletedProcess(argv, 1, stderr="disk full")

                    def importer(**kwargs):
                        self.assertEqual(original.read_bytes(), b"original html")
                        self.assertEqual(kwargs["html_path"].read_bytes(), b"original html")
                        if import_fails:
                            raise subprocess.CalledProcessError(1, "stub import")
                        return "fictional-uuid"

                    with mock.patch.object(sf.sys, "argv", ["ingest", str(original), "--force"]), \
                            mock.patch.object(sf, "parse_source_url", return_value="https://example.com/"), \
                            mock.patch.object(sf, "derive_title", return_value=("Capture", False, False)), \
                            mock.patch.object(sf.subprocess, "run", side_effect=compressor), \
                            mock.patch.object(sf, "run_defuddle", return_value=False), \
                            mock.patch.object(sf, "import_to_devonthink", side_effect=importer):
                        self.assertEqual(sf.main(), 1 if import_fails else 0)
                    if import_fails:
                        self.assertEqual(original.read_bytes(), b"original html")
                    else:
                        self.assertFalse(original.exists())

    def test_compressor_write_and_replace_failures_leave_input_unchanged(self):
        compressor = load("compress-singlefile-images.py", "compress_singlefile_images")
        for failure in ("write", "replace"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                original = Path(tmp) / "capture.html"
                original.write_bytes(b"original html")
                real_fdopen = os.fdopen

                def failing_fdopen(fd, *args, **kwargs):
                    os.write(fd, b"partial")
                    os.close(fd)
                    raise OSError("disk full")

                with mock.patch.object(compressor.sys, "argv", ["compress", str(original)]), \
                        mock.patch.object(compressor.sys, "stderr", io.StringIO()), \
                        mock.patch.object(compressor.os, "fdopen", side_effect=failing_fdopen if failure == "write" else real_fdopen), \
                        mock.patch.object(compressor.os, "replace", side_effect=OSError("rename failed")):
                    with self.assertRaises(SystemExit):
                        compressor.main()
                self.assertEqual(original.read_bytes(), b"original html")
                self.assertEqual(list(Path(tmp).iterdir()), [original])


class ImportArgv(unittest.TestCase):
    """The capture stamps must ride the osascript argv into the AppleScript,
    which reads them as items 10/11 — a drifted position silently stamps
    bullets with the wrong field."""

    def test_capture_stamps_reach_the_applescript(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _Echo("a|b|c\n")

        orig = sf.subprocess.run
        sf.subprocess.run = fake_run
        try:
            sf.import_to_devonthink(
                html_path=Path("/tmp/x.html"),
                md_path=None,
                bookmark_uuid="",
                source_url="https://ex.com",
                safe_title="Example",
                capture_date="2026-07-21",
                capture_time="11:59pm",
            )
        finally:
            sf.subprocess.run = orig

        argv = calls[0][2:]
        self.assertEqual(argv[9], "2026-07-21")
        self.assertEqual(argv[10], "11:59pm")
        self.assertIn("set captureDate to item 10 of argv", sf.APPLESCRIPT)
        self.assertIn("set captureTime to item 11 of argv", sf.APPLESCRIPT)


if __name__ == "__main__":
    unittest.main()
