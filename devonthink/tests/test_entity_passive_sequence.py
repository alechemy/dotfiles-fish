import copy
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import BIN, capture_logs
import test_entity_passive_fixes as fixes

ef, passive, srv, Lock = fixes.ef, fixes.passive, fixes.srv, fixes.Lock
from test_entity_passive_bridge import production


class PassiveProductionSequence(unittest.TestCase):
    setUp = fixes.PassiveFixes.setUp
    bridge = fixes.PassiveFixes.bridge
    context = fixes.PassiveFixes.context
    origin = fixes.PassiveFixes.origin

    def worker(self):
        stack = self.context()
        stack.enter_context(patch.dict(os.environ, ENTITY_FILING_SCHEDULED='1'))
        stack.enter_context(patch.object(ef.sys, 'argv', ['entity-filing.py', '--apply-only']))
        stack.enter_context(patch.object(ef.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)))
        stack.enter_context(patch.object(ef, 'acquire_lock', return_value=Lock()))
        stack.enter_context(patch.object(ef, 'load_state', return_value={}))
        stack.enter_context(patch.object(ef.os.path, 'exists', return_value=True))
        stack.enter_context(patch.object(ef.capture_reminders, 'sync'))
        stack.enter_context(patch.object(ef, 'should_record_success', return_value=True))
        return stack

    def test_production_new_person_promotion_retires_candidate_and_recovers_protected_writes(self):
        self.person['uuid'] = 'FFF-666'
        candidate = self.origin()
        candidate['md'] = {}
        self.proposal = '# No existing proposal'
        self.writes.clear()
        original = self.bridge
        def empty_roster(ops):
            return [result for op in ops for result in ([[]] if op['op'] == 'dump_people' else original([op]))]
        self.bridge = empty_roster
        with self.context():
            ef.promote_candidates(False)
        self.proposal = next(op['text'] for op in self.writes if op['op'] == 'create_record')
        envelope = passive.read(ef.proposal_ops(self.proposal))
        self.assertEqual(envelope['status'], 'ready')
        records = [dict(uuid=self.source['uuid'], name=self.source['name'], body=self.text, group='/Meetings',
                        md={'mddocumenttype':'Meeting Notes','mdeventdate':'2026-09-01'}),
                   dict(uuid=candidate['uuid'], name=candidate['name'], body=candidate['text'], md={}, group=ef.ec.CANDIDATES_PATH + '/Approved'),
                   dict(uuid='DDD-444', name='File candidate', body=self.proposal, md={'mddocumenttype':'Entity Filing Proposal Passive v1'}, group=ef.APPROVED_PATH)]
        for failure in range(0, 11):
            with self.subTest(failure=failure):
                live = copy.deepcopy(records)
                batches = []
                mutex = threading.Lock()
                started, finished = threading.Event(), threading.Event()
                worker = None
                class ThreadLock:
                    def close(self):
                        mutex.release()
                def acquire():
                    mutex.acquire()
                    return ThreadLock()
                def bridge(ops):
                    nonlocal live
                    nonlocal worker
                    batches.append(ops)
                    if failure == 0 and ops[0]['op'] == 'passive_plan':
                        def upsert():
                            started.set()
                            ef.ec.upsert_mentions(bridge, [{'name':'Wren Vale', 'email':'', 'sid':'cal:fictional', 'sighting':{'person':'Wren Vale','date':'2026-09-02','kind':'calendar','name':'Fictional appointment','facts':[],'updates':{},'interacted':True}}], ef.log)
                            finished.set()
                        worker = threading.Thread(target=upsert)
                        worker.start()
                        self.assertTrue(started.wait(2))
                        self.assertFalse(finished.wait(.05))
                    response = production(ops, live, failure if ops[0]['op'] == 'passive_plan' and len([b for b in batches if b[0]['op']=='passive_plan']) == 1 else 0)
                    live = response['records']
                    if ops[0]['op'] == 'passive_plan' and failure == 0:
                        self.assertFalse(finished.is_set())
                        self.assertEqual(next(r for r in live if r['uuid']==candidate['uuid'])['group'], '/Trash')
                    if ops[0]['op'] == 'passive_plan' and failure and len([b for b in batches if b[0]['op']=='passive_plan']) == 1:
                        self.assertFalse(response['result']['ok'])
                    if not response['result']['ok']:
                        raise RuntimeError(response['result']['error'])
                    return response['result']['results']
                with patch.object(ef, 'run_bridge', bridge), patch.object(ef.ec, 'acquire_candidates_lock', side_effect=acquire), capture_logs(ef) as logs:
                    ef.validate_passive_sources(envelope)
                    ef.validate_passive_origin(envelope, [])
                    ef.apply_approved(False)
                    if worker:
                        worker.join(5)
                        self.assertTrue(finished.is_set())
                    ef.apply_approved(False)
                self.assertTrue(any(r['uuid']=='BBB-222' for r in live), logs.messages())
                person = next(r for r in live if r['uuid'] == 'BBB-222')
                self.assertEqual(person['name'], 'Wren Vale')
                self.assertEqual(len(passive.bio.render(person['body'], person['uuid'])), 1)
                self.assertEqual(next(r for r in live if r['uuid']==candidate['uuid'])['group'], '/Trash')
                self.assertEqual(next(r for r in live if r['uuid']=='DDD-444')['group'], '/Trash')
                self.assertTrue(any(b[0]['op']=='passive_plan' for b in batches))
        wrong = copy.deepcopy(records)
        wrong.append(dict(uuid='BBB-222', name='Wren Vale', aliases='', body='# Wren Vale\n<!-- passive-created:' + envelope['id'] + ':BBB-222 -->', md={'mdentitytype':'Person'}, group='/20_ENTITIES/People'))
        result = production([envelope] + envelope['ops'], wrong)
        self.assertFalse(result['result']['ok'])
        self.assertEqual(result['mutations'], [])

    def pointer_candidate(self):
        candidate = self.origin()
        complete = passive.read(ef.proposal_ops(self.proposal))
        with patch.object(passive, 'MAX_BYTES', 1000):
            pointer = passive.bounded(complete, complete)
        self.assertEqual(pointer['variant'], 'oversized_evidence')
        self.proposal = passive.body(pointer)
        self.writes.clear()
        return candidate, pointer

    def test_full_worker_unreadable_candidate_pointer_reserves_origin(self):
        for damage in ('missing', 'corrupt', 'digest', 'symlink', 'fifo'):
            with self.subTest(damage=damage):
                self.setUp()
                candidate, pointer = self.pointer_candidate()
                path = Path(passive.evidence_path(pointer['evidence']))
                before = self.proposal
                original = path.read_bytes()
                path.unlink()
                if damage == 'corrupt':
                    path.write_bytes(b'not json')
                elif damage == 'digest':
                    path.write_bytes(original.replace(b'Wren', b'Fern'))
                elif damage == 'symlink':
                    foreign = path.parent / 'fictional-foreign.json'
                    foreign.write_bytes(original)
                    path.symlink_to(foreign)
                elif damage == 'fifo':
                    os.mkfifo(path)
                stages = []
                wrappers = {}
                for name in ('retry_passive_proposals','promote_candidates','apply_approved'):
                    fn = getattr(ef, name)
                    def wrapped(*args, fn=fn, name=name):
                        stages.append(name)
                        return fn(*args)
                    wrappers[name] = wrapped
                try:
                    with self.worker() as stack:
                        stack.enter_context(patch.object(ef, 'record_success'))
                        for name, fn in wrappers.items():
                            stack.enter_context(patch.object(ef, name, side_effect=fn))
                        stack.enter_context(patch.object(ef, 'extract_omlx', side_effect=AssertionError('No re-extraction')))
                        stack.enter_context(patch.object(ef, 'semantic_suggestions', side_effect=AssertionError('No comparison')))
                        stack.enter_context(patch.object(passive, 'store_evidence', side_effect=AssertionError('No reconstruction')))
                        stack.enter_context(patch.object(ef, 'load_config', side_effect=[{'TRANSPORT':'local','IDLE_MINUTES':'0'}, AssertionError('No replacement config')]))
                        ef.main()
                        ef.record_success.assert_not_called()
                    self.assertEqual(stages, ['retry_passive_proposals','promote_candidates','apply_approved'])
                    self.assertEqual(self.proposal, before)
                    self.assertEqual(self.writes, [])
                    self.assertFalse(candidate.get('pending', False))
                    if damage == 'missing':
                        self.assertFalse(path.exists())
                    elif damage == 'symlink':
                        self.assertTrue(path.is_symlink())
                    elif damage == 'fifo':
                        import stat
                        self.assertTrue(stat.S_ISFIFO(path.lstat().st_mode))
                    else:
                        self.assertNotEqual(path.read_bytes(), original)
                finally:
                    if os.path.lexists(path):
                        path.unlink()
                    path.write_bytes(original)

    def test_fifo_evidence_prompt_valueerror_with_reaped_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = 'a' * 64
            path = Path(directory) / (identity + '.json')
            os.mkfifo(path)
            script = 'import sys;sys.path.insert(0,sys.argv[1]);import entity_passive as p;p.evidence_directory=lambda:sys.argv[2]\ntry:p.evidence_bytes("a"*64)\nexcept ValueError:print("rejected")\nelse:raise AssertionError("accepted FIFO")'
            process = subprocess.Popen(['/usr/bin/python3','-c',script,str(BIN),directory], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail('FIFO evidence blocked beyond two seconds')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), 'rejected')

    def test_persisted_inline_blocked_is_unhealthy_on_second_worker_without_inference(self):
        waiting = copy.deepcopy(self.envelope)
        waiting.update(status='waiting', questions=[], answers={}, ops=[])
        self.proposal = passive.body(waiting)
        blocked = dict(waiting, status='blocked', reason='ownership_unreadable')
        original = self.bridge
        def persistent(ops):
            results = original(ops)
            for op in ops:
                if op['op'] == 'set_text' and op['uuid'] == 'DDD-444':
                    self.proposal = op['text']
            return results
        self.bridge = persistent
        with self.worker() as stack:
            stack.enter_context(patch.object(ef, 'load_config', return_value={'TRANSPORT':'local','IDLE_MINUTES':'0'}))
            stack.enter_context(patch.object(ef, 'omlx_available', return_value=True))
            stack.enter_context(patch.object(ef, 'memory_pressure_normal', return_value=True))
            stack.enter_context(patch.object(ef, 'semantic_suggestions', return_value=lambda *args: []))
            stack.enter_context(patch.object(passive, 'analyze', return_value=blocked))
            stack.enter_context(patch.object(ef, 'record_success'))
            ef.main()
            self.assertEqual(passive.read(ef.proposal_ops(self.proposal))['status'], 'blocked')
            ef.record_success.assert_not_called()
        self.writes.clear()
        with self.worker() as stack:
            stack.enter_context(patch.object(ef, 'record_success'))
            stack.enter_context(patch.object(ef, 'semantic_suggestions', side_effect=AssertionError('No inference')))
            stack.enter_context(patch.object(passive, 'analyze', side_effect=AssertionError('No analysis')))
            ef.main()
            ef.record_success.assert_not_called()
        self.assertEqual(self.writes, [])

    def test_server_pointer_view_actual_renderer_offers_source_reason_and_reject_only(self):
        complete = passive.prepare(self.ops, self.envelope['sources'], [self.person], analyze_now=False)
        with patch.object(passive, 'MAX_BYTES', 1000):
            pointer = passive.bounded(complete, complete)
        view = srv.proposal_view({'uuid':'DDD-444','name':'Fictional proposal','text':passive.body(pointer)}, [self.person])
        self.assertFalse(view['editable'])
        self.assertEqual(view['plans'], [])
        asset = Path(__file__).resolve().parents[2] / 'stow/devonthink/.local/share/entity-review/index.html'
        script = asset.read_text().split('<script>',1)[1].split('document.addEventListener("click"',1)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pointer.js'
            path.write_text(script + '\nconst pv=' + json.dumps(view) + ';queue={proposals:[pv],candidates:[],captures:[],roster:[]};local={};console.log(proposalCard(pv,overlayOf(local,"c-"+pv.uuid)));')
            result = subprocess.run(['node',str(path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('x-devonthink-item://AAA-111', result.stdout)
        self.assertIn('oversized', result.stdout.lower())
        self.assertIn('data-action="reject"', result.stdout)
        self.assertNotIn('data-act="approve"', result.stdout)
        self.assertNotIn('data-act="passive-edit"', result.stdout)
        self.assertNotIn('Save changes', result.stdout)

    def test_healthy_pointer_changed_candidate_text_refuses_old_origin_and_preserves_fresh_evidence(self):
        candidate, pointer = self.pointer_candidate()
        original_pointer = self.proposal
        original_bytes = Path(passive.evidence_path(pointer['evidence'])).read_bytes()
        original_complete = passive.load_evidence(pointer)
        candidate['text'] += '\nNew fictional candidate note.\n'
        with self.context(), patch.object(ef, 'omlx_available', return_value=True), patch.object(ef, 'semantic_suggestions', side_effect=AssertionError('Stale origin must not infer')):
            self.assertFalse(ef.retry_passive_proposals({'TRANSPORT':'local','IDLE_MINUTES':'0'},False,True))
        self.assertEqual(self.proposal, original_pointer)
        self.assertEqual(self.writes, [])
        with self.context():
            ef.promote_candidates(False)
            ef.apply_approved(False)
        created = [op for op in self.writes if op['op']=='create_record']
        self.assertEqual(len(created), 1)
        fresh = passive.read(ef.proposal_ops(created[0]['text']))
        self.assertNotEqual(fresh['id'], original_complete['id'])
        self.assertEqual(fresh['origin']['text'], candidate['text'])
        self.assertEqual(fresh['answers'], {})
        self.assertEqual(fresh['sources'], original_complete['sources'])
        self.assertEqual(Path(passive.evidence_path(pointer['evidence'])).read_bytes(), original_bytes)
        self.assertEqual(self.proposal, original_pointer)
        self.assertFalse(any(op['op'] in {'passive_plan','ensure_person','add_aliases','trash'} for op in self.writes))

    def test_full_worker_stale_promotion_controls_bounce_without_filing(self):
        candidate, pointer = self.pointer_candidate()
        original_pointer = self.proposal
        candidate['md']['mdtracktarget'] = 'FFF-666'
        with self.worker() as stack:
            stack.enter_context(patch.object(ef, 'load_config', return_value={'TRANSPORT':'local','IDLE_MINUTES':'0'}))
            stack.enter_context(patch.object(ef, 'omlx_available', return_value=True))
            stack.enter_context(patch.object(ef, 'memory_pressure_normal', return_value=True))
            stack.enter_context(patch.object(ef, 'semantic_suggestions', side_effect=AssertionError('Stale controls must not infer')))
            stack.enter_context(patch.object(ef, 'record_success'))
            ef.main()
            ef.record_success.assert_not_called()
        self.assertTrue(candidate['pending'])
        self.assertEqual(self.proposal, original_pointer)
        self.assertEqual([op['op'] for op in self.writes], ['set_text','move_to'])
        self.assertEqual(passive.load_evidence(pointer)['origin']['tracktarget'], 'BBB-222')

    def test_documented_candidate_rejection_matches_withdrawal_api(self):
        docs = Path(__file__).resolve().parents[1] / 'docs/entities.md'
        text = docs.read_text()
        self.assertIn("review app's Reject action, which returns the candidate to Pending", text)
        self.assertIn('that proposal leaves its candidate Approved and can recreate the proposal', text)
        self.assertIn('Older frozen JSON fences remain editable', text)
        self.assertIn('Deleting an ordinary\nsource proposal rejects it', text)
        candidate = self.origin()
        with self.context(srv.ef), patch.object(srv.ef, 'acquire_lock', return_value=Lock()), patch.object(srv.scheduler, 'kick'):
            srv.handle_proposal('DDD-444', {'action':'reject'})
        self.assertTrue(candidate['pending'])
        self.writes.clear()
        with self.context():
            ef.promote_candidates(False)
        self.assertEqual(self.writes, [])

    def test_unreadable_pointer_does_not_reserve_unrelated_candidate(self):
        candidate, pointer = self.pointer_candidate()
        path = Path(passive.evidence_path(pointer['evidence']))
        original = path.read_bytes()
        path.unlink()
        unrelated = dict(candidate, uuid='GGG-777')
        try:
            with self.context():
                ef.prepare_candidate_passive(unrelated, ef.ec.parse_candidate(candidate['text']), self.person['uuid'], [self.person], False)
            created = [op for op in self.writes if op['op']=='create_record']
            self.assertEqual(len(created), 1)
            fresh = passive.read(ef.proposal_ops(created[0]['text']))
            self.assertEqual(fresh['origin']['uuid'], 'GGG-777')
            self.assertFalse(path.exists())
        finally:
            path.write_bytes(original)
