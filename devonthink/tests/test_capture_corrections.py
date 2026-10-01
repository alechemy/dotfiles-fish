import copy
import unittest
from datetime import datetime
from unittest import mock

from helpers import load, person
from test_capture_recovery import FakeBridge, SOURCE, TEXT, plan

capture = load("entity_capture.py", "capture_corrections")


class CorrectionBridge(FakeBridge):
    def __init__(self, failure=0):
        super().__init__(failure)
        self.operations = []
        self.days = {}
        self.ignored = []

    def __call__(self, ops):
        out = []
        for op in ops:
            self.operations.append(copy.deepcopy(op))
            kind = op["op"]
            if kind == "capture_store":
                if capture.source_text(self.bodies["SRC-1"]) != op["text"]:
                    raise RuntimeError("capture source changed")
            if kind == "capture_person":
                if op["uuid"]:
                    target = op["uuid"]
                else:
                    target = "P" + str(len(self.people) + 1)
                    self.people.append(person(op["name"], target, entitytype="Person", entitystatus="active"))
                    self.bodies[target] = "# " + op["name"] + "\n\n"
                    self.mutate()
                out.append({"uuid": target, "initialized": True})
            elif kind == "capture_remove":
                if capture.source_text(self.bodies["SRC-1"]) != op["text"] or self.manifest != op["expected_operation"] or self.bodies[op["uuid"]] != op["expected_body"]:
                    raise RuntimeError("capture changed before removal")
                self.bodies[op["uuid"]] = self.bodies[op["uuid"]].replace(op["block"], "")
                self.mutate()
                out.append({"uuid": op["uuid"]})
            elif kind == "capture_append":
                body = self.bodies[op["uuid"]]
                begin = "<!-- capture:" + op["id"] + ":begin -->"
                end = "<!-- capture:" + op["id"] + ":end -->"
                if begin in body or end in body:
                    if body.count(begin) != 1 or body.count(end) != 1 or op["block"] not in body:
                        raise RuntimeError("capture contribution was edited; retained for correction")
                elif op["expected_present"]:
                    raise RuntimeError("capture contribution was removed; retained for correction")
                else:
                    self.bodies[op["uuid"]] += "\n\n" + op["block"] + "\n"
                    self.mutate()
                out.append({"uuid": op["uuid"]})
            elif kind == "capture_source_edit":
                if capture.source_text(self.bodies["SRC-1"]) != op["expected_text"]:
                    raise RuntimeError("source changed")
                self.bodies["SRC-1"] = "# Capture\n\n" + op["text"]
                self.mutate()
                out.append({"uuid": "SRC-1"})
            elif kind == "get_or_create_daily":
                key = self.days.setdefault(op["date"], "DAY-" + op["date"])
                self.bodies.setdefault(key, "# Today\n\n- Handwritten text.\n")
                out.append({"uuid": key})
            elif kind == "append_pinned":
                if op["line"] not in self.bodies[op["uuid"]]:
                    self.bodies[op["uuid"]] += op["line"] + "\n"
                    self.mutate()
                out.append({"uuid": op["uuid"]})
            elif kind == "list_candidates":
                out.append({"pending": [], "approved": [], "ignored": self.ignored})
            else:
                out.extend(super().__call__([op]))
        return out


def filed_bridge():
    bridge = CorrectionBridge()
    capture.replay(bridge, plan())
    bridge.people.append(person("Kestrel", "P2", entitytype="Person", entitystatus="active"))
    bridge.bodies["P2"] = "# Kestrel\n\nManual paragraph.\n"
    bridge.mutations = 0
    bridge.operations = []
    return bridge


def replacement(bridge, text=TEXT, extracted=None, decisions=None):
    extracted = extracted or [{"mention": "Wren"}]
    decisions = decisions or {0: {"target": "P2"}}
    result = capture.resolve(text, extracted, bridge.people, decisions=decisions)
    return capture.prepare(SOURCE, "2026-09-29", text, result, "2026-09-30")


