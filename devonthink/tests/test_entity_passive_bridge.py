import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, load
from test_entity_biographical_bridge import HARNESS

passive = load("entity_passive.py", "passive_bridge")
ef = load("entity-filing.py", "passive_bridge_filing")
bio = passive.bio

PASSIVE_HARNESS = HARNESS.replace("if (c.fn === 'literal') {", r"""
        if (c.fn === 'passive_hash') return {hash:passiveHash(c.text), encoded:passiveEncoded(c.value)};

        if (c.fn === 'passive_handlers') {
          let mutationCount = 0, failure = c.failure || 0, calls = 0;
          const records = [];
          for (const p of c.initial || []) records.push(record(p.uuid,p.name,p.body,p.md));
          const mutation = () => {mutationCount++; if (failure && mutationCount === failure) throw new Error('interrupted')};
          function record(uuid, name, body, initialMd) {
            const md = initialMd || {};
            let aliases = '';
            const rec = {uuid:() => uuid, type:() => 'markdown', customMetaData:() => md};
            Object.defineProperty(rec, 'name', {get:() => () => name, set:v => {name=v; mutation()}});
            Object.defineProperty(rec, 'plainText', {get:() => () => body, set:v => {body=v; mutation()}});
            Object.defineProperty(rec, 'aliases', {get:() => () => aliases, set:v => {aliases=v; mutation()}});
            return rec;
          }
          const byUuid = uuid => records.find(rec => rec.uuid() === uuid);
          const groupAt = () => ({children:() => records});
          const findPerson = name => records.filter(rec => personKeys(rec).includes(normName(name)));
          const dt = {
            createRecordWith:properties => {const rec=record(records.length ? 'CCC-333' : 'BBB-222',properties.name,''); records.push(rec); mutation(); return rec},
            addCustomMetaData:(value, options) => {options.to.customMetaData()['md'+options.for]=String(value); mutation()},
            move:() => {mutation()}
          };
          const sources = c.sources;
          const services = {
            record:byUuid, source:id => sources.find(source => source.uuid === id), ignored:() => false,
            people:() => records.map(rec => ({uuid:rec.uuid(), name:rec.name(), aliases:rec.aliases(), body:rec.plainText(),
              email:mdValue(rec,'email'), entitytype:mdValue(rec,'entitytype'), filingsuppressed:mdValue(rec,'filingsuppressed')}))
          };
          const guard = () => {
            calls++;
            if (c.plan.status !== 'ready') throw new Error('waiting');
            if (c.race && calls === c.race) sources[0].raw_text = 'Changed source.';
            validatePassivePlan(c.plan, c.plan.ops, services);
          };
          passiveGuard = guard;
          const handlers = {};
          for (const name of ['ensure_person','append_log','set_field','add_aliases']) {
            const start = testSource.indexOf('    ' + name + '(op) {');
            const end = testSource.indexOf('\\n    },',start);
            Object.assign(handlers, Function('dt','groupAt','byUuid','findPerson','migrationBlocked','passiveGuard','mdValue','flagSet',
              'unionAliases','personSkeleton','appendLogLines','lastContactGuard','normalizeEmail',
              'const PEOPLE_PATH="/People";let entityIndex=null;let peopleIndex=null;return ({'+testSource.slice(start,end+7)+'})')(
              dt,groupAt,byUuid,findPerson,() => false,guard,mdValue,flagSet,unionAliases,name => '# '+name+'\\n\\n',
              appendLogLines,lastContactGuard,normalizeEmail));
          }
          let failed = false;
          function apply() {
            guard();
            for (const frozen of c.plan.ops) {
              const op = Object.assign({},frozen);
              guard();
              if (op.target_name) op.uuid=findPerson(op.target_name)[0].uuid();
              handlers[op.op](op);
            }
          }
          try {apply()} catch(e) {failed=true}
          failure=0;
          if (!c.race && c.plan.status === 'ready') apply();
          passiveGuard=null;
          return {failed:failed, records:records.map(rec => ({name:rec.name(),body:rec.plainText(),md:rec.customMetaData(),aliases:rec.aliases()}))};
        }
        if (c.fn === 'passive_validate') {
          const records = c.records || {};
          const services = {
            record:id => ({plainText:() => records[id].text,
                            customMetaData:() => records[id].md || {}}),
            source:id => c.sources.find(s => s.uuid === id),
            people:() => c.people,
            ignored:() => !!c.ignored
          };
          validatePassivePlan(c.plan, c.plan.ops, services);
          return {valid:true};
        }
        if (c.fn === 'literal') {
""")


