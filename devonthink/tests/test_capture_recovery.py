import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import load, person

capture = load("entity_capture.py", "capture_recovery")
ef = load("entity-filing.py", "filing_capture_recovery")

SOURCE = {"uuid": "SRC-1", "kind": "fact", "name": "Capture", "added_at": "2026-09-30T15:00:00"}
TEXT = "Wren moved to Denver.\nHer daughter starts college in October."


class FakeBridge:
    def __init__(self, fail_after=0):
        self.fail_after = fail_after
        self.mutations = 0
        self.manifest = ""
        self.people = []
        self.bodies = {"SRC-1": "# Capture\n\n" + TEXT, "DAY-1": "# Today\n\n- Handwritten text.\n"}
        self.calls = []

    def mutate(self):
        self.mutations += 1
        if self.mutations == self.fail_after:
            raise RuntimeError("injected crash after mutation")

    def __call__(self, ops):
        out = []
        for op in ops:
            kind = op["op"]
            self.calls.append(kind)
            if kind == "capture_store":
                if self.manifest != op["expected"]:
                    raise RuntimeError("capture operation changed")
                self.manifest = op["value"]
                self.mutate()
                out.append({"uuid": op["uuid"]})
            elif kind == "capture_person":
                if not self.people:
                    self.people.append(person("Wren", "P1", entitytype="Person", entitystatus="active"))
                    self.bodies["P1"] = "# Wren\n\n"
                    self.mutate()
                out.append({"uuid": "P1", "initialized": True})
            elif kind == "capture_append":
                body = self.bodies[op["uuid"]]
                if op["block"] not in body:
                    if op["expected_present"]:
                        raise RuntimeError("capture contribution changed")
                    self.bodies[op["uuid"]] = body + op["block"] + "\n"
                    self.mutate()
                out.append({"uuid": op["uuid"]})
            elif kind == "get_text":
                out.append({"text": self.bodies[op["uuid"]]})
            elif kind == "get_or_create_daily":
                out.append({"uuid": "DAY-1"})
            elif kind == "append_pinned":
                if op["line"] not in self.bodies["DAY-1"]:
                    self.bodies["DAY-1"] += op["line"] + "\n"
                    self.mutate()
                out.append({"uuid": "DAY-1"})
            elif kind == "capture_retire_source":
                self.mutate()
                out.append({"uuid": op["uuid"]})
            elif kind == "mark_filed":
                self.mutate()
                out.append({"uuid": op["uuid"]})
            elif kind == "dump_people":
                out.append(copy.deepcopy(self.people))
            elif kind == "list_candidates":
                out.append({"pending": [], "approved": [], "ignored": []})
            elif kind == "get_source":
                out.append(dict(SOURCE, modified="2026-09-30T16:00:00", capture_operation=self.manifest))
            elif kind == "list_sources":
                out.append([dict(SOURCE, modified="2026-09-30T16:00:00", capture_operation=self.manifest)])
            else:
                raise AssertionError(kind)
        return out


def plan():
    return capture.prepare(SOURCE, "2026-09-29", TEXT,
                           capture.resolve(TEXT, [{"mention": "Wren"}], []), "2026-09-30")


