import copy
import types
import unittest
from unittest import mock

from helpers import load
from test_capture_corrections import filed_bridge
from test_capture_recovery import SOURCE, TEXT

srv = load("entity-review-server.py", "capture_retention_server")
capture = srv.ef.capture


class LegacyRetention(unittest.TestCase):
    def setUp(self):
        self.bridge = filed_bridge()
        self.bridge.manifest = capture.encode_manifest(capture.pending(
            SOURCE, "2026-09-29", TEXT, [],
            capture.outcome("unresolved", "legacy_filing"), "2026-09-30"))
        self.bridge.calls.clear()
        self.bridge.operations.clear()
        self.before = copy.deepcopy(self.bridge.bodies)

        def bridge(ops):
            rows = self.bridge(ops)
            for op, row in zip(ops, rows):
                if op["op"] == "dump_people" and op.get("include_bodies"):
                    for person in row:
                        person["body"] = self.bridge.bodies[person["uuid"]]
            return rows

        patches = [mock.patch.object(srv.ef, "run_bridge", bridge),
                   mock.patch.object(srv.ef, "load_config", return_value={}),
                   mock.patch.object(srv.ef, "self_names", return_value=set()),
                   mock.patch.object(srv.ef, "acquire_lock", return_value=mock.Mock()),
                   mock.patch.object(srv.ec, "acquire_candidates_lock", return_value=mock.Mock()),
                   mock.patch.object(srv.subprocess, "run", return_value=types.SimpleNamespace(returncode=0))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def retain(self):
        return srv.handle_capture("SRC-1", {"action": "retain", "revision": capture.revision(TEXT)})

    def test_existing_filings_are_kept_without_person_or_receipt_writes(self):
        self.assertEqual(self.retain(), {"ok": True, "status": "retained"})
        manifest = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        self.assertEqual(manifest["status"], "retained")
        self.assertEqual(manifest["subjects"], [])
        self.assertFalse(manifest["receipt"])
        self.assertEqual(manifest["legacy_filings"][0]["uuid"], "P1")
        self.assertEqual(manifest["legacy_filings"][0]["body_revision"], capture.revision(self.before["P1"]))
        self.assertEqual(self.bridge.bodies, self.before)
        self.assertNotIn("capture_append", self.bridge.calls)
        self.assertNotIn("capture_remove", self.bridge.calls)
        self.assertNotIn("append_pinned", self.bridge.calls)
        self.assertIn("capture_retire_source", self.bridge.calls)

    def test_repeat_acknowledgement_is_idempotent(self):
        self.retain()
        manifest = self.bridge.manifest
        self.retain()
        self.assertEqual(self.bridge.manifest, manifest)
        self.assertEqual(self.bridge.bodies, self.before)

    def test_acknowledgement_recovers_after_each_mutation(self):
        baseline = self.bridge.mutations
        for offset in (1, 2):
            self.bridge.manifest = capture.encode_manifest(capture.pending(
                SOURCE, "2026-09-29", TEXT, [],
                capture.outcome("unresolved", "legacy_filing"), "2026-09-30"))
            self.bridge.fail_after = self.bridge.mutations + offset
            with self.assertRaises(RuntimeError):
                self.retain()
            self.bridge.fail_after = 0
            self.retain()
            self.assertEqual(capture.decode_manifest(self.bridge.manifest, "SRC-1")["status"], "retained")
            self.assertEqual(self.bridge.bodies, self.before)
        self.assertGreater(self.bridge.mutations, baseline)

    def test_missing_old_filing_does_not_close_the_question(self):
        self.bridge.bodies["P1"] = "# Wren\n\nManual text.\n"
        before = self.bridge.manifest
        with self.assertRaises(srv.RequestError):
            self.retain()
        self.assertEqual(self.bridge.manifest, before)

    def test_nonlegacy_question_is_not_acknowledged(self):
        manifest = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        manifest["reason"] = "ambiguous_name"
        self.bridge.manifest = capture.encode_manifest(manifest)
        with self.assertRaises(srv.RequestError):
            self.retain()

    def test_changed_source_cannot_acknowledge_the_previous_filing(self):
        text = "Wren moved to Prague."
        self.bridge.bodies["SRC-1"] = "# Capture\n\n" + text
        with self.assertRaises(srv.RequestError):
            srv.handle_capture("SRC-1", {"action": "retain", "revision": capture.revision(text)})
        self.assertEqual(capture.decode_manifest(self.bridge.manifest, "SRC-1")["status"], "question")

    def test_retained_operation_cannot_be_retried_or_resaved_as_a_new_filing(self):
        self.retain()
        for action in ("save", "retry", "undo", "defer"):
            with self.subTest(action=action), self.assertRaises(srv.RequestError):
                srv.handle_capture("SRC-1", {"action": action, "revision": capture.revision(TEXT),
                    "subjects": [{"mention": "Wren", "passage": TEXT}]})
        self.assertEqual(self.bridge.bodies, self.before)

    def test_source_edit_is_not_displayed_as_completed_before_the_worker_runs(self):
        self.retain()
        text = "Wren moved to Prague."
        views = srv.capture_views([{"uuid": "SRC-1", "operation": self.bridge.manifest,
                                    "text": text}], self.bridge.people)
        self.assertEqual(views[0]["status"], "waiting")
        self.assertFalse(views[0]["can_retain_legacy"])
        self.assertEqual(views[0]["revision"], capture.revision(text))
        self.assertIn("original note changed", views[0]["question"])
        self.assertEqual(capture.decode_manifest(self.bridge.manifest, "SRC-1")["status"], "retained")

    def test_retained_manifest_requires_legacy_reference_evidence(self):
        self.retain()
        manifest = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        for update in ({"legacy_filings": []}, {"reason": "ambiguous_name"},
                       {"legacy_filings": [{"uuid": "P1", "body_revision": "bad"}]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                capture.decode_manifest(capture.encode_manifest(dict(manifest, **update)), "SRC-1")
