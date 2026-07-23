"""brief_events: event keys, event-line parsing in both grammars, note↔event
matching, and the in-place timeline splices the smart rules perform.

The matcher's failure mode must be silence, never a wrong link: an ambiguous
or weak match falls back to the plain timeline-bullet path, so every
"returns None" case here is load-bearing.
"""

import unittest

from helpers import load

be = load("brief_events.py", "brief_events")
mb = load("dt-morning-brief.py", "dt_morning_brief")

TODAY = "2026-07-14"

# A pre-flatten note: events live in a ## Briefing section, " — " separator.
BRIEF = """# Tuesday, July 14, 2026

-

## Today's Notes

- 9:01am: [📄 Some scan](x-devonthink-item://SCAN)

## Briefing

<!-- brief:2026-07-14 -->

- 8:00am — SE / Prod Engineering Sync (tentative)
- 11:00am — [Vendor Roundtable](dtnote://open?date=2026-07-14&title=Vendor%20Roundtable)
  - [Priya Raman](x-devonthink-item://uuid-priya-raman)
- 12:00pm — Call Priya
- 12:00pm — Weekly PCD CAB 2026 edition
- 1:30pm — Roundtable: Round 2
- 3:00pm — Private event

## On This Day

- something old
"""

# A flat note: events are 📅 bullets in the root timeline, ": " separator.
TIMELINE = """# Tuesday, July 14, 2026

- 7:20am: slow start, skipped the run
- 8:00am: 📅 SE / Prod Engineering Sync (tentative)
- 9:01am: 📄 [Some scan](x-devonthink-item://SCAN)
- 11:00am: 📅 [Vendor Roundtable](dtnote://open?date=2026-07-14&title=Vendor%20Roundtable)
  - 👤 [Priya Raman](x-devonthink-item://uuid-priya-raman)
- 12:00pm: 📅 Call Priya
- 12:00pm: 📅 Weekly PCD CAB 2026 edition
- 1:30pm: 📅 Roundtable: Round 2
- 3:00pm: 📅 Private event
- 📔 [Journal](x-devonthink-item://JRNL)
"""


class Keys(unittest.TestCase):
    def test_slug_folds_case_punctuation_and_diacritics(self):
        self.assertEqual(be.slug("SE / Prod Engineering Sync"),
                         "se-prod-engineering-sync")
        self.assertEqual(be.slug("Café: Süß & Co"), "cafe-suss-co")

    def test_event_key_is_date_plus_slug(self):
        self.assertEqual(be.event_key(TODAY, "Call Priya"),
                         f"{TODAY}-call-priya")

    def test_key_is_stable_across_name_and_title_forms(self):
        """The adopt rule derives the key from the record name the create
        link chose; it must land on the key the renderer derived from the
        event title."""
        parsed = be.parse_name_date(f"{TODAY} Call Priya")
        self.assertEqual(be.event_key(parsed[0], parsed[1]),
                         be.event_key(TODAY, "Call Priya"))

    def test_parse_name_date_rejects_undated_and_invalid(self):
        self.assertIsNone(be.parse_name_date("Call Priya"))
        self.assertIsNone(be.parse_name_date("2026-13-40 Call Priya"))


class DtnoteUrl(unittest.TestCase):
    def test_encodes_date_and_title(self):
        url = be.dtnote_url(TODAY, "SE / Prod Engineering Sync")
        self.assertEqual(
            url, "dtnote://open?date=2026-07-14"
                 "&title=SE%20%2F%20Prod%20Engineering%20Sync")

    def test_no_unencoded_parens_break_the_markdown_link(self):
        url = be.dtnote_url(TODAY, "Sync (EU)")
        self.assertNotIn("(", url)
        self.assertNotIn(")", url)


