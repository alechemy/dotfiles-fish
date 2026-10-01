import types
import unittest
from unittest import mock

from helpers import load
from test_capture_corrections import filed_bridge
from test_capture_recovery import TEXT

srv = load("entity-review-server.py", "capture_review_server")
capture = srv.ef.capture


class Review(unittest.TestCase):
    def setUp(self):
        self.bridge = filed_bridge()
        self.patches = [mock.patch.object(srv.ef, "run_bridge", self.bridge),
                        mock.patch.object(srv.ef, "load_config", return_value={}),
                        mock.patch.object(srv.ef, "self_names", return_value=set()),
                        mock.patch.object(srv.ef, "acquire_lock", return_value=mock.Mock()),
                        mock.patch.object(srv.ec, "acquire_candidates_lock", return_value=mock.Mock()),
                        mock.patch.object(srv.subprocess, "run", return_value=types.SimpleNamespace(returncode=0))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def views(self):
        return srv.capture_views([{"uuid": "SRC-1", "operation": self.bridge.manifest,
                                    "text": capture.source_text(self.bridge.bodies["SRC-1"])}], self.bridge.people)[0]

    def test_source_revision_token_uses_live_source_through_correction_conflicts(self):
        self.bridge.bodies["P1"] = self.bridge.bodies["P1"].replace("> Wren moved", "> Wren relocated")
        revised = "Wren moved to Oslo."
        with self.assertRaises(RuntimeError):
            srv.handle_capture("SRC-1", {"action": "save", "revision": capture.revision(TEXT), "text": revised,
                "subjects": [{"mention": "Wren", "passage": revised, "target": "P2"}]})
        view = self.views()
        self.assertEqual(view["revision"], capture.revision(revised))
        self.assertEqual(view["text"], revised)
        self.assertTrue(view["conflict"])
        result = srv.handle_capture("SRC-1", {"action": "keep-edited", "revision": view["revision"]})
        self.assertEqual(result["status"], "filed")
        self.assertIn("> Wren relocated", self.bridge.bodies["P1"])
        self.assertIn("> Wren moved to Oslo.", self.bridge.bodies["P2"])

    def test_external_edit_then_ui_edit_remains_decodable_after_interruption(self):
        baseline = "Wren moved to Oslo."
        revised = "Wren moved to Prague."
        self.bridge.bodies["SRC-1"] = "# Capture\n\n" + baseline
        self.bridge.fail_after = 1
        with self.assertRaises(RuntimeError):
            srv.handle_capture("SRC-1", {"action": "save", "revision": capture.revision(baseline), "text": revised,
                "subjects": [{"mention": "Wren", "passage": revised, "target": "P2"}]})
        manifest = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        self.assertEqual(manifest["correction"]["source_edit"]["from_revision"], capture.revision(baseline))
        view = self.views()
        self.assertEqual(view["text"], revised)
        self.assertEqual(view["current_text"], baseline)
        self.assertEqual(view["revision"], capture.revision(baseline))
        self.bridge.fail_after = 0
        result = srv.handle_capture("SRC-1", {"action": "retry", "revision": view["revision"]})
        self.assertEqual(result["status"], "filed")

    def test_pending_external_edit_then_ui_edit_recovers_frozen_replacement(self):
        baseline = "Wren moved to Oslo."
        revised = "Wren moved to Prague."
        self.bridge.manifest = capture.encode_manifest(capture.pending({"uuid": "SRC-1"}, "2026-09-29", TEXT,
            [{"mention": "Wren"}], capture.outcome("unresolved", "ambiguous_name"), "2026-09-30"))
        self.bridge.bodies["SRC-1"] = "# Capture\n\n" + baseline
        self.bridge.fail_after = 1
        with self.assertRaises(RuntimeError):
            srv.handle_capture("SRC-1", {"action": "save", "revision": capture.revision(baseline), "text": revised,
                "subjects": [{"mention": "Wren", "passage": revised, "target": "P2"}]})
        manifest = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        self.assertEqual(manifest["replacement_from_revision"], capture.revision(baseline))
        bad = dict(manifest, replacement_from_revision="bad")
        with self.assertRaises(ValueError):
            capture.decode_manifest(capture.encode_manifest(bad), "SRC-1")
        self.bridge.fail_after = 0
        with self.assertRaises(srv.RequestError):
            srv.handle_capture("SRC-1", {"action": "save", "revision": capture.revision(baseline), "text": revised,
                "subjects": [{"mention": "Wren", "passage": revised, "target": "P2"}]})
        result = srv.handle_capture("SRC-1", {"action": "retry", "revision": capture.revision(baseline)})
        self.assertEqual(result["status"], "filed")
        final = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        self.assertEqual([h["text"] for h in final["history"]], [TEXT, baseline])
        self.assertEqual(capture.source_text(self.bridge.bodies["SRC-1"]), revised)
        self.assertIn("> Wren moved to Prague.", self.bridge.bodies["P2"])

    def test_pending_replacement_recovers_after_each_mutation(self):
        baseline = "Wren moved to Oslo."
        revised = "Wren moved to Prague."
        def new_bridge():
            bridge = filed_bridge()
            bridge.manifest = capture.encode_manifest(capture.pending({"uuid": "SRC-1"}, "2026-09-29", TEXT,
                [{"mention": "Wren"}], capture.outcome("unresolved", "ambiguous_name"), "2026-09-30"))
            bridge.bodies["SRC-1"] = "# Capture\n\n" + baseline
            return bridge
        payload = {"action": "save", "revision": capture.revision(baseline), "text": revised,
            "subjects": [{"mention": "Wren", "passage": revised, "target": "P2"}]}
        bridge = new_bridge()
        with mock.patch.object(srv.ef, "run_bridge", bridge):
            srv.handle_capture("SRC-1", payload)
        for failure in range(1, bridge.mutations + 1):
            with self.subTest(failure=failure):
                crashed = new_bridge()
                crashed.fail_after = failure
                with mock.patch.object(srv.ef, "run_bridge", crashed):
                    with self.assertRaises(RuntimeError):
                        srv.handle_capture("SRC-1", payload)
                    frozen = capture.decode_manifest(crashed.manifest, "SRC-1")
                    crashed.fail_after = 0
                    if frozen["status"] != "filed":
                        srv.handle_capture("SRC-1", {"action": "retry",
                            "revision": capture.revision(capture.source_text(crashed.bodies["SRC-1"]))})
                final = capture.decode_manifest(crashed.manifest, "SRC-1")
                self.assertEqual(final["status"], "filed")
                self.assertEqual([h["text"] for h in final["history"]], [TEXT, baseline])
                self.assertEqual(crashed.bodies["P2"].count(":begin -->"), 1)
                self.assertEqual(capture.source_text(crashed.bodies["SRC-1"]), revised)

    def test_stale_decision_is_rejected_without_mutation(self):
        with self.assertRaises(srv.RequestError):
            srv.handle_capture("SRC-1", {"action": "undo", "revision": "stale"})
        self.assertEqual(self.bridge.mutations, 0)

    def test_an_unnamed_question_can_be_bound_to_an_existing_person(self):
        text = "Someone moved to Oslo."
        self.bridge.bodies["SRC-1"] = "# Capture\n\n" + text
        self.bridge.manifest = capture.encode_manifest(capture.pending({"uuid": "SRC-1"}, "2026-09-29", text, [], capture.outcome("unresolved", "no_subject"), "2026-09-30"))
        result = srv.handle_capture("SRC-1", {"action": "save", "revision": capture.revision(text),
            "subjects": [{"mention": "", "passage": text, "target": "P1"}]})
        self.assertEqual(result["status"], "filed")
        self.assertIn("> Someone moved to Oslo.", self.bridge.bodies["P1"])
        capture.decode_manifest(self.bridge.manifest, "SRC-1")
