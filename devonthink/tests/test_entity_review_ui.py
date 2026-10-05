"""The entity-review page's pure core: card reconcile + edit collection.

The page is one static HTML asset the server hands out whole, so its script
can't be a separate module a test could import. The DOM-free half is fenced
off between BEGIN/END PURE CORE markers instead, and this extracts that fence
and drives it through the same osascript eval harness as
test_section_span.py — the marker extraction mirrors what
test_applescript_line_endings.py does for AppleScript embedded in Python.

planRender is what keeps a background refresh (the 4-8s apply poll, the toast
timer, visibilitychange) from rebuilding a card the user is typing into: an
unchanged card yields no DOM op at all, and the card holding the focused
editor is never replaced, moved, or removed while it is pinned.
"""

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ASSET = (Path(__file__).resolve().parents[2] / "stow" / "devonthink" /
         ".local" / "share" / "entity-review" / "index.html")

FENCE = re.compile(r"BEGIN PURE CORE \*/(.*?)/\* END PURE CORE", re.S)

HARNESS = textwrap.dedent("""
    ObjC.import('Foundation')

    function readText(path) {
      return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(
        path, $.NSUTF8StringEncoding, $()))
    }

    function run(argv) {
      const coreSrc = readText(argv[0])
      const cases = JSON.parse(readText(argv[1]))
      eval(coreSrc)
      const registry = {
        fpOf: fpOf,
        emptyOverlay: emptyOverlay,
        overlayOf: overlayOf,
        editedValue: editedValue,
        isOff: isOff,
        pruneOverlay: pruneOverlay,
        planRender: planRender,
        buildSpec: buildSpec,
      }
      return JSON.stringify(cases.map(function (c) {
        return registry[c.fn].apply(null, c.args)
      }))
    }
""")


def make_tmp(name):
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / name
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def extract_core():
    m = FENCE.search(ASSET.read_text())
    if not m:
        raise AssertionError(f"pure-core markers missing from {ASSET}")
    return m.group(1)


def overlay(edits=None, off=None):
    return {"edits": edits or {}, "off": {k: True for k in (off or [])}}


def items(*pairs):
    return [{"key": k, "fp": fp} for k, fp in pairs]


class PureCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-entity-review-ui-test")
        cls.core = cls.tmp / "core.js"
        cls.core.write_text(extract_core())

    def call(self, fn, args):
        payload = self.tmp / "cases.json"
        payload.write_text(json.dumps([{"fn": fn, "args": args}]))
        harness = self.tmp / "harness.js"
        harness.write_text(HARNESS)
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", str(harness),
             str(self.core), str(payload)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise AssertionError(f"osascript failed: {result.stderr.strip()}")
        return json.loads(result.stdout)[0]

    def plan(self, current, desired, pinned=None):
        return self.call("planRender", [current, desired, pinned])

    def spec(self, pv, ov):
        return self.call("buildSpec", [pv, ov])


