"""The bridge's daily-note timeline machinery: timelineMerge, appendPinned,
parseEventBullet, and rootSpan.

These are pure functions over body-line arrays, driven through the same
osascript eval harness as test_section_span.py — a Python reimplementation
would only prove the copy agrees with itself. The merge is the load-bearing
half of the flatten: every manual line is an anchor, so the cases here are
mostly about what the merge must NOT touch.
"""

import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path

from helpers import load

be = load("brief_events.py", "brief_events")

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
        timelineMerge: timelineMerge,
        appendPinned: appendPinned,
        parseEventBullet: parseEventBullet,
        rootSpan: rootSpan,
        isMachineSubline: isMachineSubline,
        isMachineBullet: isMachineBullet,
      }
      return JSON.stringify(cases.map(function (c) {
        return registry[c.fn].apply(null, c.args)
      }))
    }
""")


def call_many(cases, tmp):
    harness = tmp / "harness.js"
    harness.write_text(HARNESS)
    payload = tmp / "cases.json"
    payload.write_text(json.dumps(cases))
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", str(harness), str(BRIDGE),
         str(payload)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"osascript failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def call(fn, args, tmp):
    return call_many([{"fn": fn, "args": args}], tmp)[0]


def make_tmp(name):
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / name
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def block(minutes, time_str, title, url="dtnote://x", sub_lines=None):
    return {"minutes": minutes, "title": title, "subLines": sub_lines or [],
            "line": f"- {time_str}: 📅 [{title}]({url})"}


def redacted(minutes, time_str):
    return {"minutes": minutes, "redacted": True, "subLines": [],
            "line": f"- {time_str}: 📅 Private event"}


BODY = [
    "# Thursday, July 23, 2026",
    "",
    "- 6:41am: 🔗 [noclip](x-devonthink-item://AAA)",
    "- 8:00am: 📅 [SE Sync](dtnote://x) (tentative)",
    "- 9:12am: manual jot",
    "- 11:00am: 📅 [Roundtable](x-devonthink-item://BBB)",
    "  - 👤 [Priya Raman](x-devonthink-item://PPP) — last contact 2026-07-10",
    "  - manual subnote",
    "- 12:00pm: 📅 Private event",
    "- 📔 [Journal](x-devonthink-item://CCC)",
    "",
]

BLOCKS = [
    block(480, "8:00am", "SE Sync", url="dtnote://x"),
    block(660, "11:00am", "Roundtable", url="x-devonthink-item://BBB",
          sub_lines=["  - 👤 [Priya Raman](x-devonthink-item://PPP)"
                     " — last contact 2026-07-10"]),
    redacted(720, "12:00pm"),
]
BLOCKS[0]["line"] += " (tentative)"


class Merge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-timeline-merge-test")

    def merge(self, body, blocks):
        return call("timelineMerge", [body, blocks], self.tmp)

    def test_identical_state_is_a_no_op(self):
        out = self.merge(BODY, BLOCKS)
        self.assertFalse(out["changed"])
        self.assertIsNone(out["text"])

    def test_new_event_inserts_chronologically_between_manual_lines(self):
        out = self.merge(BODY, BLOCKS + [block(600, "10:00am", "Standup")])
        lines = out["text"].splitlines()
        at = next(i for i, l in enumerate(lines) if "Standup" in l)
        self.assertEqual(lines[at - 1], "- 9:12am: manual jot")
        self.assertIn("Roundtable", lines[at + 1])

    def test_virgin_skeleton_placeholder_is_replaced(self):
        out = self.merge(["# Day", "", "- ", ""], BLOCKS)
        self.assertEqual(out["text"].splitlines()[:3],
                         ["# Day", "", BLOCKS[0]["line"]])

    def test_an_event_placeholder_is_kept_when_nothing_inserts(self):
        out = self.merge(["# Day", "", "- ", ""], [])
        self.assertFalse(out["changed"])

    def test_line_update_preserves_manual_sublines(self):
        blocks = [dict(BLOCKS[0]), dict(BLOCKS[1]), BLOCKS[2]]
        blocks[1] = dict(blocks[1],
                         line="- 11:00am: 📅 [Roundtable](x-devonthink-item://NEW)")
        out = self.merge(BODY, blocks)
        self.assertIn("x-devonthink-item://NEW", out["text"])
        self.assertIn("  - manual subnote", out["text"])
        self.assertEqual(out["text"].count("Priya Raman"), 1)

    def test_machine_sublines_are_rebuilt_not_duplicated(self):
        blocks = [BLOCKS[0],
                  dict(BLOCKS[1], subLines=[
                      "  - 👤 [Priya Raman](x-devonthink-item://PPP)"
                      " — last contact 2026-07-22",
                      "  - ⚠️ identity unresolved"]),
                  BLOCKS[2]]
        out = self.merge(BODY, blocks)
        self.assertIn("2026-07-22", out["text"])
        self.assertNotIn("2026-07-10", out["text"])
        self.assertIn("⚠️ identity unresolved", out["text"])
        text = out["text"]
        again = self.merge(text.split("\n"), blocks)
        self.assertFalse(again["changed"])

    def test_rescheduled_event_relocates_with_manual_sublines(self):
        blocks = [BLOCKS[0],
                  dict(BLOCKS[1], minutes=540,
                       line="- 9:00am: 📅 [Roundtable](x-devonthink-item://BBB)"),
                  BLOCKS[2]]
        out = self.merge(BODY, blocks)
        lines = out["text"].splitlines()
        at = next(i for i, l in enumerate(lines) if "9:00am" in l)
        self.assertIn("Roundtable", lines[at])
        self.assertIn("manual subnote", lines[at + 2])
        self.assertLess(at, lines.index("- 9:12am: manual jot"))
        self.assertEqual(out["text"].count("Roundtable"), 1)

    def test_cancelled_event_without_manual_sublines_is_removed(self):
        out = self.merge(BODY, [BLOCKS[1], BLOCKS[2]])
        self.assertNotIn("SE Sync", out["text"])
        self.assertIn("manual jot", out["text"])

    def test_cancelled_event_with_manual_sublines_survives(self):
        out = self.merge(BODY, [BLOCKS[0], BLOCKS[2]])
        self.assertFalse(out["changed"])

    def test_cancelled_redacted_event_is_removed(self):
        out = self.merge(BODY, BLOCKS[:2])
        self.assertNotIn("Private event", out["text"])

    def test_two_redacted_events_at_one_minute_are_counted(self):
        body = BODY[:9] + ["- 12:00pm: 📅 Private event"] + BODY[9:]
        out = self.merge(body, BLOCKS)
        self.assertEqual(out["text"].count("Private event"), 1)
        out2 = self.merge(body, BLOCKS + [redacted(720, "12:00pm")])
        self.assertFalse(out2["changed"])

    def test_same_title_same_day_events_are_distinct_by_time(self):
        blocks = BLOCKS + [block(900, "3:00pm", "SE Sync")]
        blocks[3]["line"] = "- 3:00pm: 📅 [SE Sync](dtnote://y)"
        out = self.merge(BODY, blocks)
        self.assertEqual(out["text"].count("SE Sync"), 2)
        again = self.merge(out["text"].split("\n"), blocks)
        self.assertFalse(again["changed"])

    def test_a_manual_calendar_emoji_bullet_is_never_removed(self):
        body = BODY[:5] + ["- 10:30am: 📅 lunch with sam"] + BODY[5:]
        out = self.merge(body, BLOCKS)
        self.assertIn("lunch with sam", out["text"] or "\n".join(body))

    def test_empty_blocks_remove_only_bare_machine_events(self):
        out = self.merge(BODY, [])
        self.assertNotIn("SE Sync", out["text"])
        self.assertNotIn("Private event", out["text"])
        self.assertIn("manual jot", out["text"])
        self.assertIn("noclip", out["text"])
        self.assertIn("Roundtable", out["text"])
        self.assertIn("📔", out["text"])

    def test_events_insert_before_the_pinned_journal(self):
        out = self.merge(BODY, BLOCKS + [block(1260, "9:00pm", "Late call")])
        lines = out["text"].splitlines()
        self.assertLess(
            next(i for i, l in enumerate(lines) if "Late call" in l),
            next(i for i, l in enumerate(lines) if "📔" in l))

    def test_midnight_and_noon_sort_correctly(self):
        out = self.merge(BODY, BLOCKS + [block(4, "12:04am", "Insomnia")])
        lines = out["text"].splitlines()
        self.assertLess(
            next(i for i, l in enumerate(lines) if "Insomnia" in l),
            next(i for i, l in enumerate(lines) if "noclip" in l))

    def test_legacy_note_is_refused(self):
        body = ["# Day", "", "- jot", "", "## Briefing", "",
                "- 8:00am — Old"]
        out = self.merge(body, BLOCKS)
        self.assertFalse(out["changed"])
        self.assertTrue(out["legacy"])

    def test_hybrid_note_with_only_todays_notes_is_refused(self):
        body = ["# Day", "", "- ", "", "## Today's Notes", "", "- old"]
        out = self.merge(body, BLOCKS)
        self.assertFalse(out["changed"])
        self.assertTrue(out["legacy"])

    def test_unparseable_blocks_are_skipped_not_looped(self):
        """A block whose rendered line can't be re-parsed would read back as
        a manual anchor — never matched, re-inserted on every run — so the
        merge must drop it and say so."""
        bad = [
            {"minutes": 600, "title": "", "subLines": [],
             "line": "- 10:00am: 📅 "},
            {"minutes": 600, "title": "Planning\nsession", "subLines": [],
             "line": "- 10:00am: 📅 [Planning\nsession](dtnote://x)"},
            {"minutes": 600, "title": "Review [draft](v2) with team",
             "subLines": [],
             "line": "- 10:00am: 📅 [Review [draft](v2) with team](dtnote://x)"},
        ]
        out = self.merge(BODY, BLOCKS + bad)
        self.assertEqual(out["skipped"], 3)
        self.assertFalse(out["changed"])
        out2 = self.merge(BODY, bad)
        self.assertNotIn("10:00am", out2["text"] or "")

    def test_blank_separated_manual_sublines_travel_on_reschedule(self):
        body = BODY[:8] + ["  ", "  - manual two after spacer"] + BODY[8:]
        blocks = [BLOCKS[0],
                  dict(BLOCKS[1], minutes=540,
                       line="- 9:00am: 📅 [Roundtable](x-devonthink-item://BBB)"),
                  BLOCKS[2]]
        out = self.merge(body, blocks)
        lines = out["text"].splitlines()
        at = next(i for i, l in enumerate(lines) if "9:00am" in l)
        block = lines[at:at + 5]
        self.assertIn("  - manual subnote", block)
        self.assertIn("  - manual two after spacer", block)
        self.assertEqual(out["text"].count("Roundtable"), 1)

    def test_a_trailing_user_heading_ends_machine_territory(self):
        body = BODY[:10] + ["", "# My Own Heading", "- kept forever"]
        out = self.merge(body, BLOCKS + [block(1380, "11:00pm", "Nightcap")])
        lines = out["text"].splitlines()
        self.assertLess(
            next(i for i, l in enumerate(lines) if "Nightcap" in l),
            lines.index("# My Own Heading"))
        self.assertIn("- kept forever", out["text"])

    def test_cr_delimited_bodies_never_reach_the_merge_unsplit(self):
        """bodyLines splits CR-delimited bodies before timelineMerge sees
        them; a pre-split CR body arriving as one line must not match any
        machine pattern (so nothing is destroyed, nothing duplicated)."""
        one_line = "\r".join(BODY)
        out = self.merge([one_line], [])
        self.assertFalse(out["changed"])


class AppendPinned(unittest.TestCase):
    LINE = "- 📔 [Journal](x-devonthink-item://JRNL)"

    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-append-pinned-test")

    def pinned(self, body):
        return call("appendPinned", [body, self.LINE], self.tmp)

    def test_appends_at_end_before_trailing_blanks(self):
        out = self.pinned(["# Day", "", "- 9:00am: jot", ""])
        self.assertEqual(out, ["# Day", "", "- 9:00am: jot", self.LINE, ""])

    def test_reappend_is_a_no_op_by_item_link(self):
        once = self.pinned(["# Day", "", "- 9:00am: jot", ""])
        self.assertEqual(self.pinned(once), once)

    def test_legacy_note_routes_to_todays_notes(self):
        out = self.pinned(["# Day", "", "- jot", "", "## Today's Notes", "",
                           "- old", ""])
        at = out.index(self.LINE)
        self.assertGreater(at, out.index("## Today's Notes"))


class ClassifierParity(unittest.TestCase):
    """The sub-line/bullet classifiers exist twice — brief_events.py for the
    Python writers and entity-filing, the bridge for the merge. A line the
    two sides classify differently would be preserved by one and rebuilt by
    the other, so drive both over the same corpus."""

    CORPUS = [
        "- 8:00am: 📅 [SE Sync](dtnote://x)",
        "- 6:41am: 🔗 [noclip](x-devonthink-item://AAA)",
        "- 9:12am: manual jot",
        "- 📔 [Journal](x-devonthink-item://CCC)",
        "- 2:10pm: see 🔗 [x](x-devonthink-item://A)",
        "  - 👤 [Priya Raman](x-devonthink-item://PPP) — last contact",
        "  - ✏️ [scan](x-devonthink-item://B)",
        "  - [✏️ scan](x-devonthink-item://B)",
        "  - ⚠️ identity unresolved",
        "    - 2026-07-13 — moved.",
        "  - 2026-01-01 — remember: anniversary planning",
        "  - ask about the demo",
        "- 2026-07-13 — top level",
    ]

    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-classifier-parity-test")

    def test_bullet_and_subline_classifiers_agree(self):
        cases = ([{"fn": "isMachineBullet", "args": [l]} for l in self.CORPUS]
                 + [{"fn": "isMachineSubline", "args": [l]}
                    for l in self.CORPUS])
        got = call_many(cases, self.tmp)
        js_bullet = got[:len(self.CORPUS)]
        js_subline = got[len(self.CORPUS):]
        for line, js in zip(self.CORPUS, js_bullet):
            self.assertEqual(js, be.is_machine_bullet(line), line)
        for line, js in zip(self.CORPUS, js_subline):
            self.assertEqual(js, be.is_machine_subline(line), line)


if __name__ == "__main__":
    unittest.main()
