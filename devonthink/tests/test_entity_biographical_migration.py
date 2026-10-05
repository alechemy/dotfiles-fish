import copy
import json
import tempfile
import unittest
from pathlib import Path

from helpers import load, person

capture = load("entity_capture.py", "capture_bio_migration")
migration = load("entity_biographical_migration.py", "bio_migration")


class Bridge:
    def __init__(self):
        self.people = [person("Wren", "BBB-222", entitytype="Person", entitystatus="active")]
        self.bodies = {"BBB-222": "# Wren\n\nManual paragraph.\n"}
        self.operations = {}
        self.mutations = 0
        self.failure = 0
        self.facts = []
        for uuid in ("AAA-111", "CCC-333"):
            text = "Wren has two siblings."
            result = capture.resolve(text, [{"mention": "Wren"}], self.people)
            manifest = capture.prepare({"uuid": uuid}, "2026-09-01", text, result, "2026-09-02")
            manifest.update(status="filed", receipt=True)
            manifest["subjects"][0]["applied"] = True
            self.operations[uuid] = capture.encode_manifest(manifest)
            self.bodies[uuid] = "# Capture\n\n" + text
            self.bodies["BBB-222"] += "\n\n" + manifest["subjects"][0]["contribution"]["block"] + "\n"
        self.facts = [{"uuid": "AAA-111", "capture_operation": self.operations["AAA-111"]}]

    def __call__(self, ops):
        out = []
        for op in ops:
            kind, uuid = op["op"], op.get("uuid")
            if kind == "dump_people":
                out.append([dict(copy.deepcopy(p), **({"body": self.bodies[p["uuid"]]} if op.get("include_bodies") else {})) for p in self.people])
            elif kind == "list_registered_captures":
                out.append([{"uuid": key, "operation": value} for key, value in self.operations.items()])
            elif kind == "list_fact_captures":
                out.append(copy.deepcopy(self.facts))
            elif kind == "list_candidates":
                out.append({"pending": [], "approved": [], "ignored": []})
            elif kind == "get_text":
                out.append({"text": self.bodies[uuid]})
            elif kind == "get_fields":
                out.append({"fields": {field: self.operations.get(uuid, "") if field == "captureoperation" else
                    "Person" if field == "entitytype" and uuid == "BBB-222" else "" for field in op["fields"]}})
            elif kind == "biographical_migration_write":
                actual = {"body": self.bodies[uuid], "operation": self.operations.get(uuid, "")}
                if actual != op["before"]:
                    raise ValueError("Synthetic state changed.")
                self.bodies[uuid] = op["after"]["body"]
                if uuid in self.operations:
                    self.operations[uuid] = op["after"]["operation"]
                self.mutations += 1
                if self.failure == self.mutations:
                    raise RuntimeError("Synthetic crash after write.")
                out.append({"uuid": uuid})
            else:
                raise AssertionError(kind)
        return out


