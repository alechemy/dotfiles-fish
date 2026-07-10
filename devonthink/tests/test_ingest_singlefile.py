import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
