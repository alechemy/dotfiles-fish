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


class Norm(unittest.TestCase):
    def test_strips_accents_case_and_runs_of_space(self):
        self.assertEqual(ef.norm("  Renée   VAN Dam "), "renee van dam")

    def test_none(self):
        self.assertEqual(ef.norm(None), "")


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

    def test_interacted_flag_carries_through(self):
        got = self.plans([{"name": "Maya Chen", "interacted": True,
                           "facts": [{"fact": "x"}]}], [person("Maya Chen")])
        self.assertTrue(got[0]["interacted"])
        got = self.plans([{"name": "Maya Chen", "facts": [{"fact": "x"}]}],
                         [person("Maya Chen")])
        self.assertFalse(got[0]["interacted"])


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
        for kind in ("meeting", "handwritten", "daily"):
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


if __name__ == "__main__":
    unittest.main()
