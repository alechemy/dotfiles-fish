import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, load

capture = load("entity_capture.py", "capture_bio_bridge")
HARNESS = r"""
ObjC.import('Foundation')
function readText(path) {
  return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, $()))
}
function run(argv) {
  var source = readText(argv[0])
  var cases = JSON.parse(readText(argv[1]))
  return eval(source + '\n const testSource = ' + JSON.stringify(source) + '; const cases = ' + JSON.stringify(cases) + ';' + `
    entityIndex = [];
    var answer = cases.map(c => {
      try {
        migrationFence = c.fence || null;
        entityIndex = c.entities || [];
        if (c.fn === 'fence') {
          try { checkMigrationFence(c.op); return {blocked:false} }
          catch(e) { return {blocked:true} }
        }
        if (c.fn === 'migration') {
          let body = c.body, operation = c.operation || '';
          const rec = {uuid: () => c.uuid,
            customMetaData: () => ({mdcaptureoperation:operation, mdentitytype:c.entitytype || '',
                                   mdfilingsuppressed:c.filingsuppressed || ''})};
          Object.defineProperty(rec, 'plainText', {get:() => () => body, set:v => {body = v}});
          const dt = {addCustomMetaData: value => {operation = value}};
          const start = testSource.indexOf('    biographical_migration_write(op) {');
          const end = testSource.indexOf('\\n    },', start);
          const handlers = Function('byUuid', 'mdValue', 'biographicalRows', 'dt',
            'return ({' + testSource.slice(start, end + 7) + '})')(() => rec, mdValue, biographicalRows, dt);
          try {
            for (const op of c.ops) {checkMigrationFence(op); handlers.biographical_migration_write(op)}
            return {body:body, operation:operation, stopped:false};
          } catch(e) {return {body:body, operation:operation, stopped:true}}
        }
        if (c.fn === 'literal') {
          return {encoded:encodeBiographicalLiteral(c.text),
                  decoded:decodeBiographicalLiteral(encodeBiographicalLiteral(c.text))};
        }
        if (c.fn === 'rows') return {rows:biographicalRows(c.body, c.person_uuid)};
        if (c.fn === 'write') {
          let body = c.body;
          let operation = c.operation || '';
          const sourceRecord = {plainText: () => '# Capture\\n\\nWren moved.', customMetaData: () => ({mdcaptureoperation:operation})};
          const target = {uuid: () => 'BBB-222', customMetaData: () => ({mdentitytype:'Person'})};
          Object.defineProperty(target, 'plainText', {get:() => () => body, set:v => {body = v}});
          const byUuid = id => id === 'AAA-111' ? sourceRecord : target;
          const start = testSource.indexOf('    biographical_write(op) {');
          const end = testSource.indexOf('\\n    },', start);
          const handlers = Function('byUuid', 'mdValue', 'captureSourceText', 'flagSet', 'biographicalRows',
            'return ({' + testSource.slice(start, end + 7) + '})')(byUuid, mdValue, captureSourceText, flagSet, biographicalRows);
          try {handlers.biographical_write(c.op); return {body:body, stopped:false}}
          catch(e) {return {body:body, stopped:true}}
        }
        let body = c.body;
        const rec = {uuid: () => 'BBB-222'};
        Object.defineProperty(rec, 'plainText', {get:() => () => body, set:v => {body = v}});
        const first = appendBiographical(rec, c.assertions);
        const second = appendBiographical(rec, c.assertions);
        return {body:body, first:first, second:second, rows:biographicalRows(body),
                signatures:bodyFactSignatures(body.split('\\n'))};
      } catch(e) {return {error:String(e.message || e)}}
    });
    JSON.stringify(answer);
  `)
}
"""


def cases(values):
    with tempfile.TemporaryDirectory() as directory:
        script, payload = Path(directory) / "harness.js", Path(directory) / "cases.json"
        script.write_text(HARNESS)
        payload.write_text(json.dumps(values))
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(script),
                                 str(BIN / "entity-dt-bridge.js"), str(payload)],
                                capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)


