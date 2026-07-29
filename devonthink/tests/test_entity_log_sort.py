"""Reverse-chronological ordering and same-date grouping of entity log
sections.

Sort and grouping live in entity-dt-bridge.js, so the test drives the real
JXA functions through osascript rather than reimplementing them in Python — a
Python copy would only prove the copy sorts. No DEVONthink involved:
sortLogSection, groupLogSection, and bodyFactSignatures are pure, taking and
returning body lines.
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
        if (c.fn === 'signatures') {
          return bodyFactSignatures(c.body.split('\\n'))
        }
        const pass = function (lines) {
          const sorted = sortLogSection(lines, c.header)
          return c.fn === 'group'
            ? groupLogSection(sorted, c.header) : sorted
        }
        let out = pass(c.body.split('\\n'))
        if (c.twice) out = pass(out)
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


def fact(date, text, src="AAA", marker="0badf00d"):
    return (f"- {date} — {text} ([source](x-devonthink-item://{src}))"
            f" <!-- fact:{marker} -->")


class LogGrouping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "dt-log-group-test"
        cls.tmp.mkdir(parents=True, exist_ok=True)

    def group(self, body, header=LOG, twice=False):
        return sort_sections(
            [{"body": body, "header": header, "twice": twice, "fn": "group"}],
            self.tmp)[0]

    def test_same_date_facts_group_under_one_bullet(self):
        a = fact("2026-07-28", "Attended Davidson College.", marker="aaaa1111")
        b = fact("2026-07-28", "Completed a master's program.",
                 marker="bbbb2222")
        self.assertEqual(self.group("\n".join([LOG, a, b])).split("\n")[1:], [
            "- 2026-07-28",
            "  - Attended Davidson College. ([source](x-devonthink-item://AAA))"
            " <!-- fact:aaaa1111 -->",
            "  - Completed a master's program."
            " ([source](x-devonthink-item://AAA)) <!-- fact:bbbb2222 -->",
        ])

    def test_a_lone_fact_stays_flat(self):
        a = fact("2026-07-28", "Attended Davidson College.")
        self.assertEqual(self.group("\n".join([LOG, a])).split("\n")[1:], [a])

    def test_grouping_is_idempotent(self):
        body = "\n".join([
            LOG,
            fact("2026-07-28", "One.", marker="aaaa1111"),
            fact("2026-07-28", "Two.", marker="bbbb2222"),
            fact("2026-06-01", "Three.", marker="cccc3333"),
        ])
        self.assertEqual(self.group(body, twice=True), self.group(body))

    def test_facts_group_across_dates_after_the_sort(self):
        """A fresh fact for an old date appends at the end; the sort brings it
        next to its date-mates and the group folds it in."""
        body = "\n".join([
            LOG,
            fact("2026-07-28", "One.", marker="aaaa1111"),
            fact("2026-06-01", "Old.", marker="cccc3333"),
            fact("2026-07-28", "Two.", marker="bbbb2222"),
        ])
        self.assertEqual(self.group(body).split("\n")[1:], [
            "- 2026-07-28",
            "  - One. ([source](x-devonthink-item://AAA))"
            " <!-- fact:aaaa1111 -->",
            "  - Two. ([source](x-devonthink-item://AAA))"
            " <!-- fact:bbbb2222 -->",
            fact("2026-06-01", "Old.", marker="cccc3333"),
        ])

    def test_a_new_fact_joins_an_existing_group(self):
        body = "\n".join([
            LOG,
            "- 2026-07-28",
            "  - One. ([source](x-devonthink-item://AAA)) <!-- fact:aaaa1111 -->",
            "  - Two. ([source](x-devonthink-item://AAA)) <!-- fact:bbbb2222 -->",
            fact("2026-07-28", "Three.", marker="cccc3333"),
        ])
        self.assertEqual(self.group(body).split("\n")[1:], [
            "- 2026-07-28",
            "  - One. ([source](x-devonthink-item://AAA)) <!-- fact:aaaa1111 -->",
            "  - Two. ([source](x-devonthink-item://AAA)) <!-- fact:bbbb2222 -->",
            "  - Three. ([source](x-devonthink-item://AAA))"
            " <!-- fact:cccc3333 -->",
        ])

    def test_a_group_left_with_one_fact_collapses_to_flat(self):
        body = "\n".join([
            LOG,
            "- 2026-07-28",
            "  - Only one left. ([source](x-devonthink-item://AAA))"
            " <!-- fact:aaaa1111 -->",
        ])
        self.assertEqual(self.group(body).split("\n")[1:], [
            fact("2026-07-28", "Only one left.", marker="aaaa1111"),
        ])

    def test_hand_typed_dated_bullets_never_group(self):
        """No source link means no machine provenance — the line is the
        user's, and its shape is not ours to rewrite."""
        a = "- 2026-07-28 — met at the park."
        b = "- 2026-07-28 — talked about the reunion."
        self.assertEqual(self.group("\n".join([LOG, a, b])).split("\n")[1:],
                         [a, b])

    def test_an_entry_with_manual_detail_under_it_never_groups(self):
        a = fact("2026-07-28", "One.", marker="aaaa1111")
        body = "\n".join([
            LOG, a,
            "  hand-typed detail under the fact",
            fact("2026-07-28", "Two.", marker="bbbb2222"),
        ])
        self.assertEqual(self.group(body).split("\n")[1:], [
            a,
            "  hand-typed detail under the fact",
            fact("2026-07-28", "Two.", marker="bbbb2222"),
        ])

    def test_grouping_never_reaches_across_a_manual_line(self):
        manual = "- 2026-07-28 — hand-typed, no source."
        body = "\n".join([
            LOG,
            fact("2026-07-28", "One.", marker="aaaa1111"),
            manual,
            fact("2026-07-28", "Two.", marker="bbbb2222"),
        ])
        self.assertEqual(self.group(body).split("\n")[1:], [
            fact("2026-07-28", "One.", marker="aaaa1111"),
            manual,
            fact("2026-07-28", "Two.", marker="bbbb2222"),
        ])

    def test_other_sections_are_untouched(self):
        body = "\n".join([
            LOG,
            fact("2026-07-28", "One.", marker="aaaa1111"),
            fact("2026-07-28", "Two.", marker="bbbb2222"),
            "",
            "## Notes",
            fact("2026-07-28", "Not a log fact.", marker="cccc3333"),
            fact("2026-07-28", "Also not.", marker="dddd4444"),
        ])
        got = self.group(body).split("\n")
        self.assertEqual(got[1:3], [
            "- 2026-07-28",
            "  - One. ([source](x-devonthink-item://AAA))"
            " <!-- fact:aaaa1111 -->",
        ])
        self.assertEqual(got[-2:], [
            fact("2026-07-28", "Not a log fact.", marker="cccc3333"),
            fact("2026-07-28", "Also not.", marker="dddd4444"),
        ])

    def test_event_log_section_groups_too(self):
        a = fact("2026-07-28", "Kickoff summary.", marker="aaaa1111")
        b = fact("2026-07-28", "Second note.", marker="bbbb2222")
        got = self.group("\n".join(["## Log", a, b]), header="## Log")
        self.assertEqual(got.split("\n")[1], "- 2026-07-28")


