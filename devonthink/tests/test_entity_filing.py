import hashlib
import json
import os
import re
import tempfile
import unittest

from helpers import load, person

ef = load("entity-filing.py", "entity_filing")

SOURCE = {"uuid": "SRC-1", "name": "2026-03-16 Call", "kind": "daily"}


def roster(*people):
    return list(people), ef.roster_index(list(people))


class ParseExtraction(unittest.TestCase):
    def test_plain_object(self):
        people, events = ef.parse_extraction(
            '{"people": [{"name": "Alison"}], "events": []}')
        self.assertEqual(people, [{"name": "Alison"}])
        self.assertEqual(events, [])

    def test_fenced_object(self):
        people, _ = ef.parse_extraction(
            '```json\n{"people": [{"name": "Alison"}]}\n```')
        self.assertEqual(people, [{"name": "Alison"}])

    def test_unclosed_people_array(self):
        # Qwen3-VL-32B-4bit closes the last person but not the array, leaving
        # "events" as a bare string inside it. Deterministic at temperature 0,
        # so the source burns all its attempts and parks.
        people, events = ef.parse_extraction(
            '{"people": [{"name": "Alison", "facts": []}, "events": []}')
        self.assertEqual(people, [{"name": "Alison", "facts": []}])
        self.assertEqual(events, [])

    def test_unclosed_empty_people_array(self):
        people, events = ef.parse_extraction(
            '{"people": ["events": [{"name": "Trip"}]}')
        self.assertEqual(people, [])
        self.assertEqual(events, [{"name": "Trip"}])

    def test_colon_inside_a_string_is_not_a_defect(self):
        people, _ = ef.parse_extraction(
            '{"people": [{"fact": "said: [see \\"x\\"]"}], "events": []}')
        self.assertEqual(people, [{"fact": 'said: [see "x"]'}])

    def test_unrepairable_output_raises_the_original_error(self):
        with self.assertRaises(json.JSONDecodeError) as caught:
            ef.parse_extraction('{"people": [{"name": ')
        self.assertIn("column 21", str(caught.exception))

    def test_non_object_raises(self):
        with self.assertRaises(ValueError):
            ef.parse_extraction('{"events": []}')


class Norm(unittest.TestCase):
    def test_strips_accents_case_and_runs_of_space(self):
        self.assertEqual(ef.norm("  Renée   VAN Dam "), "renee van dam")

    def test_none(self):
        self.assertEqual(ef.norm(None), "")


class CollapseWs(unittest.TestCase):
    def test_collapses_interior_newlines_tabs_and_runs_of_space(self):
        self.assertEqual(ef.collapse_ws("moved to\nDenver  in\tMarch"),
                         "moved to Denver in March")

    def test_strips_leading_and_trailing_whitespace(self):
        self.assertEqual(ef.collapse_ws("  hello  "), "hello")


class ValidDate(unittest.TestCase):
    def test_accepts_iso(self):
        self.assertEqual(ef.valid_date("2026-02-29"), None)  # 2026 is not a leap year
        self.assertEqual(ef.valid_date("2026-03-16"), "2026-03-16")

    def test_rejects_junk(self):
        for bad in ("16-03-2026", "2026-3-16", "", None, "yesterday"):
            self.assertIsNone(ef.valid_date(bad), bad)


class SourceDate(unittest.TestCase):
    def test_prefers_eventdate_then_name_prefix_then_added(self):
        self.assertEqual(ef.source_date_of({"eventdate": "2026-01-02"}), "2026-01-02")
        self.assertEqual(ef.source_date_of({"name": "2026-01-03 Foo"}), "2026-01-03")
        self.assertEqual(ef.source_date_of({"name": "Foo", "added": "2026-01-04"}),
                         "2026-01-04")


class RosterIndex(unittest.TestCase):
    def test_indexes_name_and_aliases_case_insensitively(self):
        _, index = roster(person("Alison Vance", aliases="Alison, Ali"))
        for key in ("alison vance", "alison", "ali"):
            self.assertEqual(len(index[key]), 1, key)

    def test_collision_yields_two_hits(self):
        _, index = roster(person("Jonathan Marsh", aliases="Jonathan"),
                          person("Jonathan Vega", aliases="Jonathan"))
        self.assertEqual(len(index["jonathan"]), 2)


class NearMatches(unittest.TestCase):
    def test_shares_a_name_token(self):
        people = [person("Alison Vance")]
        self.assertEqual(ef.near_matches("Alison", people), ["Alison Vance"])

    def test_ignores_tokens_under_three_chars(self):
        self.assertEqual(ef.near_matches("Al", [person("Al Green")]), [])

    def test_no_overlap(self):
        self.assertEqual(ef.near_matches("Wren", [person("Alison Vance")]), [])


class StalePersonOps(unittest.TestCase):
    def op(self, name, **extra):
        return dict({"op": "ensure_person", "name": name, "fields": {}}, **extra)

    def test_exact_name_hit_is_not_stale(self):
        people, index = roster(person("Alison"))
        self.assertEqual(ef.stale_person_ops([self.op("Alison")], index, people), [])

    def test_alias_hit_is_not_stale(self):
        """The whole point of seeding with aliases: a frozen bare-first-name op
        must resolve to the full-name record instead of duplicating it."""
        people, index = roster(person("Alison Vance", aliases="Alison"))
        self.assertEqual(ef.stale_person_ops([self.op("Alison")], index, people), [])

    def test_shared_token_without_exact_hit_is_stale(self):
        people, index = roster(person("Alison Vance"))
        stale = ef.stale_person_ops([self.op("Alison")], index, people)
        self.assertEqual(stale, [("Alison", ["Alison Vance"])])

    def test_confirm_new_opts_out(self):
        people, index = roster(person("Alison Vance"))
        self.assertEqual(
            ef.stale_person_ops([self.op("Alison", confirm_new=True)], index, people), [])

    def test_empty_roster_is_never_stale(self):
        people, index = roster()
        self.assertEqual(ef.stale_person_ops([self.op("Alison")], index, people), [])

    def test_two_hit_key_is_stale_even_with_confirm_new(self):
        people, index = roster(person("Jonathan Marsh", aliases="Jonathan"),
                               person("Jonathan Vega", aliases="Jonathan"))
        for extra in ({}, {"confirm_new": True}):
            stale = ef.stale_person_ops([self.op("Jonathan", **extra)], index, people)
            self.assertEqual(
                stale, [("Jonathan", ["Jonathan Marsh", "Jonathan Vega"])], extra)

    def test_ignores_other_ops(self):
        people, index = roster(person("Alison Vance"))
        ops = [{"op": "bump_lastcontact", "uuid": "x", "date": "2026-01-01"},
               {"op": "ensure_event", "name": "Alison's Graduation"}]
        self.assertEqual(ef.stale_person_ops(ops, index, people), [])


