import subprocess
import unittest

from helpers import BIN

SCRIPT = BIN / "clean-web-title"


def clean(title):
    return subprocess.run(
        ["/usr/bin/python3", str(SCRIPT)],
        input=title, capture_output=True, text=True, check=True,
    ).stdout


class BrandSuffix(unittest.TestCase):
    def test_strips_trailing_pipe_brand(self):
        self.assertEqual(clean("Great Article | Pitchfork"), "Great Article")

    def test_strips_only_the_final_pipe_segment(self):
        self.assertEqual(clean("a | b | c"), "a | b")

    def test_dash_subtitle_is_preserved(self):
        self.assertEqual(clean("Title - Subtitle"), "Title - Subtitle")


class Normalization(unittest.TestCase):
    def test_nfkc_folds_fullwidth_colon_to_ascii(self):
        self.assertEqual(clean("Best New Music： Album"), "Best New Music: Album")

    def test_collapses_runs_of_whitespace(self):
        self.assertEqual(clean("a   b\tc\n d"), "a b c d")


if __name__ == "__main__":
    unittest.main()
