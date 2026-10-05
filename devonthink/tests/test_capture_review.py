import types
import unittest
from unittest import mock

from helpers import load
from test_capture_corrections import filed_bridge
from test_capture_recovery import TEXT

srv = load("entity-review-server.py", "capture_review_server")
capture = srv.ef.capture


def unapplied_question(text="Wren has two siblings."):
    import entity_biographical as bio
    bridge = filed_bridge()
    bridge.bodies["SRC-1"] = "# Capture\n\n" + text
    bridge.bodies["P1"] = "# Wren\n\nManual paragraph.\n"
    prior = "Wren has two siblings."
    old = capture.prepare_unified({"uuid": "OLD-2"}, "2026-09-28", prior,
        capture.resolve(prior, [{"mention": "Wren"}], bridge.people), "2026-09-28", bridge.people)
    bridge.bodies["P1"] = bio.attach(bridge.bodies["P1"], old["subjects"][0]["contribution"]["assertions"][0], "P1")
    people = [dict(p, body=bridge.bodies[p["uuid"]]) for p in bridge.people]
    manifest = capture.prepare_unified({"uuid": "SRC-1"}, "2026-09-29", text,
        capture.resolve(text, [{"mention": "Wren"}], people), "2026-09-30", people)
    bridge.manifest = capture.encode_manifest(manifest)
    bridge.mutations = 0
    bridge.operations = []
    return bridge, manifest