class ParseEvents(unittest.TestCase):
    def test_reads_plain_linked_and_tentative_lines(self):
        evs = be.parse_events(BRIEF)
        self.assertEqual(
            [e["title"] for e in evs],
            ["SE / Prod Engineering Sync", "Vendor Roundtable", "Call Priya",
             "Weekly PCD CAB 2026 edition", "Roundtable: Round 2"])

    def test_redacted_events_are_withheld(self):
        self.assertNotIn(mb.REDACTED_TITLE,
                         [e["title"] for e in be.parse_events(BRIEF)])

    def test_sub_bullets_and_other_sections_are_not_events(self):
        evs = be.parse_events(BRIEF)
        self.assertNotIn("Priya Raman", [e["title"] for e in evs])
        self.assertNotIn("Some scan", [e["title"] for e in evs])

    def test_tentative_suffix_is_separated_not_part_of_the_title(self):
        ev = be.parse_events(BRIEF)[0]
        self.assertEqual(ev["suffix"], " (tentative)")

    def test_no_event_lines_means_no_events(self):
        self.assertEqual(be.parse_events("# A day\n\n- nothing\n"), [])

    def test_constants_stay_in_sync_with_the_renderer(self):
        self.assertEqual(be.REDACTED_TITLE, mb.REDACTED_TITLE)


class ParseTimelineEvents(unittest.TestCase):
    def test_reads_plain_linked_and_tentative_lines(self):
        evs = be.parse_events(TIMELINE)
        self.assertEqual(
            [e["title"] for e in evs],
            ["SE / Prod Engineering Sync", "Vendor Roundtable", "Call Priya",
             "Weekly PCD CAB 2026 edition", "Roundtable: Round 2"])
        self.assertTrue(all(e["style"] == "timeline" for e in evs))

    def test_redacted_events_and_non_event_bullets_are_withheld(self):
        titles = [e["title"] for e in be.parse_events(TIMELINE)]
        self.assertNotIn(mb.REDACTED_TITLE, titles)
        self.assertNotIn("Some scan", titles)
        self.assertNotIn("Priya Raman", titles)
        self.assertNotIn("slow start, skipped the run", titles)

    def test_legacy_events_carry_their_own_style(self):
        self.assertTrue(
            all(e["style"] == "legacy" for e in be.parse_events(BRIEF)))


class MachineClassifiers(unittest.TestCase):
    def test_machine_bullets_are_the_emoji_typed_lines(self):
        machine = [l for l in TIMELINE.splitlines()
                   if be.is_machine_bullet(l)]
        self.assertEqual(len(machine), 8)
        self.assertNotIn("- 7:20am: slow start, skipped the run", machine)
        self.assertIn("- 📔 [Journal](x-devonthink-item://JRNL)", machine)

    def test_an_emoji_later_in_the_line_stays_manual(self):
        self.assertFalse(
            be.is_machine_bullet("- 2:10pm: see 🔗 [x](x-devonthink-item://A)"))

    def test_sublines_by_token_and_news_date(self):
        self.assertTrue(be.is_machine_subline(
            "  - 👤 [Priya Raman](x-devonthink-item://P) — last contact"))
        self.assertTrue(be.is_machine_subline(
            "  - ✏️ [scan](x-devonthink-item://B)"))
        self.assertTrue(be.is_machine_subline(
            "  - [✏️ scan](x-devonthink-item://B)"))
        self.assertTrue(be.is_machine_subline("  - ⚠️ identity unresolved"))
        self.assertTrue(be.is_machine_subline("    - 2026-07-13 — moved."))
        self.assertFalse(be.is_machine_subline("  - ask about the demo"))
        self.assertFalse(be.is_machine_subline("- 2026-07-13 — top level"))