class CaptureRecovery(unittest.TestCase):
    def test_crash_after_each_mutation_replays_one_contribution_and_receipt(self):
        baseline = FakeBridge()
        capture.replay(baseline, plan())
        for failure in range(1, baseline.mutations + 1):
            with self.subTest(failure=failure):
                bridge = FakeBridge(failure)
                with self.assertRaises(RuntimeError):
                    capture.replay(bridge, plan())
                frozen = capture.decode_manifest(bridge.manifest, SOURCE["uuid"])
                capture.replay(bridge, frozen, bridge.manifest)
                frozen = capture.decode_manifest(bridge.manifest, SOURCE["uuid"])
                self.assertEqual(frozen["status"], "filed")
                self.assertEqual(len(bridge.people), 1)
                self.assertEqual(bridge.bodies["P1"].count(":begin -->"), 1)
                self.assertEqual(bridge.bodies["DAY-1"].count("capture-receipt:"), 1)
                self.assertIn("Handwritten text", bridge.bodies["DAY-1"])
                self.assertIn(TEXT.splitlines()[1], bridge.bodies["P1"])
                self.assertNotIn("mdlastcontact", bridge.people[0]["md"])

    def test_modified_owned_contribution_is_not_reapplied(self):
        bridge = FakeBridge()
        capture.replay(bridge, plan())
        frozen = capture.decode_manifest(bridge.manifest, SOURCE["uuid"])
        bridge.bodies["P1"] = "Manual replacement."
        with self.assertRaisesRegex(RuntimeError, "contribution changed"):
            capture.replay(bridge, frozen, bridge.manifest)
        self.assertEqual(bridge.bodies["P1"], "Manual replacement.")

    def test_corrupt_manifest_fails_closed(self):
        for raw in ("bad", "{}", "null", json.dumps(dict(plan(), revision="broken"))):
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                capture.decode_manifest(raw, SOURCE["uuid"])

    def test_manifest_is_bound_to_source(self):
        with self.assertRaises(ValueError):
            capture.decode_manifest(capture.encode_manifest(plan()), "OTHER-SOURCE")

    def test_explicit_boundary_never_defaults_to_today(self):
        self.assertFalse(capture.eligible(SOURCE, ""))
        self.assertFalse(capture.eligible(SOURCE, "2026-09-30T15:00:00"))
        self.assertTrue(capture.eligible(SOURCE, "2026-09-30T14:59:59"))
        with self.assertRaises(ValueError):
            capture.eligible(SOURCE, "yesterday")


