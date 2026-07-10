import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

from helpers import BIN


def _load_extensionless(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ssf = _load_extensionless(BIN / "should-skip-singlefile", "should_skip_singlefile")


class WithConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cfg = Path(self.dir.name) / "skip.txt"
        self.orig = ssf.CONFIG_PATH
        ssf.CONFIG_PATH = self.cfg

    def tearDown(self):
        ssf.CONFIG_PATH = self.orig
        self.dir.cleanup()

    def write(self, text):
        self.cfg.write_text(text)

    def test_exact_host_matches(self):
        self.write("youtube.com\n")
        self.assertTrue(ssf.should_skip("https://youtube.com/watch?v=1"))

    def test_subdomain_matches_via_dot_suffix(self):
        self.write("youtube.com\n")
        self.assertTrue(ssf.should_skip("https://music.youtube.com/x"))

    def test_non_listed_host_is_not_skipped(self):
        self.write("youtube.com\n")
        self.assertFalse(ssf.should_skip("https://example.com/a"))

    def test_suffix_lookalike_does_not_match(self):
        self.write("youtube.com\n")
        self.assertFalse(ssf.should_skip("https://notyoutube.com/a"))

    def test_comments_and_case_are_normalized(self):
        self.write("# skip these\nYouTube.com  # video host\n\n")
        self.assertEqual(ssf.load_domains(), {"youtube.com"})
        self.assertTrue(ssf.should_skip("https://YOUTUBE.com/a"))

    def test_unparseable_url_is_not_skipped(self):
        self.write("youtube.com\n")
        self.assertFalse(ssf.should_skip("not a url"))


class WithoutConfig(unittest.TestCase):
    def test_missing_config_skips_nothing(self):
        orig = ssf.CONFIG_PATH
        ssf.CONFIG_PATH = Path(tempfile.gettempdir()) / "does-not-exist-skip-domains.txt"
        try:
            self.assertEqual(ssf.load_domains(), set())
            self.assertFalse(ssf.should_skip("https://youtube.com/a"))
        finally:
            ssf.CONFIG_PATH = orig


if __name__ == "__main__":
    unittest.main()