class TimelineInsert(unittest.TestCase):
    def test_slots_between_bullets_by_time(self):
        lines = TIMELINE.splitlines()
        got = be.timeline_insert(
            lines, ["- 10:15am: 🔗 [late import](x-devonthink-item://L)"])
        at = got.index("- 10:15am: 🔗 [late import](x-devonthink-item://L)")
        self.assertEqual(got[at - 1],
                         "- 9:01am: 📄 [Some scan](x-devonthink-item://SCAN)")
        self.assertIn("Vendor Roundtable", got[at + 1])

    def test_same_minute_appends_after_the_existing_bullet(self):
        lines = TIMELINE.splitlines()
        got = be.timeline_insert(lines, ["- 12:00pm: second noon jot"])
        at = got.index("- 12:00pm: second noon jot")
        self.assertIn("Weekly PCD CAB", got[at - 1])

    def test_untimed_block_lands_before_the_pinned_journal(self):
        lines = TIMELINE.splitlines()
        got = be.timeline_insert(lines, ["- an untimed note"])
        at = got.index("- an untimed note")
        self.assertIn("📔", got[at + 1])

    def test_block_sublines_travel_with_the_bullet(self):
        lines = TIMELINE.splitlines()
        got = be.timeline_insert(
            lines, ["- 10:15am: ✏️ [page](x-devonthink-item://H)",
                    "  - extracted line"])
        at = got.index("- 10:15am: ✏️ [page](x-devonthink-item://H)")
        self.assertEqual(got[at + 1], "  - extracted line")

    def test_virgin_skeleton_placeholder_is_replaced(self):
        lines = ["# Tuesday, July 14, 2026", "", "- ", ""]
        got = be.timeline_insert(lines, ["- 8:00am: first thing"])
        self.assertEqual(
            got, ["# Tuesday, July 14, 2026", "", "- 8:00am: first thing", ""])

    def test_untimed_manual_bullets_are_anchors(self):
        lines = ["# Day", "", "- 9:00am: 📅 Standup", "- follow-up thought", ""]
        got = be.timeline_insert(lines, ["- 10:00am: later"])
        self.assertEqual(got.index("- 10:00am: later"),
                         got.index("- follow-up thought") + 1)


class Matching(unittest.TestCase):
    TITLES = ["SE / Prod Engineering Sync", "Vendor Roundtable", "Call Priya",
              "Weekly PCD CAB 2026 edition", "Roundtable: Round 2"]

    def test_stopword_drift_still_matches(self):
        title, status = be.best_match("Call with Priya", self.TITLES)
        self.assertEqual((title, status), ("Call Priya", "match"))

    def test_word_order_and_partial_title_match(self):
        title, status = be.best_match("PCD CAB", self.TITLES)
        self.assertEqual((title, status),
                         ("Weekly PCD CAB 2026 edition", "match"))

    def test_two_equal_candidates_refuse_to_choose(self):
        title, status = be.best_match("Roundtable", self.TITLES)
        self.assertEqual((title, status), (None, "ambiguous"))

    def test_unrelated_name_matches_nothing(self):
        title, status = be.best_match("Grocery list", self.TITLES)
        self.assertEqual((title, status), (None, "none"))

    def test_all_stopword_name_falls_back_to_its_own_tokens(self):
        """A name that is nothing but stopwords must not divide by zero or
        match everything; its raw tokens still have to earn the overlap."""
        title, status = be.best_match("Weekly Sync", self.TITLES)
        self.assertEqual(status, "none")

    def test_match_note_strips_a_date_prefixed_note_name(self):
        """boox renames dated notes to "YYYY-MM-DD <title>"; the date tokens
        must not dilute the overlap."""
        hit = be.match_note(f"{TODAY} Call with Priya", [(TODAY, BRIEF)])
        self.assertEqual(hit["key"], f"{TODAY}-call-priya")

    def test_match_note_falls_through_to_the_buffer_day_on_no_match(self):
        yesterday_brief = BRIEF.replace("Call Priya", "Budget Kickoff")
        hit = be.match_note(
            "Budget Kickoff",
            [(TODAY, BRIEF.replace("Call Priya", "Design Review")),
             ("2026-07-13", yesterday_brief)])
        self.assertEqual(hit["date"], "2026-07-13")

    def test_match_note_does_not_fall_through_past_an_ambiguous_day(self):
        """Two lookalike events today plus a weak echo yesterday: linking to
        yesterday because today was ambiguous would be a confident wrong
        answer, so ambiguity ends the search."""
        hit = be.match_note("Roundtable",
                            [(TODAY, BRIEF), ("2026-07-13", BRIEF)])
        self.assertIsNone(hit)