class CaptureScan(unittest.TestCase):
    def test_empty_roster_capture_uses_own_policy_and_refreshes_roster(self):
        bridge = FakeBridge()
        state = {"processed": {}, "attempts": {}, "parked": {}}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ef, "run_bridge", bridge), \
                mock.patch.object(ef, "save_state"), \
                mock.patch.object(ef, "self_names", return_value=set()), \
                mock.patch.object(ef.ec, "CANDIDATE_LOCK_FILE", str(Path(tmp) / "candidates.lock")), \
                mock.patch.object(ef, "omlx_available", return_value=True), \
                mock.patch.object(ef, "acquire_llm_lock", return_value=object()), \
                mock.patch.object(ef, "extract_omlx", return_value='{"people":[{"mention":"Wren"}]}') as extract:
            config = {"TRANSPORT": "local", "CAPTURE_AUTO_AFTER": "2026-09-30T14:59:59",
                      "MIN_ROSTER": "1", "SKIP_SOURCE_TITLES": "", "MAX_PER_RUN": "3",
                      "FILING_MODE": "suggest", "IDLE_MINUTES": "0", "SELF_NAME": ""}
            self.assertTrue(ef.scan(config, state, False, None, True))
        self.assertIn(SOURCE["uuid"], state["processed"])
        self.assertNotIn("create_record", bridge.calls)
        self.assertIn("Identify the main subjects", extract.call_args[0][1])
        self.assertNotIn("omit it", extract.call_args[0][1])
        self.assertGreaterEqual(bridge.calls.count("dump_people"), 3)

    def test_source_change_after_writing_does_not_adopt_the_changed_stamp(self):
        bridge = FakeBridge()
        state = {"processed": {}, "attempts": {}, "parked": {}}
        original = bridge.__call__
        def changed_bridge(ops):
            if [o["op"] for o in ops] == ["get_source", "get_text"]:
                return [dict(SOURCE, modified="2026-10-01T00:00:00"), {"text": "Changed source."}]
            return original(ops)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ef, "run_bridge", changed_bridge), \
                mock.patch.object(ef, "save_state"), \
                mock.patch.object(ef, "self_names", return_value=set()), \
                mock.patch.object(ef.ec, "CANDIDATE_LOCK_FILE", str(Path(tmp) / "candidates.lock")):
            ef.file_capture({"SELF_NAME": ""}, state, dict(SOURCE, modified="2026-09-30T15:00:00"),
                            "2026-09-30", TEXT, [{"mention": "Wren"}], False)
        self.assertEqual(state["processed"]["SRC-1"]["modified"], "2026-09-30T15:00:00")

    def test_roster_changed_after_planning_refuses_the_write(self):
        bridge = FakeBridge()
        frozen = plan()
        frozen["subjects"][0]["kind"] = "existing"
        frozen["subjects"][0]["uuid"] = "OLD"
        bridge.people = [person("Wren", "NEW", entitytype="Person")]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ef, "run_bridge", bridge), \
                mock.patch.object(ef, "self_names", return_value=set()), \
                mock.patch.object(ef.ec, "CANDIDATE_LOCK_FILE", str(Path(tmp) / "candidates.lock")):
            result = ef.file_capture({"SELF_NAME": ""}, {}, SOURCE, "2026-09-30", TEXT, None, False,
                                     manifest=frozen, previous=capture.encode_manifest(frozen))
        self.assertEqual(result["reason"], "identity_changed")
        self.assertEqual(bridge.mutations, 0)

    def test_missing_local_state_recovers_from_source_manifest_without_inference(self):
        config = {"TRANSPORT": "local", "CAPTURE_AUTO_AFTER": "2026-09-30T14:59:59",
                  "MIN_ROSTER": "1", "SKIP_SOURCE_TITLES": "", "MAX_PER_RUN": "3",
                  "FILING_MODE": "suggest", "IDLE_MINUTES": "0", "SELF_NAME": ""}
        bridge = FakeBridge()
        bridge.manifest = capture.encode_manifest(plan())
        original = bridge.__call__
        def local_bridge(ops):
            if len(ops) == 1 and ops[0]["op"] == "list_sources":
                return [[dict(SOURCE, entityfiled=True, capture_operation=bridge.manifest)]]
            return original(ops)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ef, "STATE_FILE", str(Path(tmp) / "missing-state.json")), \
                mock.patch.object(ef, "run_bridge", local_bridge), \
                mock.patch.object(ef, "save_state"), \
                mock.patch.object(ef, "self_names", return_value=set()), \
                mock.patch.object(ef.ec, "CANDIDATE_LOCK_FILE", str(Path(tmp) / "lock")), \
                mock.patch.object(ef, "omlx_available", return_value=False) as available, \
                mock.patch.object(ef, "acquire_llm_lock") as inference_lock, \
                mock.patch.object(ef, "extract_omlx") as extract:
            state = ef.load_state()
            self.assertEqual(ef.rebuild_processed_from_dt(state), 0)
            self.assertNotIn("SRC-1", state["processed"])
            self.assertTrue(ef.scan(config, state, False, None, True))
        self.assertIn("SRC-1", state["processed"])
        self.assertEqual(capture.decode_manifest(bridge.manifest, "SRC-1")["status"], "filed")
        available.assert_not_called()
        inference_lock.assert_not_called()
        extract.assert_not_called()

    def test_a_failed_frozen_capture_does_not_abort_a_healthy_source(self):
        config = {"TRANSPORT": "local", "CAPTURE_AUTO_AFTER": "2026-09-30T14:59:59",
                  "MIN_ROSTER": "0", "SKIP_SOURCE_TITLES": "", "MAX_PER_RUN": "3",
                  "FILING_MODE": "auto", "IDLE_MINUTES": "0", "SELF_NAME": ""}
        passive_text = "Wren mentioned the move and the new city during a small meeting today. " * 3
        for corrupt in (True, False):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as tmp:
                bridge = FakeBridge()
                frozen = capture.encode_manifest(plan()) if not corrupt else "bad"
                state = {"processed": {}, "attempts": {}, "parked": {}}
                original = bridge.__call__
                def local_bridge(ops):
                    if ops[0]["op"] == "list_sources":
                        return [[dict(SOURCE, capture_operation=frozen),
                                 {"uuid": "PASSIVE", "kind": "meeting", "name": "Meeting"}]]
                    if ops[0]["op"] == "get_text" and ops[0]["uuid"] == "PASSIVE":
                        return [{"text": passive_text}]
                    if ops[0]["op"] == "capture_person":
                        raise RuntimeError("edited contribution")
                    if ops[0]["op"] == "dump_people" and len(ops) == 3 and ops[1]["op"] == "list_sources":
                        return [[], [dict(SOURCE, capture_operation=frozen),
                                     {"uuid": "PASSIVE", "kind": "meeting", "name": "Meeting"}],
                                {"pending": [], "approved": [], "ignored": []}]
                    return original(ops)
                with mock.patch.object(ef, "run_bridge", local_bridge), \
                        mock.patch.object(ef, "save_state"), \
                        mock.patch.object(ef, "self_names", return_value=set()), \
                        mock.patch.object(ef.ec, "CANDIDATE_LOCK_FILE", str(Path(tmp) / "lock")), \
                        mock.patch.object(ef, "omlx_available", return_value=True), \
                        mock.patch.object(ef, "acquire_llm_lock", return_value=object()), \
                        mock.patch.object(ef, "extract_omlx", return_value='{"people":[]}'), \
                        mock.patch.object(ef, "file_source") as filed:
                    self.assertTrue(ef.scan(config, state, False, None, True))
                self.assertEqual(filed.call_count, 1)
                self.assertEqual(state["attempts"]["SRC-1"]["count"], 1)

    def test_frozen_creation_refreshes_roster_before_passive_extraction(self):
        config = {"TRANSPORT": "local", "CAPTURE_AUTO_AFTER": "2026-09-30T14:59:59",
                  "MIN_ROSTER": "0", "SKIP_SOURCE_TITLES": "", "MAX_PER_RUN": "3",
                  "FILING_MODE": "auto", "IDLE_MINUTES": "0", "SELF_NAME": ""}
        bridge = FakeBridge()
        bridge.manifest = capture.encode_manifest(plan())
        state = {"processed": {}, "attempts": {}, "parked": {}}
        original = bridge.__call__
        def local_bridge(ops):
            if ops[0]["op"] == "dump_people" and len(ops) == 3 and ops[1]["op"] == "list_sources":
                return [[], [dict(SOURCE, capture_operation=bridge.manifest),
                             {"uuid": "PASSIVE", "kind": "meeting", "name": "Meeting"}],
                        {"pending": [], "approved": [], "ignored": []}]
            if ops[0]["op"] == "get_text" and ops[0]["uuid"] == "PASSIVE":
                return [{"text": "Wren moved to Denver and mentioned this to us during our small meeting today. " * 3}]
            return original(ops)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ef, "run_bridge", local_bridge), \
                mock.patch.object(ef, "save_state"), \
                mock.patch.object(ef, "self_names", return_value=set()), \
                mock.patch.object(ef.ec, "CANDIDATE_LOCK_FILE", str(Path(tmp) / "lock")), \
                mock.patch.object(ef, "omlx_available", return_value=True), \
                mock.patch.object(ef, "acquire_llm_lock", return_value=object()), \
                mock.patch.object(ef, "extract_omlx", return_value='{"people":[{"name":"Wren","facts":[{"fact":"Moved to Denver."}]}]}'), \
                mock.patch.object(ef, "divert_new_plans", side_effect=lambda plans, *a: (plans, 0)), \
                mock.patch.object(ef, "file_source") as filed:
            self.assertTrue(ef.scan(config, state, False, None, True))
        self.assertEqual(filed.call_args[0][4][0]["kind"], "existing")

    def test_receipt_is_excluded_from_daily_extraction(self):
        manifest = plan()
        manifest["subjects"][0]["uuid"] = "P1"
        line = capture.receipt_line(manifest)
        self.assertTrue(ef.be.is_machine_bullet(line))
        self.assertNotIn("Saved your note", ef.strip_generated_sections("# Today\n\n" + line))