class BuildPersonPlans(unittest.TestCase):
    def plans(self, extracted, people=(), selves=frozenset()):
        people = list(people)
        return ef.build_person_plans(extracted, ef.roster_index(people), selves,
                                     people, "2026-03-16")

    def test_single_hit_is_existing(self):
        got = self.plans([{"name": "Alison", "facts": [{"fact": "moved"}]}],
                         [person("Alison")])
        self.assertEqual(got[0]["kind"], "existing")

    def test_multiple_hits_are_ambiguous(self):
        got = self.plans([{"name": "Jonathan", "facts": [{"fact": "x"}]}],
                         [person("Jonathan Marsh", aliases="Jonathan"),
                          person("Jonathan Vega", aliases="Jonathan")])
        self.assertEqual(got[0]["kind"], "ambiguous")
        self.assertEqual(len(got[0]["candidates"]), 2)

    def test_no_hit_is_new_and_flags_single_token(self):
        got = self.plans([{"name": "Wren", "facts": [{"fact": "x"}]}])
        self.assertEqual(got[0]["kind"], "new")
        self.assertTrue(got[0]["single_token"])

    def test_self_is_dropped(self):
        got = self.plans([{"name": "Alec", "facts": [{"fact": "x"}]}], selves={"alec"})
        self.assertEqual(got, [])

    def test_person_without_facts_or_updates_is_dropped(self):
        self.assertEqual(self.plans([{"name": "Ghost", "facts": []}]), [])

    def test_undated_fact_inherits_the_source_date(self):
        got = self.plans([{"name": "Wren", "facts": [{"fact": "x"}]}])
        self.assertEqual(got[0]["facts"][0][0], "2026-03-16")

    def test_single_token_alias_match_is_weak(self):
        got = self.plans([{"name": "Maya", "facts": [{"fact": "x"}]}],
                         [person("Maya Chen", aliases="Maya")])
        self.assertEqual(got[0]["kind"], "existing")
        self.assertTrue(got[0]["weak_match"])

    def test_full_name_match_is_strong(self):
        got = self.plans([{"name": "Maya Chen", "facts": [{"fact": "x"}]}],
                         [person("Maya Chen", aliases="Maya")])
        self.assertFalse(got[0]["weak_match"])

    def test_single_token_record_name_match_is_strong(self):
        """A person whose record name IS one token can't match any stronger."""
        got = self.plans([{"name": "Alison", "facts": [{"fact": "x"}]}],
                         [person("Alison")])
        self.assertFalse(got[0]["weak_match"])

    def test_interior_newlines_in_a_fact_are_collapsed(self):
        got = self.plans([{"name": "Wren",
                           "facts": [{"fact": "moved to\nDenver in March"}]}])
        self.assertEqual(got[0]["facts"][0][1], "moved to Denver in March")

    def test_interior_newlines_in_an_update_are_collapsed(self):
        got = self.plans([{"name": "Wren", "facts": [{"fact": "x"}],
                           "updates": {"city": "Denver\nColorado"}}])
        self.assertEqual(got[0]["updates"]["city"], "Denver Colorado")

    def test_interacted_flag_carries_through(self):
        got = self.plans([{"name": "Maya Chen", "interacted": True,
                           "facts": [{"fact": "x"}]}], [person("Maya Chen")])
        self.assertTrue(got[0]["interacted"])
        got = self.plans([{"name": "Maya Chen", "facts": [{"fact": "x"}]}],
                         [person("Maya Chen")])
        self.assertFalse(got[0]["interacted"])


class FilingSuppressed(unittest.TestCase):
    def test_reads_a_flag_set_either_way(self):
        """Set by the bridge it reads back '1'; ticked in DEVONthink's Info
        panel it reads back 'true'. Checking one form ignores the other."""
        for raw in ("1", "true", "True", " TRUE "):
            self.assertTrue(ef.filing_suppressed(person("W", filingsuppressed=raw)), raw)
        for raw in ("0", "false", "", None):
            self.assertFalse(ef.filing_suppressed(person("W", filingsuppressed=raw)), raw)

    def test_absent_flag_is_not_suppressed(self):
        self.assertFalse(ef.filing_suppressed(person("Wren Talbot")))

    def test_briefingsuppressed_does_not_suppress_filing(self):
        """The two flags are independent. Reading either for the other's job is
        the bug this pair of flags exists to prevent."""
        self.assertFalse(
            ef.filing_suppressed(person("Wren Talbot", briefingsuppressed="1")))


class SuppressedPersonPlans(unittest.TestCase):
    def plans(self, extracted, people=(), selves=frozenset()):
        people = list(people)
        return ef.build_person_plans(extracted, ef.roster_index(people), selves,
                                     people, "2026-03-16")

    def test_facts_updates_and_interaction_are_all_dropped(self):
        got = self.plans(
            [{"name": "Wren Talbot", "interacted": True,
              "facts": [{"fact": "moved to Leeds"}],
              "updates": {"location": "Leeds"}}],
            [person("Wren Talbot", filingsuppressed="1")])
        self.assertEqual(got, [])

    def test_an_alias_hit_is_suppressed_too(self):
        got = self.plans([{"name": "Wren", "facts": [{"fact": "x"}]}],
                         [person("Wren Talbot", aliases="Wren",
                                 filingsuppressed="1")])
        self.assertEqual(got, [])

    def test_the_llm_claiming_a_match_does_not_defeat_it(self):
        got = self.plans(
            [{"name": "she", "match": "Wren Talbot", "facts": [{"fact": "x"}]}],
            [person("Wren Talbot", filingsuppressed="1")])
        self.assertEqual(got, [])

    def test_others_in_the_same_source_still_file(self):
        got = self.plans([{"name": "Wren Talbot", "facts": [{"fact": "x"}]},
                          {"name": "Maya Chen", "facts": [{"fact": "y"}]}],
                         [person("Wren Talbot", filingsuppressed="1"),
                          person("Maya Chen")])
        self.assertEqual([p["name"] for p in got], ["Maya Chen"])

    def test_an_ambiguous_name_is_still_proposed(self):
        """Two people share the alias and only one is suppressed, so the mention
        is not known to be hers. Dropping it would silently discard a fact about
        the other — the ambiguity is exactly what review is for."""
        got = self.plans([{"name": "Wren", "facts": [{"fact": "x"}]}],
                         [person("Wren Talbot", aliases="Wren",
                                 filingsuppressed="1"),
                          person("Wren Vega", aliases="Wren")])
        self.assertEqual(got[0]["kind"], "ambiguous")

    def test_she_stays_in_the_roster_the_llm_is_shown(self):
        """The load-bearing half. Suppression must not remove her from the
        prompt: an unresolvable mention comes back as a `new` plan proposing a
        second record for someone who already has one — louder than the noise
        the flag was set to silence."""
        people = [person("Wren Talbot", aliases="Wren", filingsuppressed="1")]
        self.assertIn("Wren Talbot", ef.roster_text(people))
        self.assertIn("wren", ef.roster_index(people))


