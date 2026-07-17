"""Reverse-chronological ordering of entity log sections.

The sort lives in entity-dt-bridge.js, so the test drives the real JXA
function through osascript rather than reimplementing it in Python — a Python
copy would only prove the copy sorts. No DEVONthink involved: sortLogSection
is pure, taking and returning body lines.
"""

import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path

BRIDGE = (Path(__file__).resolve().parents[2] / "stow" / "devonthink" /
          ".local" / "bin" / "entity-dt-bridge.js")

HARNESS = textwrap.dedent("""
    ObjC.import('Foundation')

    function readText(path) {
      return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(
        path, $.NSUTF8StringEncoding, $()))
    }

    function run(argv) {
      const bridgeSrc = readText(argv[0])
      const cases = JSON.parse(readText(argv[1]))
      eval(bridgeSrc)
      return JSON.stringify(cases.map(function (c) {
        let out = sortLogSection(c.body.split('\\n'), c.header)
        if (c.twice) out = sortLogSection(out, c.header)
        return out.join('\\n')
      }))
    }
""")

LOG = "## Biographical Log"


def sort_sections(cases, tmpdir):
    harness = tmpdir / "harness.js"
    harness.write_text(HARNESS)
    payload = tmpdir / "cases.json"
    payload.write_text(json.dumps(cases))
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", str(harness), str(BRIDGE),
         str(payload)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"osascript failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


class LogSort(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "dt-log-sort-test"
        cls.tmp.mkdir(parents=True, exist_ok=True)

    def sort(self, body, header=LOG, twice=False):
        return sort_sections(
            [{"body": body, "header": header, "twice": twice}], self.tmp)[0]

    def test_entries_are_ordered_newest_first(self):
        body = "\n".join([
            "# Someone",
            "",
            LOG,
            "",
            "- 2026-07-10 — Best friend from school.",
            "- 2026-03-16 — Working on a thesis.",
            "- 2026-05-30 — Graduating in May.",
        ])
        self.assertEqual(self.sort(body).split("\n")[4:], [
            "- 2026-07-10 — Best friend from school.",
            "- 2026-05-30 — Graduating in May.",
            "- 2026-03-16 — Working on a thesis.",
        ])

    def test_same_date_entries_keep_their_filed_order(self):
        body = "\n".join([
            LOG,
            "- 2026-03-16 — First filed.",
            "- 2026-03-16 — Second filed.",
            "- 2026-07-10 — Later fact.",
        ])
        self.assertEqual(self.sort(body).split("\n")[1:], [
            "- 2026-07-10 — Later fact.",
            "- 2026-03-16 — First filed.",
            "- 2026-03-16 — Second filed.",
        ])

    def test_sort_is_idempotent(self):
        body = "\n".join([
            LOG,
            "- 2026-01-01 — A.",
            "- 2026-02-02 — B.",
            "- 2026-01-01 — C.",
        ])
        self.assertEqual(self.sort(body, twice=True), self.sort(body))

    def test_header_fields_and_later_sections_are_untouched(self):
        body = "\n".join([
            "# Someone",
            "",
            "**Role:** Architect",
            "**City:** Springfield",
            "",
            LOG,
            "",
            "- 2026-01-01 — Older.",
            "- 2026-09-09 — Newer.",
            "",
            "## Notes",
            "",
            "- 2026-12-31 — Not a fact, another section.",
        ])
        self.assertEqual(self.sort(body).split("\n"), [
            "# Someone",
            "",
            "**Role:** Architect",
            "**City:** Springfield",
            "",
            LOG,
            "",
            "- 2026-09-09 — Newer.",
            "- 2026-01-01 — Older.",
            "",
            "## Notes",
            "",
            "- 2026-12-31 — Not a fact, another section.",
        ])

    def test_undated_lines_hold_their_position(self):
        body = "\n".join([
            LOG,
            "",
            "Facts I never dated:",
            "- Went to school somewhere.",
            "- 2026-01-01 — Older.",
            "- 2026-09-09 — Newer.",
        ])
        self.assertEqual(self.sort(body).split("\n"), [
            LOG,
            "",
            "Facts I never dated:",
            "- Went to school somewhere.",
            "- 2026-09-09 — Newer.",
            "- 2026-01-01 — Older.",
        ])

    def test_continuation_lines_travel_with_their_entry(self):
        body = "\n".join([
            LOG,
            "- 2026-01-01 — Older.",
            "  indented detail under the older fact",
            "- 2026-09-09 — Newer.",
        ])
        self.assertEqual(self.sort(body).split("\n")[1:], [
            "- 2026-09-09 — Newer.",
            "- 2026-01-01 — Older.",
            "  indented detail under the older fact",
        ])

    def test_indented_dated_sub_bullet_travels_with_its_parent(self):
        """An indented dated bullet is a nested detail, not a top-level entry:
        it must move with the fact above it, not sort on its own date."""
        body = "\n".join([
            LOG,
            "- 2026-01-01 — Older parent.",
            "  - 2026-12-31 — dated detail under the older fact.",
            "- 2026-09-09 — Newer.",
        ])
        self.assertEqual(self.sort(body).split("\n")[1:], [
            "- 2026-09-09 — Newer.",
            "- 2026-01-01 — Older parent.",
            "  - 2026-12-31 — dated detail under the older fact.",
        ])

    def test_blank_line_separators_stay_between_entries(self):
        body = "\n".join([
            LOG,
            "",
            "- 2026-01-01 — Older.",
            "",
            "- 2026-09-09 — Newer.",
        ])
        self.assertEqual(self.sort(body).split("\n"), [
            LOG,
            "",
            "- 2026-09-09 — Newer.",
            "",
            "- 2026-01-01 — Older.",
        ])

    def test_record_without_the_section_is_returned_unchanged(self):
        body = "# Someone\n\n**Role:** Architect\n"
        self.assertEqual(self.sort(body), body)

    def test_event_log_section_sorts_too(self):
        body = "\n".join([
            "## Log",
            "- 2026-01-01 — Older.",
            "- 2026-09-09 — Newer.",
        ])
        self.assertEqual(self.sort(body, header="## Log").split("\n")[1:], [
            "- 2026-09-09 — Newer.",
            "- 2026-01-01 — Older.",
        ])

    def test_source_links_and_fact_markers_survive_the_move(self):
        older = ("- 2026-01-01 — Older. ([source](x-devonthink-item://AAA))"
                 " <!-- fact:0badf00d -->")
        newer = ("- 2026-09-09 — Newer. ([source](x-devonthink-item://BBB))"
                 " <!-- fact:1badf00d -->")
        self.assertEqual(
            self.sort("\n".join([LOG, older, newer])).split("\n")[1:],
            [newer, older])


if __name__ == "__main__":
    unittest.main()
