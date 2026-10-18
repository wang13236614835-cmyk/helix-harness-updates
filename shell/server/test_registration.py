"""本机自主注册隔离验收。不会读写正式 data/，不会请求真实 Notion。"""
from __future__ import annotations
import concurrent.futures
import json
import shutil
import tempfile
import unittest
from pathlib import Path
import identity as ID
from test_helix05 import Srv

class RegistrationUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='helix-register-unit-')
        self.root = Path(self.tmp.name)
        self.ident = ID.Identity(self.root)
        self.people = []
    def tearDown(self):
        self.tmp.cleanup()
    def save(self, rows):
        self.people[:] = rows
    def reg(self, name='测试成员', pin='member-123456'):
        return self.ident.register(self.people, name, pin, self.save)
    def test_register_without_lead(self):
        r = self.reg()
        self.assertEqual(r['person']['role'], 'both')
        self.assertEqual(self.people[0]['keys'], [])
        self.assertFalse(ID.is_admin(self.people[0], self.people, self.ident)[0])
        self.assertFalse(self.ident.status(self.people)['bootstrap'])
    def test_preserve_lead(self):
        self.people.append({'id':'lead-id','name':'原组长','role':'lead','active':True})
        self.ident.set_pin(self.people,'lead-id','existing-lead-123')
        before = self.ident._load()['lead-id'].copy()
        self.reg()
        self.assertEqual(before, self.ident._load()['lead-id'])
        self.assertTrue(self.ident.login(self.people,'lead-id','existing-lead-123')['token'])
    def test_hash_not_plaintext(self):
        self.reg()
        raw = self.ident.path.read_text()
        self.assertNotIn('member-123456', raw)
        self.assertNotIn('token', raw)
        self.assertEqual(len(next(iter(self.ident._load().values()))['salt']), 32)
    def test_duplicate_normalized(self):
        self.reg('Alice')
        for name in ('alice', 'Ａｌｉｃｅ', ' Alice '):
            with self.assertRaises(ID.AuthError): self.reg(name)
    def test_unregistered_task_identity_reserved(self):
        for name in ("数据里的甲", "成员A"):
            with self.assertRaises(ID.AuthError):
                self.ident.register([],name,"member-123456",self.save,reserved_names=["数据里的甲","成员A"])
    def test_inactive_name_reserved(self):
        self.people.append({'id':'inactive','name':'已停用','active':False})
        with self.assertRaises(ID.AuthError): self.reg('已停用')
    def test_reserved_names(self):
        for name in ('helix','组长','ADMIN','管理员','root'):
            with self.assertRaises(ID.AuthError): self.reg(name)
    def test_invalid_name(self):
        for name in ('a','<script>','a\nb','a'*25):
            with self.assertRaises(ID.AuthError): self.reg(name)
    def test_invalid_pin(self):
        for pin in ('1234567','x'*129):
            with self.assertRaises(ID.AuthError): self.reg(pin=pin)
    def test_limit(self):
        self.people[:] = [{'id':str(i),'name':'成员'+str(i)} for i in range(60)]
        with self.assertRaises(ID.AuthError): self.reg()
    def test_rollback_on_roster_failure(self):
        def fail(rows): raise OSError('disk failure')
        with self.assertRaises(OSError): self.ident.register([], '测试成员','member-123456', fail)
        self.assertEqual(self.ident._load(), {})
    def test_corrupt_credentials_fail_closed(self):
        self.ident.path.write_text('not json')
        with self.assertRaises(ID.AuthError): self.reg()
    def test_password_persists_recreate_identity(self):
        pid = self.reg()['person']['id']
        fresh = ID.Identity(self.root)
        self.assertTrue(fresh.login(self.people,pid,'member-123456')['token'])
    def test_concurrent_duplicates(self):
        def work(_):
            try:
                with self.ident.store_lock: self.reg('并发成员')
                return True
            except ID.AuthError: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(sum(pool.map(work, range(6))), 1)
        self.assertEqual(len(self.people),1)
        self.assertEqual(len(self.ident._load()),1)
    def test_member_package_never_bootstraps(self):
        member = ID.Identity(self.root,member_mode=True)
        self.assertFalse(member.status([])['bootstrap'])
        self.assertFalse(ID.is_admin(None,[],member)[0])
        with self.assertRaises(ID.AuthError):
            member.set_pin([{'id':'lead','name':'组长','role':'lead'}],'lead','lead-123456')

class RegistrationAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='helix-register-api-')
        cls.root = Path(cls.tmp.name)/'shell'
        src = Path(__file__).resolve().parent.parent
        shutil.copytree(src,cls.root,ignore=shutil.ignore_patterns('data','_devstate','__pycache__','docs','*.zip'))
        (cls.root/'data').mkdir()
        cls.srv = Srv(cls.root); cls.srv.start()
    @classmethod
    def tearDownClass(cls):
        cls.srv.stop(); cls.tmp.cleanup()
    def register(self, name, **kw):
        return self.srv.post('/api/auth/register',{'name':name,'pin':'member-123456','pin_confirm':'member-123456',**kw})
    def test_01_round_trip_and_denied_admin(self):
        r = self.register('接口成员')
        self.assertTrue(r['ok'],r)
        pid = r['person']['id']
        token = self.srv.post('/api/auth/login',{'person_id':pid,'pin':'member-123456'})['token']
        a = self.srv.get('/api/auth/me',token)
        self.assertFalse(a['can_admin']); self.assertEqual(a['account_scope'],'local')
        self.assertFalse(self.srv.post('/api/people',{'people':[]},token)['ok'])
        self.assertFalse(self.srv.post('/api/cloud/config',{},token)['ok'])
        snap = self.srv.get('/api/snapshot',token)
        self.assertFalse(snap['people']['editable'])
    def test_02_mismatch(self):
        self.assertFalse(self.register('确认错误',pin_confirm='other-password')['ok'])
    def test_03_privileged_fields(self):
        for field, val in [('role','lead'),('person_id','owner'),('keys',['abcdef123456']),('admin_pin','never-send')]:
            self.assertFalse(self.register('冒认成员',**{field:val})['ok'])
    def test_04_existing_name_rejected(self):
        self.assertFalse(self.register('接口成员')['ok'])
    def test_05_no_cross_site_write(self):
        import urllib.request, urllib.error
        req=urllib.request.Request(f'http://127.0.0.1:{self.srv.port}/api/auth/register', data=b'{}',headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(req)
        self.assertEqual(e.exception.code,403)
    def test_06_restart_keeps_password(self):
        r=self.register('重启成员'); pid=r['person']['id']
        self.srv.stop(); self.srv.start()
        self.assertTrue(self.srv.post('/api/auth/login',{'person_id':pid,'pin':'member-123456'})['ok'])
    def test_07_inactive_project_identity_reserved(self):
        folder=self.root/'data/projects/inactive-project'
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'project.json').write_text(json.dumps({'id':'inactive-project','name':'非当前项目','members':[],
            'tasks':[{'task_id':'OLD1','owner':'预置研究员','cross_reviewer':'复核人员'}]},ensure_ascii=False),encoding='utf-8')
        self.assertFalse(self.register('预置研究员')['ok'])

if __name__=='__main__':
    unittest.main(verbosity=2)