class Reconcile(PureCore):
    def test_unchanged_queue_touches_nothing(self):
        cards = items(("h:cands", "a1"), ("c-u1", "b2"), ("c-u2", "c3"))
        plan = self.plan(cards, cards)
        self.assertEqual(plan["remove"], [])
        self.assertEqual(plan["replace"], [])
        self.assertEqual(plan["insert"], [])
        self.assertEqual(plan["defer"], [])
        self.assertFalse(plan["stale"])

    def test_unchanged_queue_touches_nothing_while_editing(self):
        cards = items(("h:cands", "a1"), ("c-u1", "b2"), ("c-u2", "c3"))
        plan = self.plan(cards, cards, "c-u1")
        self.assertEqual(
            [plan["remove"], plan["replace"], plan["insert"], plan["defer"]],
            [[], [], [], []])

    def test_pinned_card_is_deferred_not_replaced(self):
        current = items(("c-u1", "b2"), ("c-u2", "c3"))
        desired = items(("c-u1", "CHANGED"), ("c-u2", "ALSO"))
        plan = self.plan(current, desired, "c-u1")
        self.assertEqual(plan["defer"], ["c-u1"])
        self.assertEqual(plan["replace"], ["c-u2"])
        self.assertFalse(plan["stale"])

    def test_pinned_card_survives_disappearing_and_reports_stale(self):
        current = items(("h:cands", "a1"), ("c-u1", "b2"), ("c-u2", "c3"))
        desired = items(("h:cands", "a1"), ("c-u2", "c3"))
        plan = self.plan(current, desired, "c-u1")
        self.assertEqual(plan["remove"], [])
        self.assertTrue(plan["stale"])
        self.assertEqual(plan["order"], ["h:cands", "c-u1", "c-u2"])

    def test_pinned_card_holds_its_slot_at_the_front(self):
        current = items(("c-u1", "b2"), ("c-u2", "c3"))
        desired = items(("c-u2", "c3"))
        plan = self.plan(current, desired, "c-u1")
        self.assertEqual(plan["order"], ["c-u1", "c-u2"])

    def test_unpinned_card_is_removed(self):
        current = items(("c-u1", "b2"), ("c-u2", "c3"))
        desired = items(("c-u2", "c3"))
        plan = self.plan(current, desired)
        self.assertEqual(plan["remove"], ["c-u1"])
        self.assertFalse(plan["stale"])
        self.assertEqual(plan["order"], ["c-u2"])

    def test_new_cards_insert_and_order_follows_desired(self):
        current = items(("c-u2", "c3"))
        desired = items(("h:cands", "a1"), ("c-u1", "b2"), ("c-u2", "c3"))
        plan = self.plan(current, desired)
        self.assertEqual(plan["insert"], ["h:cands", "c-u1"])
        self.assertEqual(plan["order"], ["h:cands", "c-u1", "c-u2"])

    def test_unkeyed_placeholder_is_replaced_wholesale(self):
        plan = self.plan(items(("", "")), items(("state:clear", "z9")))
        self.assertEqual(plan["remove"], [""])
        self.assertEqual(plan["insert"], ["state:clear"])

    def test_fingerprint_is_stable_and_discriminating(self):
        html = '<div class="card">Jane</div>'
        self.assertEqual(self.call("fpOf", [html]), self.call("fpOf", [html]))
        self.assertNotEqual(self.call("fpOf", [html]),
                            self.call("fpOf", [html.replace("Jane", "Jane S")]))


class Overlay(PureCore):
    def test_edited_value_falls_back_to_server_text(self):
        ov = overlay({"cname": "Jane Smith"})
        self.assertEqual(self.call("editedValue", [ov, "cname", "Jane"]),
                         "Jane Smith")
        self.assertEqual(self.call("editedValue", [ov, "pname:0", "Ada"]), "Ada")

    def test_empty_string_edit_is_honoured_not_treated_as_absent(self):
        ov = overlay({"fact:0:0": ""})
        self.assertEqual(self.call("editedValue", [ov, "fact:0:0", "old"]), "")

    def test_prune_drops_dead_cards_but_keeps_the_pinned_one(self):
        store = {"c-u1": overlay({"cname": "A"}),
                 "c-u2": overlay({"cname": "B"}),
                 "c-u3": overlay({"cname": "C"})}
        kept = self.call("pruneOverlay", [store, ["c-u2"], "c-u3"])
        self.assertEqual(sorted(kept.keys()), ["c-u2", "c-u3"])

    def test_prune_without_a_pin_keeps_only_live_cards(self):
        store = {"c-u1": overlay({"cname": "A"}), "c-u2": overlay()}
        kept = self.call("pruneOverlay", [store, ["c-u2"], None])
        self.assertEqual(list(kept.keys()), ["c-u2"])


PERSON = {
    "kind": "existing", "name": "Jane", "interacted": True,
    "facts": [["2026-07-01", "runs the book club"],
              ["2026-07-02", "moved to Leeds"]],
    "updates": {"Employer": "Acme", "City": "Leeds"},
}

EVENT = {
    "kind": "event", "name": "Book club", "date": "2026-07-01",
    "location": "Library", "attendees": ["Jane"], "summary": "monthly meet",
}


