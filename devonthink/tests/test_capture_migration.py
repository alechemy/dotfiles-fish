import json
import tempfile
import unittest
from pathlib import Path

from helpers import load
from test_capture_corrections import CorrectionBridge
from test_capture_reminders import Things

capture = load("entity_capture.py", "migration_capture")
migration = load("entity_capture_migration.py", "capture_migration_test")


class MigrationBridge(CorrectionBridge):
    def __init__(self):
        super().__init__()
        self.sources = [{"uuid": "SRC-1", "kind": "fact", "name": "Capture", "added": "2026-09-29", "modified": "initial"},
                        {"uuid": "SRC-2", "kind": "fact", "name": "Capture", "added": "2026-09-29", "modified": "initial"}]
        self.bodies["SRC-1"] = "# Capture\n\nWren moved to Denver."
        self.bodies["SRC-2"] = "# Capture\n\nWren started college."
        self.manifests = {}

    def __call__(self, ops):
        out = []
        for op in ops:
            kind = op["op"]
            if kind == "list_fact_captures":
                out.append([dict(s, capture_operation=self.manifests.get(s["uuid"], "")) for s in self.sources])
            elif kind == "list_review": out.append({"pending": [], "approved": []})
            elif kind == "capture_store":
                if self.manifests.get(op["uuid"], "") != op["expected"] or capture.source_text(self.bodies[op["uuid"]]) != op["text"]:
                    raise RuntimeError("source changed")
                self.manifests[op["uuid"]] = op["value"]
                out.append({"uuid": op["uuid"]})
            else:
                out.extend(super().__call__([op]))
        return out


class Migration(unittest.TestCase):
    def test_plans_have_distinct_durable_identity_and_scope(self):
        def plan(ids): return {"version": 1, "items": [{"source_uuid": uuid, "modified": "before"} for uuid in ids]}
        first = migration.journal_for_plan(plan(["A", "B"]))
        narrowed = migration.journal_for_plan(plan(["B"]))
        another = migration.journal_for_plan(plan(["C"]))
        self.assertEqual(len({j["plan_id"] for j in (first, narrowed, another)}), 3)
        self.assertEqual(first["plan_id"], migration.journal_for_plan(plan(["B", "A"]))["plan_id"])
        self.assertEqual(narrowed["scope"], ["B"])
        with self.assertRaises(ValueError): migration.journal_for_plan(plan(["A", "A"]))

    def test_two_sources_for_the_same_new_person_use_the_refreshed_roster(self):
        bridge = MigrationBridge()
        journal = {"version": 1, "items": {}}
        seen = []
        def extract(source, text, people):
            seen.append(len(people))
            return [{"mention": "Wren"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.json"
            migration.apply(bridge, extract, {}, journal, path)
            saved = json.loads(path.read_text())
            migration.apply(bridge, extract, {}, saved, path)
        self.assertEqual(seen, [0, 1])
        self.assertEqual(len(bridge.people), 1)
        self.assertEqual(bridge.bodies["P1"].count(":begin -->"), 2)
        self.assertEqual([v["status"] for v in journal["items"].values()], ["done", "done"])
        self.assertEqual(capture.decode_manifest(bridge.manifests["SRC-2"], "SRC-2")["subjects"][0]["kind"], "existing")

    def test_apply_obeys_scope_and_expected_source_revision(self):
        bridge = MigrationBridge()
        journal = {"version": 1, "items": {}, "scope": ["SRC-2"], "expected_modified": {"SRC-2": "stale"}}
        with tempfile.TemporaryDirectory() as tmp:
            migration.apply(bridge, lambda *args: self.fail("stale source inferred"), {}, journal, Path(tmp) / "journal.json")
        self.assertEqual(bridge.manifests, {})
        self.assertNotIn("SRC-1", journal["items"])
        self.assertEqual(journal["items"]["SRC-2"]["disposition"], "source_changed")

    def test_deleted_legacy_task_finishes_cleanup_without_deciding_data(self):
        things = Things()
        things.find_projects = lambda title: []
        journal = {"legacy_tasks": {"GONE": "waiting_for_token"}}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(migration.retire_legacy_tasks(things, "Entity Filing", journal, Path(tmp) / "journal.json"))
        self.assertEqual(journal["legacy_tasks"]["GONE"], "terminal")
        self.assertEqual(things.updates, [])

    def test_unreadable_things_database_is_not_confirmed_task_deletion(self):
        things = Things()
        things.find_projects = lambda title: []
        things.readable = False
        journal = {"legacy_tasks": {"UNKNOWN": "waiting_for_token"}}
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RuntimeError):
            migration.retire_legacy_tasks(things, "Entity Filing", journal, Path(tmp) / "journal.json")
        self.assertEqual(journal["legacy_tasks"]["UNKNOWN"], "waiting_for_token")