class GroupedFactSignatures(unittest.TestCase):
    """A fact's dedup identity must not depend on which shape it is stored
    in, or every re-run would re-file every grouped fact."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "dt-log-sig-test"
        cls.tmp.mkdir(parents=True, exist_ok=True)

    def signatures(self, *lines):
        return sort_sections(
            [{"body": "\n".join(lines), "fn": "signatures"}], self.tmp)[0]

    def test_grouped_sub_bullet_matches_its_flat_form(self):
        flat = self.signatures(fact("2026-07-28", "Moved to Denver."))
        grouped = self.signatures(
            "- 2026-07-28",
            "  - Moved to Denver. ([source](x-devonthink-item://AAA))"
            " <!-- fact:0badf00d -->")
        self.assertIn(flat[0], grouped)

    def test_same_text_on_another_date_is_a_different_fact(self):
        a = self.signatures(
            "- 2026-07-28",
            "  - Moved. ([source](x-devonthink-item://AAA))")
        b = self.signatures(
            "- 2026-06-01",
            "  - Moved. ([source](x-devonthink-item://AAA))")
        self.assertNotEqual(a[1], b[1])

    def test_an_indented_bullet_outside_a_group_keeps_its_own_identity(self):
        got = self.signatures(
            "- 2026-07-28 — parent. ([source](x-devonthink-item://AAA))",
            "  - nested detail, not a grouped fact")
        self.assertEqual(got[1], "|- nested detail, not a grouped fact")


if __name__ == "__main__":
    unittest.main()
