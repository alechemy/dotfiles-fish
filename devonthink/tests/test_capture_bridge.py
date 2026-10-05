import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from helpers import BIN, load

ef = load("entity-filing.py", "capture_bridge_filing")

BRIDGE = BIN / "entity-dt-bridge.js"
HARNESS = textwrap.dedent(r"""
ObjC.import('Foundation')
function readText(path) {
  return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, $()))
}
function run(argv) {
  const source = readText(argv[0])
  const cases = JSON.parse(readText(argv[1]))
  eval(source)
  return JSON.stringify(cases.map(function(c) {
    try {
      if (c.fn === 'targets') return {value: captureTargets(c.evidence, c.people)}
      if (c.fn === 'source') return {value: captureSourceText(c.text)}
      if (c.fn === 'block') return {value: captureBlockMutation(c.text, c.id, c.block, c.expected)}
      if (c.fn === 'receipt') return {value: isMachineBullet(c.line)}
      if (c.fn === 'pinned') {
        let body = c.text.split('\n')
        for (const line of c.lines) body = appendPinned(body, line)
        return {value:body.join('\n')}
      }
      if (c.fn === 'registry') {
        const rows = Array.from({length: 250}, (_, i) => ({uuid: () => 'R' + i, modificationDate: () => i}))
        const queries = []
        const dt = {search: q => {queries.push(q); return q.includes('==') ? [rows[0]] : q.includes('NOT') ? [rows[1]] : rows}}
        const result = captureRegistry(dt, {root: () => ({})}, 200)
        return {value: {count:result.length, ids:result.map(r => r.uuid()), queries:queries}}
      }
      let count = 0
      let failure = c.failure || 0
      const records = []
      function mutation() {
        count++
        if (count === failure) throw new Error('injected mutation failure')
      }
      function record(uuid, name, body) {
        const md = {}
        const rec = {uuid: () => uuid, type: () => 'markdown', aliases: () => '', customMetaData: () => md}
        Object.defineProperty(rec, 'name', {get: () => () => name, set: v => {name=v; mutation()}})
        Object.defineProperty(rec, 'plainText', {get: () => () => body, set: v => {body=v; mutation()}})
        return rec
      }
      const src = record('SRC', 'Capture', '# Capture\n\nWren moved.')
      const groupAt = () => ({children: () => records})
      const byUuid = uuid => uuid === 'SRC' ? src : records.find(r => r.uuid() === uuid)
      let moved = null
      const dt = {
        move: op => {moved = op.record.uuid()},
        createRecordWith: p => {
          const rec = record('P' + (records.length + 1), p.name, '')
          records.push(rec)
          mutation()
          return rec
        },
        addCustomMetaData: (value, opts) => {
          opts.to.customMetaData()['md' + opts.for] = String(value)
          mutation()
        }
      }
      function handler(name) {
        const start = source.indexOf('    ' + name + '(op) {')
        const end = source.indexOf('\n    },', start)
        if (start < 0 || end < 0) throw new Error('handler missing')
        return Function('dt', 'groupAt', 'byUuid', 'mdValue', 'flagSet', 'captureSourceText',
                        'captureTargets', 'captureBlockMutation', 'personSkeleton', 'isoStamp', 'handlers', 'db', 'migrationBlocked',
          'const PEOPLE_PATH = "/People"; const FACTS_PATH = "/Facts"; let entityIndex = null; let peopleIndex = null; return ({' + source.slice(start, end + 7) + '})'
        )(dt, groupAt, byUuid, mdValue, flagSet, captureSourceText, captureTargets,
          captureBlockMutation, name => '# ' + name, isoStamp,
          {capture_retire_record: op => ({uuid: op.uuid, group: op.group})},
          {trashGroup: () => 'TRASH'}, () => false)
      }
      if (c.fn === 'guarded_write') {
        const op = {uuid:'SRC', text:'Updated text.'}
        if ('expected' in c) op.expected_text = c.expected === 'current' ? src.plainText() : c.expected
        let failed = false
        try {handler(c.operation)[c.operation](op)} catch(e) {failed = true}
        return {value:{text:src.plainText(), moved:moved, failed:failed}}
      }
      if (c.fn === 'retained_source') {
        src.location = () => '/Facts/'
        const value = JSON.stringify({source_uuid:'SRC', status:'retained'})
        handler('capture_store').capture_store({uuid:'SRC', expected:'', text:'Wren moved.', value:value})
        const retired = handler('capture_retire_source').capture_retire_source({uuid:'SRC'})
        return {value:{retired:retired, status:src.customMetaData().mdcapturestatus,
                      operation:src.customMetaData().mdcaptureoperation}}
      }
      if (c.fn === 'fact_sources') {
        src.additionDate = () => new Date(2026, 8, 29, 23, 59, 59)
        src.modificationDate = () => new Date(2026, 8, 30, 0, 1, 2)
        const filed = record('FILED-SRC', 'Older capture', 'Wren stayed.')
        filed.additionDate = () => null
        filed.modificationDate = () => null
        filed.customMetaData().mdneedsprocessing = true
        filed.customMetaData().mdcaptureoperation = 'frozen-operation'
        records.push(src, {type: () => 'group', children: () => [filed]})
        return {value: handler('list_fact_captures').list_fact_captures({})}
      }
      const person = handler('capture_person')
      const append = handler('capture_append')
      const op = {name:'Wren', uuid:'', evidence:{mention:'Wren', email:''},
                  creation_id:'abc', source_uuid:'SRC', text:'Wren moved.'}
      let failed = false
      try {
        const p = person.capture_person(op)
        append.capture_append({uuid:p.uuid, id:'abc', block:c.block,
                               evidence:op.evidence, source_uuid:'SRC', text:op.text})
      } catch(e) {failed = true}
      failure = 0
      if (c.clear_status) {
        records[0].customMetaData().mdentitystatus = ''
        op.uuid = records[0].uuid()
        op.initialize = false
      }
      const p = person.capture_person(op)
      append.capture_append({uuid:p.uuid, id:'abc', block:c.block,
                             evidence:op.evidence, source_uuid:'SRC', text:op.text})
      return {value:{count:records.length, name:records[0].name(), md:records[0].customMetaData(),
                    body:records[0].plainText(), initialized:p.initialized, failed:failed}}
    } catch(e) {return {error:String(e.message || e)}}
  }))
}
""")