class LinkTitle(unittest.TestCase):
    def test_swaps_a_create_url_for_the_item_link(self):
        key = be.event_key(TODAY, "Vendor Roundtable")
        got = be.link_title(BRIEF, TODAY, key, "NOTE-UUID")
        self.assertIn(
            "- 11:00am — [Vendor Roundtable](x-devonthink-item://NOTE-UUID)",
            got)
        self.assertNotIn("dtnote://", got)

    def test_wraps_a_plain_title_preserving_the_tentative_suffix(self):
        key = be.event_key(TODAY, "SE / Prod Engineering Sync")
        got = be.link_title(BRIEF, TODAY, key, "NOTE-UUID")
        self.assertIn("- 8:00am — [SE / Prod Engineering Sync]"
                      "(x-devonthink-item://NOTE-UUID) (tentative)", got)

    def test_an_item_linked_title_is_left_alone(self):
        key = be.event_key(TODAY, "Vendor Roundtable")
        once = be.link_title(BRIEF, TODAY, key, "NOTE-UUID")
        self.assertEqual(be.link_title(once, TODAY, key, "OTHER-UUID"), once)

    def test_unknown_key_changes_nothing(self):
        self.assertEqual(
            be.link_title(BRIEF, TODAY, f"{TODAY}-nope", "NOTE-UUID"), BRIEF)

    def test_timeline_lines_are_relinked_in_their_own_grammar(self):
        key = be.event_key(TODAY, "Vendor Roundtable")
        got = be.link_title(TIMELINE, TODAY, key, "NOTE-UUID")
        self.assertIn("- 11:00am: 📅 [Vendor Roundtable]"
                      "(x-devonthink-item://NOTE-UUID)", got)
        self.assertNotIn("dtnote://", got)

    def test_timeline_plain_title_keeps_the_tentative_suffix(self):
        key = be.event_key(TODAY, "SE / Prod Engineering Sync")
        got = be.link_title(TIMELINE, TODAY, key, "NOTE-UUID")
        self.assertIn("- 8:00am: 📅 [SE / Prod Engineering Sync]"
                      "(x-devonthink-item://NOTE-UUID) (tentative)", got)


class InsertSubbullet(unittest.TestCase):
    BULLET = "- [✏️ Priya prep](x-devonthink-item://HW-UUID)"

    def test_inserts_indented_directly_under_the_event_line(self):
        key = be.event_key(TODAY, "Call Priya")
        got = be.insert_subbullet(BRIEF, TODAY, key, self.BULLET)
        lines = got.splitlines()
        at = lines.index("- 12:00pm — Call Priya")
        self.assertEqual(lines[at + 1], "  " + self.BULLET)

    def test_is_idempotent_by_item_link(self):
        key = be.event_key(TODAY, "Call Priya")
        once = be.insert_subbullet(BRIEF, TODAY, key, self.BULLET)
        self.assertEqual(
            be.insert_subbullet(once, TODAY, key, self.BULLET), once)

    def test_unknown_key_changes_nothing(self):
        got = be.insert_subbullet(BRIEF, TODAY, f"{TODAY}-nope", self.BULLET)
        self.assertEqual(got, BRIEF)

    def test_preserves_the_trailing_newline(self):
        key = be.event_key(TODAY, "Call Priya")
        got = be.insert_subbullet(BRIEF, TODAY, key, self.BULLET)
        self.assertTrue(got.endswith("\n"))

    def test_splices_under_a_timeline_event_line(self):
        key = be.event_key(TODAY, "Call Priya")
        got = be.insert_subbullet(TIMELINE, TODAY, key, self.BULLET)
        lines = got.splitlines()
        at = lines.index("- 12:00pm: 📅 Call Priya")
        self.assertEqual(lines[at + 1], "  " + self.BULLET)
        self.assertEqual(
            be.insert_subbullet(got, TODAY, key, self.BULLET), got)


if __name__ == "__main__":
    unittest.main()