class Migration(unittest.TestCase):
    def test_registered_capture_outside_facts_and_shared_targets_use_chained_states(self):
        import entity_biographical as bio
        bridge = Bridge()
        plan = migration.preview(bridge, lambda *args: [])
        self.assertEqual(plan["counts"]["upgraded_captures"], 2)
        self.assertIn("CCC-333", plan["scope"])
        migration.validate(plan)
        with tempfile.TemporaryDirectory() as directory:
            report = migration.transact(bridge, plan, Path(directory) / "journal.json", Path(directory) / "fence.json")
            self.assertEqual(report["status"], "applied")
            rows = bio.render(bridge.bodies["BBB-222"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0]["data"]["references"]), 2)
            self.assertNotIn("### Captured", bridge.bodies["BBB-222"])
            self.assertIn("Manual paragraph", bridge.bodies["BBB-222"])
            self.assertEqual(migration.preview(bridge)["steps"], [])

    def test_recovery_and_rollback_after_every_mutation_are_frozen(self):
        baseline = Bridge()
        plan = migration.preview(baseline, lambda *args: [])
        for failure in range(1, len(plan["steps"]) + 1):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                bridge = Bridge()
                original_bodies, original_ops = copy.deepcopy(bridge.bodies), copy.deepcopy(bridge.operations)
                bridge.failure = failure
                journal, fence = Path(directory) / "journal.json", Path(directory) / "fence.json"
                with self.assertRaises(RuntimeError):
                    migration.transact(bridge, plan, journal, fence)
                self.assertTrue(fence.exists())
                self.assertEqual(fence.stat().st_mode & 0o777, 0o600)
                self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
                bridge.failure = 0
                migration.transact(bridge, json.loads(json.dumps(plan)), journal, fence)
                self.assertFalse(fence.exists())
                migration.transact(bridge, plan, journal, fence, rollback=True)
                self.assertEqual(bridge.bodies, original_bodies)
                self.assertEqual(bridge.operations, original_ops)

    def test_v1_quoted_ownership_migrates_and_rollback_restores_exact_body(self):
        import entity_biographical as bio
        text = ('Wren likes examples 😀.\n> <!-- bio:v2:literal -->\n'
                '  > > <!-- fact:abcd -->\n`<!-- capture:example:begin -->`\n'
                '<\u2060!-- bio:v2:already escaped --> &lt;!-- fact:abcd --&gt;')
        for ending in ("\n", "\r", "\r\n"):
            with self.subTest(ending=repr(ending)), tempfile.TemporaryDirectory() as directory:
                bridge = Bridge()
                bridge.operations = {}
                bridge.bodies = {"BBB-222": "# Wren\n\nManual paragraph.\n"}
                for uuid in ("AAA-111", "CCC-333"):
                    resolved = capture.resolve(text, [{"mention": "Wren"}], bridge.people)
                    manifest = capture.prepare({"uuid": uuid}, "2026-09-01", text, resolved, "2026-09-02")
                    manifest.update(status="filed", receipt=True)
                    manifest["subjects"][0]["applied"] = True
                    bridge.operations[uuid] = capture.encode_manifest(manifest)
                    bridge.bodies[uuid] = ("# Capture\n\n" + text).replace("\n", ending)
                    bridge.bodies["BBB-222"] += "\n\n" + manifest["subjects"][0]["contribution"]["block"] + "\n"
                bridge.bodies["BBB-222"] = bridge.bodies["BBB-222"].replace("\n", ending)
                bridge.facts = []
                before_bodies, before_ops = copy.deepcopy(bridge.bodies), copy.deepcopy(bridge.operations)
                plan = migration.preview(bridge, lambda *args: [])
                self.assertEqual(plan["counts"]["upgraded_captures"], 2)
                self.assertEqual(plan["counts"]["conflicted"], 0)
                journal, fence = Path(directory) / "journal", Path(directory) / "fence"
                migration.transact(bridge, plan, journal, fence)
                rows = bio.render(bridge.bodies["BBB-222"], "BBB-222")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["visible"], bio.relative(text, "Wren"))
                self.assertTrue(all(ref["evidence"] == text for ref in rows[0]["data"]["references"]))
                self.assertEqual(len(rows[0]["data"]["references"]), 2)
                self.assertEqual(bridge.bodies["AAA-111"], before_bodies["AAA-111"])
                for uuid in bridge.operations:
                    upgraded = capture.decode_manifest(bridge.operations[uuid], uuid)
                    self.assertEqual(upgraded["version"], 2)
                    self.assertEqual(upgraded["history"][-1]["version"], 1)
                migration.transact(bridge, plan, journal, fence, rollback=True)
                self.assertEqual(bridge.bodies, before_bodies)
                self.assertEqual(bridge.operations, before_ops)

    def test_interrupted_rollback_recovers_each_reverse_mutation(self):
        for failure in range(1, 7):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                bridge = Bridge()
                before_bodies, before_ops = copy.deepcopy(bridge.bodies), copy.deepcopy(bridge.operations)
                plan = migration.preview(bridge, lambda *args: [])
                self.assertEqual(len(plan["steps"]), 6)
                journal, fence = Path(directory) / "journal", Path(directory) / "fence"
                migration.transact(bridge, plan, journal, fence)
                bridge.failure = bridge.mutations + failure
                with self.assertRaises(RuntimeError):
                    migration.transact(bridge, plan, journal, fence, rollback=True)
                self.assertTrue(fence.exists())
                bridge.failure = 0
                result = migration.transact(bridge, plan, journal, fence, rollback=True)
                self.assertEqual(result["status"], "rolled_back")
                self.assertFalse(fence.exists())
                self.assertEqual(bridge.bodies, before_bodies)
                self.assertEqual(bridge.operations, before_ops)

    def test_copied_person_ownership_is_conflicted_without_body_mutation(self):
        import entity_biographical as bio
        bridge = Bridge()
        result = capture.resolve("Wren moved.", [{"mention": "Wren"}], bridge.people)
        row = capture.prepare_unified({"uuid": "EEE-555"}, "2026-09-01", "Wren moved.", result,
            "2026-09-01", bridge.people)["subjects"][0]["contribution"]["assertions"][0]
        bridge.bodies["BBB-222"] += "\n" + bio.attach("# Copied\n\n", row, "DDD-444")
        original = copy.deepcopy(bridge.bodies)
        plan = migration.preview(bridge, lambda *args: [])
        self.assertEqual(plan["steps"], [])
        self.assertGreater(plan["counts"]["conflicted"], 0)
        self.assertEqual(bridge.bodies, original)

    def test_stale_source_body_operation_and_ownership_stop_apply_and_rollback(self):
        for field in ("source", "body", "operation"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                bridge = Bridge()
                plan = migration.preview(bridge, lambda *args: [])
                if field == "source":
                    bridge.bodies["AAA-111"] += "\nAdditional source clause."
                elif field == "body":
                    bridge.bodies["BBB-222"] += "\nLater manual edit."
                else:
                    bridge.operations["AAA-111"] += " "
                with self.assertRaises(ValueError):
                    migration.transact(bridge, plan, Path(directory) / "journal", Path(directory) / "fence")
                self.assertEqual(bridge.mutations, 0)
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge()
            plan = migration.preview(bridge, lambda *args: [])
            journal, fence = Path(directory) / "journal", Path(directory) / "fence"
            migration.transact(bridge, plan, journal, fence)
            bridge.bodies["BBB-222"] += "\nLater owner edit."
            count = bridge.mutations
            with self.assertRaises(ValueError):
                migration.transact(bridge, plan, journal, fence, rollback=True)
            self.assertEqual(bridge.mutations, count)

    def test_edited_legacy_capture_and_unknown_lines_are_not_rewritten(self):
        bridge = Bridge()
        bridge.bodies["BBB-222"] = bridge.bodies["BBB-222"].replace("> Wren has two siblings.", "> Wren has three siblings.")
        body = bridge.bodies["BBB-222"]
        plan = migration.preview(bridge)
        self.assertEqual(plan["steps"], [])
        self.assertEqual(plan["counts"]["conflicted"], 2)
        self.assertEqual(bridge.bodies["BBB-222"], body)

    def test_cross_date_migration_questions_have_an_inference_free_answer_path(self):
        import entity_biographical as bio
        ef = load("entity-filing.py", "bio_migration_question_format")
        bridge = Bridge()
        bridge.bodies["BBB-222"] += "\n\n## Biographical Log\n\n" + ef.fact_line(
            "2026-08-01", "Wren has two siblings.", "EEE-555")
        plan = migration.preview(bridge, lambda *args: [])
        self.assertEqual(plan["counts"]["pending_questions"], 2)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                migration.transact(bridge, plan, Path(directory) / "journal", Path(directory) / "fence")
            first = plan["questions"][0]
            q = first["manifest"]["semantic_questions"][0]
            partial = migration.resolve(bridge, json.loads(json.dumps(plan)), {"plan_id": plan["plan_id"],
                "answers": {first["source_uuid"]: {q["key"]: q["candidates"][0]["id"]}}})
            self.assertNotEqual(partial["plan_id"], plan["plan_id"])
            self.assertEqual(partial["counts"]["pending_questions"], 1)
            second = partial["questions"][0]
            q2 = second["manifest"]["semantic_questions"][0]
            full = migration.resolve(bridge, partial, {"plan_id": partial["plan_id"],
                "answers": {second["source_uuid"]: {q2["key"]: q2["candidates"][0]["id"]}}})
            self.assertEqual(full["counts"]["pending_questions"], 0)
            migration.validate(full)
            migration.transact(bridge, full, Path(directory) / "journal", Path(directory) / "fence")
            rows = bio.render(bridge.bodies["BBB-222"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["baseline"]["log_date"], "2026-08-01")
            self.assertEqual(len(rows[0]["data"]["references"]), 3)
            self.assertNotIn("### Captured", bridge.bodies["BBB-222"])

    def test_resolve_cannot_expand_scope_to_later_unrelated_records(self):
        ef = load("entity-filing.py", "bio_frozen_scope_format")
        bridge = Bridge()
        bridge.bodies["BBB-222"] += "\n\n## Biographical Log\n\n" + ef.fact_line(
            "2026-08-01", "Wren has two siblings.", "EEE-555")
        plan = migration.preview(bridge, lambda *args: [])
        bridge.people.append(person("Kestrel", "DDD-444", entitytype="Person"))
        bridge.bodies["DDD-444"] = "# Kestrel\n\n## Biographical Log\n\n" + ef.fact_line(
            "2026-09-01", "Kestrel moved to Denver.", "FFF-666")
        question = plan["questions"][0]
        resolved = migration.resolve(bridge, plan, {"plan_id": plan["plan_id"], "answers": {
            question["source_uuid"]: {q["key"]: q["candidates"][0]["id"]
                                     for q in question["manifest"]["semantic_questions"]}}})
        self.assertNotIn("DDD-444", resolved["scope"])
        self.assertTrue(set(resolved["scope"]).issubset(plan["scope"]))
        self.assertFalse(any(step["uuid"] == "DDD-444" for step in resolved["steps"]))

    def test_resolve_rejects_changes_between_snapshot_and_rebuild(self):
        ef = load("entity-filing.py", "bio_review_race_format")
        bridge = Bridge()
        bridge.bodies["BBB-222"] += "\n\n## Biographical Log\n\n" + ef.fact_line(
            "2026-08-01", "Wren has two siblings.", "EEE-555")
        plan = migration.preview(bridge, lambda *args: [])
        question = plan["questions"][0]
        answers = {"plan_id": plan["plan_id"], "answers": {question["source_uuid"]: {
            q["key"]: q["candidates"][0]["id"] for q in question["manifest"]["semantic_questions"]}}}
        def racing(ops):
            if ops[0]["op"] == "dump_people" and ops[0].get("include_bodies"):
                bridge.bodies["BBB-222"] += "\nIntervening manual edit."
            return bridge(ops)
        with self.assertRaisesRegex(ValueError, "changed during review"):
            migration.resolve(racing, plan, answers)
        self.assertEqual(bridge.mutations, 0)

    def test_resolve_rejects_candidate_inventory_changes_during_rebuild(self):
        ef = load("entity-filing.py", "bio_candidate_race_format")
        bridge = Bridge()
        bridge.bodies["BBB-222"] += "\n\n## Biographical Log\n\n" + ef.fact_line(
            "2026-08-01", "Wren has two siblings.", "EEE-555")
        plan = migration.preview(bridge, lambda *args: [])
        question = plan["questions"][0]
        answers = {"plan_id": plan["plan_id"], "answers": {question["source_uuid"]: {
            q["key"]: q["candidates"][0]["id"] for q in question["manifest"]["semantic_questions"]}}}
        changed = False
        def racing(ops):
            nonlocal changed
            if ops[0]["op"] == "dump_people" and ops[0].get("include_bodies"):
                changed = True
            rows = bridge(ops)
            for i, op in enumerate(ops):
                if changed and op["op"] == "list_candidates":
                    rows[i]["pending"].append({"uuid": "GGG-777", "name": "Falcon", "text": ""})
            return rows
        with self.assertRaisesRegex(ValueError, "changed during review"):
            migration.resolve(racing, plan, answers)
        self.assertEqual(bridge.mutations, 0)

    def test_recorded_aliases_normalize_without_touching_manual_or_compound_subjects(self):
        import entity_biographical as bio
        ef = load("entity-filing.py", "bio_migration_alias_format")
        line = ef.fact_line("2026-09-01", "Birdy moved to Denver.", "AAA-111")
        body = "# Wren\n\n## Biographical Log\n\n" + line
        normalized = bio.normalize_legacy(body, "BBB-222", ["Wren", "Birdy"])
        self.assertEqual(bio.render(normalized)[0]["data"]["baseline"]["text"], "Moved to Denver.")
        self.assertEqual(bio.relative("Birdy and Rowan moved.", ["Wren", "Birdy"]), "Birdy and Rowan moved.")

    def test_fingerprint_required_and_manual_continuations_are_protected(self):
        import entity_biographical as bio
        ef = load("entity-filing.py", "bio_legacy_format")
        eligible = ef.fact_line("2026-09-01", "Wren has two siblings.", "AAA-111")
        body = "# Wren\n\n## Biographical Log\n\n" + eligible
        self.assertIn("Has two siblings.", bio.normalize_legacy(body, "BBB-222", "Wren"))
        for value in (body.replace("two", "three"), body + "\n    Manual continuation.", body + "\n  Manual continuation.",
                      body.replace("<!-- fact:", "<!-- unknown:")):
            self.assertEqual(bio.normalize_legacy(value, "BBB-222", "Wren"), value)