class Review(unittest.TestCase):
    def setUp(self):
        self.bridge = filed_bridge()
        self.patches = [mock.patch.object(srv.ef, "run_bridge", self.bridge),
                        mock.patch.object(srv.ef, "load_config", return_value={}),
                        mock.patch.object(srv.ef, "self_names", return_value=set()),
                        mock.patch.object(srv.ef, "semantic_suggestions", return_value=lambda *args: []),
                        mock.patch.object(srv.ef, "acquire_lock", return_value=mock.Mock()),
                        mock.patch.object(srv.ec, "acquire_candidates_lock", return_value=mock.Mock()),
                        mock.patch.object(srv.subprocess, "run", return_value=types.SimpleNamespace(returncode=0))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def views(self):
        return srv.capture_views([{"uuid": "SRC-1", "operation": self.bridge.manifest,
                                    "text": capture.source_text(self.bridge.bodies["SRC-1"])}], self.bridge.people)[0]

    def process(self, bridge=None):
        bridge = bridge or self.bridge
        raw = bridge.manifest
        manifest = capture.decode_manifest(raw, "SRC-1")
        with mock.patch.object(srv.ef, "run_bridge", bridge):
            if manifest.get("replacement"):
                capture.replace_pending(bridge, manifest, raw)
            elif manifest["status"] == "correcting":
                capture.correct(bridge, manifest, raw)
            elif manifest["status"] == "filing":
                capture.replay(bridge, manifest, raw)
            else:
                srv.ef.process_capture_analysis({}, {"uuid": "SRC-1"}, manifest, raw,
                    capture.source_text(bridge.bodies["SRC-1"]), set())

    def payload(self, text, baseline=TEXT):
        return {"action": "save", "revision": capture.revision(baseline), "text": text,
                "subjects": [{"mention": "Wren", "passage": text, "target": "P2"}]}

    def test_queued_save_preserves_originals_until_destination_first_correction(self):
        self.bridge.bodies["P1"] = self.bridge.bodies["P1"].replace("> Wren moved", "> Wren relocated")
        revised = "Wren moved to Oslo."
        original = self.bridge.bodies["P1"]
        result = srv.handle_capture("SRC-1", self.payload(revised))
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(self.bridge.bodies["P1"], original)
        self.assertNotIn("Moved to Oslo", self.bridge.bodies["P2"])
        self.assertEqual(self.views()["text"], revised)
        with self.assertRaises(ValueError):
            self.process()
        view = self.views()
        self.assertEqual(view["revision"], capture.revision(revised))
        self.assertTrue(view["conflict"])
        result = srv.handle_capture("SRC-1", {"action": "keep-edited", "revision": view["revision"]})
        self.assertEqual(result["status"], "filed")
        self.assertIn("> Wren relocated", self.bridge.bodies["P1"])
        self.assertIn("Moved to Oslo.", self.bridge.bodies["P2"])

    def test_external_edit_then_ui_edit_remains_decodable_after_interruption(self):
        baseline, revised = "Wren moved to Oslo.", "Wren moved to Prague."
        self.bridge.bodies["SRC-1"] = "# Capture\n\n" + baseline
        self.bridge.fail_after = 1
        with self.assertRaises(RuntimeError):
            srv.handle_capture("SRC-1", self.payload(revised, baseline))
        manifest = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        self.assertEqual(manifest["analysis_request"]["from_revision"], capture.revision(baseline))
        self.assertEqual(self.views()["current_text"], baseline)
        self.assertEqual(self.views()["text"], revised)
        self.bridge.fail_after = 0
        self.process()
        self.assertEqual(capture.decode_manifest(self.bridge.manifest, "SRC-1")["status"], "filed")

    def test_pending_external_edit_preserves_history_through_worker_recovery(self):
        baseline, revised = "Wren moved to Oslo.", "Wren moved to Prague."
        self.bridge.manifest = capture.encode_manifest(capture.pending({"uuid": "SRC-1"}, "2026-09-29", TEXT,
            [{"mention": "Wren"}], capture.outcome("unresolved", "ambiguous_name"), "2026-09-30"))
        self.bridge.bodies["SRC-1"] = "# Capture\n\n" + baseline
        srv.handle_capture("SRC-1", self.payload(revised, baseline))
        manifest = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        bad = dict(manifest, analysis_request=dict(manifest["analysis_request"], from_revision="bad"))
        with self.assertRaises(ValueError):
            capture.decode_manifest(capture.encode_manifest(bad), "SRC-1")
        with self.assertRaises(srv.RequestError):
            srv.handle_capture("SRC-1", self.payload(revised, baseline))
        self.process()
        final = capture.decode_manifest(self.bridge.manifest, "SRC-1")
        self.assertEqual([h["text"] for h in final["history"]], [TEXT, baseline])
        self.assertEqual(capture.source_text(self.bridge.bodies["SRC-1"]), revised)
        self.assertIn("Moved to Prague.", self.bridge.bodies["P2"])

    def test_queued_analysis_and_correction_recover_after_every_mutation(self):
        revised = "Wren moved to Prague."
        def queued():
            bridge = filed_bridge()
            with mock.patch.object(srv.ef, "run_bridge", bridge):
                srv.handle_capture("SRC-1", self.payload(revised))
            bridge.mutations = 0
            return bridge
        baseline = queued()
        self.process(baseline)
        for failure in range(1, baseline.mutations + 1):
            with self.subTest(failure=failure):
                crashed = queued()
                crashed.fail_after = failure
                with self.assertRaises(RuntimeError):
                    self.process(crashed)
                capture.decode_manifest(crashed.manifest, "SRC-1")
                crashed.fail_after = 0
                final = capture.decode_manifest(crashed.manifest, "SRC-1")
                if final["status"] != "filed":
                    self.process(crashed)
                self.assertEqual(capture.decode_manifest(crashed.manifest, "SRC-1")["status"], "filed")
                self.assertEqual(crashed.bodies["P2"].count("<!-- bio:v2:"), 1)
                self.assertEqual(capture.source_text(crashed.bodies["SRC-1"]), revised)

    def test_editing_unapplied_question_uses_recoverable_replacement(self):
        import entity_biographical as bio
        original = "Wren has two siblings."
        revised = "Wren has three siblings."
        def queued():
            bridge, manifest = unapplied_question(original)
            self.assertEqual(manifest["status"], "question")
            with mock.patch.object(srv.ef, "run_bridge", bridge):
                result = srv.handle_capture("SRC-1", self.payload(revised, original))
            self.assertEqual(result["status"], "waiting")
            bridge.mutations = 0
            return bridge
        baseline = queued()
        self.process(baseline)
        for failure in range(1, baseline.mutations + 1):
            with self.subTest(failure=failure):
                bridge = queued()
                bridge.fail_after = failure
                with self.assertRaises(RuntimeError):
                    self.process(bridge)
                capture.decode_manifest(bridge.manifest, "SRC-1")
                bridge.fail_after = 0
                if capture.decode_manifest(bridge.manifest, "SRC-1")["status"] != "filed":
                    self.process(bridge)
                final = capture.decode_manifest(bridge.manifest, "SRC-1")
                self.assertEqual(final["status"], "filed")
                self.assertEqual(final["history"][-1]["text"], original)
                self.assertEqual(len(bio.render(bridge.bodies["P1"], "P1")), 1)
                self.assertIn("Has three siblings.", bridge.bodies["P2"])
                self.assertEqual(capture.source_text(bridge.bodies["SRC-1"]), revised)
                self.assertNotIn("correction", final)

    def test_worker_source_edit_distinguishes_unapplied_question_from_started_filing(self):
        import entity_biographical as bio
        original = "Wren has two siblings."
        revised = "Wren has three siblings."
        config = {"TRANSPORT": "local", "MIN_ROSTER": "1", "MAX_PER_RUN": "1", "FILING_MODE": "suggest",
                  "IDLE_MINUTES": "0", "CAPTURE_AUTO_AFTER": "", "SKIP_SOURCE_TITLES": ""}
        for started in (False, True):
            def queued():
                bridge, manifest = unapplied_question(original)
                if started:
                    people = [dict(p, body=bridge.bodies[p["uuid"]]) for p in bridge.people]
                    manifest = capture.prepare_unified({"uuid": "SRC-1"}, "2026-09-29", original,
                        capture.resolve(original, [{"mention": "Wren"}], people), "2026-09-30", people,
                        decisions={manifest["semantic_questions"][0]["key"]: "separate"})
                    bridge.manifest = capture.encode_manifest(manifest)
                    bridge.bodies["P1"] = bio.attach(bridge.bodies["P1"], manifest["subjects"][0]["contribution"]["assertions"][0], "P1")
                    self.assertFalse(manifest["subjects"][0]["applied"])
                    self.assertNotIn("applied_references", manifest)
                bridge.bodies["SRC-1"] = "# Capture\n\n" + revised
                with mock.patch.object(srv.ef, "run_bridge", bridge), \
                        mock.patch.object(srv.ef, "save_state"), \
                        mock.patch.object(srv.ef, "memory_pressure_normal", return_value=True):
                    srv.ef.scan(config, {"processed": {}, "attempts": {}, "parked": {}}, False, "SRC-1", True)
                    observed = capture.decode_manifest(bridge.manifest, "SRC-1")
                    self.assertEqual(observed["status"], "revision_question" if started else "question")
                    if not started:
                        self.assertEqual(observed["subjects"], [])
                        self.assertEqual(observed["history"][-1]["text"], original)
                    self.assertEqual(srv.handle_capture("SRC-1", self.payload(revised, revised))["status"], "waiting")
                bridge.mutations = 0
                return bridge
            baseline = queued()
            self.process(baseline)
            for failure in range(1, baseline.mutations + 1):
                with self.subTest(started=started, failure=failure):
                    bridge = queued()
                    bridge.fail_after = failure
                    with self.assertRaises(RuntimeError):
                        self.process(bridge)
                    capture.decode_manifest(bridge.manifest, "SRC-1")
                    bridge.fail_after = 0
                    if capture.decode_manifest(bridge.manifest, "SRC-1")["status"] != "filed":
                        self.process(bridge)
                    self.assertEqual(capture.decode_manifest(bridge.manifest, "SRC-1")["status"], "filed")
                    rows = bio.render(bridge.bodies["P1"], "P1")
                    self.assertEqual(len(rows), 1)
                    self.assertFalse(any(ref["source_uuid"] == "SRC-1" for row in rows for ref in row["data"]["references"]))
                    self.assertIn("Has three siblings.", bridge.bodies["P2"])
                    self.assertEqual(capture.source_text(bridge.bodies["SRC-1"]), revised)

    def test_rebuilt_question_returns_question_and_keeps_prior_choices(self):
        import entity_biographical as bio
        text = "Wren has two siblings. Wren likes hiking."
        for choice in ("separate", "source", "confirmed"):
            with self.subTest(choice=choice):
                bridge, manifest = unapplied_question(text)
                question = manifest["semantic_questions"][0]
                prior = "Wren likes hiking."
                old = capture.prepare_unified({"uuid": "OLD-3"}, "2026-09-28", prior,
                    capture.resolve(prior, [{"mention": "Wren"}], bridge.people), "2026-09-28", bridge.people)
                bridge.bodies["P1"] = bio.attach(bridge.bodies["P1"], old["subjects"][0]["contribution"]["assertions"][0], "P1")
                answer = question["candidates"][0]["id"] if choice == "confirmed" else choice
                payload = self.payload(text, text)
                payload["subjects"][0]["target"] = "P1"
                payload["semantic_answers"] = {question["key"]: answer}
                with mock.patch.object(srv.ef, "run_bridge", bridge):
                    result = srv.handle_capture("SRC-1", payload)
                    self.assertEqual(result["status"], "question")
                    pending = capture.decode_manifest(bridge.manifest, "SRC-1")
                    self.assertEqual(pending["status"], "question")
                    self.assertEqual(len(pending["semantic_questions"]), 1)
                    self.assertEqual(pending["semantic_questions"][0]["assertion"], "Likes hiking.")
                    self.assertEqual(bridge.mutations, 1)
                    payload["semantic_answers"] = {pending["semantic_questions"][0]["key"]: "separate"}
                    result = srv.handle_capture("SRC-1", payload)
                self.assertEqual(result["status"], "filed")
                final = capture.decode_manifest(bridge.manifest, "SRC-1")
                self.assertEqual(final["status"], "filed")
                self.assertEqual(len(bio.render(bridge.bodies["P1"], "P1")), 3 if choice == "confirmed" else 4)
                if choice == "source":
                    self.assertIn("Wren has two siblings. ([source]", bridge.bodies["P1"])

    def test_rebuilt_analysis_question_retains_source_until_all_answers(self):
        import entity_biographical as bio
        original = "Wren has two siblings."
        revised = "Wren has a brother and a sister. Wren likes hiking."
        bridge, unused = unapplied_question(original)
        payload = self.payload(revised, original)
        payload["subjects"][0]["target"] = "P1"
        with mock.patch.object(srv.ef, "run_bridge", bridge):
            self.assertEqual(srv.handle_capture("SRC-1", payload)["status"], "waiting")
        suggest = lambda text, day, rows: [r["id"] for r in rows if
            text.startswith("Has a brother") and r["text"] == "Has two siblings."]
        with mock.patch.object(srv.ef, "semantic_suggestions", return_value=suggest):
            self.process(bridge)
        frozen = capture.decode_manifest(bridge.manifest, "SRC-1")
        self.assertEqual(len(frozen["analysis_plan"]["semantic_questions"]), 1)
        prior = "Wren likes hiking."
        old = capture.prepare_unified({"uuid": "OLD-3"}, "2026-09-28", prior,
            capture.resolve(prior, [{"mention": "Wren"}], bridge.people), "2026-09-28", bridge.people)
        bridge.bodies["P1"] = bio.attach(bridge.bodies["P1"], old["subjects"][0]["contribution"]["assertions"][0], "P1")
        payload["semantic_answers"] = {frozen["analysis_plan"]["semantic_questions"][0]["key"]: "separate"}
        with mock.patch.object(srv.ef, "run_bridge", bridge):
            self.assertEqual(srv.handle_capture("SRC-1", payload)["status"], "question")
            self.assertEqual(capture.source_text(bridge.bodies["SRC-1"]), original)
            pending = capture.decode_manifest(bridge.manifest, "SRC-1")
            self.assertIn("analysis_request", pending)
            self.assertNotIn("correction", pending)
            self.assertEqual(len(bio.render(bridge.bodies["P1"], "P1")), 2)
            payload["semantic_answers"] = {pending["analysis_plan"]["semantic_questions"][0]["key"]: "separate"}
            self.assertEqual(srv.handle_capture("SRC-1", payload)["status"], "filed")
        self.assertEqual(capture.source_text(bridge.bodies["SRC-1"]), revised)
        final = capture.decode_manifest(bridge.manifest, "SRC-1")
        self.assertEqual(final["history"][-1]["text"], original)
        self.assertEqual(len(bio.render(bridge.bodies["P1"], "P1")), 4)

    def test_stale_decision_is_rejected_without_mutation(self):
        with self.assertRaises(srv.RequestError):
            srv.handle_capture("SRC-1", {"action": "undo", "revision": "stale"})
        self.assertEqual(self.bridge.mutations, 0)

    def test_concurrent_source_edit_keeps_queued_evidence_and_needs_new_decision(self):
        srv.handle_capture("SRC-1", self.payload("Wren moved to Oslo."))
        self.bridge.bodies["SRC-1"] = "# Capture\n\nWren moved to Rome."
        self.process()
        self.assertEqual(self.views()["status"], "question")
        self.assertNotIn("Moved to Oslo", self.bridge.bodies["P2"])
        srv.handle_capture("SRC-1", self.payload("Wren moved to Rome.", "Wren moved to Rome."))
        self.process()
        self.assertEqual(capture.decode_manifest(self.bridge.manifest, "SRC-1")["status"], "filed")

    def test_an_unnamed_question_can_be_bound_to_an_existing_person(self):
        text = "Someone moved to Oslo."
        self.bridge.bodies["SRC-1"] = "# Capture\n\n" + text
        self.bridge.manifest = capture.encode_manifest(capture.pending({"uuid": "SRC-1"}, "2026-09-29", text, [],
            capture.outcome("unresolved", "no_subject"), "2026-09-30"))
        result = srv.handle_capture("SRC-1", {"action": "save", "revision": capture.revision(text),
            "subjects": [{"mention": "", "passage": text, "target": "P1"}]})
        self.assertEqual(result["status"], "waiting")
        self.process()
        self.assertIn("Someone moved to Oslo.", self.bridge.bodies["P1"])
        capture.decode_manifest(self.bridge.manifest, "SRC-1")