class SpecCollection(PureCore):
    def test_untouched_proposal_is_unchanged(self):
        spec = self.spec({"plans": [PERSON]}, overlay())
        self.assertFalse(spec["changed"])
        self.assertEqual(spec["people"][0]["name"], "Jane")
        self.assertEqual(len(spec["people"][0]["facts"]), 2)
        self.assertEqual(spec["people"][0]["updates"],
                         {"Employer": "Acme", "City": "Leeds"})

    def test_renamed_person_carries_the_edit(self):
        spec = self.spec({"plans": [PERSON]},
                         overlay({"pname:0": "Jane Smith"}))
        self.assertTrue(spec["changed"])
        self.assertEqual(spec["people"][0]["name"], "Jane Smith")

    def test_edited_fact_text_carries_the_edit(self):
        spec = self.spec({"plans": [PERSON]},
                         overlay({"fact:0:1": "moved to York"}))
        self.assertTrue(spec["changed"])
        self.assertEqual([f["fact"] for f in spec["people"][0]["facts"]],
                         ["runs the book club", "moved to York"])

    def test_edited_field_value_carries_the_edit(self):
        spec = self.spec({"plans": [PERSON]},
                         overlay({"field:0:City": "York"}))
        self.assertTrue(spec["changed"])
        self.assertEqual(spec["people"][0]["updates"]["City"], "York")

    def test_deselected_rows_drop_out(self):
        spec = self.spec({"plans": [PERSON]},
                         overlay(off=["fact:0:0", "field:0:Employer"]))
        self.assertTrue(spec["changed"])
        self.assertEqual([f["fact"] for f in spec["people"][0]["facts"]],
                         ["moved to Leeds"])
        self.assertEqual(spec["people"][0]["updates"], {"City": "Leeds"})

    def test_deselected_plan_drops_the_whole_person(self):
        spec = self.spec({"plans": [PERSON]}, overlay(off=["plan:0"]))
        self.assertTrue(spec["changed"])
        self.assertEqual(spec["people"], [])

    def test_plan_off_beats_a_re_enabled_row(self):
        spec = self.spec({"plans": [PERSON]},
                         overlay({"fact:0:0": "kept"}, off=["plan:0"]))
        self.assertEqual(spec["people"], [])

    def test_person_stripped_to_nothing_is_omitted(self):
        spec = self.spec(
            {"plans": [PERSON]},
            overlay(off=["fact:0:0", "fact:0:1", "field:0:Employer",
                         "field:0:City"]))
        self.assertEqual(spec["people"], [])
        self.assertTrue(spec["changed"])

    def test_events_pass_through_and_can_be_deselected(self):
        spec = self.spec({"plans": [EVENT, PERSON]}, overlay())
        self.assertEqual(spec["events"][0]["name"], "Book club")
        self.assertEqual(spec["events"][0]["attendees"], ["Jane"])
        off = self.spec({"plans": [EVENT, PERSON]}, overlay(off=["plan:0"]))
        self.assertEqual(off["events"], [])
        self.assertTrue(off["changed"])
        self.assertEqual(off["people"][0]["name"], "Jane")

    def test_slots_are_scoped_per_plan(self):
        second = dict(PERSON, name="Ada")
        spec = self.spec({"plans": [PERSON, second]},
                         overlay({"pname:1": "Ada L"}, off=["fact:1:0"]))
        self.assertEqual(spec["people"][0]["name"], "Jane")
        self.assertEqual(len(spec["people"][0]["facts"]), 2)
        self.assertEqual(spec["people"][1]["name"], "Ada L")
        self.assertEqual(len(spec["people"][1]["facts"]), 1)