class BiographicalBridge(unittest.TestCase):
    def assertion(self):
        result = capture.resolve("Wren moved.", [{"mention": "Wren"}], [])
        return capture.prepare_unified({"uuid": "AAA-111"}, "2026-09-01", "Wren moved.", result,
            "2026-09-01")["subjects"][0]["contribution"]["assertions"][0]

    def test_passive_attachment_to_capture_prevents_undo_and_replay_duplicates(self):
        import entity_biographical as bio
        assertion = self.assertion()
        body = bio.attach("# Wren\n\n", assertion, "BBB-222")
        passive = json.loads(json.dumps(assertion))
        passive["reference"].update(id="e" * 64, kind="passive", source_uuid="CCC-333", evidence_kind="extracted_assertion")
        result = cases([{"body": body, "assertions": [passive]}])[0]
        self.assertNotIn("error", result)
        self.assertEqual(len(result["rows"][0]["data"]["references"]), 2)
        self.assertEqual(result["body"].count(" — Moved."), 1)
        self.assertIn("CCC-333|- 2026-09-01 — Moved. (source)", result["signatures"])
        self.assertIn("Moved.", bio.detach(result["body"], assertion, "BBB-222"))
        self.assertEqual(result["second"], {"appended": 0, "skipped": 1})

    def test_auto_links_never_change_ownership_metadata(self):
        import entity_biographical as bio
        text = "Wren likes Rowan."
        resolved = capture.resolve(text, [{"mention": "Wren"}], [])
        assertion = capture.prepare_unified({"uuid": "AAA-111"}, "2026-09-01", text, resolved,
            "2026-09-01")["subjects"][0]["contribution"]["assertions"][0]
        entities = [{"name": "Rowan", "uuid": "DDD-444"}, {"name": "observed", "uuid": "EEE-555"}]
        result = cases([{"body": "# Wren\n\n", "assertions": [assertion], "entities": entities}])[0]
        self.assertNotIn("error", result)
        self.assertIn("Likes [Rowan](x-devonthink-item://DDD-444)", result["body"])
        self.assertEqual(result["rows"][0]["data"]["references"], [assertion["reference"]])
        self.assertEqual(result["rows"][0]["data"]["baseline"], assertion["baseline"])
        bio.verify(result["body"], assertion, "BBB-222")

    def test_passive_real_repeated_dated_events_remain_separate(self):
        first = self.assertion()
        first["reference"].update(kind="passive", evidence_kind="extracted_assertion")
        second = json.loads(json.dumps(first))
        second["id"] = "d" * 64
        second["reference"].update(id="b" * 64, original_date="2026-09-02")
        second["baseline"].update(log_date="2026-09-02", temporal_context="observed:2026-09-02")
        result = cases([{"body": "# Wren\n\n", "assertions": [first, second]}])[0]
        self.assertNotIn("error", result)
        self.assertEqual(len(result["rows"]), 2)
        self.assertLess(result["body"].index("2026-09-02"), result["body"].index("2026-09-01"))

    def test_fence_covers_v1_correction_passive_fields_sort_and_migration(self):
        fence = {"version": 2, "plan_id": "a" * 64, "scope": ["AAA-111", "BBB-222"], "names": ["wren"]}
        operations = [{"op": op, "uuid": "BBB-222"} for op in
                      ("capture_append", "capture_remove", "append_log", "set_field", "set_text", "capture_store")]
        operations += [{"op": "capture_person", "name": "Wren"},
                       {"op": "capture_source_edit", "uuid": "AAA-111"},
                       {"op": "biographical_migration_write", "uuid": "BBB-222", "migration_plan": "bad"},
                       {"op": "biographical_migration_write", "uuid": "BBB-222", "migration_plan": "a" * 64},
                       {"op": "append_log", "uuid": "FFF-444"}, {"op": "get_text", "uuid": "BBB-222"}]
        results = cases([{"fn": "fence", "fence": fence, "op": op} for op in operations])
        self.assertEqual([r["blocked"] for r in results], [True] * 9 + [False] * 3)

    def test_actual_write_handler_revalidates_source_operation_body_and_destinations(self):
        op = {"uuid": "BBB-222", "source_uuid": "AAA-111", "text": "Wren moved.",
              "expected_operation": "frozen", "expected_body": "before", "body": "after",
              "destinations": []}
        values = [{"fn": "write", "body": "before", "operation": "frozen", "op": op}]
        for field, stale in (("text", "Changed source."), ("expected_operation", "stale"),
                             ("expected_body", "stale"), ("destinations", [{"uuid": "DDD-444", "body": "stale"}])):
            values.append({"fn": "write", "body": "before", "operation": "frozen", "op": dict(op, **{field: stale})})
        results = cases(values)
        self.assertEqual(results[0], {"body": "after", "stopped": False})
        self.assertTrue(all(result == {"body": "before", "stopped": True} for result in results[1:]))

    def test_literal_escape_and_supplementary_unicode_match_python(self):
        import entity_biographical as bio
        texts = ['<!-- bio:v2:example -->', '<\u2060!-- bio:v2:escaped -->',
                 '<\u2060\u2060!-- fact:abcd -->', '&lt;!-- bio:v2:entity --&gt; &amp;',
                 '`<!-- capture:literal:begin -->` "<!-- capture-created:literal -->"',
                 '<!-- capture-receipt:literal --> 😀 𐐀 é e\u0301']
        results = cases([{"fn": "literal", "text": text} for text in texts])
        for text, result in zip(texts, results):
            self.assertEqual(result, {"encoded": bio.encode_literal(text), "decoded": text})
        text = 'Wren likes 😀 and 𐐀.\n"<!-- bio:v2:literal -->" &lt;!-- fact:abcd --&gt;'
        resolved = capture.resolve(text, [{"mention": "Wren"}], [])
        assertion = capture.prepare_unified({"uuid": "AAA-111"}, "2026-09-01", text, resolved,
            "2026-09-01")["subjects"][0]["contribution"]["assertions"][0]
        body = bio.attach("# Wren\n\n", assertion, "BBB-222")
        parsed, appended = cases([{"fn": "rows", "body": body, "person_uuid": "BBB-222"},
                                  {"body": "# Wren\n\n", "assertions": [assertion]}])
        self.assertNotIn("error", parsed)
        self.assertNotIn("error", appended)
        self.assertEqual(parsed["rows"][0]["visible"], assertion["baseline"]["text"])
        expected = dict(parsed["rows"][0]["data"], protected=True)
        self.assertEqual(bio.render(appended["body"], "BBB-222")[0]["data"], expected)
        bio.verify(appended["body"], assertion, "BBB-222")
        wrong_length = json.loads(json.dumps(assertion))
        wrong_length["reference"]["end"] += 2
        self.assertIn("error", cases([{"body": "# Wren\n\n", "assertions": [wrong_length]}])[0])

    def test_copied_binding_and_zero_support_fail_closed_in_actual_jxa(self):
        import entity_biographical as bio
        assertion = self.assertion()
        body = bio.attach("# Wren\n\n", assertion, "BBB-222")
        data = bio.render(body)[0]["data"]
        copied = json.loads(json.dumps(data))
        copied["person_uuid"] = "DDD-444"
        empty = json.loads(json.dumps(data))
        empty["references"] = []
        protected = dict(empty, protected=True)
        missing = dict(data)
        del missing["person_uuid"]
        values = [copied, empty, missing, protected]
        results = cases([{"body": "## Biographical Log\n" + bio.format_data(value),
                          "assertions": [assertion]} for value in values])
        self.assertTrue(all("error" in result for result in results[:3]))
        self.assertNotIn("error", results[3])
        self.assertTrue(results[3]["rows"][0]["data"]["protected"])
        self.assertEqual(len(results[3]["rows"][0]["data"]["references"]), 1)
        parsed = cases([{"fn": "rows", "body": "## Biographical Log\n" + bio.format_data(protected),
                         "person_uuid": "BBB-222"}])[0]
        self.assertEqual(parsed["rows"][0]["data"]["references"], [])
        op = {"uuid": "BBB-222", "source_uuid": "AAA-111", "text": "Wren moved.",
              "expected_operation": "frozen", "expected_body": body,
              "body": "## Biographical Log\n" + bio.format_data(copied), "destinations": []}
        result = cases([{"fn": "write", "body": body, "operation": "frozen", "op": op}])[0]
        self.assertEqual(result, {"body": body, "stopped": True})

    def test_quoted_markers_are_inert_and_grouping_preserves_support(self):
        import entity_biographical as bio
        first = self.assertion()
        text = 'Wren has plans.'
        resolved = capture.resolve(text, [{"mention": "Wren"}], [])
        second = capture.prepare_unified({"uuid": "CCC-333"}, "2026-09-01", text, resolved,
            "2026-09-01")["subjects"][0]["contribution"]["assertions"][0]
        body = bio.attach("# Wren\n\n", first, "BBB-222")
        quoted = "\n".join("  > > " + line for line in body.splitlines())
        inert = cases([{"fn": "rows", "body": quoted, "person_uuid": "BBB-222"},
                       {"fn": "rows", "body": body + "\n\n" + quoted, "person_uuid": "BBB-222"}])
        self.assertEqual(inert[0], {"rows": []})
        self.assertEqual(len(inert[1]["rows"]), 1)
        body = bio.attach(body, second, "BBB-222")
        result = cases([{"body": body, "assertions": [second]}])[0]
        self.assertNotIn("error", result)
        self.assertIn("- 2026-09-01\n  - Moved.", result["body"])
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["second"], {"appended": 0, "skipped": 1})
        for assertion in (first, second):
            bio.verify(result["body"], assertion, "BBB-222")
        detached = bio.detach(result["body"], first, "BBB-222")
        bio.verify(detached, second, "BBB-222")
        self.assertEqual(bio.render(bio.detach(detached, second, "BBB-222")), [])

    def test_frozen_migration_handler_writes_and_reverses_quoted_v1_body(self):
        import entity_biographical as bio
        row = self.assertion()
        before = '# Wren\n\n> > <!-- bio:v2:quoted v1 source -->\n'
        after = bio.attach(before, row, "BBB-222")
        fence = {"version": 2, "plan_id": "a" * 64, "scope": ["BBB-222"], "names": ["wren"]}
        controls = {"entitytype": "Person", "filingsuppressed": ""}
        forward = {"op": "biographical_migration_write", "uuid": "BBB-222", "migration_plan": "a" * 64,
                   "before": {"body": before, "operation": ""},
                   "after": {"body": after, "operation": ""}, "controls": controls}
        reverse = dict(forward, before=forward["after"], after=forward["before"])
        values = [{"fn": "migration", "uuid": "BBB-222", "body": before, "entitytype": "Person",
                   "fence": fence, "ops": [forward, reverse]}]
        for changed in (dict(forward, controls=dict(controls, filingsuppressed="true")),
                        dict(forward, before=dict(forward["before"], operation="stale")),
                        dict(forward, after={"body": after.replace('"person_uuid":"BBB-222"',
                                                                   '"person_uuid":"DDD-444"'), "operation": ""}),
                        dict(forward, migration_plan="b" * 64)):
            values.append({"fn": "migration", "uuid": "BBB-222", "body": before,
                           "entitytype": "Person", "fence": fence, "ops": [changed]})
        results = cases(values)
        self.assertEqual(results[0], {"body": before, "operation": "", "stopped": False})
        self.assertTrue(all(result == {"body": before, "operation": "", "stopped": True}
                            for result in results[1:]))
        operation = dict(forward, uuid="AAA-111", controls={"entitytype": "", "filingsuppressed": ""},
                         before={"body": "# Capture\n\nWren moved.", "operation": "v1-frozen"},
                         after={"body": "# Capture\n\nWren moved.", "operation": "v2-frozen"})
        reversed_operation = dict(operation, before=operation["after"], after=operation["before"])
        result = cases([{"fn": "migration", "uuid": "AAA-111", "body": operation["before"]["body"],
                         "operation": "v1-frozen", "fence": dict(fence, scope=["AAA-111"]),
                         "ops": [operation, reversed_operation]}])[0]
        self.assertEqual(result, {"body": operation["before"]["body"], "operation": "v1-frozen", "stopped": False})

    def test_malformed_and_edited_ownership_stops_passive_attachment(self):
        import entity_biographical as bio
        assertion = self.assertion()
        body = bio.attach("# Wren\n\n", assertion, "BBB-222")
        for edited in (body + body, body.replace("Moved. ([source]", "Moved twice. ([source]"),
                       body.replace('"references":', '"broken":')):
            result = cases([{"body": edited, "assertions": [assertion]}])[0]
            self.assertIn("error", result)