class BuildEventPlans(unittest.TestCase):
    def plans(self, events, people=(), selves=frozenset()):
        return ef.build_event_plans(events, ef.roster_index(list(people)), selves,
                                    "2026-03-16")

    def test_a_suppressed_attendee_is_dropped_from_who(self):
        got = self.plans([{"name": "Housewarming",
                           "attendees": ["Wren Talbot", "Maya Chen"]}],
                         [person("Wren Talbot", filingsuppressed="1"),
                          person("Maya Chen")])
        self.assertEqual(got[0]["attendees"], ["Maya Chen"])

    def test_free_text_is_not_scrubbed(self):
        """Documents the flag's honest limit: it is a noise control, not a
        privacy one. BriefingSuppressed is what redacts a name from free text."""
        got = self.plans([{"name": "Wren's birthday", "attendees": [],
                           "summary": "Wren turned 30"}],
                         [person("Wren Talbot", aliases="Wren",
                                 filingsuppressed="1")])
        self.assertEqual(got[0]["name"], "Wren's birthday")
        self.assertEqual(got[0]["summary"], "Wren turned 30")

    def test_an_unsuppressed_roster_hit_survives(self):
        got = self.plans([{"name": "Standup", "attendees": ["Maya Chen"]}],
                         [person("Maya Chen")])
        self.assertEqual(got[0]["attendees"], ["Maya Chen"])

    def test_an_ambiguous_attendee_survives(self):
        got = self.plans([{"name": "Standup", "attendees": ["Wren"]}],
                         [person("Wren Talbot", aliases="Wren",
                                 filingsuppressed="1"),
                          person("Wren Vega", aliases="Wren")])
        self.assertEqual(got[0]["attendees"], ["Wren"])

    def test_self_and_duplicates_are_still_dropped(self):
        got = self.plans([{"name": "Standup",
                           "attendees": ["Alec", "Maya Chen", "Maya Chen"]}],
                         [person("Maya Chen")], selves={"alec"})
        self.assertEqual(got[0]["attendees"], ["Maya Chen"])

    def test_interior_newlines_in_a_summary_are_collapsed(self):
        got = self.plans([{"name": "Trip", "attendees": [],
                           "summary": "great\ntrip this\r\nyear"}])
        self.assertEqual(got[0]["summary"], "great trip this year")


class ThingsNoteStub(unittest.TestCase):
    def test_source_less_proposal_does_not_render_a_broken_link(self):
        got = ef.things_note_stub(None, "PROPOSAL-1")
        self.assertNotIn("None", got)
        self.assertIn("x-devonthink-item://PROPOSAL-1", got)

    def test_rebuilt_mapping_recognizes_stub_as_edit_disabled(self):
        got = ef.things_note_stub(None, "PROPOSAL-1")
        self.assertFalse(ef.things_note_is_editable(got))

    def test_rebuilt_mapping_recognizes_editable_note(self):
        plans = [{"kind": "new", "name": "Wren", "updates": {}, "facts": []}]
        got = ef.things_note_body("SOURCE-1", "PROPOSAL-1", plans)
        self.assertTrue(ef.things_note_is_editable(got))


class FactLine(unittest.TestCase):
    def test_carries_a_deterministic_provenance_marker(self):
        a = ef.fact_line("2026-03-16", "moved to Denver", "SRC-1")
        b = ef.fact_line("2026-03-16", "moved to Denver", "SRC-1")
        self.assertEqual(a, b)
        self.assertRegex(a, r" <!-- fact:[0-9a-f]{8} -->$")
        self.assertIn("([source](x-devonthink-item://SRC-1))", a)

    def test_id_changes_with_text_date_or_source(self):
        base = ef.fact_id("2026-03-16", "moved to Denver.", "SRC-1")
        self.assertNotEqual(
            base, ef.fact_id("2026-03-16", "moved to Boulder.", "SRC-1"))
        self.assertNotEqual(
            base, ef.fact_id("2026-03-17", "moved to Denver.", "SRC-1"))
        self.assertNotEqual(
            base, ef.fact_id("2026-03-16", "moved to Denver.", "SRC-2"))


