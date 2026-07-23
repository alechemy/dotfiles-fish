import unittest

from helpers import load

jot = load("insert-jot-into-daily-note.py", "insert_jot_into_daily_note")

MARKER = "## Today's Notes"


class FlatTimeline(unittest.TestCase):
    NOTE = ("# Day\n\n"
            "- 8:00am: 📅 Standup\n"
            "- 9:12am: first thought\n"
            "- 3:00pm: 📅 Late meeting\n")

    def test_jot_slots_by_its_own_time_prefix(self):
        out = jot.insert(self.NOTE, "- 10:04am: J <!-- jot:U -->")
        lines = out.splitlines()
        at = lines.index("- 10:04am: J <!-- jot:U -->")
        self.assertEqual(lines[at - 1], "- 9:12am: first thought")
        self.assertEqual(lines[at + 1], "- 3:00pm: 📅 Late meeting")

    def test_jot_lands_before_a_future_event_not_at_the_end(self):
        out = jot.insert(self.NOTE, "- 1:00pm: J <!-- jot:U -->")
        self.assertLess(out.index("- 1:00pm: J"), out.index("- 3:00pm:"))

    def test_untimed_jot_appends_after_the_last_bullet(self):
        out = jot.insert(self.NOTE, "- J <!-- jot:U -->")
        self.assertEqual(out.splitlines()[-1], "- J <!-- jot:U -->")

    def test_virgin_skeleton_placeholder_is_replaced(self):
        out = jot.insert("# Day\n\n- \n", "- 9:00am: J <!-- jot:U -->")
        self.assertEqual(out, "# Day\n\n- 9:00am: J <!-- jot:U -->")


class LegacyContentBulletBeforeHeader(unittest.TestCase):
    def test_inserts_after_last_content_bullet_skipping_continuations(self):
        note = "# D\n\n- one\n- two\n  continued\n\n" + MARKER + "\n\n- body\n"
        out = jot.insert(note, "- J")
        self.assertEqual(
            out, "# D\n\n- one\n- two\n  continued\n- J\n\n" + MARKER + "\n\n- body"
        )

    def test_a_bullet_after_the_header_is_never_the_target(self):
        note = "# D\n\n" + MARKER + "\n\n- body\n"
        out = jot.insert(note, "- J")
        self.assertTrue(out.index("- J") < out.index(MARKER))


class LegacyEmptyBulletPlaceholder(unittest.TestCase):
    def test_replaces_empty_bullet_before_header(self):
        note = "# D\n\n-\n\n" + MARKER + "\n\n- body\n"
        out = jot.insert(note, "- J")
        self.assertEqual(out, "# D\n\n- J\n\n" + MARKER + "\n\n- body")


class LegacyNoBulletsBeforeHeader(unittest.TestCase):
    def test_inserts_before_header_with_blank_padding(self):
        note = "# D\n\n" + MARKER + "\n\n- body\n"
        out = jot.insert(note, "- J")
        self.assertEqual(out, "# D\n\n- J\n\n" + MARKER + "\n\n- body")


if __name__ == "__main__":
    unittest.main()
