import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import load, person
from test_entity_biographical_migration import Bridge
from test_entity_biographical_recovery import bridge_and_plan, SOURCE, TEXT

import entity_biographical as bio

capture = load("entity_capture.py", "human_comparison_capture")
ef = load("entity-filing.py", "human_comparison_filing")
migration = load("entity_biographical_migration.py", "human_comparison_migration")
review = load("entity_biographical_review.py", "human_comparison_review")


def unavailable(*args):
    raise bio.ComparisonUnavailable("Local comparison is unavailable.")


def compared_people():
    text = "Wren likes hiking."
    manifest = capture.prepare_unified({"uuid": "OLD-1"}, "2026-09-01", text,
        capture.resolve(text, [{"mention": "Wren"}], []), "2026-09-01")
    row = manifest["subjects"][0]["contribution"]["assertions"][0]
    return [dict(person("Wren", "P1", entitytype="Person"), body=bio.attach("# Wren\n\n", row, "P1"))]


class Comparison(unittest.TestCase):
    def test_failed_invalid_or_oversized_comparison_never_returns_all_candidates(self):
        candidate = {"id": "a" * 64, "text": "Likes hiking.", "log_date": "2026-09-01",
                     "temporal_context": "observed:2026-09-01", "origin": "capture"}
        for raw in ('not JSON', '{"candidates":"all"}', '{"candidates":["unknown"]}', '[]'):
            with self.subTest(raw=raw), mock.patch.object(ef, "extract_omlx", return_value=raw):
                with self.assertRaises(bio.ComparisonUnavailable):
                    ef.semantic_suggestions({})("Has two siblings.", "2026-09-02", [candidate])
        with mock.patch.object(ef, "extract_omlx", side_effect=ef.LLMUnavailable("Synthetic HTTP 507")):
            with self.assertRaises(bio.ComparisonUnavailable):
                ef.semantic_suggestions({})("Has two siblings.", "2026-09-02", [candidate])
        with mock.patch.object(ef, "extract_omlx") as infer:
            for text, rows in (("Has two siblings.", [candidate] * 81), ("x" * 33000, [candidate])):
                with self.assertRaises(bio.ComparisonUnavailable):
                    ef.semantic_suggestions({})(text, "2026-09-02", rows)
            infer.assert_not_called()
        with mock.patch.object(ef, "extract_omlx", return_value='{"candidates":[]}'):
            self.assertEqual(ef.semantic_suggestions({})("Has two siblings.", "2026-09-02", [candidate]), [])

    def test_comparison_accepts_only_complete_bare_or_json_fenced_responses(self):
        candidate = {"id": "a" * 64, "text": "Likes hiking.", "log_date": "2026-09-01",
                     "temporal_context": "observed:2026-09-01", "origin": "capture"}
        payload = '{"candidates":["' + candidate["id"] + '"]}'
        for raw in (payload, "```json\n" + payload + "\n```", "```\n" + payload + "\n```",
                    " \n```JSON\r\n" + payload + "\r\n```\n "):
            with self.subTest(raw=raw), mock.patch.object(ef, "extract_omlx", return_value=raw):
                self.assertEqual(ef.semantic_suggestions({})("Has two siblings.", "2026-09-02", [candidate]), [candidate["id"]])
        for raw in (None, [], {}, "Explanation\n```json\n" + payload + "\n```",
                    "```json\n" + payload + "\n```\nExplanation", "```json\n" + payload,
                    '```json\n{"candidates":["unknown"]}\n```', '```json\n{"candidates":"all"}\n```'):
            with self.subTest(raw=raw), mock.patch.object(ef, "extract_omlx", return_value=raw):
                with self.assertRaises(bio.ComparisonUnavailable):
                    ef.semantic_suggestions({})("Has two siblings.", "2026-09-02", [candidate])

    def test_unavailable_comparison_freezes_complete_waiting_plan_without_questions(self):
        text = "Wren has two siblings. Wren works in Denver."
        people = compared_people()
        manifest = capture.prepare_unified({"uuid": "SRC-1"}, "2026-09-02", text,
            capture.resolve(text, [{"mention": "Wren"}], people), "2026-09-02", people, suggest=unavailable)
        self.assertEqual(manifest["status"], "deferred")
        self.assertEqual(manifest["reason"], "comparison_unavailable")
        self.assertNotIn("semantic_questions", manifest)
        self.assertEqual("".join(row["reference"]["evidence"] for row in
            manifest["subjects"][0]["contribution"]["assertions"]), text)
        capture.decode_manifest(capture.encode_manifest(manifest), "SRC-1")
        bridge, unused = bridge_and_plan()
        bridge.bodies["SRC-1"] = "# Capture\n\n" + text
        bridge.bodies["P1"] = people[0]["body"]
        original = copy.deepcopy(bridge.bodies)
        result = capture.replay(bridge, manifest)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(bridge.bodies, original)
        self.assertEqual(capture.decode_manifest(bridge.manifest, "SRC-1")["status"], "deferred")
        self.assertFalse(manifest["receipt"])

    def test_new_capture_comparison_failure_does_not_file_and_can_retry(self):
        bridge, unused = bridge_and_plan()
        bridge.bodies["P1"] = compared_people()[0]["body"]
        original = copy.deepcopy(bridge.bodies)
        state = {"processed": {}, "attempts": {}, "parked": {}}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(ef, "run_bridge", bridge), \
                mock.patch.object(ef, "save_state"), \
                mock.patch.object(ef, "self_names", return_value=set()), \
                mock.patch.object(ef.ec, "CANDIDATE_LOCK_FILE", str(Path(directory) / "lock")), \
                mock.patch.object(ef, "semantic_suggestions", return_value=unavailable) as suggestions:
            result = ef.file_capture({"TRANSPORT": "local"}, state, SOURCE, "2026-09-29", TEXT,
                                     [{"mention": "Wren"}], False)
            self.assertEqual(result["status"], "deferred")
            self.assertEqual(bridge.bodies, original)
            frozen = capture.decode_manifest(bridge.manifest, "SRC-1")
            self.assertEqual(frozen["reason"], "comparison_unavailable")
            self.assertIn("SRC-1", state["processed"])
            self.assertEqual(state["attempts"], {})
            self.assertFalse(frozen["receipt"])
            suggestions.return_value = lambda *args: []
            result = ef.file_capture({"TRANSPORT": "local"}, state,
                dict(SOURCE, capture_operation=bridge.manifest), "2026-09-29", TEXT, [{"mention": "Wren"}], False)
            self.assertEqual(result["status"], "filed")
            final = capture.decode_manifest(bridge.manifest, "SRC-1")
            self.assertEqual(final["status"], "filed")
            self.assertEqual(final["text"], frozen["text"])
            self.assertEqual(final["revision"], frozen["revision"])
            self.assertEqual(len(bio.render(bridge.bodies["P1"], "P1")), 3)

    def test_draft_paraphrases_are_not_presented_as_already_saved_entries(self):
        text = "Wren has a brother and a sister. Wren has two siblings."
        people = [dict(person("Wren", "P1", entitytype="Person"), body="# Wren\n")]
        suggest = mock.Mock(side_effect=unavailable)
        manifest = capture.prepare_unified({"uuid": "SRC-1"}, "2026-09-02", text,
            capture.resolve(text, [{"mention": "Wren"}], people), "2026-09-02", people, suggest=suggest)
        self.assertEqual(len(manifest["subjects"][0]["contribution"]["assertions"]), 2)
        self.assertNotIn("semantic_questions", manifest)
        suggest.assert_not_called()
        people = compared_people()
        suggest = mock.Mock(return_value=[])
        capture.prepare_unified({"uuid": "SRC-1"}, "2026-09-02", text,
            capture.resolve(text, [{"mention": "Wren"}], people), "2026-09-02", people, suggest=suggest)
        self.assertEqual(suggest.call_count, 2)
        self.assertTrue(all([row["text"] for row in call.args[2]] == ["Likes hiking."] for call in suggest.call_args_list))

    def test_failed_transport_creates_waiting_state_instead_of_unrelated_questions(self):
        people = compared_people()
        with mock.patch.object(ef, "extract_omlx", side_effect=ef.LLMUnavailable("Synthetic HTTP 507")):
            manifest = capture.prepare_unified({"uuid": "SRC-1"}, "2026-09-02", TEXT,
                capture.resolve(TEXT, [{"mention": "Wren"}], people), "2026-09-02", people,
                suggest=ef.semantic_suggestions({}))
        self.assertEqual(manifest["status"], "deferred")
        self.assertEqual(manifest["reason"], "comparison_unavailable")
        self.assertNotIn("semantic_questions", manifest)

    def test_new_private_artifact_creation_cannot_overwrite_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.json"
            original = {"original": "fictional evidence"}
            migration.private_save(path, original)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                migration.private_save(path, {"replacement": "fictional evidence"}, replace=False)
            self.assertEqual(path.read_bytes(), before)
            fresh = Path(directory) / "new.json"
            migration.private_save(fresh, original, replace=False)
            self.assertEqual(fresh.read_bytes(), before)
            self.assertEqual(fresh.stat().st_mode & 0o777, 0o600)

    def test_cross_day_exact_question_does_not_need_model_comparison(self):
        people = compared_people()
        text = "Wren likes hiking."
        suggest = mock.Mock(side_effect=unavailable)
        manifest = capture.prepare_unified({"uuid": "SRC-1"}, "2026-09-02", text,
            capture.resolve(text, [{"mention": "Wren"}], people), "2026-09-02", people, suggest=suggest)
        self.assertEqual(manifest["status"], "question")
        self.assertEqual(len(manifest["semantic_questions"][0]["candidates"]), 1)
        suggest.assert_not_called()

    def test_queued_comparison_failure_keeps_original_filing_and_request(self):
        bridge, manifest = bridge_and_plan()
        capture.replay(bridge, manifest)
        old = capture.decode_manifest(bridge.manifest, "SRC-1")
        revised = "Wren works in Denver."
        resolved = capture.resolve(revised, [{"mention": "Wren"}], bridge.people)
        old.update(status="revision_question", analysis_request={"from_text": TEXT,
            "from_revision": capture.revision(TEXT), "text": revised, "subjects": resolved["subjects"]})
        bridge.manifest = capture.encode_manifest(old)
        original = copy.deepcopy(bridge.bodies)
        with mock.patch.object(ef, "run_bridge", bridge), \
                mock.patch.object(ef, "semantic_suggestions", return_value=unavailable):
            ef.process_capture_analysis({"SELF_NAME": ""}, {"uuid": "SRC-1"}, old, bridge.manifest, TEXT, set())
        frozen = capture.decode_manifest(bridge.manifest, "SRC-1")
        self.assertIn("analysis_request", frozen)
        self.assertNotIn("analysis_plan", frozen)
        self.assertNotIn("correction", frozen)
        self.assertEqual(frozen["reason"], "comparison_unavailable")
        self.assertEqual(bridge.bodies, original)
        self.assertEqual(capture.view(frozen, bridge.people)["status"], "waiting")

    def test_waiting_migration_remains_visible_but_cannot_resolve_or_apply(self):
        bridge = Bridge()
        bridge.bodies["BBB-222"] += "\n\n## Biographical Log\n\n" + ef.fact_line(
            "2026-08-01", "Wren likes hiking.", "EEE-555")
        original = copy.deepcopy(bridge.bodies)
        plan = migration.preview(bridge, unavailable)
        self.assertEqual(plan["counts"]["pending_questions"], 0)
        self.assertEqual(plan["counts"]["waiting_comparisons"], 2)
        self.assertEqual(plan["counts"]["conflicted"], 0)
        self.assertEqual(len(plan["questions"]), 2)
        migration.validate(plan)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            review.register(state, plan)
            views = review.views(state)
            self.assertEqual(len(views), 2)
            self.assertTrue(all(view["status"] == "waiting" and not view["semantic_questions"] for view in views))
            with self.assertRaisesRegex(ValueError, "waiting"):
                migration.resolve(bridge, plan, {"plan_id": plan["plan_id"], "answers": {"AAA-111": {}}})
            with self.assertRaisesRegex(ValueError, "waiting"):
                migration.transact(bridge, plan, state / "journal.json", state / migration.FENCE_NAME)
            self.assertFalse((state / migration.FENCE_NAME).exists())
        self.assertEqual(bridge.bodies, original)
        self.assertEqual(bridge.mutations, 0)


if __name__ == "__main__":
    unittest.main()
