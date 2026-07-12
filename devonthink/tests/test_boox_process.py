import os
import tempfile
import unittest
from datetime import date

from helpers import load

jp = load("boox-process.py", "boox_process")

TODAY = date(2026, 7, 11)  # a Saturday
YEAR = 2026


class ParseDateLine(unittest.TestCase):
    def parse(self, line):
        return jp.parse_date_line(line, YEAR, TODAY)

    def test_accepted_formats(self):
        for line in (
            "# Sat, Jul 4",
            "# Saturday, July 4th",
            "Sat Jul 4",
            "# 2026-07-04 Sat",
            "# 7/4",
            "# 7/4/26",
            "# 7/4/2026",
            "## sat. jul 4",
        ):
            self.assertEqual(self.parse(line), date(2026, 7, 4), line)

    def test_weekday_is_a_check_digit(self):
        with self.assertRaises(jp.DateParseError):
            self.parse("# Fri, Jul 4")  # 2026-07-04 is a Saturday

    def test_no_weekday_still_parses(self):
        self.assertEqual(self.parse("# Jul 4"), date(2026, 7, 4))

    def test_year_anchored_to_notebook(self):
        with self.assertRaises(jp.DateParseError):
            self.parse("# Sat, Jul 4, 2025")

    def test_future_dates_rejected(self):
        with self.assertRaises(jp.DateParseError):
            self.parse("# Thu, Dec 31")

    def test_invalid_and_missing_dates_rejected(self):
        for line in ("# Jul 32", "# groceries and errands", "", "#"):
            with self.assertRaises(jp.DateParseError):
                self.parse(line)

    def test_date_mentioned_in_prose_is_not_matched(self):
        # Only the first line is ever passed in; a heading that is prose
        # with no date must park rather than fish one out.
        with self.assertRaises(jp.DateParseError):
            self.parse("# planning the trip")


class AssemblePages(unittest.TestCase):
    def test_cont_heading_merges_into_previous_section(self):
        pages = [
            "# Trip Notes\n\n## Packing\n- boots\n- rain shell",
            "## Packing (cont.)\n- headlamp\n\n## Food\n- oats",
        ]
        got = jp.assemble_pages(pages)
        self.assertEqual(got.count("## Packing"), 1)
        self.assertIn("- headlamp", got)
        self.assertIn("## Food", got)

    def test_cont_only_merges_across_a_boundary(self):
        got = jp.assemble_pages(["## Ideas (cont.)\n- first page keeps it"])
        self.assertIn("## Ideas (cont.)", got)

    def test_blank_pages_dropped(self):
        self.assertEqual(jp.assemble_pages(["# A", "  ", "# B"]), "# A\n\n# B")


class ExtractTasks(unittest.TestCase):
    def test_bullets_under_tasks_header(self):
        text = ("# Sat, Jul 4\n\nprose\n\n## Tasks\n"
                "- fix the bike tire\n- [ ] call the plumber\n"
                "* renew passport\n\n## Later\n- not a task")
        self.assertEqual(
            jp.extract_tasks(text),
            ["fix the bike tire", "call the plumber", "renew passport"])

    def test_no_section_no_tasks(self):
        self.assertEqual(jp.extract_tasks("# Sat, Jul 4\n\n- groceries"), [])

    def test_action_items_variant_and_colon(self):
        text = "Action Items:\n- send the deck"
        self.assertEqual(jp.extract_tasks(text), ["send the deck"])


class FirstHeadingLine(unittest.TestCase):
    def test_skips_leading_blanks(self):
        self.assertEqual(
            jp.first_heading_line("\n\n# Sat, Jul 4\ntext"), "# Sat, Jul 4")

    def test_empty_document(self):
        self.assertEqual(jp.first_heading_line(""), "")


class LoadConfig(unittest.TestCase):
    def write(self, contents):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(contents)
        self.addCleanup(os.unlink, path)
        return path

    def test_model_never_inherited_from_entities_conf(self):
        saved = (jp.ENTITIES_CONFIG, jp.CONFIG_FILE)
        jp.ENTITIES_CONFIG = self.write(
            "OMLX_MODEL=Some-Text-Model\n"
            "OMLX_URL=http://127.0.0.1:9999\n"
            "OMLX_API_KEY=k\n")
        jp.CONFIG_FILE = self.write("")
        try:
            config = jp.load_config()
        finally:
            jp.ENTITIES_CONFIG, jp.CONFIG_FILE = saved
        self.assertEqual(config["OMLX_URL"], "http://127.0.0.1:9999")
        self.assertEqual(config["OMLX_API_KEY"], "k")
        self.assertEqual(config["OMLX_MODEL"], jp.DEFAULTS["OMLX_MODEL"])

    def test_journal_conf_overrides(self):
        saved = (jp.ENTITIES_CONFIG, jp.CONFIG_FILE)
        jp.ENTITIES_CONFIG = self.write("OMLX_URL=http://127.0.0.1:9999\n")
        jp.CONFIG_FILE = self.write("OMLX_MODEL=Other-VL\nMAX_PER_RUN=2\n")
        try:
            config = jp.load_config()
        finally:
            jp.ENTITIES_CONFIG, jp.CONFIG_FILE = saved
        self.assertEqual(config["OMLX_MODEL"], "Other-VL")
        self.assertEqual(config["MAX_PER_RUN"], "2")
        self.assertEqual(config["OMLX_URL"], "http://127.0.0.1:9999")


if __name__ == "__main__":
    unittest.main()
