import copy
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from helpers import load
from test_entity_biographical_migration import Bridge

migration = load("entity_biographical_migration.py", "bio_review_migration")
review = load("entity_biographical_review.py", "bio_review_store")
srv = load("entity-review-server.py", "bio_review_server")


def pending_plan(bridge):
    ef = load("entity-filing.py", "bio_review_legacy_format")
    bridge.bodies["BBB-222"] += "\n\n## Biographical Log\n\n" + ef.fact_line(
        "2026-08-01", "Wren has two siblings.", "EEE-555")
    return migration.preview(bridge, lambda *args: [])


def answer(plan):
    row = plan["questions"][0]
    return {"source_uuid": row["source_uuid"], "answers": {q["key"]: q["candidates"][0]["id"]
            for q in row["manifest"]["semantic_questions"]}}


class PrivateReview(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        self.bridge = Bridge()
        self.plan = pending_plan(self.bridge)
        review.register(self.state, self.plan)
        self.patches = [mock.patch.object(srv.ef, "STATE_DIR", str(self.state)),
                        mock.patch.object(srv.ef, "run_bridge", self.bridge),
                        mock.patch.object(srv.ef, "acquire_lock", return_value=mock.Mock()),
                        mock.patch.object(srv.ec, "acquire_candidates_lock", return_value=mock.Mock()),
                        mock.patch.object(srv.subprocess, "run", return_value=types.SimpleNamespace(returncode=0))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_partial_review_survives_restart_and_rejects_stale_branches(self):
        original = copy.deepcopy(self.bridge.bodies)
        first = srv.handle_biographical_plan(self.plan["plan_id"], answer(self.plan))
        self.assertEqual(first["status"], "migration_waiting")
        self.assertEqual(first["pending_questions"], 1)
        self.assertEqual(self.bridge.bodies, original)
        stored = review.load_current(self.state, first["plan_id"])
        self.assertEqual(len(review.views(self.state)), 1)
        self.assertTrue(stored["decisions"])
        with self.assertRaises(srv.RequestError):
            srv.handle_biographical_plan(self.plan["plan_id"], answer(self.plan))
        second = srv.handle_biographical_plan(first["plan_id"], answer(stored))
        self.assertEqual(second["status"], "migration_reviewed")
        self.assertEqual(review.views(self.state), [])
        self.assertEqual(review.read_private(review.plan_path(self.state, self.plan["plan_id"])), self.plan)
        self.assertEqual(self.bridge.mutations, 0)

    def test_stale_source_person_controls_or_decision_are_rejected(self):
        for choice in ({"source_uuid": "unknown", "answers": {}},
                       {"source_uuid": answer(self.plan)["source_uuid"], "answers": {"unknown": "separate"}},
                       {**answer(self.plan), "path": "/fictional/private/file"}):
            with self.assertRaises(srv.RequestError):
                srv.handle_biographical_plan(self.plan["plan_id"], choice)
        self.bridge.bodies["BBB-222"] += "\nLater edit."
        with self.assertRaises(srv.RequestError):
            srv.handle_biographical_plan(self.plan["plan_id"], answer(self.plan))
        self.assertEqual(self.bridge.mutations, 0)
        self.assertEqual(review.registry(self.state)["plans"][self.plan["plan_id"]]["status"], "pending")

    def test_registration_rejects_paths_symlinks_permissions_owner_size_and_digest(self):
        for plan_id in ("../outside", "A" * 64, "a" * 63, "a" * 65):
            with self.assertRaises(ValueError):
                review.load_current(self.state, plan_id)
        path = review.plan_path(self.state, self.plan["plan_id"])
        os.chmod(path, 0o644)
        with self.assertRaises(ValueError):
            review.load_current(self.state, self.plan["plan_id"])
        os.chmod(path, 0o600)
        with mock.patch.object(review.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(ValueError):
                review.load_current(self.state, self.plan["plan_id"])
        with self.assertRaises(ValueError):
            review.read_private(path, 10)
        saved = path.read_text()
        path.unlink()
        other = self.state / "other.json"
        other.write_text(saved)
        os.chmod(other, 0o600)
        path.symlink_to(other)
        with self.assertRaises(OSError):
            review.load_current(self.state, self.plan["plan_id"])
        path.unlink()
        path.write_text(saved.replace('"pending_questions": 2', '"pending_questions": 0'))
        os.chmod(path, 0o600)
        with self.assertRaises(ValueError):
            review.load_current(self.state, self.plan["plan_id"])

    def test_read_only_views_remain_available_during_fence_but_choices_stop(self):
        migration.private_save(self.state / migration.FENCE_NAME, {"version": 2, "plan_id": self.plan["plan_id"],
            "scope": self.plan["scope"], "names": self.plan["names"]})
        self.assertEqual(len(review.views(self.state)), 2)
        with self.assertRaises(srv.RequestError):
            srv.handle_biographical_plan(self.plan["plan_id"], answer(self.plan))
        self.assertEqual(self.bridge.mutations, 0)

    def test_registry_failure_preserves_original_plan_and_retry_uses_same_new_digest(self):
        with mock.patch.object(review, "save_registry", side_effect=RuntimeError("Synthetic registry interruption")):
            with self.assertRaises(RuntimeError):
                review.decide(self.bridge, self.state, self.plan["plan_id"], **answer(self.plan))
        self.assertEqual(review.registry(self.state)["plans"][self.plan["plan_id"]]["status"], "pending")
        result = review.decide(self.bridge, self.state, self.plan["plan_id"], **answer(self.plan))
        self.assertEqual(result["pending_questions"], 1)

    def post(self, path, raw, host="localhost"):
        handler = srv.Handler.__new__(srv.Handler)
        handler.headers = {"Host": host, "Content-Length": str(len(raw))}
        handler.path, handler.rfile = path, io.BytesIO(raw)
        handler.close_connection = False
        replies = []
        handler._json = lambda status, body: replies.append((status, body))
        handler.do_POST()
        return replies[0]

    def test_cli_sanitizes_backend_output_and_does_not_infer_during_resolve(self):
        cli = load("entity-biographical-migrate", "bio_review_cli")
        fake = types.SimpleNamespace(STATE_DIR=str(self.state), run_bridge=self.bridge,
            acquire_lock=lambda: mock.Mock(), load_config=lambda: {}, memory_pressure_normal=lambda: True,
            pick_transport=lambda config: "local", acquire_llm_lock=lambda: mock.Mock(), self_names=lambda config: set(),
            semantic_suggestions=mock.Mock(return_value=lambda *args: []))
        spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
        output = self.state / "preview.json"
        with mock.patch.object(cli.importlib.util, "spec_from_file_location", return_value=spec), \
                mock.patch.object(cli.importlib.util, "module_from_spec", return_value=fake):
            console = io.StringIO()
            with mock.patch("sys.stdout", console):
                self.assertEqual(cli.main(["--preview", "--output", str(output)]), 0)
            report = json.loads(console.getvalue())
            self.assertEqual(report["pending_questions"], 2)
            self.assertNotIn("Wren", console.getvalue())
            self.assertNotIn("AAA-111", console.getvalue())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            choices = self.state / "choices.json"
            payload = answer(self.plan)
            migration.private_save(choices, {"plan_id": self.plan["plan_id"], "answers": {payload["source_uuid"]: payload["answers"]}})
            resolved = self.state / "resolved.json"
            with mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(cli.main(["--resolve", "--plan", str(output), "--decisions", str(choices), "--output", str(resolved)]), 0)
            fake.semantic_suggestions.assert_called_once()
            self.assertEqual(self.bridge.mutations, 0)
            self.assertTrue(output.exists())
            def noisy_backend(ops):
                print("Fictional private body for Wren, AAA-111")
                raise RuntimeError("Fictional private backend diagnostics")
            fake.run_bridge = noisy_backend
            console = io.StringIO()
            with mock.patch("sys.stdout", console):
                self.assertEqual(cli.main(["--preview", "--output", str(self.state / "failure.json")]), 1)
            self.assertEqual(json.loads(console.getvalue()), {"status": "stopped"})
            self.assertNotIn("Wren", console.getvalue())

    def test_endpoint_has_exact_plan_id_origin_gate_and_duplicate_key_rejection(self):
        path = "/api/biographical-plan/" + self.plan["plan_id"]
        raw = json.dumps(answer(self.plan)).encode()
        self.assertEqual(self.post(path.upper(), raw)[0], 404)
        self.assertEqual(self.post(path + "/extra", raw)[0], 404)
        self.assertEqual(self.post(path, raw, host="untrusted.invalid")[0], 403)
        duplicate = b'{"source_uuid":"AAA-111","source_uuid":"CCC-333","answers":{}}'
        self.assertEqual(self.post(path, duplicate)[0], 400)
        result = self.post(path, raw)
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["status"], "migration_waiting")
        self.assertEqual(self.post(path, raw)[0], 409)
