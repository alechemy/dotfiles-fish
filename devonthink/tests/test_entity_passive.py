import copy
import unittest

from helpers import load

passive = load("entity_passive.py", "entity_passive")
bio = passive.bio
ef = load("entity-filing.py", "filing_passive_tests")


class PassiveComparison(unittest.TestCase):
    def setUp(self):
        self.source = {"uuid": "AAA-111", "name": "Fictional meeting", "kind": "meeting", "eventdate": "2026-09-01", "added": "2026-09-01", "ready": True}
        self.text = "Wren moved to Denver."
        self.person = {"uuid": "BBB-222", "name": "Wren Vale", "aliases": "Wren", "md": {"mdentitytype": "Person"}, "body": "# Wren Vale\n\n"}
        self.plan = {"kind": "existing", "uuid": "BBB-222", "name": "Wren Vale", "aliases": "Wren", "md": self.person["md"], "facts": [("2026-09-01", self.text)], "updates": {}}
        self.ops = ef.ops_for_plan(self.plan, dict(self.source, text_revision=bio.digest(self.text)), "2026-09-01")
        self.saved = copy.deepcopy(self.ops[0]["assertions"][0])
        self.saved["id"] = "b" * 64
        self.saved["reference"]["id"] = "c" * 64
        self.saved["baseline"]["text"] = "Lives in Denver."
        self.person["body"] = bio.attach(self.person["body"], self.saved, "BBB-222")

    def envelope(self, suggest):
        return passive.prepare(self.ops, [passive.snapshot(self.source, self.text, self.text, "2026-09-01")], [self.person], suggest=suggest)

    def test_model_suggestion_never_attaches_without_explicit_choice(self):
        envelope = self.envelope(lambda *args: [self.saved["id"]])
        self.assertEqual(envelope["status"], "question")
        with self.assertRaises(ValueError):
            passive.executable(envelope)
        question = envelope["questions"][0]
        ready = passive.answer(envelope, {question["key"]: self.saved["id"]}, [self.person])
        assertion = passive.executable(ready)[0]["assertions"][0]
        self.assertEqual(assertion["id"], self.saved["id"])
        self.assertTrue(assertion["existing"])
        self.assertEqual(assertion["reference"]["evidence_kind"], "extracted_assertion")
        self.assertEqual(assertion["reference"]["evidence"], self.text)

    def test_failed_comparison_retains_full_extraction_and_retries(self):
        def unavailable(*args):
            raise bio.ComparisonUnavailable()
        waiting = self.envelope(unavailable)
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(waiting["questions"], [])
        self.assertEqual(waiting["inputs"], self.ops)
        self.assertEqual(waiting["sources"][0]["raw_text"], self.text)
        recovered = passive.analyze(waiting, [self.person], lambda *args: [])
        self.assertEqual(recovered["status"], "ready")

    def test_explicit_separate_is_not_exact_coalescing(self):
        self.person["body"] = bio.attach("# Wren\n\n", self.ops[0]["assertions"][0], "BBB-222")
        self.ops[0]["assertions"][0]["baseline"]["temporal_context"] = "observed:2026-09-02"
        question = self.envelope(None)
        self.assertEqual(question["status"], "question")
        ready = passive.answer(question, {question["questions"][0]["key"]: "separate"}, [self.person])
        self.assertTrue(passive.executable(ready)[0]["assertions"][0]["separate"])

    def test_selected_protected_row_edits_and_wrong_person_stop(self):
        envelope = self.envelope(lambda *args: [self.saved["id"]])
        question = envelope["questions"][0]
        edited = dict(self.person, body=self.person["body"].replace("Lives in Denver. ([source]", "Lives elsewhere. ([source]"))
        with self.assertRaises(ValueError):
            passive.answer(envelope, {question["key"]: self.saved["id"]}, [edited])
        with self.assertRaises(ValueError):
            passive.answer(envelope, {question["key"]: self.saved["id"]}, [dict(self.person, uuid="CCC-333")])

    def test_richer_negated_changed_and_repeated_facts_remain_complete(self):
        for text in ("Wren moved to Denver with her daughter.", "Wren does not live in Denver.", "Wren used to live in Denver.", "Wren visited Denver again."):
            self.text = text
            plan = dict(self.plan, facts=[("2026-09-01", text)])
            self.ops = ef.ops_for_plan(plan, dict(self.source, text_revision=bio.digest(text)), "2026-09-01")
            envelope = self.envelope(lambda *args: [self.saved["id"]])
            q = envelope["questions"][0]
            ready = passive.answer(envelope, {q["key"]: "source"}, [self.person])
            self.assertEqual(passive.executable(ready)[0]["assertions"][0]["baseline"]["text"], text)

    def test_unknown_model_candidate_and_malformed_ownership_retain_evidence(self):
        waiting = self.envelope(lambda *args: ["unknown"])
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(waiting["questions"], [])
        self.person["body"] += "\n<!-- bio:v2:broken -->"
        blocked = self.envelope(lambda *args: [])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["reason"], "ownership_unreadable")
        self.assertEqual(blocked["inputs"], self.ops)

    def test_oversized_review_refuses_to_truncate_or_apply(self):
        large = "fictional " * 300000
        sources = [passive.snapshot(self.source, large, large, "2026-09-01")]
        original = copy.deepcopy(self.ops)
        pointer = passive.prepare(self.ops, sources, [self.person])
        self.assertEqual(pointer["reason"], "oversized_review")
        self.assertEqual(passive.load_evidence(pointer)["inputs"], original)
        with self.assertRaises(ValueError):
            passive.executable(pointer)
        self.assertEqual(self.ops, original)

    def test_lastcontact_and_added_support_do_not_invalidate_answer(self):
        envelope = self.envelope(lambda *args: [self.saved["id"]])
        person = copy.deepcopy(self.person)
        person["md"]["mdlastcontact"] = "2026-09-10"
        additional = copy.deepcopy(self.saved)
        additional["reference"]["id"] = "d" * 64
        person["body"] = bio.attach(person["body"], additional, "BBB-222")
        ready = passive.answer(envelope, {envelope["questions"][0]["key"]: self.saved["id"]}, [person])
        self.assertEqual(ready["status"], "ready")
