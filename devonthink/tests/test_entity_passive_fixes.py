import copy
import io
import os
import subprocess
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from helpers import load
import test_entity_passive_review as review_fixture

Lock = review_fixture.Lock

import entity_passive as passive

ef = load("entity-filing.py", "passive_fixes_filing")
srv = load("entity-review-server.py", "passive_fixes_server")


class PassiveFixes(unittest.TestCase):
    setUp = review_fixture.PassiveReview.setUp
    bridge = review_fixture.PassiveReview.bridge

    def context(self, module=ef):
        stack = ExitStack()
        stack.enter_context(patch.object(module, "run_bridge", self.bridge))
        stack.enter_context(patch.object(module, "load_config", return_value={"TRANSPORT": "off", "IDLE_MINUTES": "0"}))
        stack.enter_context(patch.object(module, "acquire_llm_lock", return_value=Lock()))
        stack.enter_context(patch.object(module.ec, "acquire_candidates_lock", return_value=Lock()))
        stack.enter_context(patch.object(module, "save_state"))
        return stack

    def test_file_source_oversized_quarantine_retry_integrity_and_stale_source(self):
        plan = {"kind": "existing", "uuid": self.person["uuid"], "name": self.person["name"], "aliases": "Wren", "md": self.person["md"], "facts": [("2026-09-01", "Qualified fictional fact " * 120000)], "updates": {}}
        state = {"processed": {}, "attempts": {}, "parked": {}}
        with self.context(), patch.object(ef, "extract_omlx", side_effect=AssertionError("No re-extraction")):
            self.assertFalse(ef.file_source({}, state, self.source, "2026-09-01", [plan], "review", False, self.text))
        created = next(op for op in self.writes if op["op"] == "create_record")
        self.proposal = created["text"]
        pointer = passive.read(ef.proposal_ops(self.proposal))
        self.assertEqual(pointer["reason"], "oversized_review")
        complete = passive.load_evidence(pointer)
        expected = ef.ops_for_plan(plan, dict(self.source, text_revision=passive.bio.digest(self.text)), "2026-09-01")
        self.assertEqual(complete["inputs"][:len(expected)], expected)
        self.assertEqual(complete["sources"][0]["input"], ef.cap_words(self.text))
        self.assertFalse(any(op["op"] == "mark_filed" for op in self.writes))
        path = Path(passive.evidence_path(pointer["evidence"]))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        data = path.read_bytes()
        for changed in (None, b"{}"):
            if changed is None:
                path.unlink()
            else:
                path.write_bytes(changed)
            with self.assertRaises(ValueError):
                passive.load_evidence(pointer)
            path.write_bytes(data)
        path.unlink()
        path.symlink_to(Path(passive.evidence_directory()) / "foreign.json")
        with self.assertRaises(ValueError):
            passive.load_evidence(pointer)
        path.unlink()
        path.write_bytes(data)
        for identity in ("../foreign", "A" * 64, "f" * 64):
            with self.assertRaises(ValueError):
                passive.load_evidence(dict(pointer, evidence=identity))
        self.writes.clear()
        config = {"TRANSPORT": "local", "IDLE_MINUTES": "0"}
        with self.context(), patch.object(ef, "omlx_available", return_value=True), patch.object(ef, "semantic_suggestions", return_value=lambda *args: []), patch.object(ef, "extract_omlx", side_effect=AssertionError("No extraction")):
            self.assertFalse(ef.retry_passive_proposals(config, False, True))
        self.assertEqual(self.writes, [])
        self.text = "Changed fictional source."
        with self.context(), patch.object(ef, "omlx_available", return_value=True), patch.object(ef, "semantic_suggestions", side_effect=AssertionError("Stale source must stop before comparison")):
            self.assertFalse(ef.retry_passive_proposals(config, False, True))
        self.assertEqual(self.writes, [])
        with self.context():
            ef.apply_approved(False)
        self.assertEqual(self.writes, [])

    def test_comparison_growth_retains_preanalysis_evidence(self):
        initial = passive.prepare(self.ops, self.envelope["sources"], [self.person], analyze_now=False)
        analyzed = passive.analyze(initial, [self.person], lambda *args: [self.envelope["questions"][0]["candidates"][0]["id"]])
        limit = len(passive.body(initial).encode()) + 1
        self.assertGreater(len(passive.body(analyzed).encode()), limit)
        with patch.object(passive, "MAX_BYTES", limit):
            pointer = passive.prepare(self.ops, self.envelope["sources"], [self.person], lambda *args: [self.envelope["questions"][0]["candidates"][0]["id"]])
            self.assertEqual(pointer["reason"], "oversized_review")
            self.assertEqual(passive.load_evidence(pointer), initial)
            with self.assertRaises(ValueError):
                passive.executable(pointer)
        self.assertNotIn("variant", initial)

    def origin(self):
        data = ef.ec.new_candidate("Wren Vale")
        ef.ec.upsert_sighting(data, "dt:AAA-111", {"person": "Wren Vale", "name": self.source["name"], "kind": "meeting", "date": "2026-09-01", "hash": passive.bio.digest(self.text), "facts": [["2026-09-01", self.text]], "updates": {}, "interacted": False})
        candidate = {"uuid": "EEE-555", "name": "Candidate: Wren Vale", "text": ef.ec.render_candidate(data), "md": {"mdtracktarget": self.person["uuid"]}}
        original = self.bridge
        def bridge(ops):
            results = []
            for op in ops:
                if op.get("uuid") == candidate["uuid"] and op["op"] == "get_text":
                    results.append({"text": candidate["text"]})
                elif op.get("uuid") == candidate["uuid"] and op["op"] == "get_fields":
                    results.append({"md": candidate["md"]})
                elif op["op"] == "list_candidates":
                    results.append({"pending": [candidate] if candidate.get("pending") else [], "approved": [] if candidate.get("pending") else [candidate], "ignored": []})
                else:
                    results += original([op])
                    if op.get("uuid") == candidate["uuid"] and op["op"] == "move_to":
                        candidate["pending"] = op["group"] == ef.ec.CANDIDATES_PATH
                    if op.get("uuid") == candidate["uuid"] and op["op"] == "set_field":
                        candidate["md"]["md" + op["field"]] = op["value"]
            return results
        self.bridge = bridge
        with self.context():
            ef.prepare_candidate_passive(candidate, data, self.person["uuid"], [self.person], False)
        self.proposal = next(op["text"] for op in self.writes if op["op"] == "create_record")
        self.writes.clear()
        return candidate

    def test_candidate_reject_returns_pending_and_actual_promotion_does_not_recreate(self):
        candidate = self.origin()
        with self.context(srv.ef), patch.object(srv.ef, "acquire_lock", return_value=Lock()), patch.object(srv.scheduler, "kick"):
            srv.handle_proposal("DDD-444", {"action": "reject"})
        self.assertTrue(candidate["pending"])
        self.assertEqual(candidate["md"]["mdtracktarget"], "")
        self.writes.clear()
        with self.context():
            ef.promote_candidates(False)
        self.assertEqual(self.writes, [])

    def test_changed_candidate_reject_refuses_all_writes(self):
        candidate = self.origin()
        candidate["text"] += "\nFresh queued evidence."
        with self.context(srv.ef), patch.object(srv.ef, "acquire_lock", return_value=Lock()), patch.object(srv.scheduler, "kick"):
            with self.assertRaises(srv.RequestError):
                srv.handle_proposal("DDD-444", {"action": "reject"})
        self.assertEqual(self.writes, [])

    def test_candidate_apply_holds_lock_against_actual_upsert(self):
        self.origin()
        held = passive.read(ef.proposal_ops(self.proposal))
        held = passive.analyze(held, [self.person], lambda *args: [])
        self.proposal = passive.body(held)
        mutex = threading.Lock()
        started, finished = threading.Event(), threading.Event()
        writes = self.bridge
        worker = None
        class ThreadLock:
            def close(self):
                mutex.release()
        def acquire():
            mutex.acquire()
            return ThreadLock()
        def bridge(ops):
            nonlocal worker
            if ops[0]["op"] == "passive_plan":
                def upsert():
                    started.set()
                    ef.ec.upsert_mentions(writes, [{"name": "Wren Vale", "email": "", "sid": "cal:fictional", "sighting": {"person": "Wren Vale", "date": "2026-09-02", "kind": "calendar", "name": "Fictional appointment", "facts": [], "updates": {}, "interacted": True}}], ef.log)
                    finished.set()
                worker = threading.Thread(target=upsert)
                worker.start()
                self.assertTrue(started.wait(2))
                self.assertFalse(finished.wait(.05))
            return writes(ops)
        with patch.object(ef.ec, "acquire_candidates_lock", side_effect=acquire), patch.object(ef, "run_bridge", bridge):
            ef.apply_approved(False)
            worker.join(5)
        self.assertTrue(finished.is_set())
        self.assertTrue(any(op["op"] == "passive_plan" for op in self.writes))

    def test_scheduler_marker_and_worker_resource_gates(self):
        calls = []
        scheduler = srv.ApplyScheduler()
        scheduler._lock_free = lambda: True
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)
        with patch.object(srv.subprocess, "run", side_effect=run):
            scheduler.kick(0)
            scheduler._thread.join(3)
        command, kwargs = calls[0]
        self.assertIn("--apply-only", command)
        self.assertEqual(kwargs["env"]["ENTITY_FILING_SCHEDULED"], "1")
        self.assertEqual(kwargs["env"]["PIPELINE_MANUAL"], "1")
        for blocked in ("should-run-background-job", "should-run-dt-driver"):
            def gated(command, **kwargs):
                return subprocess.CompletedProcess(command, 1 if command[0].endswith(blocked) else 0)
            with patch.dict(os.environ, ENTITY_FILING_SCHEDULED="1"), patch.object(ef.sys, "argv", ["entity-filing.py", "--apply-only"]), patch.object(ef.subprocess, "run", side_effect=gated), patch.object(ef, "load_config", side_effect=AssertionError("Resource gate must precede inference")):
                ef.main()

    def test_legacy_edit_discriminator_survives_removed_fence(self):
        self.proposal = "# Legacy\n\n```json\n" + passive.bio.encoded(self.ops) + "\n```\n"
        payload = {"people": [{"name": "Wren Vale", "facts": [{"date": "2026-09-01", "fact": self.text}]}]}
        with patch.object(srv.ef, "load_config", return_value={"SELF_NAME": ""}):
            srv.approve_with_edits("DDD-444", payload, False, self.bridge)
        self.assertEqual(self.writes[0]["value"], "Entity Filing Proposal Passive v1")
        self.proposal = "# Edited fence removed\n\n```json\n[]\n```\n"
        self.writes.clear()
        with self.context():
            ef.apply_approved(False)
        self.assertEqual(self.writes, [])

    def test_scheduled_apply_only_recovery_and_success_accounting(self):
        for healthy in (False, True):
            with patch.dict(os.environ, ENTITY_FILING_SCHEDULED="1"), patch.object(ef.sys, "argv", ["entity-filing.py", "--apply-only"]), patch.object(ef.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), patch.object(ef, "acquire_lock", return_value=Lock()), patch.object(ef, "load_config", return_value={}), patch.object(ef, "load_state", return_value={}), patch.object(ef.os.path, "exists", return_value=True), patch.object(ef, "retry_passive_proposals", return_value=healthy) as retry, patch.object(ef, "promote_candidates"), patch.object(ef, "apply_approved"), patch.object(ef.capture_reminders, "sync"), patch.object(ef, "should_record_success", return_value=True), patch.object(ef, "record_success") as success:
                ef.main()
                retry.assert_called_once_with({}, False, False)
                self.assertEqual(success.called, healthy)

    def test_initial_extraction_then_empty_choices_durably_waits(self):
        import json
        extraction = {"people": [{"name": "Wren Vale", "facts": [{"date": "2026-09-01", "fact": self.text}], "updates": {}}], "events": []}
        replies = [io.StringIO(json.dumps({"choices": [{"message": {"content": json.dumps(extraction)}}]})), io.StringIO('{"choices":[]}')]
        config = {"OMLX_MODEL": "fictional", "OMLX_URL": "http://fictional.invalid", "TRANSPORT": "local"}
        state = {"processed": {}, "attempts": {}, "parked": {}}
        self.proposal = "# Unrelated legacy proposal"
        with self.context(), patch.object(ef, "_omlx_headers", return_value={}), patch.object(ef.urllib.request, "urlopen", side_effect=replies):
            people, events = ef.parse_extraction(ef.extract_omlx(config, "Fictional extraction prompt"))
            plans = ef.build_person_plans(people, ef.roster_index([self.person]), [], [self.person], "2026-09-01")
            self.assertFalse(ef.file_source(config, state, self.source, "2026-09-01", plans, "review", False, self.text))
        created = next(op for op in self.writes if op["op"] == "create_record")
        held = passive.read(ef.proposal_ops(created["text"]))
        self.assertEqual(held["status"], "waiting")
        self.assertEqual(held["inputs"][0]["assertions"][0]["reference"]["evidence"], self.text)
        self.assertEqual(passive.analyze(held, [self.person], lambda *args: [])["status"], "ready")

    def test_generic_malformed_legacy_reject_and_undo_stay_available(self):
        bridge = self.bridge
        self.proposal = "# Legacy malformed\n\n```json\nnot json\n```\n"
        def legacy(ops):
            return [{"md": {}}] if ops[0]["op"] == "get_fields" else bridge(ops)
        def combined(ops):
            results = []
            for op in ops:
                results += legacy([op])
            return results
        with patch.object(srv.ef, "run_bridge", combined), patch.object(srv.ef, "acquire_lock", return_value=Lock()), patch.object(srv.ec, "acquire_candidates_lock", return_value=Lock()), patch.object(srv.scheduler, "kick"):
            srv.handle_proposal("DDD-444", {"action": "reject"})
            srv.handle_proposal("DDD-444", {"action": "undo"})
        self.assertEqual([op["op"] for op in self.writes], ["trash", "move_to"])
