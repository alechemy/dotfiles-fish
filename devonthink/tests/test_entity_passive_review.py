import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import load

passive = load("entity_passive.py", "passive_review_module")
ef = load("entity-filing.py", "passive_review_filing")
srv = load("entity-review-server.py", "passive_review_server")
bio = passive.bio


class Lock:
    def close(self):
        pass


class PassiveReview(unittest.TestCase):
    def setUp(self):
        self.source = {"uuid": "AAA-111", "name": "Fictional meeting", "kind": "meeting", "eventdate": "2026-09-01", "added": "2026-09-01", "ready": True}
        self.text = "Wren moved to Denver."
        self.person = {"uuid": "BBB-222", "name": "Wren Vale", "aliases": "Wren", "md": {"mdentitytype": "Person"}, "body": "# Wren\n\n"}
        plan = {"kind": "existing", "uuid": "BBB-222", "name": "Wren Vale", "aliases": "Wren", "md": {}, "facts": [("2026-09-01", self.text)], "updates": {}}
        self.ops = ef.ops_for_plan(plan, dict(self.source, text_revision=bio.digest(self.text)), "2026-09-01") + [{"op": "mark_filed", "uuid": "AAA-111"}]
        saved = copy.deepcopy(self.ops[0]["assertions"][0])
        saved["id"] = "b" * 64
        saved["reference"]["id"] = "c" * 64
        saved["baseline"]["text"] = "Lives in Denver."
        self.person["body"] = bio.attach(self.person["body"], saved, "BBB-222")
        self.envelope = passive.prepare(self.ops, [passive.snapshot(self.source, self.text, self.text, "2026-09-01")], [self.person], lambda *args: [saved["id"]])
        self.proposal = passive.body(self.envelope)
        self.writes = []

    def bridge(self, ops):
        out = []
        for op in ops:
            name = op["op"]
            if name == "get_text":
                out.append({"text": self.proposal if op["uuid"] == "DDD-444" else self.text})
            elif name == "get_source":
                out.append(dict(self.source))
            elif name == "get_fields":
                out.append({"md": {"mddocumenttype": "Entity Filing Proposal Passive v1"}})
            elif name == "dump_people":
                out.append([self.person])
            elif name == "list_candidates":
                out.append({"pending": [], "approved": [], "ignored": []})
            elif name == "list_review":
                out.append({"pending": [{"uuid": "DDD-444", "name": "File: Fictional meeting", "text": self.proposal}], "approved": []})
            elif name == "list_group":
                out.append([{"uuid": "DDD-444", "name": "File: Fictional meeting"}])
            else:
                self.writes.append(op)
                out.append({})
        return out

    def test_api_refuses_generic_approval_waiting_stale_revision_and_missing_answers(self):
        for status in ("question", "waiting", "blocked"):
            self.envelope["status"] = status
            self.proposal = passive.body(self.envelope)
            for revision in ("old", bio.digest(self.proposal)):
                with patch.object(srv.ef, "run_bridge", self.bridge), patch.object(srv.ef, "acquire_lock", return_value=Lock()), patch.object(srv.ec, "acquire_candidates_lock", return_value=Lock()), patch.object(srv.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                    with self.assertRaises(srv.RequestError):
                        srv.handle_proposal("DDD-444", {"action": "approve", "revision": revision})
                self.assertEqual(self.writes, [])

    def test_api_confirmation_has_no_inference_and_freezes_explicit_answer(self):
        q = self.envelope["questions"][0]
        with patch.object(srv.ef, "run_bridge", self.bridge), patch.object(srv.ef, "acquire_lock", return_value=Lock()), patch.object(srv.ec, "acquire_candidates_lock", return_value=Lock()), patch.object(srv.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), patch.object(srv.scheduler, "kick"), patch.object(srv.ef, "extract_omlx", side_effect=AssertionError("Review must not infer")):
            result = srv.handle_proposal("DDD-444", {"action": "approve", "revision": bio.digest(self.proposal), "semantic_answers": {q["key"]: q["candidates"][0]["id"]}})
        self.assertEqual(result["status"], "queued")
        frozen = passive.read(ef.proposal_ops(self.writes[0]["text"]))
        self.assertEqual(frozen["answers"], {q["key"]: q["candidates"][0]["id"]})
        self.assertEqual(self.writes[0]["expected_text"], self.proposal)
        self.assertEqual(self.writes[1]["group"], ef.APPROVED_PATH)

    def test_approved_group_bypass_and_stripped_envelope_never_mutate(self):
        for proposal in (self.proposal, "# File\n\n```json\n[]\n```\n"):
            self.proposal = proposal
            with patch.object(ef, "run_bridge", self.bridge):
                ef.apply_approved(False)
            self.assertEqual(self.writes, [])

    def test_novel_edit_queues_analysis_and_retains_full_extraction(self):
        payload = {"revision": bio.digest(self.proposal), "people": [{"name": "Wren Vale", "facts": [{"date": "2026-09-01", "fact": "Does not live in Denver."}], "updates": {}}]}
        with patch.object(srv.ef, "extract_omlx", side_effect=AssertionError("Review must not infer")), patch.object(srv.ef, "load_config", return_value={"SELF_NAME": ""}):
            result = srv.approve_with_edits("DDD-444", payload, False, self.bridge)
        self.assertEqual(result["status"], "waiting")
        held = passive.read(ef.proposal_ops(self.writes[1]["text"]))
        self.assertEqual(held["status"], "waiting")
        self.assertEqual(held["ops"], [])
        self.assertEqual(held["inputs"][0]["assertions"][0]["reference"]["evidence"], "Does not live in Denver.")
        self.assertEqual(self.writes[2]["group"], ef.REVIEW_PATH)

    def test_source_edits_cannot_be_rebaselined_by_proposal_edits(self):
        self.text = "Wren moved to Oslo."
        payload = {"revision": bio.digest(self.proposal), "people": [{"name": "Wren Vale", "facts": [{"date": "2026-09-01", "fact": "Lives in Denver."}]}]}
        with self.assertRaises(ValueError):
            srv.approve_with_edits("DDD-444", payload, False, self.bridge)
        self.assertEqual(self.writes, [])

    def test_worker_memory_gate_and_frozen_comparison_retry(self):
        self.envelope.update(status="waiting", questions=[], answers={}, ops=[])
        self.proposal = passive.body(self.envelope)
        config = {"TRANSPORT": "local", "IDLE_MINUTES": "0"}
        with patch.object(ef, "run_bridge", self.bridge), patch.object(ef, "omlx_available", return_value=True), patch.object(ef, "memory_pressure_normal", return_value=False), patch.object(ef, "acquire_llm_lock") as inference_lock:
            self.assertFalse(ef.retry_passive_proposals(config, False, False))
            inference_lock.assert_not_called()
        self.assertEqual(self.writes, [])
        with patch.object(ef, "run_bridge", self.bridge), patch.object(ef, "omlx_available", return_value=True), patch.object(ef, "memory_pressure_normal", return_value=True), patch.object(ef, "acquire_llm_lock", return_value=Lock()), patch.object(ef.ec, "acquire_candidates_lock", return_value=Lock()), patch.object(ef, "semantic_suggestions", return_value=lambda *args: []), patch.object(ef, "extract_omlx", side_effect=AssertionError("No extraction retry")):
            self.assertTrue(ef.retry_passive_proposals(config, False, False))
        held = passive.read(ef.proposal_ops(self.writes[0]["text"]))
        self.assertEqual(held["status"], "ready")
        self.assertEqual(held["inputs"], self.envelope["inputs"])

    def test_existing_candidate_target_stages_facts_and_aliases_without_mutating_person(self):
        data = ef.ec.new_candidate("Wren Vale")
        ef.ec.upsert_sighting(data, "dt:AAA-111", {"person": "Wren Vale", "name": self.source["name"], "kind": "meeting", "date": "2026-09-01", "hash": bio.digest(self.text), "facts": [["2026-09-01", self.text]], "updates": {}, "interacted": False})
        rec = {"uuid": "EEE-555", "text": ef.ec.render_candidate(data), "md": {"mdtracktarget": "BBB-222"}}
        with patch.object(ef, "run_bridge", self.bridge), patch.object(ef, "load_config", return_value={"TRANSPORT": "off"}), patch.object(ef, "extract_omlx", side_effect=AssertionError("Transport is off")):
            ef.prepare_candidate_passive(rec, data, "BBB-222", [self.person], False)
        self.assertEqual([op["op"] for op in self.writes], ["create_record"])
        held = passive.read(ef.proposal_ops(self.writes[0]["text"]))
        self.assertEqual(held["status"], "waiting")
        self.assertEqual(held["origin"]["text"], rec["text"])
        self.assertEqual([op["op"] for op in held["inputs"]], ["append_log", "add_aliases", "trash"])

    def test_waiting_ui_has_no_approval_and_keeps_edit_and_answer_tokens(self):
        asset = Path(__file__).resolve().parents[2] / "stow/devonthink/.local/share/entity-review/index.html"
        script = asset.read_text().split("<script>", 1)[1].split('document.addEventListener("click"', 1)[0]
        view = srv.proposal_view({"uuid": "DDD-444", "name": "Fictional meeting", "text": self.proposal}, [self.person])
        probe = r'''
        (async function() {
          const pv = VIEW;
          queue = {proposals:[pv], candidates:[], captures:[], roster:[]}; local = {};
          const o = overlayOf(local, "c-" + pv.uuid);
          const question = proposalCard(pv, o);
          const waiting = proposalCard({...pv,status:"waiting"},o);
          o.captureAnswers = {[pv.semantic_questions[0].key]:"separate"};
          const answered = proposalCard(pv,o);
          let posted, message;
          api = async (path,body) => {posted=body;throw new Error("stale proposal")};
          banner = text => {message=text};
          queue.proposals = [{...pv,revision:"changed"}];
          const stale = proposalCard(queue.proposals[0],o);
          await approveProposal(pv.uuid);
          console.log(JSON.stringify({question,waiting,answered,stale,posted,message}));
        })().catch(e => {console.error(e);process.exitCode=1});
        '''.replace("VIEW", json.dumps(view))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "passive-ui.js"
            path.write_text(script + probe)
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertIn("New extracted information", row["question"])
        self.assertIn("Already saved", row["question"])
        self.assertIn(" disabled", row["question"])
        self.assertNotIn('data-act="approve"', row["waiting"])
        self.assertIn('data-act="passive-edit"', row["waiting"])
        self.assertNotIn('data-act="approve"', row["stale"])
        self.assertEqual(row["posted"]["revision"], view["revision"])
        self.assertEqual(row["posted"]["semantic_answers"], {view["semantic_questions"][0]["key"]: "separate"})
        self.assertEqual(row["message"], "stale proposal")

    def test_registered_radio_input_enables_button_without_replacing_focused_card(self):
        asset = Path(__file__).resolve().parents[2] / "stow/devonthink/.local/share/entity-review/index.html"
        script = asset.read_text().split("<script>", 1)[1].split("</script>", 1)[0].rsplit("refresh(true);", 1)[0]
        probe = r'''
        const handlers = {};
        const button = {disabled:true};
        const card = {dataset:{key:"c-DDD-444"},querySelector:() => button};
        const radio = {dataset:{semanticKey:"a"},type:"radio",checked:true,value:"separate",
          closest:selector => selector === "#content" ? {} : card, matches:() => true};
        const document = {activeElement:radio,addEventListener:(name,fn) => handlers[name]=fn};
        const window = {addEventListener:() => {}};
        ''' + script + r'''
        queue={proposals:[{uuid:"DDD-444",passive:true,semantic_questions:[{key:"a"},{key:"b"}]}]};local={};
        let replaced=false;
        render=() => {const plan=planRender([{key:"c-DDD-444",fp:"old"}],[{key:"c-DDD-444",fp:"new"}],pinnedKey());replaced=plan.replace.length>0};
        handlers.input({target:radio}); const first=button.disabled;
        radio.dataset.semanticKey="b";handlers.input({target:radio});
        if (!first || button.disabled || replaced || document.activeElement!==radio) throw new Error("focused answer failed");
        radio.type="text";if(pinnedKey()!=="c-DDD-444")throw new Error("text pin lost");
        '''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.js"
            path.write_text(probe)
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_retry_health_aggregates_both_orders(self):
        waiting = copy.deepcopy(self.envelope)
        waiting.update(status="waiting", questions=[], answers={}, ops=[])
        body = passive.body(waiting)
        bridge = self.bridge
        def listing(ops):
            if ops[0]["op"] == "list_review":
                return [{"pending": [{"uuid": uuid, "text": body} for uuid in ("DDD-444", "FFF-666")], "approved": []}]
            return bridge(ops)
        config = {"TRANSPORT": "local", "IDLE_MINUTES": "0"}
        for statuses in (("waiting", "ready"), ("ready", "waiting"), ("ready", "ready")):
            outcomes = [dict(waiting, status=status) for status in statuses]
            with patch.object(ef, "run_bridge", listing), patch.object(ef, "omlx_available", return_value=True), patch.object(ef, "acquire_llm_lock", return_value=Lock()), patch.object(ef.ec, "acquire_candidates_lock", return_value=Lock()), patch.object(ef.passive if hasattr(ef,"passive") else passive, "analyze", side_effect=outcomes):
                import entity_passive
                with patch.object(entity_passive, "analyze", side_effect=outcomes):
                    self.assertEqual(ef.retry_passive_proposals(config, False, True), statuses == ("ready", "ready"))

    def test_empty_http_choices_comparison_retains_waiting_then_recovers(self):
        import io
        config = {"OMLX_MODEL": "fictional", "OMLX_URL": "http://fictional.invalid", "TRANSPORT": "local"}
        with patch.object(ef, "_omlx_headers", return_value={}), patch.object(ef.urllib.request, "urlopen", return_value=io.StringIO('{"choices":[]}')):
            envelope = passive.prepare(self.ops, self.envelope["sources"], [self.person], ef.semantic_suggestions(config))
        self.assertEqual(envelope["status"], "waiting")
        self.assertEqual(envelope["inputs"], self.ops)
        self.assertEqual(passive.analyze(envelope, [self.person], lambda *args: [])["status"], "ready")