class OpsForPlan(unittest.TestCase):
    def test_existing_with_field_change_carries_temporal_guards(self):
        plan = {"kind": "existing", "name": "Bob", "uuid": "U1",
                "md": {"mdemployer": "Acme"}, "facts": [],
                "updates": {"employer": "Globex"}, "interacted": True}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        kinds = [o["op"] for o in ops]
        self.assertEqual(kinds, ["set_field", "bump_lastcontact"])
        self.assertEqual(ops[0]["effective_date"], "2026-03-16")
        self.assertEqual(ops[0]["expected_previous"], "Acme")
        self.assertIn("Employer: Acme → Globex", ops[0]["transition_line"])

    def test_first_value_has_no_transition_line(self):
        plan = {"kind": "existing", "name": "Bob", "uuid": "U1",
                "md": {}, "facts": [], "updates": {"employer": "Globex"}}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        self.assertEqual([o["op"] for o in ops], ["set_field"])
        self.assertNotIn("transition_line", ops[0])
        self.assertEqual(ops[0]["expected_previous"], "")

    def test_unchanged_field_emits_no_set_field(self):
        plan = {"kind": "existing", "name": "Bob", "uuid": "U1",
                "md": {"mdemployer": "Acme"}, "facts": [],
                "updates": {"employer": "acme"}, "interacted": True}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        self.assertEqual([o["op"] for o in ops], ["bump_lastcontact"])

    def test_no_interaction_files_facts_without_contact_bump(self):
        plan = {"kind": "existing", "name": "Bob", "uuid": "U1", "md": {},
                "facts": [("2026-03-16", "x")], "updates": {}}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        self.assertEqual([o["op"] for o in ops], ["append_log"])

    def test_new_person_becomes_ensure_person(self):
        plan = {"kind": "new", "name": "Wren", "facts": [("2026-03-16", "x")],
                "updates": {"city": "Durango"}}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        self.assertEqual(ops[0]["op"], "ensure_person")
        self.assertEqual(ops[0]["fields"], {"city": "Durango"})
        self.assertIn("x-devonthink-item://SRC-1", ops[0]["log_lines"][0])

    def test_new_person_from_interaction_gets_lastcontact(self):
        plan = {"kind": "new", "name": "Wren", "facts": [("2026-03-16", "x")],
                "updates": {}, "interacted": True}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        self.assertEqual(ops[0]["fields"], {"lastcontact": "2026-03-16"})

    def test_event_with_summary_gets_a_marked_log_line(self):
        plan = {"kind": "event", "name": "Portland Trip", "date": "2026-03-16",
                "location": "", "attendees": [], "summary": "great trip"}
        op = ef.ops_for_plan(plan, SOURCE, "2026-03-16")[0]
        self.assertRegex(op["log_line"], r" <!-- fact:[0-9a-f]{8} -->$")
        self.assertIn("great trip", op["log_line"])

    def test_event_without_summary_has_no_log_line(self):
        plan = {"kind": "event", "name": "Portland Trip", "date": "2026-03-16",
                "location": "", "attendees": [], "summary": ""}
        op = ef.ops_for_plan(plan, SOURCE, "2026-03-16")[0]
        self.assertNotIn("log_line", op)


class ProposalRoundTrip(unittest.TestCase):
    def build(self):
        plan = {"kind": "new", "name": "Wren", "single_token": True, "near": [],
                "facts": [("2026-03-16", "attends Cedar Ridge")], "updates": {}}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        return ops, ef.proposal_body(SOURCE, "2026-03-16", [plan], ops)

    def test_ops_survive_the_fenced_block_apply_approved_parses(self):
        """apply_approved reads back the last ```json fence. If proposal_body ever
        stops emitting exactly that, approvals silently become no-ops."""
        ops, body = self.build()
        self.assertEqual(ef.proposal_ops(body), ops)

    def test_dt_editor_line_endings_still_parse(self):
        """DEVONthink's editor saves markdown with classic-Mac CR endings, so
        any proposal the user hand-edits before approving comes back that way."""
        ops, body = self.build()
        self.assertEqual(ef.proposal_ops(body.replace("\n", "\r")), ops)
        self.assertEqual(ef.proposal_ops(body.replace("\n", "\r\n")), ops)

    def test_no_fence_is_none_and_bad_json_raises(self):
        self.assertIsNone(ef.proposal_ops("# File: x\n\nno ops here\n"))
        with self.assertRaises(ValueError):
            ef.proposal_ops("```json\n{not json\n```")
        with self.assertRaises(ValueError):
            ef.proposal_ops('```json\n{"op": "not-a-list"}\n```')


class PickTransport(unittest.TestCase):
    def setUp(self):
        self.saved = (ef.omlx_available, ef.ollama_available)
        ef.omlx_available = lambda c: True
        ef.ollama_available = lambda c: True

    def tearDown(self):
        ef.omlx_available, ef.ollama_available = self.saved

    def test_off_disables_every_kind(self):
        for kind in ("meeting", "handwritten", "daily", "journal", "fact"):
            self.assertIsNone(ef.pick_transport({"TRANSPORT": "off"}, kind), kind)

    def test_local_never_returns_dtchat(self):
        self.assertEqual(ef.pick_transport({"TRANSPORT": "local"}, "meeting"), "omlx")
        ef.omlx_available = lambda c: False
        self.assertEqual(ef.pick_transport({"TRANSPORT": "local"}, "meeting"), "ollama")
        ef.ollama_available = lambda c: False
        self.assertIsNone(ef.pick_transport({"TRANSPORT": "local"}, "meeting"))

    def test_daily_notes_never_reach_dtchat(self):
        self.assertIsNone(ef.pick_transport({"TRANSPORT": "dtchat"}, "daily"))
        ef.omlx_available = lambda c: False
        ef.ollama_available = lambda c: False
        self.assertIsNone(ef.pick_transport({"TRANSPORT": "auto"}, "daily"))

    def test_personal_kinds_never_reach_dtchat(self):
        for kind in ("journal", "handwritten", "fact"):
            self.assertIsNone(
                ef.pick_transport({"TRANSPORT": "dtchat"}, kind), kind)
        ef.omlx_available = lambda c: False
        ef.ollama_available = lambda c: False
        for kind in ("journal", "handwritten", "fact"):
            self.assertIsNone(
                ef.pick_transport({"TRANSPORT": "auto"}, kind), kind)

    def test_auto_falls_back_to_dtchat_for_meetings(self):
        ef.omlx_available = lambda c: False
        ef.ollama_available = lambda c: False
        self.assertEqual(ef.pick_transport({"TRANSPORT": "auto"}, "meeting"), "dtchat")


class LoadState(unittest.TestCase):
    def run_with_state(self, contents):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(contents)
        saved = ef.STATE_FILE
        ef.STATE_FILE = path
        try:
            return ef.load_state()
        finally:
            ef.STATE_FILE = saved
            os.unlink(path)

    def test_fails_closed_on_corrupt_json(self):
        with self.assertRaises(RuntimeError):
            self.run_with_state("{not json")

    def test_fails_closed_on_unknown_schema(self):
        with self.assertRaises(RuntimeError):
            self.run_with_state('{"version": 99, "processed_ids": []}')

    def test_v1_state_migrates_in_place(self):
        state = self.run_with_state(
            '{"version": 1, "processed_ids": ["a"], "attempts": {"b": 2}}')
        self.assertEqual(state["version"], 2)
        self.assertIn("a", state["processed"])
        self.assertIsNone(state["processed"]["a"]["hash"])
        self.assertTrue(state["processed"]["a"]["modified"])
        self.assertEqual(state["attempts"]["b"], {"count": 2})
        self.assertEqual(state["parked"], {})

    def test_valid_v2_state_gets_default_maps(self):
        state = self.run_with_state('{"version": 2, "processed": {}}')
        self.assertEqual(state["attempts"], {})
        self.assertEqual(state["parked"], {})


