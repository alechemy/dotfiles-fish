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


class OpsForPlan(unittest.TestCase):
    def test_existing_with_field_change_logs_the_previous_value(self):
        plan = {"kind": "existing", "name": "Bob", "uuid": "U1",
                "md": {"mdemployer": "Acme"}, "facts": [],
                "updates": {"employer": "Globex"}}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        kinds = [o["op"] for o in ops]
        self.assertEqual(kinds, ["set_field", "append_log", "bump_lastcontact"])
        self.assertIn("Employer: Acme → Globex", ops[1]["lines"][0])

    def test_unchanged_field_emits_no_set_field(self):
        plan = {"kind": "existing", "name": "Bob", "uuid": "U1",
                "md": {"mdemployer": "Acme"}, "facts": [],
                "updates": {"employer": "acme"}}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        self.assertEqual([o["op"] for o in ops], ["bump_lastcontact"])

    def test_new_person_becomes_ensure_person(self):
        plan = {"kind": "new", "name": "Wren", "facts": [("2026-03-16", "x")],
                "updates": {"city": "Durango"}}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        self.assertEqual(ops[0]["op"], "ensure_person")
        self.assertEqual(ops[0]["fields"], {"city": "Durango"})
        self.assertIn("x-devonthink-item://SRC-1", ops[0]["log_lines"][0])


class ProposalRoundTrip(unittest.TestCase):
    def test_ops_survive_the_fenced_block_apply_approved_parses(self):
        """apply_approved reads back the last ```json fence. If proposal_body ever
        stops emitting exactly that, approvals silently become no-ops."""
        plan = {"kind": "new", "name": "Wren", "single_token": True, "near": [],
                "facts": [("2026-03-16", "attends Cedar Ridge")], "updates": {}}
        ops = ef.ops_for_plan(plan, SOURCE, "2026-03-16")
        body = ef.proposal_body(SOURCE, "2026-03-16", [plan], ops)
        blocks = re.findall(r"```json\s*\n(.*?)\n```", body, re.DOTALL)
        self.assertTrue(blocks)
        self.assertEqual(json.loads(blocks[-1]), ops)


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

    def test_valid_state_gets_default_attempts(self):
        state = self.run_with_state('{"version": 1, "processed_ids": ["a"]}')
        self.assertEqual(state["attempts"], {})


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
