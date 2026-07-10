import subprocess
import unittest

from helpers import BIN

SCRIPT = BIN / "sync-markdown-h1.py"


def run(text, title):
    return subprocess.run(
        ["/usr/bin/python3", str(SCRIPT), title],
        input=text, capture_output=True, text=True, check=True,
    ).stdout


class ExistingH1(unittest.TestCase):
    def test_matching_h1_leaves_input_byte_identical(self):
        text = "# Title\n\nbody\n"
        self.assertEqual(run(text, "Title"), text)

    def test_differing_h1_is_replaced_in_place(self):
        self.assertEqual(run("# Old\n\nbody\n", "New"), "# New\n\nbody\n")

    def test_hash_inside_a_fenced_block_is_not_treated_as_h1(self):
        text = "# Real\n\n```\n# not h1\n```\n"
        self.assertEqual(run(text, "Real"), text)


class MissingH1(unittest.TestCase):
    def test_injected_after_frontmatter(self):
        out = run("---\nk: v\n---\nbody\n", "Injected")
        self.assertEqual(out, "---\nk: v\n---\n\n# Injected\n\nbody\n")

    def test_injected_at_top_when_no_frontmatter(self):
        self.assertEqual(run("body text\n", "Top"), "# Top\n\nbody text\n")


class EmptyInput(unittest.TestCase):
    def test_whitespace_only_input_is_returned_unchanged(self):
        self.assertEqual(run("", "X"), "")
        self.assertEqual(run("   \n\n", "X"), "   \n\n")


if __name__ == "__main__":
    unittest.main()
