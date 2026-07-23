import subprocess
import unittest

from helpers import BIN, load

SCRIPT = BIN / "insert-daily-note-section.py"
sect = load("insert-daily-note-section.py", "insert_daily_note_section")


def run(note, content):
    return subprocess.run(
        ["/usr/bin/python3", str(SCRIPT), "--content", content],
        input=note, capture_output=True, text=True, check=True,
    ).stdout


class FlatTimeline(unittest.TestCase):
    NOTE = ("# Day\n\n"
            "- 7:00am: 🔗 [early](x-devonthink-item://E)\n"
            "- 9:12am: a jot\n"
            "- 3:00pm: 📅 Late meeting\n"
            "- 📔 [Journal](x-devonthink-item://J)\n")

    def test_timed_block_slots_between_bullets(self):
        out = run(self.NOTE, "- 8:00am: 📄 [scan](x-devonthink-item://S)\n")
        lines = out.splitlines()
        at = lines.index("- 8:00am: 📄 [scan](x-devonthink-item://S)")
        self.assertIn("early", lines[at - 1])
        self.assertEqual(lines[at + 1], "- 9:12am: a jot")

    def test_a_late_import_lands_before_a_future_event(self):
        out = run(self.NOTE, "- 1:00pm: 🔗 [x](x-devonthink-item://X)\n")
        self.assertLess(out.index("- 1:00pm:"), out.index("- 3:00pm:"))

    def test_a_block_with_sublines_stays_together(self):
        out = run(self.NOTE,
                  "- 10:00am: ✏️ [page](x-devonthink-item://H)\n"
                  "  - extracted one\n  extracted prose\n")
        lines = out.splitlines()
        at = lines.index("- 10:00am: ✏️ [page](x-devonthink-item://H)")
        self.assertEqual(lines[at + 1], "  - extracted one")
        self.assertEqual(lines[at + 2], "  extracted prose")

    def test_virgin_skeleton_placeholder_is_replaced(self):
        out = run("# Day\n\n- \n", "- 8:00am: 🔗 [x](x-devonthink-item://X)\n")
        self.assertEqual(out, "# Day\n\n- 8:00am: 🔗 [x](x-devonthink-item://X)")


class LegacyHeaderPath(unittest.TestCase):
    def test_inserts_under_header_before_next_heading(self):
        note = "# Day\n\n## Today's Notes\n\n- old\n\n## Other\n\n- x\n"
        out = run(note, "- new")
        self.assertEqual(
            out, "# Day\n\n## Today's Notes\n\n- old\n- new\n\n## Other\n\n- x"
        )

    def test_consecutive_list_items_merge_without_a_blank_separator(self):
        self.assertEqual(run("## Today's Notes\n\n- a\n", "- b"),
                         "## Today's Notes\n\n- a\n- b")

    def test_span_stops_at_an_indented_h1_heading(self):
        note = ("# Day\n\n## Today's Notes\n\n- old\n\n"
                "  # Indented Heading\n\n- other")
        out = run(note, "- new")
        self.assertEqual(
            out,
            "# Day\n\n## Today's Notes\n\n- old\n- new\n\n"
            "  # Indented Heading\n\n- other",
        )

    def test_timed_bullet_slots_into_time_order(self):
        note = "## Today's Notes\n\n- 9:00am: a\n- 7:00am: b\n"
        out = run(note, "- 8:00am: c")
        self.assertEqual(
            out, "## Today's Notes\n\n- 7:00am: b\n- 8:00am: c\n- 9:00am: a")

    def test_sort_moves_whole_blocks_never_bare_sublines(self):
        note = ("## Today's Notes\n\n"
                "- 3:00pm: 🔗 [Later clip](x-devonthink-item://L)\n"
                "- 9:24am: ✏️ [doc](x-devonthink-item://D)\n"
                "  - 4:30pm dentist reminder\n"
                "  - call the plumber\n")
        out = run(note, "- 1:00pm: 🔗 [mid](x-devonthink-item://M)")
        lines = out.splitlines()
        at = lines.index("- 9:24am: ✏️ [doc](x-devonthink-item://D)")
        self.assertEqual(lines[at + 1], "  - 4:30pm dentist reminder")
        self.assertEqual(lines[at + 2], "  - call the plumber")
        self.assertLess(at, lines.index(
            "- 1:00pm: 🔗 [mid](x-devonthink-item://M)"))


class TimeKey(unittest.TestCase):
    def test_meridiem_boundaries(self):
        self.assertEqual(sect.time_key("- 12:00am: midnight"), 0)
        self.assertEqual(sect.time_key("- 12:30pm: noon"), 12 * 60 + 30)
        self.assertEqual(sect.time_key("- 1:05pm: after"), 13 * 60 + 5)

    def test_untimed_lines_have_no_key(self):
        self.assertIsNone(sect.time_key("- plain bullet"))
        self.assertIsNone(sect.time_key("not a bullet at all"))


class CarriageReturnContent(unittest.TestCase):
    """--content is built in AppleScript, where `return` is a CR."""

    def test_cr_content_becomes_separate_lines(self):
        note = ("# Day\n\n- 7:00am: early\n")
        out = run(note, "- 9:00am: ✏️ [doc](x-devonthink-item://D)\r"
                        "  - one\r  - two\r")
        self.assertEqual(
            out,
            "# Day\n\n- 7:00am: early\n"
            "- 9:00am: ✏️ [doc](x-devonthink-item://D)\n  - one\n  - two",
        )


if __name__ == "__main__":
    unittest.main()
