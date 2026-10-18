"""Generic recognized-source adoption; isolated fake files, accounts and mock runs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import urllib.request
from test_helix05 import Srv, wait_run


class SourceSrv(Srv):
    def start(self):
        old = os.environ.get('HELIX_RESEARCH_ONLY_BUNDLED')
        os.environ['HELIX_RESEARCH_ONLY_BUNDLED'] = '1'
        try: super().start()
        finally:
            if old is None: os.environ.pop('HELIX_RESEARCH_ONLY_BUNDLED', None)
            else: os.environ['HELIX_RESEARCH_ONLY_BUNDLED'] = old


class SourceProjectAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='helix-source-project-')
        base = Path(self.tmp.name); self.root = base/'shell'; self.library = base/'research'
        shutil.copytree(Path(__file__).resolve().parent.parent, self.root,
                        ignore=shutil.ignore_patterns('data','_devstate','__pycache__','docs','*.zip'))
        (self.root/'data').mkdir()
        (self.root/'helix.config.json').write_text(json.dumps({'project_id':'DEMO_SOURCE','project_name':'Example study','workbench_app':'','ledger_root':''}),encoding='utf-8')
        source = {
            'v4/current/authority/DISCOVERY_STATE_V4.json':json.dumps({'project_id':'DEMO','target':{'target_id':'ExampleTarget'},'gates':{'D0':{'raw_status':'HOLD'}},'discovery_status':'DISCOVERY_BLOCKED'}),
            'v4/current/authority/VALIDATION_STATE_V4.json':'{}',
            'v4/current/authority/CANDIDATE_FREEZE.json':'{"status":"NOT_FROZEN"}',
            'sprint/CURRENT_SPRINT_TASKS_20261012.csv':'task_id,week,start,end,owner,cross_reviewer,research_track,deliverable,acceptance,depends_on\nT1,6,2026-10-12,2026-10-18,owner,peer,SCORING,Review source,Check evidence,\nT2,7,2026-10-19,2026-10-25,owner,peer,SCORING,Write report,Independent review,T1\n',
            'sprint/CURRENT_CHECKIN_W6_W9_MAP_20261012.csv':'canonical_task_id,html_week\nT1,W6\n',
            'gnn/human_review/adora2a_mcule_20260927/review_result.template.json':'{}'
        }
        for name, content in source.items():
            p=self.library/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,encoding='utf-8')
        self.original = {name:hashlib.sha256((self.library/name).read_bytes()).hexdigest() for name in source}
        (self.library/'manifest.json').write_text(json.dumps({'files':[{'path':name,'bytes':(self.library/name).stat().st_size,'sha256':digest,'group':name.split('/')[0]} for name,digest in self.original.items()]}),encoding='utf-8')
        self.s=SourceSrv(self.root);self.s.start()
        people=[{'id':'owner-id','name':'owner','role':'lead','keys':[],'active':True},{'id':'peer-id','name':'peer','role':'both','keys':[],'active':True}]
        self.assertTrue(self.s.post('/api/people',{'people':people})['ok'])
        self.assertTrue(self.s.post('/api/auth/set-pin',{'person_id':'owner-id','new_pin':'owner-password-123'})['ok'])
        self.login()
        self.creds=(self.root/'data/credentials.json').read_bytes()

    def login(self):
        self.token=self.s.post('/api/auth/login',{'person_id':'owner-id','pin':'owner-password-123'})['token']

    def tearDown(self): self.s.stop();self.tmp.cleanup()

    def adopt(self):
        p=self.s.post('/api/projects/source-preview',{},self.token);self.assertTrue(p['ok'],p)
        result=self.s.post('/api/projects/adopt',{'snapshot_hash':p['snapshot_hash']},self.token)
        self.assertTrue(result['ok'],result);return result

    def test_normal_project_preserves_sources_accounts_and_dependencies(self):
        r=self.adopt();s=self.s.get('/api/snapshot',self.token)
        self.assertEqual(s['project']['id'],r['id']);self.assertEqual(s['project']['kind'],'local')
        self.assertTrue(s['harness']['writable']);self.assertEqual(len(s['plan']['tasks']),2)
        self.assertEqual(s['plan']['tasks'][1]['depends_on'],['T1'])
        self.assertEqual([t['week'] for t in s['plan']['tasks']],[1,2])
        self.assertTrue(any(m['role']=='lead' for m in s['plan']['members']))
        self.assertEqual(s['skeleton']['gates'][0]['raw_status'],'HOLD')
        self.assertEqual(s['project']['claim_ceiling'],'EXPLORATORY_ONLY')
        self.assertEqual(self.creds,(self.root/'data/credentials.json').read_bytes())
        for n,h in self.original.items():self.assertEqual(hashlib.sha256((self.library/n).read_bytes()).hexdigest(),h)

    def test_stale_preview_rejected(self):
        p=self.s.post('/api/projects/source-preview',{},self.token)
        path=self.library/'v4/current/authority/CANDIDATE_FREEZE.json';path.write_text('{"status":"CHANGED"}')
        r=self.s.post('/api/projects/adopt',{'snapshot_hash':p['snapshot_hash']},self.token)
        self.assertFalse(r['ok']);self.assertIn('来源已变化',r['error'])

    def test_repeat_adoption_preserves_edited_plan(self):
        first=self.adopt()
        self.assertTrue(self.s.post('/api/projects/update',{'name':'Edited study'},self.token)['ok'])
        self.s.post('/api/projects/active',{'id':'DEMO_SOURCE'},self.token)
        second=self.adopt();self.assertTrue(second['existing']);self.assertEqual(second['id'],first['id'])
        self.assertEqual(self.s.get('/api/snapshot',self.token)['project']['name'],'Edited study')

    def test_anonymous_and_member_cannot_adopt(self):
        self.assertFalse(self.s.post('/api/projects/source-preview',{})['ok'])
        r=self.s.post('/api/auth/register',{'name':'reader','pin':'reader-password-123','pin_confirm':'reader-password-123'})
        tok=self.s.post('/api/auth/login',{'person_id':r['person']['id'],'pin':'reader-password-123'})['token']
        for path in ('source-preview','adopt'):
            self.assertFalse(self.s.post('/api/projects/'+path,{},tok)['ok'])

    def test_source_files_are_readable_and_changes_are_detected(self):
        self.adopt();s=self.s.get('/api/snapshot',self.token);self.assertTrue(s['files'])
        f=next(f for f in s['files'] if f['name']=='CANDIDATE_FREEZE.json')
        req=urllib.request.Request(f'http://127.0.0.1:{self.s.port}/api/file?sha='+f['sha256'],headers={'X-Helix-Token':self.token})
        with urllib.request.urlopen(req) as response:
            self.assertEqual(hashlib.sha256(response.read()).hexdigest(),f['sha256'])
        p=self.s.get('/api/file/preview?sha='+f['sha256'],self.token)
        self.assertTrue(p['ok'],p)
        (self.library/'v4/current/authority/CANDIDATE_FREEZE.json').write_text('{"status":"UPDATED"}')
        changed=self.s.get('/api/snapshot',self.token)
        f2=next(x for x in changed['files'] if x['name']==f['name']);self.assertNotEqual(f2['sha256'],f['sha256'])

    def test_other_projects_do_not_inherit_source_library_or_gates(self):
        source=self.adopt()['id'];self.assertEqual(self.s.get('/api/research',self.token)['total'],6)
        r=self.s.post('/api/projects',{'name':'Unrelated project','members':[{'name':'owner','role':'lead'},{'name':'peer'}],'n_weeks':1,'tasks':[]},self.token)
        self.assertTrue(r['ok'],r)
        self.assertEqual(self.s.get('/api/research',self.token)['total'],0)
        self.assertEqual(self.s.get('/api/snapshot',self.token)['skeleton']['gates'],[])
        self.s.post('/api/projects/active',{'id':source},self.token)
        self.assertEqual(self.s.get('/api/research',self.token)['total'],6)

    def test_restart_keeps_project_and_source_links(self):
        pid=self.adopt()['id'];self.s.stop();self.s.start();self.login()
        snap=self.s.get('/api/snapshot',self.token);self.assertEqual(snap['project']['id'],pid)
        self.assertTrue(snap['files']);self.assertEqual(self.s.get('/api/research',self.token)['total'],6)

    def test_adopted_project_uses_normal_freeze_and_mock_run(self):
        self.adopt()
        self.assertTrue(self.s.post('/api/plan/freeze',{},self.token)['ok'])
        r=self.s.post('/api/runs/start',{'task_id':'T1'},self.token);self.assertTrue(r['ok'],r)
        run=wait_run(self.s,r['run_id'],self.token)
        self.assertEqual(run['status'],'completed',run)

    def test_arbitrary_path_and_account_fields_rejected(self):
        p=self.s.post('/api/projects/source-preview',{},self.token)
        self.assertFalse(self.s.post('/api/projects/adopt',{'snapshot_hash':p['snapshot_hash'],'path':'C:/Users/other','role':'lead'},self.token)['ok'])


if __name__=='__main__':unittest.main(verbosity=2)