def cases(values):
    with tempfile.TemporaryDirectory() as directory:
        script, payload = Path(directory) / "harness.js", Path(directory) / "cases.json"
        script.write_text(PASSIVE_HARNESS)
        payload.write_text(json.dumps(values))
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(script),
                                 str(BIN / "entity-dt-bridge.js"), str(payload)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)


class PassiveBridge(unittest.TestCase):
    def setUp(self):
        self.source = {"uuid": "AAA-111", "name": "Fictional meeting", "kind": "meeting", "eventdate": "2026-09-01", "added": "2026-09-01", "ready": True}
        self.text = "Wren moved to Denver."
        self.person = {"uuid": "BBB-222", "name": "Wren Vale", "aliases": "Wren", "md": {"mdentitytype": "Person"}, "body": "# Wren\n\n"}
        plan = {"kind": "existing", "uuid": "BBB-222", "name": "Wren Vale", "aliases": "Wren", "md": {}, "facts": [("2026-09-01", self.text)], "updates": {}}
        self.ops = ef.ops_for_plan(plan, dict(self.source, text_revision=bio.digest(self.text)), "2026-09-01")
        self.saved = copy.deepcopy(self.ops[0]["assertions"][0])
        self.saved["id"] = "b" * 64
        self.saved["reference"]["id"] = "c" * 64
        self.saved["baseline"]["text"] = "Lives in Denver."
        self.person["body"] = bio.attach(self.person["body"], self.saved, "BBB-222")
        envelope = passive.prepare(self.ops, [passive.snapshot(self.source, self.text, self.text, "2026-09-01")], [self.person], lambda *args: [self.saved["id"]])
        self.ready = passive.answer(envelope, {envelope["questions"][0]["key"]: self.saved["id"]}, [self.person])

    def value(self):
        return {"fn": "passive_validate", "plan": copy.deepcopy(self.ready), "sources": [dict(self.source, raw_text=self.text, raw_field="plainText")],
                "people": [{**passive.controls(self.person), "body": self.person["body"]}], "records": {}}

    def test_frozen_choice_source_identity_and_proposal_are_rechecked_in_jxa(self):
        initial = self.value()
        values = [initial]
        for field, replacement in (("raw_text", "Wren did not move."), ("eventdate", "2026-09-02"), ("raw_field", "comment"), ("ready", False)):
            value = self.value()
            value["sources"][0][field] = replacement
            values.append(value)
        for field, replacement in (("name", "Rowan Vale"), ("aliases", "Other"), ("email", "wren@x.com"), ("filingsuppressed", "true"), ("entitytype", "Place")):
            value = self.value()
            value["people"][0][field] = replacement
            values.append(value)
        for body in ("# Wren\n", self.person["body"].replace("Lives in Denver. ([source]", "Lives elsewhere. ([source]"), self.person["body"].replace('"person_uuid":"BBB-222"', '"person_uuid":"CCC-333"')):
            value = self.value()
            value["people"][0]["body"] = body
            values.append(value)
        value = self.value()
        value["plan"]["proposal"] = {"uuid": "DDD-444", "text": "frozen"}
        value["records"] = {"DDD-444": {"text": "edited"}}
        values.append(value)
        results = cases(values)
        self.assertEqual(results[0], {"valid": True})
        self.assertTrue(all("error" in result for result in results[1:]), results)

    def test_unresolved_plan_and_modified_answers_stop_before_writes(self):
        values = []
        for status in ("waiting", "blocked", "question"):
            value = self.value()
            value["plan"]["status"] = status
            values.append(value)
        value = self.value()
        value["plan"]["answers"] = {}
        values.append(value)
        self.assertTrue(all("error" in r for r in cases(values)))

    def test_reference_growth_lastcontact_and_owned_identity_updates_are_allowed(self):
        value = self.value()
        additional = copy.deepcopy(self.saved)
        additional["reference"]["id"] = "d" * 64
        value["people"][0]["body"] = bio.attach(self.person["body"], additional, "BBB-222")
        value["people"][0]["lastcontact"] = "2026-09-10"
        self.assertEqual(cases([value]), [{"valid": True}])

    def test_explicit_ids_never_fall_back_and_separate_never_recoalesces(self):
        selected = self.ready["ops"][0]["assertions"][0]
        separate = copy.deepcopy(self.ops[0]["assertions"][0])
        separate.update(passive_explicit=True, separate=True)
        separate["baseline"]["text"] = self.saved["baseline"]["text"]
        values = [{"body": "# Wren\n", "assertions": [selected]},
                  {"body": self.person["body"], "assertions": [separate]}]
        result = cases(values)
        self.assertIn("error", result[0])
        self.assertNotIn("error", result[1])
        self.assertEqual(len(result[1]["rows"]), 2)
        self.assertEqual(result[1]["second"], {"appended": 0, "skipped": 1})

    def test_actual_new_person_handlers_recover_each_interrupted_mutation(self):
        source = passive.snapshot(self.source, self.text, self.text, "2026-09-01")
        incoming = copy.deepcopy(self.ops[0])
        incoming.update(uuid="new:Wren Vale", target_name="Wren Vale")
        inputs = [{"op": "ensure_person", "name": "Wren Vale", "aliases": "Wren"}, incoming,
                  {"op": "set_field", "uuid": "new:Wren Vale", "target_name": "Wren Vale", "field": "email", "value": "wren@x.com", "effective_date": "2026-09-01"}]
        plan = passive.prepare(inputs, [source], [], suggest=None)
        values = [{"fn": "passive_handlers", "plan": plan, "sources": [dict(self.source, raw_text=self.text, raw_field="plainText")], "failure": i} for i in range(1, 12)]
        for i, result in enumerate(cases(values)):
            with self.subTest(mutation=i + 1):
                self.assertNotIn("error", result)
                self.assertEqual(len(result["records"]), 1)
                rec = result["records"][0]
                self.assertEqual(rec["name"], "Wren Vale")
                self.assertEqual(rec["md"]["mdemail"], "wren@x.com")
                self.assertEqual(len(bio.render(rec["body"], "BBB-222")), 1)

    def test_actual_handler_source_race_refuses_destination_write(self):
        source = passive.snapshot(self.source, self.text, self.text, "2026-09-01")
        incoming = copy.deepcopy(self.ops[0])
        incoming.update(uuid="new:Wren Vale", target_name="Wren Vale")
        plan = passive.prepare([{"op": "ensure_person", "name": "Wren Vale"}, incoming], [source], [], suggest=None)
        result = cases([{"fn": "passive_handlers", "plan": plan, "sources": [dict(self.source, raw_text=self.text, raw_field="plainText")], "race": 2}])[0]
        self.assertNotIn("error", result)
        self.assertTrue(result["failed"])
        self.assertEqual(result["records"], [])

    def test_raw_hash_and_internal_json_parity_with_unicode_and_line_endings(self):
        texts = ["Wren 😀 𐐀 e\u0301 ß\n", "Wren\rtext\r", "Wren\r\ntext\r\n", "<literal>\u007f"]
        values = [{"fn": "passive_hash", "text": text, "value": {"text": text, "array": [True, None, "<x>"]}} for text in texts]
        for value, result in zip(values, cases(values)):
            self.assertEqual(result["hash"], bio.digest(value["text"]))
            self.assertEqual(result["encoded"], bio.encoded(value["value"]))

    def test_two_new_people_recover_every_write_with_normalized_emails(self):
        source = passive.snapshot(self.source, self.text, self.text, "2026-09-01")
        inputs = []
        for name, email in (("Wren Vale", "mailto:Wren@x.com"), ("Rowan Vale", "Rowan@x.com")):
            incoming = copy.deepcopy(self.ops[0])
            incoming.update(uuid="new:" + name, target_name=name)
            inputs += [{"op": "ensure_person", "name": name}, incoming,
                       {"op": "set_field", "uuid": "new:" + name, "target_name": name, "field": "email", "value": email, "effective_date": "2026-09-01"}]
        plan = passive.prepare(inputs, [source], [], suggest=None)
        self.assertEqual(len({t["creation"] for t in plan["targets"]}), 2)
        values = [{"fn": "passive_handlers", "plan": plan, "sources": [dict(self.source, raw_text=self.text, raw_field="plainText")], "failure": i} for i in range(1, 23)]
        for result in cases(values):
            self.assertNotIn("error", result)
            self.assertEqual([r["name"] for r in result["records"]], ["Wren Vale", "Rowan Vale"])
            self.assertEqual([r["md"]["mdemail"] for r in result["records"]], ["wren@x.com", "rowan@x.com"])
        waiting = copy.deepcopy(plan)
        waiting.update(status="waiting", ops=[])
        result = cases([{"fn": "passive_handlers", "plan": waiting, "sources": []}])[0]
        self.assertEqual(result["records"], [])

    def test_existing_email_recovers_before_fieldasof_stamp(self):
        for incoming in ("Wren@x.com", "mailto:Wren@x.com"):
            person = dict(self.person, aliases="", md={"mdentitytype": "Person", "mdemail": "Original@x.com"})
            inputs = [{"op": "set_field", "uuid": person["uuid"], "field": "email", "value": incoming, "effective_date": "2026-09-01", "expected_previous": "Original@x.com"}]
            plan = passive.prepare(inputs, [], [person], suggest=None)
            self.assertEqual(plan["targets"][0]["allowed_email"], ["Original@x.com", "wren@x.com"])
            for result in cases([{"fn": "passive_handlers", "plan": plan, "sources": [], "initial": [person], "failure": i} for i in (1, 2)]):
                self.assertNotIn("error", result)
                self.assertEqual(result["records"][0]["md"]["mdemail"], "wren@x.com")
                self.assertIn("mdfieldasof", result["records"][0]["md"])