class SourceNeedsFiling(unittest.TestCase):
    def state(self, processed=None, parked=None):
        return {"version": 2, "processed": processed or {},
                "attempts": {}, "parked": parked or {}}

    def src(self, ready=True, modified="2026-01-02T00:00:00"):
        return {"uuid": "U", "ready": ready, "modified": modified}

    def test_unready_source_is_never_a_candidate(self):
        self.assertFalse(ef.source_needs_filing(self.src(ready=False),
                                                self.state()))

    def test_new_source_is_a_candidate(self):
        self.assertTrue(ef.source_needs_filing(self.src(), self.state()))

    def test_unchanged_source_is_skipped(self):
        st = self.state(processed={
            "U": {"modified": "2026-01-02T00:00:00", "hash": "h"}})
        self.assertFalse(ef.source_needs_filing(self.src(), st))

    def test_changed_source_re_enters_filing(self):
        st = self.state(processed={
            "U": {"modified": "2026-01-01T00:00:00", "hash": "h"}})
        self.assertTrue(ef.source_needs_filing(self.src(), st))

    def test_parked_source_returns_only_when_changed(self):
        st = self.state(parked={"U": {"modified": "2026-01-02T00:00:00"}})
        self.assertFalse(ef.source_needs_filing(self.src(), st))
        self.assertTrue(ef.source_needs_filing(
            self.src(modified="2026-03-01T00:00:00"), st))

    def test_missing_ready_defaults_to_candidate(self):
        self.assertTrue(ef.source_needs_filing(
            {"uuid": "U", "modified": "2026-01-02T00:00:00"}, self.state()))


