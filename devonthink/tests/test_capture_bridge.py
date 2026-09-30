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
      const dt = {
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
                        'captureTargets', 'captureBlockMutation', 'personSkeleton',
          'const PEOPLE_PATH = "/People"; let entityIndex = null; let peopleIndex = null; return ({' + source.slice(start, end + 7) + '})'
        )(dt, groupAt, byUuid, mdValue, flagSet, captureSourceText, captureTargets,
          captureBlockMutation, name => '# ' + name)
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
        cases = [{"fn": "initialize", "failure": i, "block": BLOCK} for i in range(7)]
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