class CaptureEditor(unittest.TestCase):
    def test_legacy_retention_action_and_finished_label(self):
        script = ASSET.read_text().split("<script>", 1)[1].split('document.addEventListener("click"', 1)[0]
        probe = r'''
        (async function () {
          local = {};
          const capture = {uuid:"SOURCE-FAKE", status:"question", text:"Wren moved.", revision:"A",
            subjects:[], choices:[], can_retain_legacy:true};
          queue = {captures:[capture], roster:[]};
          const question = captureCard(capture, {});
          const finished = captureCard({...capture, status:"retained", can_retain_legacy:false}, {});
          let posted, message;
          api = async function (path, body) { posted = body; return {status:"retained"}; };
          toast = function (text) { message = text; };
          refresh = async function () {};
          await decideCapture(capture.uuid, "retain");
          console.log(JSON.stringify({question, finished, posted, message}));
        })().catch(function (error) { console.error(error); process.exitCode = 1; });
        '''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture-retention.js"
            path.write_text(script + probe)
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertIn('data-act="capture-retain"', row["question"])
        self.assertIn("Existing filings kept", row["finished"])
        self.assertNotIn("Save to the selected people", row["finished"])
        self.assertNotIn("Filing undone", row["finished"])
        self.assertEqual(row["posted"], {"action": "retain", "revision": "A"})
        self.assertEqual(row["message"], "Existing filings kept")

    def test_refresh_keeps_the_editor_revision_and_rejected_draft(self):
        script = ASSET.read_text().split("<script>", 1)[1].split('document.addEventListener("click"', 1)[0]
        probe = r'''
        (async function () {
          const results = [];
          for (const status of ["filed", "question"]) {
            local = {};
            const capture = {uuid:"SOURCE-FAKE", status, text:"Wren moved to Denver.", revision:"A",
              subjects:[{mention:"Wren", passage:"Wren moved to Denver.", uuid:"PERSON-FAKE", email:""}], choices:[]};
            let server = {dt:"ok", captures:[capture], roster:[]};
            queue = server;
            let posted, message;
            api = async function (path, body) {
              if (body) { posted = body; throw new Error("The note changed."); }
              return server;
            };
            banner = function (text) { message = text; };
            render = function () {
              for (const cv of queue.captures) captureCard(cv, overlayOf(local, "c-" + cv.uuid));
            };
            if (status === "filed") captureOverlay(capture.uuid).captureEdit = true;
            render();
            server = {dt:"ok", captures:[{...capture, text:"Wren moved to Oslo.", revision:"B"}], roster:[]};
            await refresh(false);
            const draft = captureOverlay(capture.uuid);
            draft.captureText = "Wren moved to Prague.";
            draft.captureRows[0].passage = draft.captureText;
            await decideCapture(capture.uuid, "save");
            results.push({posted, text:local["c-" + capture.uuid].captureText, message});
          }
          console.log(JSON.stringify(results));
        })().catch(function (error) { console.error(error); process.exitCode = 1; });
        '''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture-editor.js"
            path.write_text(script + probe)
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for row in json.loads(result.stdout):
            self.assertEqual(row["posted"]["revision"], "A")
            self.assertEqual(row["posted"]["text"], "Wren moved to Prague.")
            self.assertEqual(row["posted"]["subjects"][0]["passage"], "Wren moved to Prague.")
            self.assertEqual(row["text"], "Wren moved to Prague.")
            self.assertEqual(row["message"], "The note changed.")

    def test_migration_uses_same_semantic_renderer_and_never_reports_saved(self):
        script = ASSET.read_text().split("<script>", 1)[1].split('document.addEventListener("click"', 1)[0]
        probe = r'''
        (async function () {
          const planId = "a".repeat(64);
          const cv = {uuid:"migration:" + planId + ":AAA-111", source_uuid:"AAA-111", migration_plan:planId,
            status:"question", date:"2026-09-01", revision:"frozen", text:"Wren has two siblings.", subjects:[], choices:[],
            semantic_questions:[{key:"q", evidence:"Wren has two siblings.", assertion:"Has two siblings.",
              observed_date:"2026-09-01", candidates:[{id:"b".repeat(64), text:"Has two siblings.", temporal_context:"observed:2026-08-01"}]}]};
          queue = {dt:"ok", captures:[cv], roster:[], candidates:[], proposals:[], queued:{candidates:0,proposals:0}};
          local = {};
          const html = captureCard(cv, emptyOverlay());
          captureOverlay(cv.uuid).captureAnswers = {q:"b".repeat(64)};
          let posted, endpoint, message;
          api = async function(path, body) {endpoint=path;posted=body;return {status:"migration_reviewed",plan_id:"c".repeat(64)}};
          toast = text => {message = text};
          refresh = async function() {};
          await decideCapture(cv.uuid, "migration");
          console.log(JSON.stringify({html, posted, endpoint, message}));
        })().catch(function(error) {console.error(error);process.exitCode=1});
        '''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration-review.js"
            path.write_text(script + probe)
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertIn("Same complete assertion", row["html"])
        self.assertIn("Keep separate", row["html"])
        self.assertIn("Keep source wording", row["html"])
        self.assertIn('<label>Disposition<select data-semantic-key="q">', row["html"])
        self.assertIn('</select></label>', row["html"])
        self.assertNotIn('data-act="capture-save"', row["html"])
        self.assertNotIn("Captured note<textarea", row["html"])
        self.assertEqual(row["endpoint"], "api/biographical-plan/" + "a" * 64)
        self.assertEqual(row["posted"], {"source_uuid": "AAA-111", "answers": {"q": "b" * 64}})
        self.assertEqual(row["message"], "Migration plan reviewed. Apply it separately.")


if __name__ == "__main__":
    unittest.main()