PRODUCTION_HARNESS = r"""
ObjC.import('Foundation');
function run(argv) {
  const source = ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, $()));
  const c = JSON.parse(ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(argv[1], $.NSUTF8StringEncoding, $())));
  let count=0;
  const mutations=[];
  const records=c.records;
  const mutate=(r,field,value) => {r[field]=value;mutations.push([r.uuid,field]);if(++count===c.failure)throw new Error('interrupted')};
  function record(r) {
    const rec={uuid:()=>r.uuid,type:()=> 'markdown',recordType:()=> 'markdown',customMetaData:()=>r.md||{},
      location:()=>r.group,additionDate:()=>new Date(2026,8,1,12),modificationDate:()=>new Date(2026,8,1,12)};
    for(const field of ['name','plainText','aliases']) {
      const key=field==='plainText'?'body':field;
      Object.defineProperty(rec,field,{get:()=>()=>r[key]||'',set:v=>mutate(r,key,v)});
    }
    rec.comment=()=>'';
    rec.data=r;
    return rec;
  }
  function group(path) {
    const children=()=>records.filter(r=>r.group===path).map(record);
    for(const key of ['uuid','name','aliases','customMetaData','plainText','recordType'])Object.defineProperty(children,key,{value:()=>children().map(r=>r[key]())});
    return {children, path, uuid:()=>path, excludeFromChat:()=>true};
  }
  const dt={databases:()=>[{name:()=> 'Lorebook',trashGroup:()=>group('/Trash')}],
    getRecordAt:path=>group(path),getRecordWithUuid:id=>{const r=records.find(r=>r.uuid===id);return r?record(r):null},
    createRecordWith:(p,o)=>{const r={uuid:'BBB-222',name:p.name,body:'',aliases:'',md:{},group:o.in.path};records.push(r);mutations.push([r.uuid,'create']);return record(r)},
    addCustomMetaData:(value,o)=>{const r=o.to.data;r.md=r.md||{};r.md['md'+o.for]=String(value);mutations.push([r.uuid,o.for]);if(++count===c.failure)throw new Error('interrupted')},
    move:o=>mutate(o.record.data,'group',o.to.path)};
  const fakeApplication=()=>dt;
  fakeApplication.currentApplication=()=>({});
  const execute=Function('Application','payload',source.replace(/^#![^\n]*\n/,'')+'\nreadFile=path=>path==="ops"?JSON.stringify({ops:payload}):null;migrationFence=null;return run(["ops"]);');
  const result=JSON.parse(execute(fakeApplication,c.ops));
  return JSON.stringify({result,records,mutations});
}
"""


def production(ops, records, failure=0):
    with tempfile.TemporaryDirectory() as directory:
        script, payload = Path(directory) / 'production.js', Path(directory) / 'payload.json'
        script.write_text(PRODUCTION_HARNESS)
        payload.write_text(json.dumps({'ops': ops, 'records': records, 'failure': failure}))
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(script),
                                 str(BIN / 'entity-dt-bridge.js'), str(payload)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)
