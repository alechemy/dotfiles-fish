"""Section-span parity across the bridge's pure section machinery.

sectionBounds and insertUnderSectionOnce (both still load-bearing for entity
records' log sections) are pure functions over body-line arrays, so this
drives them through the same osascript eval harness as
test_entity_log_sort.py rather than reimplementing them in Python — a Python
copy would only prove the copy agrees with itself. The daily-note timeline
machinery is covered by test_timeline_merge.py.
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
      const registry = {
        sectionBounds: sectionBounds,
        insertUnderSectionOnce: insertUnderSectionOnce,
      }
      return JSON.stringify(cases.map(function (c) {
        return registry[c.fn].apply(null, c.args)
      }))
    }
""")


def call(fn, args, tmp):
    harness = tmp / "harness.js"
    harness.write_text(HARNESS)
    payload = tmp / "cases.json"
    payload.write_text(json.dumps([{"fn": fn, "args": args}]))
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", str(harness), str(BRIDGE),
         str(payload)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"osascript failed: {result.stderr.strip()}")
    return json.loads(result.stdout)[0]


def make_tmp(name):
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / name
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


class SectionBoundsSpanRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-section-bounds-test")

    def bounds(self, body, header):
        return call("sectionBounds", [body, header], self.tmp)

    def test_stops_at_next_h2(self):
        body = ["## Briefing", "content", "## Birthdays", "more"]
        self.assertEqual(self.bounds(body, "## Briefing"),
                          {"header": 0, "start": 1, "end": 2})

    def test_stops_at_user_h1_after_generated_section(self):
        body = ["## Briefing", "content", "# User Heading", "user prose"]
        self.assertEqual(self.bounds(body, "## Briefing"),
                          {"header": 0, "start": 1, "end": 2})

    def test_runs_to_end_of_body_with_no_following_heading(self):
        body = ["## Briefing", "content", "more content"]
        self.assertEqual(self.bounds(body, "## Briefing"),
                          {"header": 0, "start": 1, "end": 3})

    def test_missing_header_returns_null(self):
        self.assertIsNone(self.bounds(["# Note", "- a"], "## Briefing"))


class InsertUnderSectionOnceCore(unittest.TestCase):
    HEADER = "## Today's Notes"
    LINE = "- [\U0001F4D4 Journal](x-devonthink-item://ABC123)"

    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-insert-once-test")

    def once(self, body):
        return call("insertUnderSectionOnce", [body, self.HEADER, self.LINE],
                     self.tmp)

    def test_inserts_under_existing_section(self):
        out = self.once([self.HEADER, "", "- old"])
        self.assertIn(self.LINE, out)
        self.assertIn("- old", out)

    def test_reinsert_is_a_no_op(self):
        once = self.once([self.HEADER, "", "- old"])
        twice = call("insertUnderSectionOnce", [once, self.HEADER, self.LINE],
                      self.tmp)
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(self.LINE), 1)

    def test_section_missing_appends_a_new_one(self):
        out = self.once(["# Day", "", "- x"])
        self.assertEqual(
            out, ["# Day", "", "- x", "", self.HEADER, "", self.LINE, ""])


if __name__ == "__main__":
    unittest.main()
