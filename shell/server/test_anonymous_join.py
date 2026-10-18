"""Default anonymous slots; members set private credentials without losing work."""
import json,shutil,tempfile,unittest,urllib.parse,urllib.request
from pathlib import Path
from test_helix05 import Srv


class AnonymousJoin(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='helix-anonymous-')
        self.root=Path(self.tmp.name)/'shell'
        shutil.copytree(Path(__file__).resolve().parent.parent,self.root,ignore=shutil.ignore_patterns('data','docs','__pycache__','*.zip'))
        (self.root/'data').mkdir()
        cfg={'project_id':'ANON_EMPTY','workbench_app':'','ledger_root':'','anonymous_members':True}
        cfgpath=self.root/'helix.config.json';cfgpath.write_text(json.dumps(cfg),encoding='utf-8')
        self.s=Srv(self.root);self.s.start()
        # Remove the compatibility fixture's explicit mode to test the real default.
        cfg.pop('anonymous_members');cfgpath.write_text(json.dumps(cfg),encoding='utf-8')
        r=self.s.post('/api/auth/bootstrap',{'username':'leader-login','pin':'leader-password-123','pin_confirm':'leader-password-123'})
        self.assertTrue(r.get('ok'));self.token=r['token'];self.lead=r['person']['id']
        names=['leader-login','原始甲','原始乙','原始丙','原始丁']
        members=[{'name':n,'role':'lead' if i==0 else 'both'} for i,n in enumerate(names)]
        tasks=[{'task_id':f'T{i}','week':1,'owner':n,'cross_reviewer':names[(i+1)%5],
                'deliverable':'Actual practice','acceptance':'Record actual inputs and outputs','execution_mode':'human'} for i,n in enumerate(names)]
        r=self.s.post('/api/projects',{'name':'Anonymous fixture','members':members,'n_weeks':1,'start':'2026-10-12','tasks':tasks},self.token)
        self.assertTrue(r.get('ok'));self.pid=r['project']['id']
        self.pfile=self.root/'data/projects'/self.pid/'project.json';self.project=self.pfile.read_bytes()
        self.ro=self.s.get('/api/people',self.token)['roster'];self.member=next(p for p in self.ro if p['role']!='lead')
        self.creds=json.loads((self.root/'data/credentials.json').read_text(encoding='utf-8'))['creds']

    def tearDown(self):self.s.stop();self.tmp.cleanup()

    def invite(self,person=None):
        r=self.s.post('/api/auth/invite',{'person_id':(person or self.member)['id']},self.token)
        self.assertTrue(r.get('ok'))
        return urllib.parse.parse_qs(urllib.parse.urlsplit(r['path']).query)['join'][0]

    def claim(self,code,**extra):
        return self.s.post('/api/auth/claim',{'join':code,'name':'private-handle','pin':'personal-password-123','pin_confirm':'personal-password-123',**extra})

    def test_default_five_anonymous_people_and_separate_login_account(self):
        self.assertEqual({p['name'] for p in self.ro},{'组长','组员A','组员B','组员C','组员D'})
        self.assertTrue(all('username' not in p for p in self.ro))
        r=self.s.post('/api/auth/login',{'username':'leader-login','pin':'leader-password-123','portal':'lead'})
        self.assertTrue(r.get('ok'));self.assertEqual(r['person']['name'],'组长')
        snap=self.s.get('/api/snapshot',self.token);text=json.dumps(snap,ensure_ascii=False)
        for raw in ['leader-login','原始甲','原始乙','原始丙','原始丁']:self.assertNotIn(raw,text)

    def test_self_set_password_keeps_identity_assignments_and_records(self):
        code=self.invite();r=self.claim(code);self.assertTrue(r.get('ok'))
        self.assertEqual(r['person']['id'],self.member['id']);self.assertEqual(r['person']['name'],self.member['name'])
        r=self.s.post('/api/auth/login',{'username':'private-handle','pin':'personal-password-123'})
        self.assertTrue(r.get('ok'));self.assertEqual(r['person']['id'],self.member['id'])
        snap=self.s.get('/api/snapshot',r['token'])
        self.assertTrue(any(t['my_role']=='owner' for t in snap['plan']['tasks']))
        self.assertEqual(len(snap['plan']['members']),5);self.assertEqual(len(snap['people']['roster']),5)
        self.assertEqual(self.pfile.read_bytes(),self.project)
        creds=json.loads((self.root/'data/credentials.json').read_text(encoding='utf-8'))['creds']
        self.assertEqual(creds[self.lead],self.creds[self.lead]);self.assertEqual(len(creds),2)
        self.assertNotIn('personal-password-123',(self.root/'data/credentials.json').read_text())
        self.assertNotIn('private-handle',json.dumps(snap,ensure_ascii=False))
        self.assertFalse(self.claim(code).get('ok'))

    def test_links_are_target_bound_and_rotating_one_use(self):
        old=self.invite();new=self.invite()
        self.assertFalse(self.claim(old).get('ok'))
        self.assertFalse(self.claim(new,person_id=self.lead,role='lead').get('ok'))
        self.assertFalse(self.claim(new,pin_confirm='different').get('ok'))
        self.assertTrue(self.claim(new).get('ok'))
        self.assertFalse(self.claim(new).get('ok'))

    def test_bad_expired_inactive_and_leader_links_cannot_claim(self):
        self.assertFalse(self.claim('invented').get('ok'))
        self.assertFalse(self.s.post('/api/auth/invite',{'person_id':self.lead},self.token).get('ok'))
        code=self.invite();path=self.root/'data/member_invites.json';saved=json.loads(path.read_text());saved[self.member['id']]['expires']=0;path.write_text(json.dumps(saved))
        self.assertFalse(self.claim(code).get('ok'))
        code=self.invite();people=json.loads((self.root/'data/people.json').read_text(encoding='utf-8'))
        next(p for p in people['people'] if p['id']==self.member['id'])['active']=False
        (self.root/'data/people.json').write_text(json.dumps(people),encoding='utf-8')
        self.assertFalse(self.claim(code).get('ok'))

    def test_member_cannot_issue_other_links_and_cannot_become_leader(self):
        self.assertTrue(self.claim(self.invite()).get('ok'))
        r=self.s.post('/api/auth/login',{'username':'private-handle','pin':'personal-password-123'})
        token=r['token']
        self.assertFalse(self.s.post('/api/auth/invite',{'person_id':self.ro[-1]['id']},token).get('ok'))
        self.assertFalse(self.s.post('/api/auth/login',{'username':'private-handle','pin':'personal-password-123','portal':'lead'}).get('ok'))
        self.assertFalse(self.s.post('/api/auth/invite',{'person_id':self.ro[-1]['id']}).get('ok'))

    def test_restart_and_alias_edit_keep_private_login_and_assignments(self):
        self.assertTrue(self.claim(self.invite()).get('ok'))
        roster=self.s.get('/api/people',self.token)['roster']
        next(p for p in roster if p['id']==self.member['id'])['name']='匿名伙伴'
        self.assertTrue(self.s.post('/api/people',{'people':roster},self.token).get('ok'))
        self.s.stop();self.s.start()
        r=self.s.post('/api/auth/login',{'username':'private-handle','pin':'personal-password-123'})
        self.assertTrue(r.get('ok'));self.assertEqual(r['person']['name'],'匿名伙伴')
        self.assertEqual(self.pfile.read_bytes(),self.project)
        self.assertTrue(any(t['my_role']=='owner' for t in self.s.get('/api/snapshot',r['token'])['plan']['tasks']))

    def test_plain_registration_does_not_take_existing_tasks(self):
        self.assertFalse(self.s.post('/api/auth/register',{'name':'原始甲','pin':'personal-password-123','pin_confirm':'personal-password-123'}).get('ok'))
        r=self.s.post('/api/auth/register',{'name':'unassigned-handle','pin':'personal-password-123','pin_confirm':'personal-password-123'})
        self.assertTrue(r.get('ok'));self.assertTrue(r['person']['name'].startswith('组员'))
        self.assertEqual(self.pfile.read_bytes(),self.project)

    def test_registration_form_carries_link_in_browser_and_uses_claim_route(self):
        code=self.invite()
        html=urllib.request.urlopen(f'http://127.0.0.1:{self.s.port}/register?join={code}').read().decode()
        self.assertIn('/api/auth/claim',html);self.assertIn('沿用原分工',html)


if __name__=='__main__':unittest.main(verbosity=2)