def run_cases(cases):
    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / "harness.js"
        payload = Path(tmp) / "cases.json"
        harness.write_text(HARNESS)
        payload.write_text(json.dumps(cases))
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(harness),
                                 str(BRIDGE), str(payload)], capture_output=True,
                                text=True, timeout=60, check=True)
        return json.loads(result.stdout)


BLOCK = "<!-- capture:abc:begin -->\n> Wren moved.\n<!-- capture:abc:end -->"


class CaptureBridge(unittest.TestCase):
    def test_initialization_retries_after_each_real_handler_mutation(self):
        cases = [{"fn": "initialize", "failure": i, "block": BLOCK} for i in range(8)]
        results = run_cases(cases)
        for i, result in enumerate(results):
            with self.subTest(failure=i):
                self.assertNotIn("error", result)
                value = result["value"]
                self.assertEqual(value["count"], 1)
                self.assertEqual(value["name"], "Wren")
                self.assertTrue(value["initialized"])
                self.assertEqual(value["md"], {"mdentitytype": "Person", "mdentitystatus": "active"})
                self.assertEqual(value["body"].count(BLOCK), 1)
                self.assertEqual(value["failed"], i > 0)

    def test_committed_creation_does_not_restore_cleared_metadata(self):
        result = run_cases([{"fn": "initialize", "block": BLOCK, "clear_status": True}])[0]
        self.assertEqual(result["value"]["md"]["mdentitystatus"], "")
        self.assertEqual(result["value"]["body"].count(BLOCK), 1)

    def test_registry_keeps_unfinished_and_unindexed_operations_outside_history_limit(self):
        result = run_cases([{"fn": "registry"}])[0]["value"]
        self.assertEqual(result["count"], 202)
        self.assertIn("R0", result["ids"])
        self.assertIn("R1", result["ids"])
        self.assertNotIn("R2", result["ids"])
        self.assertIn("mdcapturestatus==correcting", result["queries"][0])

    def test_guarded_cleanup_rejects_stale_bodies_and_preserves_existing_callers(self):
        for operation in ("set_text", "trash"):
            cases = [{"fn": "guarded_write", "operation": operation, "expected": "stale"},
                     {"fn": "guarded_write", "operation": operation, "expected": "current"},
                     {"fn": "guarded_write", "operation": operation}]
            stale, current, compatible = [row["value"] for row in run_cases(cases)]
            with self.subTest(operation=operation):
                self.assertTrue(stale["failed"])
                self.assertEqual(stale["text"], "# Capture\n\nWren moved.")
                self.assertIsNone(stale["moved"])
                self.assertFalse(current["failed"])
                self.assertEqual(current, compatible)
                if operation == "set_text":
                    self.assertEqual(current["text"], "Updated text.")
                else:
                    self.assertEqual(current["moved"], "SRC")

    def test_retained_legacy_source_updates_its_index_and_retires_without_new_filing(self):
        result = run_cases([{"fn": "retained_source"}])[0]
        self.assertNotIn("error", result)
        self.assertEqual(result["value"]["status"], "retained")
        self.assertEqual(result["value"]["retired"], {"uuid": "SRC", "group": "/Facts/Filed"})
        self.assertEqual(json.loads(result["value"]["operation"])["status"], "retained")

    def test_migration_source_inventory_uses_local_dates_and_walks_filed_groups(self):
        result = run_cases([{"fn": "fact_sources"}])[0]
        self.assertNotIn("error", result)
        source, filed = result["value"]
        self.assertEqual(source["added"], "2026-09-29")
        self.assertEqual(source["added_at"], "2026-09-29T23:59:59")
        self.assertEqual(source["modified"], "2026-09-30T00:01:02")
        self.assertTrue(source["ready"])
        self.assertEqual(filed["uuid"], "FILED-SRC")
        self.assertEqual(filed["added"], "")
        self.assertEqual(filed["added_at"], "")
        self.assertFalse(filed["ready"])
        self.assertEqual(filed["capture_operation"], "frozen-operation")

    def test_modified_owned_block_is_refused(self):
        results = run_cases([
            {"fn": "block", "text": BLOCK.replace("moved", "retired"), "id": "abc", "block": BLOCK},
            {"fn": "block", "text": "Manual body", "id": "abc", "block": BLOCK, "expected": True},
            {"fn": "block", "text": BLOCK + "\n" + BLOCK, "id": "abc", "block": BLOCK},
        ])
        self.assertTrue(all("error" in r for r in results))

    def test_existing_block_is_idempotent_and_manual_content_survives(self):
        body = "Manual note.\n" + BLOCK + "\nLater note."
        self.assertEqual(run_cases([{"fn": "block", "text": body, "id": "abc", "block": BLOCK}]),
                         [{"value": body}])

    def test_source_h1_and_line_endings_match_python(self):
        texts = ["\r# Capture\r\rWren moved.\rRowan retired.",
                 "# Capture\n\nWren moved.\n", "# Capture\r\rWren moved.\r",
                 "# Capture\r\n\r\nWren moved.\r\n\r\n",
                 "Wren moved.\r\nRowan retired.\r\n", "Wren moved.\rRowan retired.\r",
                 "Wren moved.\n\n"]
        results = run_cases([{"fn": "source", "text": text} for text in texts])
        for text, result in zip(texts, results):
            with self.subTest(text=text):
                self.assertEqual(result, {"value": ef.normalize_source_text("fact", text)})

    def test_receipts_deduplicate_by_capture_not_person(self):
        first = "- 📝 Saved [Wren](x-devonthink-item://PERSON-1). <!-- capture-receipt:" + "a" * 64 + " -->"
        second = "- 📝 Saved [Wren](x-devonthink-item://PERSON-1). <!-- capture-receipt:" + "b" * 64 + " -->"
        later = "\n\n## Later\nManual sequel."
        for body in ("# Today\n\n- [Wren](x-devonthink-item://PERSON-1) joined." + later,
                     "# Today\n\n## Today's Notes\n\n- [Wren](x-devonthink-item://PERSON-1) joined." + later):
            result = run_cases([{"fn": "pinned", "text": body, "lines": [first, second, first]}])[0]["value"]
            expected = body.replace(later, "\n" + first + "\n" + second + later)
            self.assertEqual(result, expected)

    def test_live_short_name_and_email_checks(self):
        people = [{"uuid": "P1", "name": "Wren"}, {"uuid": "P2", "name": "Wren Vale", "email": "wv@x.com"}]
        results = run_cases([
            {"fn": "targets", "evidence": {"mention": "Wren"}, "people": people},
            {"fn": "targets", "evidence": {"mention": "Wren", "email": "wv@x.com"}, "people": people},
            {"fn": "targets", "evidence": {"mention": "Rowan"}, "people": people},
            {"fn": "targets", "evidence": {"mention": "Wren"}, "people": [{"uuid": "P3", "name": "Rowan Wren"}]},
        ])
        self.assertEqual(results, [{"value": ["P1", "P2"]}, {"value": ["P2"]}, {"value": []}, {"value": []}])

    def test_receipt_is_machine_generated_in_javascript(self):
        result = run_cases([{"fn": "receipt", "line": "- 📝 Saved your note about [Wren](x-devonthink-item://P1)."}])
        self.assertEqual(result, [{"value": True}])