class Corrections(unittest.TestCase):
    def start(self, bridge, next_manifest=None, text=TEXT):
        manifest = capture.decode_manifest(bridge.manifest, "SRC-1")
        capture.start_correction(manifest, "reassign" if next_manifest else "undo", next_manifest, text)
        return manifest

    def test_reassignment_and_undo_recover_after_every_mutation(self):
        for reassign in (False, True):
            baseline = filed_bridge()
            original = baseline.bodies["P1"]
            manifest = self.start(baseline, replacement(baseline) if reassign else None)
            capture.correct(baseline, manifest, baseline.manifest)
            for failure in range(1, baseline.mutations + 1):
                with self.subTest(reassign=reassign, failure=failure):
                    bridge = filed_bridge()
                    bridge.bodies["P1"] += "\nLater manual text.\n"
                    bridge.fail_after = failure
                    manifest = self.start(bridge, replacement(bridge) if reassign else None)
                    with self.assertRaises(RuntimeError):
                        capture.correct(bridge, manifest, bridge.manifest)
                    saved = capture.decode_manifest(bridge.manifest, "SRC-1")
                    bridge.fail_after = 0
                    final = capture.correct(bridge, saved, bridge.manifest) if saved["status"] == "correcting" else saved
                    self.assertEqual(final["status"], "filed" if reassign else "undone")
                    self.assertNotIn(":begin -->", bridge.bodies["P1"])
                    self.assertIn("Later manual text.", bridge.bodies["P1"])
                    self.assertIn("Manual paragraph.", bridge.bodies["P2"])
                    if reassign:
                        self.assertEqual(bridge.bodies["P2"].count(":begin -->"), 1)
                    self.assertEqual(sum(body.count("this capture's filing.") for body in bridge.bodies.values()), 1)
                    self.assertEqual(capture.source_text(bridge.bodies["SRC-1"]), TEXT)
                    self.assertEqual(final["history"][0]["text"], TEXT)
            self.assertIn(TEXT.splitlines()[0], original)

    def test_committed_destination_is_reverified_before_original_removal(self):
        bridge = filed_bridge()
        manifest = self.start(bridge, replacement(bridge))
        def persist(destination):
            manifest["correction"]["next"] = destination
        capture.replay(bridge, manifest["correction"]["next"], persist_manifest=persist)
        bridge.manifest = capture.encode_manifest(manifest)
        bridge.bodies["P2"] = "Destination edited manually."
        with self.assertRaisesRegex(RuntimeError, "destination contribution changed"):
            capture.correct(bridge, manifest, bridge.manifest)
        self.assertIn(":begin -->", bridge.bodies["P1"])
        self.assertEqual(bridge.bodies["P2"], "Destination edited manually.")
        token = manifest["correction"]["conflict_token"]
        manifest["correction"]["keep_edited"] = [token]
        final = capture.correct(bridge, manifest, bridge.manifest)
        self.assertEqual(final["status"], "filed")
        self.assertNotIn(":begin -->", bridge.bodies["P1"])
        self.assertEqual(bridge.bodies["P2"], "Destination edited manually.")

    def test_unfinished_destination_edits_offer_a_resumable_preserve_decision(self):
        for applied in (False, True):
            for removed in (False, True):
                with self.subTest(applied=applied, removed=removed):
                    bridge = filed_bridge()
                    manifest = self.start(bridge, replacement(bridge))
                    subject = manifest["correction"]["next"]["subjects"][0]
                    subject["applied"] = applied
                    block = subject["contribution"]["block"]
                    bridge.bodies["P2"] += block.replace("> Wren moved", "> Wren relocated") if not removed else ""
                    if removed and not applied:
                        continue
                    bridge.manifest = capture.encode_manifest(manifest)
                    with self.assertRaisesRegex(RuntimeError, "destination contribution changed"):
                        capture.correct(bridge, manifest, bridge.manifest)
                    frozen = capture.decode_manifest(bridge.manifest, "SRC-1")
                    self.assertEqual(frozen["reason"], "destination_edited")
                    frozen["correction"]["keep_edited"] = [frozen["correction"].pop("conflict_token")]
                    frozen.pop("reason")
                    edited = bridge.bodies["P2"]
                    final = capture.correct(bridge, frozen, bridge.manifest)
                    self.assertEqual(final["status"], "filed")
                    self.assertEqual(bridge.bodies["P2"], edited)
                    self.assertNotIn(":begin -->", bridge.bodies["P1"])
                    capture.decode_manifest(bridge.manifest, "SRC-1")

    def test_unchanged_same_person_correction_preserves_an_edited_or_deleted_block(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                bridge = filed_bridge()
                manifest = self.start(bridge, replacement(bridge, decisions={0: {"target": "P1"}}))
                if removed:
                    bridge.bodies["P1"] = bridge.bodies["P1"].replace(manifest["subjects"][0]["contribution"]["block"], "")
                else:
                    bridge.bodies["P1"] = bridge.bodies["P1"].replace("> Wren moved", "> Wren relocated")
                with self.assertRaisesRegex(RuntimeError, "destination contribution changed"):
                    capture.correct(bridge, manifest, bridge.manifest)
                manifest["correction"]["keep_edited"] = [manifest["correction"].pop("conflict_token")]
                edited = bridge.bodies["P1"]
                final = capture.correct(bridge, manifest, bridge.manifest)
                self.assertEqual(final["status"], "filed")
                self.assertEqual(bridge.bodies["P1"], edited)
                self.assertEqual(edited.count(":begin -->"), 0 if removed else 1)

    def test_same_text_can_be_split_without_contribution_collision(self):
        bridge = filed_bridge()
        text = "Wren moved. Kestrel stayed."
        bridge.bodies["SRC-1"] = "# Capture\n\n" + text
        old = capture.prepare(SOURCE, "2026-09-29", text, capture.resolve(text, [{"mention": "Wren"}], bridge.people), "2026-09-30")
        bridge.manifest = ""
        bridge.bodies["P1"] = "# Wren\n\n"
        capture.replay(bridge, old)
        next_manifest = replacement(bridge, text, [{"mention": "Wren", "passage": "Wren moved."}, {"mention": "Kestrel", "passage": "Kestrel stayed."}], {0: {"target": "P1"}, 1: {"target": "P2"}})
        self.assertNotEqual(old["subjects"][0]["contribution"]["id"], next_manifest["subjects"][0]["contribution"]["id"])
        final = capture.correct(bridge, self.start(bridge, next_manifest, text), bridge.manifest)
        self.assertEqual(final["status"], "filed")
        self.assertIn("> Wren moved.", bridge.bodies["P1"])
        self.assertNotIn("Kestrel stayed.", bridge.bodies["P1"])
        self.assertIn("> Kestrel stayed.", bridge.bodies["P2"])

    def test_external_edit_baseline_survives_a_further_correction(self):
        bridge = filed_bridge()
        baseline = "Wren moved to Oslo."
        revised = "Wren moved to Prague."
        bridge.bodies["SRC-1"] = "# Capture\n\n" + baseline
        manifest = self.start(bridge, replacement(bridge, revised), revised)
        manifest["correction"]["source_edit"] = {"from": baseline, "to": revised, "from_revision": capture.revision(baseline), "to_revision": capture.revision(revised)}
        bridge.manifest = capture.encode_manifest(manifest)
        manifest = capture.decode_manifest(bridge.manifest, "SRC-1")
        final = capture.correct(bridge, manifest, bridge.manifest)
        self.assertEqual([h["text"] for h in final["history"]], [TEXT, baseline])
        self.assertEqual(capture.source_text(bridge.bodies["SRC-1"]), revised)
        self.assertEqual(final["source_date"], "2026-09-29")
        manifest["correction"]["source_edit"]["from_revision"] = "bad"
        with self.assertRaises(ValueError):
            capture.decode_manifest(capture.encode_manifest(manifest), "SRC-1")

    def test_removal_checks_source_revision(self):
        bridge = filed_bridge()
        manifest = self.start(bridge)
        original = bridge.__call__
        def edit_before_remove(ops):
            if ops[0]["op"] == "capture_remove":
                bridge.bodies["SRC-1"] = "Source changed concurrently."
            return original(ops)
        with self.assertRaisesRegex(RuntimeError, "changed before removal"):
            capture.correct(edit_before_remove, manifest, bridge.manifest)
        self.assertIn(":begin -->", bridge.bodies["P1"])

    def test_receipt_date_and_destination_are_frozen_across_midnight(self):
        bridge = filed_bridge()
        manifest = self.start(bridge)
        manifest["correction"]["receipt_date"] = "2026-09-30"
        original = bridge.__call__
        interrupted = False
        def fail_after_receipt(ops):
            nonlocal interrupted
            result = original(ops)
            if ops[0]["op"] == "append_pinned" and not interrupted:
                interrupted = True
                raise RuntimeError("after receipt")
            return result
        with self.assertRaises(RuntimeError):
            capture.correct(fail_after_receipt, manifest, bridge.manifest)
        with mock.patch.object(capture, "datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 1)
            final = capture.correct(bridge, capture.decode_manifest(bridge.manifest, "SRC-1"), bridge.manifest)
        self.assertEqual(final["status"], "undone")
        self.assertEqual(sum(body.count("this capture's filing.") for body in bridge.bodies.values()), 1)
        self.assertNotIn("2026-10-01", bridge.days)

    def test_unstarted_retry_rechecks_current_ignore_controls(self):
        bridge = CorrectionBridge()
        manifest = plan()
        bridge.manifest = capture.encode_manifest(manifest)
        from test_entity_candidates import make, listing
        bridge.ignored = listing(ignored=[make("Wren")])["ignored"]
        with self.assertRaisesRegex(RuntimeError, "ignored"):
            capture.replay(bridge, manifest, bridge.manifest)
        self.assertEqual(bridge.people, [])

    def test_legacy_contribution_identity_remains_readable(self):
        manifest = plan()
        s = manifest["subjects"][0]
        s["contribution"] = capture.contribution("SRC-1", manifest["revision"], s, manifest["source_date"], legacy=True)
        self.assertEqual(capture.decode_manifest(capture.encode_manifest(manifest), "SRC-1"), manifest)
