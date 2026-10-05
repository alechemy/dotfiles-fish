import copy
import unittest
from unittest import mock

from helpers import load, person
from test_capture_corrections import CorrectionBridge
from test_capture_recovery import SOURCE

capture = load("entity_capture.py", "capture_bio_recovery")
TEXT = "Wren has two siblings. Her brother may move in October."


def bridge_and_plan():
    bridge = CorrectionBridge()
    bridge.bodies["SRC-1"] = "# Capture\n\n" + TEXT
    bridge.people = [person("Wren", "P1", entitytype="Person"), person("Kestrel", "P2", entitytype="Person")]
    bridge.bodies.update(P1="# Wren\n\nManual paragraph.\n", P2="# Kestrel\n\nOther manual text.\n")
    result = capture.resolve(TEXT, [{"mention": "Wren"}], bridge.people)
    manifest = capture.prepare_unified(SOURCE, "2026-09-29", TEXT, result, "2026-09-30", bridge.people)
    return bridge, manifest


class Recovery(unittest.TestCase):
    def test_v2_replay_recovers_after_each_mutation_without_inference(self):
        import entity_biographical as bio
        baseline, manifest = bridge_and_plan()
        capture.replay(baseline, manifest)
        for failure in range(1, baseline.mutations + 1):
            with self.subTest(failure=failure):
                bridge, manifest = bridge_and_plan()
                bridge.fail_after = failure
                with self.assertRaises(RuntimeError):
                    capture.replay(bridge, manifest)
                frozen = capture.decode_manifest(bridge.manifest, "SRC-1")
                bridge.fail_after = 0
                capture.replay(bridge, frozen, bridge.manifest)
                final = capture.decode_manifest(bridge.manifest, "SRC-1")
                self.assertEqual(final["status"], "filed")
                self.assertEqual(len(bio.render(bridge.bodies["P1"])), 2)
                self.assertIn("Manual paragraph.", bridge.bodies["P1"])

    def test_within_passage_duplicates_replay_and_undo_keep_complete_history(self):
        import entity_biographical as bio
        text = "Wren has two siblings. Wren has two siblings."
        bridge, unused = bridge_and_plan()
        bridge.bodies["SRC-1"] = "# Capture\n\n" + text
        resolved = capture.resolve(text, [{"mention": "Wren"}], bridge.people)
        manifest = capture.prepare_unified(SOURCE, "2026-09-29", text, resolved, "2026-09-30", bridge.people)
        capture.replay(bridge, manifest)
        frozen = capture.decode_manifest(bridge.manifest, "SRC-1")
        capture.replay(bridge, frozen, bridge.manifest)
        rows = bio.render(bridge.bodies["P1"], "P1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["data"]["references"]), 2)
        capture.start_correction(frozen, "undo")
        final = capture.correct(bridge, frozen, bridge.manifest)
        self.assertEqual(final["status"], "undone")
        self.assertEqual(bio.render(bridge.bodies["P1"], "P1"), [])
        self.assertEqual(final["history"][-1]["text"], text)
        self.assertEqual(capture.source_text(bridge.bodies["SRC-1"]), text)

    def test_two_capture_owners_can_undo_in_either_order(self):
        import entity_biographical as bio
        bridge, manifest = bridge_and_plan()
        row = manifest["subjects"][0]["contribution"]["assertions"][0]
        other = copy.deepcopy(row)
        other["reference"].update(id="f" * 64, source_uuid="CCC-333")
        original = bridge.bodies["P1"]
        for first, last in ((row, other), (other, row)):
            body = bio.attach(bio.attach(original, row, "P1"), other, "P1")
            body = bio.detach(body, first, "P1")
            bio.verify(body, last, "P1")
            self.assertEqual(len(bio.render(body)[0]["data"]["references"]), 1)
            self.assertEqual(bio.detach(body, last, "P1"), original + "\n" + bio.HEADER + "\n")

    def test_overlap_correction_and_undo_recover_after_each_mutation(self):
        import entity_biographical as bio
        revised = "Wren has two siblings. Her brother will stay in October."
        for undo in (False, True):
            def ready():
                bridge, manifest = bridge_and_plan()
                capture.replay(bridge, manifest)
                manifest = capture.decode_manifest(bridge.manifest, "SRC-1")
                if undo:
                    capture.start_correction(manifest, "undo")
                else:
                    people = [dict(p, body=bridge.bodies[p["uuid"]]) for p in bridge.people]
                    result = capture.resolve(revised, [{"mention": "Wren"}], people)
                    next_manifest = capture.prepare_unified(SOURCE, "2026-09-29", revised, result, "2026-09-30", people)
                    capture.start_correction(manifest, "revise", next_manifest, revised)
                    manifest["correction"]["source_edit"] = {"from": TEXT, "to": revised}
                bridge.mutations = 0
                return bridge, manifest
            baseline, manifest = ready()
            capture.correct(baseline, manifest, baseline.manifest)
            for failure in range(1, baseline.mutations + 1):
                with self.subTest(undo=undo, failure=failure):
                    bridge, manifest = ready()
                    bridge.fail_after = failure
                    with self.assertRaises(RuntimeError):
                        capture.correct(bridge, manifest, bridge.manifest)
                    bridge.fail_after = 0
                    frozen = capture.decode_manifest(bridge.manifest, "SRC-1")
                    if frozen["status"] == "correcting":
                        capture.correct(bridge, frozen, bridge.manifest)
                    rows = bio.render(bridge.bodies["P1"])
                    self.assertEqual(len(rows), 0 if undo else 2)
                    if not undo:
                        self.assertEqual([r["data"]["baseline"]["text"] for r in rows],
                                         ["Has two siblings.", "Her brother will stay in October."])
                        self.assertTrue(all(len(r["data"]["references"]) == 1 for r in rows))
                    self.assertIn("Manual paragraph.", bridge.bodies["P1"])

    def test_keep_edited_protects_content_from_another_owners_undo(self):
        import entity_biographical as bio
        bridge, manifest = bridge_and_plan()
        capture.replay(bridge, manifest)
        first = capture.decode_manifest(bridge.manifest, "SRC-1")
        row = first["subjects"][0]["contribution"]["assertions"][0]
        second = copy.deepcopy(row)
        second["reference"]["id"] = "e" * 64
        body = bio.attach(bridge.bodies["P1"], second, "P1")
        body = body.replace("Has two siblings. ([source]", "Has two siblings, with a manual detail. ([source]")
        body = bio.detach(bio.protect(body, row, "P1"), row, "P1")
        body = bio.detach(body, second, "P1")
        self.assertIn("with a manual detail.", body)

    def test_registered_queued_request_recovers_without_local_state_and_needs_inference_lock(self):
        ef = load("entity-filing.py", "bio_queued_scan")
        bridge, manifest = bridge_and_plan()
        capture.replay(bridge, manifest)
        old = capture.decode_manifest(bridge.manifest, "SRC-1")
        old.update(status="revision_question", analysis_request={"from_text": TEXT, "from_revision": capture.revision(TEXT),
            "text": TEXT, "subjects": old["subjects"]})
        bridge.manifest = capture.encode_manifest(old)
        config = {"TRANSPORT": "local", "MIN_ROSTER": "1", "MAX_PER_RUN": "1", "FILING_MODE": "suggest",
                  "IDLE_MINUTES": "0", "CAPTURE_AUTO_AFTER": "", "SKIP_SOURCE_TITLES": ""}
        with mock.patch.object(ef, "run_bridge", bridge), mock.patch.object(ef, "self_names", return_value=set()), \
                mock.patch.object(ef, "omlx_available", return_value=True), \
                mock.patch.object(ef, "acquire_llm_lock", return_value=None) as inference, \
                mock.patch.object(ef, "process_capture_analysis") as process:
            ef.scan(config, {"processed": {}, "attempts": {}, "parked": {}}, False, None, True)
        inference.assert_called_once()
        process.assert_not_called()
        self.assertIn("analysis_request", capture.decode_manifest(bridge.manifest, "SRC-1"))