class CapWords(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(ef.cap_words("a b c"), "a b c")

    def test_long_text_keeps_head_and_tail(self):
        text = " ".join(str(i) for i in range(9000))
        out = ef.cap_words(text, head=10, tail=5)
        self.assertTrue(out.startswith("0 1 2"))
        self.assertTrue(out.endswith("8999"))
        self.assertIn("[...truncated...]", out)


class StripGeneratedSections(unittest.TestCase):
    DAILY = "\n".join([
        "# Thursday, March 16, 2026",
        "",
        "## Today's Notes",
        "",
        "- Coffee with Alison Vance, she starts at Delphi Labs Monday",
        "",
        "## Briefing",
        "",
        "9:00 AM Planning Roundtable",
        "- Miles Archer (miles@x.com) — no entity record yet",
        "",
        "## Birthdays",
        "",
        "- Alison Vance turns 40",
        "",
        "## Journal",
        "",
        "2 page(s) pending OCR",
    ])

    def test_strips_every_generated_section(self):
        out = ef.strip_generated_sections(self.DAILY)
        self.assertIn("Coffee with Alison Vance", out)
        for leaked in ("## Briefing", "Miles Archer", "no entity record yet",
                       "## Birthdays", "turns 40", "## Journal", "pending OCR"):
            self.assertNotIn(leaked, out)

    def test_skip_ends_at_next_human_section(self):
        text = "## Briefing\n\nstuff\n\n## Today's Notes\n\n- real note"
        out = ef.strip_generated_sections(text)
        self.assertNotIn("stuff", out)
        self.assertIn("- real note", out)

    def test_cr_delimited_body_still_strips(self):
        """A note written back by AppleScript is CR-delimited; splitting it on
        \\n would see one headerless line and feed the briefing to the LLM."""
        text = "## Briefing\r\rstuff\r\r## Today's Notes\r\r- real note"
        out = ef.strip_generated_sections(text)
        self.assertNotIn("stuff", out)
        self.assertIn("- real note", out)

    def test_subheaders_do_not_end_the_skip(self):
        text = "## On This Day\n\n### 2025\n\n- old entry\n\n## Today's Notes\n\n- now"
        out = ef.strip_generated_sections(text)
        self.assertNotIn("old entry", out)
        self.assertIn("- now", out)

    def test_generated_section_at_eof_strips_to_end(self):
        text = "## Today's Notes\n\n- kept\n\n## Reconnect\n\n- Maya Chen: 90 days"
        out = ef.strip_generated_sections(text)
        self.assertIn("- kept", out)
        self.assertNotIn("Maya Chen", out)

    def test_text_without_generated_sections_is_unchanged(self):
        text = "# Note\n\n## Today's Notes\n\n- a\n- b"
        self.assertEqual(ef.strip_generated_sections(text), text)

    def test_user_h1_block_after_generated_section_is_kept(self):
        text = "\n".join([
            "## Briefing",
            "",
            "9:00 AM Planning Roundtable",
            "",
            "# Alison Vance",
            "",
            "Ran into her at the coffee shop.",
            "",
            "## Today's Notes",
            "",
            "- real note",
        ])
        out = ef.strip_generated_sections(text)
        self.assertNotIn("Planning Roundtable", out)
        self.assertIn("# Alison Vance", out)
        self.assertIn("Ran into her at the coffee shop.", out)
        self.assertIn("- real note", out)

    def test_pure_journal_link_bullet_is_stripped(self):
        text = ("## Today's Notes\n\n"
                "- [\U0001F4D4 Journal](x-devonthink-item://ABC123)\n"
                "- real note")
        out = ef.strip_generated_sections(text)
        self.assertNotIn("Journal](x-devonthink-item", out)
        self.assertIn("- real note", out)

    def test_pure_timed_link_bullet_is_stripped(self):
        text = ("## Today's Notes\n\n"
                "- 6:10am: [\U0001F517 Some Bookmark]"
                "(x-devonthink-item://ABC123)\n"
                "- real note")
        out = ef.strip_generated_sections(text)
        self.assertNotIn("Some Bookmark", out)
        self.assertIn("- real note", out)

    def test_bullet_with_free_prose_beyond_a_link_survives(self):
        text = ("## Today's Notes\n\n"
                "- Talked to Alison Vance about the trip "
                "([source](x-devonthink-item://ABC123))\n")
        out = ef.strip_generated_sections(text)
        self.assertIn("Talked to Alison Vance about the trip", out)

    def test_jot_bullet_with_trailing_marker_survives(self):
        text = ("## Today's Notes\n\n"
                "- Had a good idea about the launch <!-- jot:ABC123 -->\n")
        out = ef.strip_generated_sections(text)
        self.assertIn("Had a good idea about the launch", out)


FACT_SOURCE = {"uuid": "FACT-1", "name": "Fact 2026-03-16 at 3.00.00PM",
               "kind": "fact", "modified": "2026-03-16T15:00:00"}


class MinWordsFor(unittest.TestCase):
    def test_fact_bypasses_the_scaffolding_gate(self):
        self.assertEqual(ef.min_words_for("fact"), 1)

    def test_other_kinds_keep_the_twenty_word_gate(self):
        for kind in ("daily", "journal", "handwritten", "meeting"):
            self.assertEqual(ef.min_words_for(kind), 20, kind)


class EffectiveFilingMode(unittest.TestCase):
    def test_fact_forces_auto_regardless_of_config(self):
        self.assertEqual(ef.effective_filing_mode("fact", "suggest"), "auto")
        self.assertEqual(ef.effective_filing_mode("fact", "auto"), "auto")

    def test_other_kinds_honor_the_configured_mode(self):
        self.assertEqual(ef.effective_filing_mode("daily", "suggest"), "suggest")
        self.assertEqual(ef.effective_filing_mode("meeting", "auto"), "auto")


class NameInText(unittest.TestCase):
    def test_whole_token_run_matches_case_and_accent_insensitively(self):
        self.assertTrue(ef.name_in_text("Renée Vann", "spoke with renee vann today"))

    def test_partial_token_does_not_match(self):
        self.assertFalse(ef.name_in_text("Dana Parker", "met Dana Parkerson"))
        self.assertFalse(ef.name_in_text("Dana Parker", "Dana moved to Denver"))

    def test_empty_name_never_matches(self):
        self.assertFalse(ef.name_in_text("", "anything"))


class FactMatchIsStrong(unittest.TestCase):
    def plan(self, name, aliases="", weak=False):
        return {"kind": "existing", "name": name, "aliases": aliases,
                "weak_match": weak}

    def test_full_name_in_capture_is_strong(self):
        self.assertTrue(ef.fact_match_is_strong(
            self.plan("Dana Parker"), "Dana Parker's kid started at Reed"))

    def test_bare_first_name_only_is_not_strong(self):
        self.assertFalse(ef.fact_match_is_strong(
            self.plan("Dana Parker", aliases="Dana"), "Dana moved to Denver"))

    def test_multi_word_alias_in_capture_is_strong(self):
        self.assertTrue(ef.fact_match_is_strong(
            self.plan("Robert Vega", aliases="Bobby Vega, Bob"),
            "ran into Bobby Vega at the market"))

    def test_weak_match_is_never_strong(self):
        self.assertFalse(ef.fact_match_is_strong(
            self.plan("Dana Parker", weak=True), "Dana Parker got promoted"))

    def test_mononym_record_without_alias_is_not_strong(self):
        self.assertFalse(ef.fact_match_is_strong(
            self.plan("Cher"), "Cher released an album"))


class StripLeadingH1(unittest.TestCase):
    def test_drops_leading_h1_and_following_blanks(self):
        self.assertEqual(
            ef.strip_leading_h1("# Fact 2026-03-16\n\nDana Parker moved."),
            "Dana Parker moved.")

    def test_leading_blank_lines_before_h1(self):
        self.assertEqual(
            ef.strip_leading_h1("\n\n# T\n\nbody"), "body")

    def test_no_h1_is_unchanged(self):
        self.assertEqual(ef.strip_leading_h1("Dana Parker moved."),
                         "Dana Parker moved.")

    def test_cr_delimited_capture_is_not_collapsed_to_empty(self):
        """DEVONthink can store the capture with classic-Mac CR endings; a
        split("\\n") would return one line and drop the whole fact."""
        self.assertEqual(ef.strip_leading_h1("# T\r\rDana Parker moved."),
                         "Dana Parker moved.")
        self.assertEqual(ef.strip_leading_h1("# T\r\nbody"), "body")


class OpsForFactPlan(unittest.TestCase):
    def test_interaction_does_not_bump_lastcontact_for_a_fact(self):
        plan = {"kind": "existing", "name": "Bob Vega", "uuid": "U1", "md": {},
                "aliases": "", "facts": [("2026-03-16", "x")], "updates": {},
                "interacted": True}
        ops = ef.ops_for_plan(plan, FACT_SOURCE, "2026-03-16")
        self.assertEqual([o["op"] for o in ops], ["append_log"])

    def test_new_person_from_a_fact_gets_no_lastcontact(self):
        plan = {"kind": "new", "name": "Wren Talbot",
                "facts": [("2026-03-16", "x")], "updates": {}, "interacted": True}
        ops = ef.ops_for_plan(plan, FACT_SOURCE, "2026-03-16")
        self.assertEqual(ops[0]["fields"], {})


class FallbackReviewBody(unittest.TestCase):
    def test_carries_the_raw_text_and_an_empty_ops_fence(self):
        body = ef.fallback_review_body(FACT_SOURCE, "2026-03-16",
                                       "  gibberish capture  ")
        self.assertIn("gibberish capture", body)
        self.assertEqual(ef.proposal_ops(body), [])


class ScanForceBypassesHashShortCircuit(unittest.TestCase):
    SOURCE = {"uuid": "SRC-1", "name": "Standup", "kind": "meeting",
              "modified": "2026-03-16T10:00:00", "ready": True}
    TEXT = ("Dana Parker mentioned during today's call that her son started "
            "at Reed College this fall and things are going well so far "
            "according to her update just now.")

    def setUp(self):
        self.saved = (ef.run_bridge, ef.save_state)
        ef.save_state = lambda s: None
        self.calls = []

        def fake_bridge(ops, timeout=300):
            self.calls.append(ops)
            out = []
            for op in ops:
                if op["op"] == "dump_people":
                    out.append([])
                elif op["op"] == "list_sources":
                    out.append([self.SOURCE])
                elif op["op"] == "get_text":
                    out.append({"text": self.TEXT})
                elif op["op"] == "chat":
                    out.append({"text": '{"people": [], "events": []}'})
                elif op["op"] == "get_source":
                    out.append(dict(self.SOURCE))
                else:
                    out.append({})
            return out

        ef.run_bridge = fake_bridge

    def tearDown(self):
        ef.run_bridge, ef.save_state = self.saved

    def config(self):
        return {"TRANSPORT": "dtchat", "MIN_ROSTER": "0",
                "SKIP_SOURCE_TITLES": "", "MAX_PER_RUN": "3",
                "FILING_MODE": "suggest", "IDLE_MINUTES": "10",
                "SELF_NAME": ""}

    def matching_hash_state(self):
        h = hashlib.sha256(self.TEXT.encode()).hexdigest()
        return {"processed": {"SRC-1": {"modified": "2026-03-15T00:00:00",
                                        "hash": h}},
                "attempts": {}, "parked": {}}

    def chat_call_count(self):
        return sum(1 for batch in self.calls for op in batch
                   if op["op"] == "chat")

    def test_force_target_extracts_despite_a_matching_hash(self):
        ef.scan(self.config(), self.matching_hash_state(), False, "SRC-1", True)
        self.assertEqual(self.chat_call_count(), 1)

    def test_unforced_source_with_a_matching_hash_still_short_circuits(self):
        ef.scan(self.config(), self.matching_hash_state(), False, None, True)
        self.assertEqual(self.chat_call_count(), 0)


class ScanPreservesParkedDiagnosisOnDeferral(unittest.TestCase):
    SOURCE = {"uuid": "SRC-1", "name": "Standup", "kind": "meeting",
              "modified": "2026-03-17T00:00:00", "ready": True}

    def setUp(self):
        self.saved = (ef.run_bridge, ef.save_state)
        ef.save_state = lambda s: None

        def fake_bridge(ops):
            out = []
            for op in ops:
                if op["op"] == "dump_people":
                    out.append([])
                elif op["op"] == "list_sources":
                    out.append([self.SOURCE])
                else:
                    out.append({})
            return out

        ef.run_bridge = fake_bridge

    def tearDown(self):
        ef.run_bridge, ef.save_state = self.saved

    def config(self):
        return {"TRANSPORT": "off", "MIN_ROSTER": "0",
                "SKIP_SOURCE_TITLES": "", "MAX_PER_RUN": "3",
                "FILING_MODE": "suggest", "IDLE_MINUTES": "10",
                "SELF_NAME": ""}

    def test_a_run_that_defers_leaves_the_parked_entry_intact(self):
        state = {"processed": {}, "attempts": {},
                 "parked": {"SRC-1": {"name": "Standup", "attempts": 5,
                                      "last_error": "boom",
                                      "modified": "2026-03-16T00:00:00",
                                      "parked_at": "2026-03-16"}}}
        ef.scan(self.config(), state, False, None, True)
        self.assertIn("SRC-1", state["parked"])
        self.assertEqual(state["parked"]["SRC-1"]["last_error"], "boom")


class FileSourceReReadsBeforeAdoptingTheFreshStamp(unittest.TestCase):
    ORIGINAL_TEXT = ("Dana Parker mentioned during today's call that her son "
                     "started at Reed College this fall and is doing well.")

    def setUp(self):
        self.saved = (ef.run_bridge, ef.save_state)
        ef.save_state = lambda s: None

    def tearDown(self):
        ef.run_bridge, ef.save_state = self.saved

    def source(self):
        return {"uuid": "SRC-1", "name": "Standup", "kind": "meeting",
                "modified": "2026-03-16T10:00:00"}

    def run_file_source(self, fresh_modified, fresh_text):
        def fake_bridge(ops):
            out = []
            for op in ops:
                if op["op"] == "get_source":
                    out.append({"uuid": "SRC-1", "name": "Standup",
                               "kind": "meeting", "modified": fresh_modified})
                elif op["op"] == "get_text":
                    out.append({"text": fresh_text})
                else:
                    out.append({})
            return out

        ef.run_bridge = fake_bridge
        state = {"processed": {}, "attempts": {}, "parked": {}}
        ef.file_source({}, state, self.source(), "2026-03-16", [], "auto",
                       False, self.ORIGINAL_TEXT)
        return state

    def test_an_edit_landing_mid_extraction_is_not_swallowed(self):
        state = self.run_file_source(
            "2026-03-16T10:05:00",
            self.ORIGINAL_TEXT + " Extra sentence added after extraction started.")
        self.assertEqual(state["processed"]["SRC-1"]["modified"],
                         "2026-03-16T10:00:00")

    def test_an_unedited_source_adopts_the_fresh_stamp(self):
        state = self.run_file_source("2026-03-16T10:05:00", self.ORIGINAL_TEXT)
        self.assertEqual(state["processed"]["SRC-1"]["modified"],
                         "2026-03-16T10:05:00")


class FileSourceProposalCreationMarksFiled(unittest.TestCase):
    """C08: EntityFiled must be set the moment a proposal or review stub is
    created, not only when it is later approved — otherwise --rebuild-state
    can never learn a pending or rejected source was already seen."""

    def setUp(self):
        self.calls = []
        self.saved = (ef.run_bridge, ef.save_state)
        ef.save_state = lambda s: None
        self.list_group_result = []

        def fake_bridge(ops):
            self.calls.append(ops)
            out = []
            for op in ops:
                if op["op"] == "get_source":
                    out.append(dict(SOURCE))
                elif op["op"] == "get_text":
                    out.append({"text": "unchanged"})
                elif op["op"] == "create_record":
                    out.append({"uuid": "NEW"})
                elif op["op"] == "list_group":
                    out.append(list(self.list_group_result))
                else:
                    out.append({})
            return out

        ef.run_bridge = fake_bridge

    def tearDown(self):
        ef.run_bridge, ef.save_state = self.saved

    def state(self):
        return {"processed": {}, "attempts": {}, "parked": {}}

    def batches_with(self, op_name):
        return [b for b in self.calls if any(o["op"] == op_name for o in b)]

    def test_a_new_proposal_batch_marks_the_source_filed(self):
        plan = {"kind": "new", "name": "Wren", "single_token": True, "near": [],
                "facts": [("2026-03-16", "attends Cedar Ridge")], "updates": {}}
        ef.file_source({}, self.state(), SOURCE, "2026-03-16", [plan], "suggest",
                       False, "some source text")
        batch = self.batches_with("create_record")[0]
        self.assertEqual([o["op"] for o in batch], ["create_record", "mark_filed"])
        self.assertEqual(batch[1]["uuid"], SOURCE["uuid"])

    def test_a_proposal_matching_an_existing_review_name_is_not_duplicated(self):
        self.list_group_result = [{"uuid": "EXISTING",
                                   "name": f"File: {SOURCE['name']}"}]
        plan = {"kind": "new", "name": "Wren", "single_token": True, "near": [],
                "facts": [("2026-03-16", "attends Cedar Ridge")], "updates": {}}
        ef.file_source({}, self.state(), SOURCE, "2026-03-16", [plan], "suggest",
                       False, "some source text")
        self.assertEqual(self.batches_with("create_record"), [])
        self.assertTrue(self.batches_with("mark_filed"))


class FileSourceFact(unittest.TestCase):
    """file_source touches the bridge, so stub it and capture the op batches.
    Locks the three fact outcomes: named clearly -> apply, bare name ->
    proposal, nothing extracted -> review stub (never a silent mark-filed).

    C71: a fact source that reaches a fully-filed terminal state (direct
    apply, or a proposal/dup-guard batch that marks it filed) is also moved
    into _Facts/Filed in that same batch, by UUID, so discovery's wholesale
    enumeration of _Facts stops seeing it. A review stub leaves the source in
    place — it is still pending human review."""

    def setUp(self):
        self.calls = []
        self.saved = (ef.run_bridge, ef.save_state)
        ef.save_state = lambda s: None
        self.list_group_result = []

        def fake_bridge(ops):
            self.calls.append(ops)
            out = []
            for op in ops:
                if op["op"] == "get_source":
                    out.append(dict(FACT_SOURCE))
                elif op["op"] == "create_record":
                    out.append({"uuid": "NEW"})
                elif op["op"] == "list_group":
                    out.append(list(self.list_group_result))
                else:
                    out.append({})
            return out

        ef.run_bridge = fake_bridge

    def tearDown(self):
        ef.run_bridge, ef.save_state = self.saved

    def state(self):
        return {"processed": {}, "attempts": {}, "parked": {}}

    def created(self):
        return [op for batch in self.calls for op in batch
                if op["op"] == "create_record"]

    def applied(self):
        return [op["op"] for batch in self.calls for op in batch]

    def batch_with(self, op_name):
        return next(b for b in self.calls if any(o["op"] == op_name for o in b))

    def test_named_clearly_auto_applies_without_a_proposal(self):
        plan = {"kind": "existing", "name": "Dana Parker", "uuid": "U1",
                "md": {}, "aliases": "Dana", "weak_match": False,
                "interacted": False, "facts": [("2026-03-16", "kid at Reed")],
                "updates": {}}
        ef.file_source({}, self.state(), FACT_SOURCE, "2026-03-16", [plan], "auto",
                       False, "Dana Parker's kid started at Reed")
        self.assertIn("append_log", self.applied())
        self.assertEqual(self.created(), [])
        batch = self.batch_with("mark_filed")
        self.assertEqual([o["op"] for o in batch],
                         ["mark_filed", "ensure_group", "move_to"])
        self.assertEqual(batch[2]["uuid"], FACT_SOURCE["uuid"])
        self.assertEqual(batch[2]["group"], ef.FACTS_FILED_PATH)

    def test_bare_first_name_becomes_a_proposal(self):
        plan = {"kind": "existing", "name": "Dana Parker", "uuid": "U1",
                "md": {}, "aliases": "Dana", "weak_match": False,
                "interacted": False, "facts": [("2026-03-16", "moved")],
                "updates": {}}
        ef.file_source({}, self.state(), FACT_SOURCE, "2026-03-16", [plan], "auto",
                       False, "Dana moved to Denver")
        names = [op["name"] for op in self.created()]
        self.assertTrue(any(n.startswith("File:") for n in names), names)
        self.assertNotIn("append_log", self.applied())
        batch = self.batch_with("create_record")
        self.assertEqual([o["op"] for o in batch],
                         ["create_record", "mark_filed", "ensure_group", "move_to"])

    def test_proposal_duplicate_guard_still_files_and_moves(self):
        self.list_group_result = [{"uuid": "EXISTING",
                                   "name": f"File: {FACT_SOURCE['name']}"}]
        plan = {"kind": "existing", "name": "Dana Parker", "uuid": "U1",
                "md": {}, "aliases": "Dana", "weak_match": False,
                "interacted": False, "facts": [("2026-03-16", "moved")],
                "updates": {}}
        ef.file_source({}, self.state(), FACT_SOURCE, "2026-03-16", [plan], "auto",
                       False, "Dana moved to Denver")
        self.assertEqual(self.created(), [])
        batch = self.batch_with("mark_filed")
        self.assertEqual([o["op"] for o in batch],
                         ["mark_filed", "ensure_group", "move_to"])

    def test_empty_extraction_surfaces_a_review_stub(self):
        ef.file_source({}, self.state(), FACT_SOURCE, "2026-03-16", [], "auto",
                       False, "not a fact")
        names = [op["name"] for op in self.created()]
        self.assertTrue(any(n.startswith("Review capture:") for n in names), names)
        batch = self.batch_with("create_record")
        self.assertEqual([o["op"] for o in batch], ["create_record", "mark_filed"])

    def test_review_stub_duplicate_guard_leaves_the_source_in_place(self):
        self.list_group_result = [{"uuid": "EXISTING",
                                   "name": f"Review capture: {FACT_SOURCE['name']}"}]
        ef.file_source({}, self.state(), FACT_SOURCE, "2026-03-16", [], "auto",
                       False, "not a fact")
        self.assertEqual(self.created(), [])
        batch = self.batch_with("mark_filed")
        self.assertEqual([o["op"] for o in batch], ["mark_filed"])


class RosterIndexEmailKey(unittest.TestCase):
    def test_exact_email_resolves_the_person(self):
        p = person("Cara Quill", email="cara@x.com")
        index = ef.roster_index([p])
        self.assertEqual(index.get("cara@x.com"), [p])

    def test_mailto_prefix_and_case_fold_onto_the_bare_address(self):
        p = person("Cara Quill", email="mailto:Cara@X.com")
        index = ef.roster_index([p])
        self.assertEqual(index.get("cara@x.com"), [p])
        self.assertNotIn("mailto:cara@x.com", index)

    def test_no_email_adds_no_email_key(self):
        p = person("Cara Quill")
        index = ef.roster_index([p])
        self.assertEqual(set(index), {"cara quill"})


if __name__ == "__main__":
    unittest.main()
