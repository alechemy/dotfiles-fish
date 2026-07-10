import unittest

from helpers import load

jot = load("insert-jot-into-daily-note.py", "insert_jot_into_daily_note")

MARKER = "## Today's Notes"


class NoSectionHeader(unittest.TestCase):
    def test_appends_at_end_when_header_absent(self):
        out = jot.insert("# Day\n\n- a", "- J", MARKER)
        self.assertEqual(out, "# Day\n\n- a\n\n- J")


class ContentBulletBeforeHeader(unittest.TestCase):
    def test_inserts_after_last_content_bullet_skipping_continuations(self):
        note = "# D\n\n- one\n- two\n  continued\n\n" + MARKER + "\n\n- body\n"
        out = jot.insert(note, "- J", MARKER)
        self.assertEqual(
            out, "# D\n\n- one\n- two\n  continued\n- J\n\n" + MARKER + "\n\n- body"
        )

    def test_a_bullet_after_the_header_is_never_the_target(self):
        note = "# D\n\n" + MARKER + "\n\n- body\n"
        out = jot.insert(note, "- J", MARKER)
        self.assertTrue(out.index("- J") < out.index(MARKER))


class EmptyBulletPlaceholder(unittest.TestCase):
    def test_replaces_empty_bullet_before_header(self):
        note = "# D\n\n-\n\n" + MARKER + "\n\n- body\n"
        out = jot.insert(note, "- J", MARKER)
        self.assertEqual(out, "# D\n\n- J\n\n" + MARKER + "\n\n- body")


class NoBulletsBeforeHeader(unittest.TestCase):
    def test_inserts_before_header_with_blank_padding(self):
        note = "# D\n\n" + MARKER + "\n\n- body\n"
        out = jot.insert(note, "- J", MARKER)
        self.assertEqual(out, "# D\n\n- J\n\n" + MARKER + "\n\n- body")


if __name__ == "__main__":
    unittest.main()
