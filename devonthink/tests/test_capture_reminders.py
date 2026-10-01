import copy
import json
import tempfile
import unittest
from pathlib import Path

from helpers import load
from test_capture_recovery import SOURCE, TEXT, plan

capture = load("entity_capture.py", "capture_reminders_capture")
reminders = load("entity_capture_reminders.py", "capture_reminders_test")


class Things:
    def __init__(self):
        self.rows = {}
        self.updates = []
        self.readable = True

    def ensure_project(self, title): return "PROJECT"
    def auth_token(self): return "token"
    def read_project_tasks(self, uuid): return list(self.rows.values())
    def add_todo_params(self, project, title, notes): return {"notes": notes}
    def build_url(self, command, params): return json.dumps(params)
    def read_tasks(self, uuids):
        if not self.readable:
            raise RuntimeError("database unavailable")
        return {uuid: self.rows[uuid] for uuid in uuids if uuid in self.rows}
    def add_todo(self, project, title, notes, marker):
        uuid = "TASK-" + str(len(self.rows) + 1)
        self.rows[uuid] = {"uuid": uuid, "notes": notes, "status": 0, "trashed": 0}
        return uuid
    def update_todo(self, uuid, token, params, expected):
        self.updates.append(uuid)
        if not token:
            return False
        self.rows[uuid]["status"] = 2
        return True


class Bridge:
    def __init__(self):
        self.manifest = capture.pending(SOURCE, "2026-09-29", TEXT, [{"mention": "Wren"}], capture.outcome("unresolved", "ambiguous_name"), "2026-09-30")
        self.text = TEXT
        self.listed = True
        self.readable = True
        self.calls = []

    def __call__(self, ops):
        out = []
        for op in ops:
            self.calls.append(op["op"])
            if op["op"] == "list_captures":
                out.append([{"uuid": "SRC-1", "operation": capture.encode_manifest(self.manifest), "text": self.text}] if self.listed else [])
            elif op["op"] == "get_source":
                if not self.readable: raise RuntimeError("source unavailable")
                out.append({"capture_operation": capture.encode_manifest(self.manifest)})
            elif op["op"] == "get_text":
                out.append({"text": "# Capture\n\n" + self.text})
            elif op["op"] == "capture_store":
                if capture.encode_manifest(self.manifest) != op["expected"] or op["text"] != self.text:
                    raise RuntimeError("source changed")
                self.manifest = json.loads(op["value"])
                out.append({"uuid": "SRC-1"})
            else: raise AssertionError(op)
        return out


class Reminders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.bridge = Bridge()
        self.things = Things()

    def sync(self): reminders.sync(self.bridge, self.things, {"THINGS_SYNC": "on"}, self.path)

    def test_reminder_is_generic_and_dismissal_does_not_decide_data(self):
        self.sync()
        self.assertNotIn(TEXT, self.things.rows["TASK-1"]["notes"])
        before = copy.deepcopy(self.bridge.manifest)
        self.things.rows["TASK-1"]["status"] = 3
        self.sync()
        self.assertEqual(self.bridge.manifest, before)
        self.assertEqual(len(self.things.rows), 1)
        self.things.rows.clear()
        self.sync()
        self.assertEqual(self.bridge.manifest, before)
        self.assertEqual(self.things.rows, {})

    def test_missing_registry_row_is_verified_before_cancellation(self):
        self.sync()
        self.bridge.listed = False
        self.sync()
        self.assertEqual(self.things.updates, [])
        self.assertIn("get_source", self.bridge.calls)
        self.bridge.manifest.update(status="undone", subjects=[])
        self.sync()
        self.assertEqual(self.things.rows["TASK-1"]["status"], 2)

    def test_retained_existing_filing_closes_its_question_reminder(self):
        self.bridge.manifest["reason"] = "legacy_filing"
        self.sync()
        self.bridge.manifest.update(status="retained", legacy_filings=[
            {"uuid": "P1", "body_revision": capture.revision("Original filing")}])
        self.sync()
        self.assertEqual(self.things.rows["TASK-1"]["status"], 2)
        self.assertEqual(json.loads(self.path.read_text())["tasks"], {})
        self.assertEqual(self.bridge.manifest["status"], "retained")

    def test_unreadable_source_never_cancels_a_question(self):
        self.sync()
        self.bridge.listed = False
        self.bridge.readable = False
        self.sync()
        self.assertEqual(self.things.updates, [])

    def test_new_revision_retires_old_reminder_and_offers_one_new_reminder(self):
        self.sync()
        self.bridge.text = "Wren moved to Oslo."
        self.bridge.manifest["changed_text"] = self.bridge.text
        self.sync()
        self.assertEqual(self.things.rows["TASK-1"]["status"], 2)
        self.assertEqual(self.things.rows["TASK-2"]["status"], 0)
        self.assertIn(capture.revision(self.bridge.text), self.things.rows["TASK-2"]["notes"])
        self.sync()
        self.assertEqual(len(self.things.rows), 2)

    def test_missing_token_keeps_obsolete_task_reference_until_cleanup(self):
        self.sync()
        self.things.auth_token = lambda: None
        self.bridge.text = "Wren moved to Oslo."
        self.sync()
        self.assertIn("TASK-1", json.loads(self.path.read_text())["obsolete"])
        self.things.auth_token = lambda: "token"
        self.sync()
        self.assertEqual(self.things.rows["TASK-1"]["status"], 2)
        self.assertEqual(json.loads(self.path.read_text())["obsolete"], {})

    def test_terminal_obsolete_task_does_not_drop_current_cancellation_retry(self):
        self.sync()
        self.things.auth_token = lambda: None
        self.bridge.text = "Wren moved to Oslo."
        self.sync()
        self.things.rows["TASK-1"]["status"] = 2
        self.bridge.manifest.update(status="undone", subjects=[])
        self.sync()
        state = json.loads(self.path.read_text())
        self.assertEqual(state["obsolete"], {})
        self.assertEqual(state["tasks"]["SRC-1"]["task_uuid"], "TASK-2")
        self.assertEqual(self.things.rows["TASK-2"]["status"], 0)
        self.things.auth_token = lambda: "token"
        self.sync()
        self.assertEqual(self.things.rows["TASK-2"]["status"], 2)
        self.assertEqual(json.loads(self.path.read_text())["tasks"], {})

    def test_source_edit_after_filing_uses_the_new_question_revision(self):
        manifest = plan()
        manifest.update(status="filed", receipt=True)
        manifest["subjects"][0].update(uuid="P1", applied=True)
        self.bridge.manifest = manifest
        self.bridge.text = "Wren moved to Oslo."
        self.sync()
        self.assertIn(capture.revision(self.bridge.text), self.things.rows["TASK-1"]["notes"])
        self.assertEqual(self.bridge.manifest["status"], "revision_question")

    def test_read_only_and_disabled_sync_do_not_write(self):
        reminders.sync(self.bridge, self.things, {"THINGS_SYNC": "off"}, self.path)
        reminders.sync(self.bridge, self.things, {"THINGS_SYNC": "on"}, self.path, dry_run=True)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.things.rows, {})
