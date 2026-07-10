import subprocess
import unittest

from helpers import BIN, load

SCRIPT = BIN / "insert-daily-note-section.py"
sect = load("insert-daily-note-section.py", "insert_daily_note_section")


def run(note, header, content):
    return subprocess.run(
        ["/usr/bin/python3", str(SCRIPT), "--header", header, "--content", content],
        input=note, capture_output=True, text=True, check=True,
    ).stdout


class HeaderExists(unittest.TestCase):
    def test_inserts_under_header_before_next_heading(self):
        note = "# Day\n\n## Today's Notes\n\n- old\n\n## Other\n\n- x\n"
        out = run(note, "## Today's Notes", "- new")
        self.assertEqual(
            out, "# Day\n\n## Today's Notes\n\n- old\n- new\n\n## Other\n\n- x"
        )

    def test_consecutive_list_items_merge_without_a_blank_separator(self):
        self.assertEqual(run("## T\n\n- a\n", "## T", "- b"), "## T\n\n- a\n- b")


class HeaderMissing(unittest.TestCase):
    def test_appends_header_and_content_at_end(self):
        out = run("# Day\n\n- x\n", "## Notes", "- new")
        self.assertEqual(out, "# Day\n\n- x\n\n## Notes\n\n- new")


class ChronologicalOrdering(unittest.TestCase):
    def test_timed_bullet_slots_into_time_order(self):
        note = "## T\n\n- 9:00am: a\n- 7:00am: b\n"
        out = run(note, "## T", "- 8:00am: c")
        self.assertEqual(out, "## T\n\n- 7:00am: b\n- 8:00am: c\n- 9:00am: a")


class TimeKey(unittest.TestCase):
    def test_meridiem_boundaries(self):
        self.assertEqual(sect.time_key("- 12:00am: midnight"), 0)
        self.assertEqual(sect.time_key("- 12:30pm: noon"), 12 * 60 + 30)
        self.assertEqual(sect.time_key("- 1:05pm: after"), 13 * 60 + 5)

    def test_untimed_lines_have_no_key(self):
        self.assertIsNone(sect.time_key("- plain bullet"))
        self.assertIsNone(sect.time_key("not a bullet at all"))


if __name__ == "__main__":
    unittest.main()
