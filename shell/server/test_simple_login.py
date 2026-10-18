"""Isolated acceptance for normal name/password sign-in and separate leader portal."""
import urllib.error,json,shutil,tempfile,unittest,urllib.request
from pathlib import Path
import test_source_project as fixture
from test_helix05 import Srv

class LoginAPI(unittest.TestCase):
 def setUp(self):
  self.fixture=fixture.SourceProjectAPI();self.fixture.setUp();self.s=self.fixture.s;self.token=self.fixture.token
  self.creds=(self.fixture.root/'data/credentials.json').read_bytes()
 def tearDown(self):self.fixture.tearDown()
 def test_owner_normal_portal_does_not_login(self):
  r=self.s.post('/api/auth/login',{'username':'owner','pin':'owner-password-123','portal':'member'});self.assertTrue(r['ok'],r)  # 3.1：同一登录页，角色由账号决定
 def test_owner_separate_portal_keeps_identity(self):
  r=self.s.post('/api/auth/login',{'username':'owner','pin':'owner-password-123','portal':'lead'});self.assertTrue(r['ok']);self.assertEqual(r['person']['role'],'lead')
  self.assertEqual(self.creds,(self.fixture.root/'data/credentials.json').read_bytes())
 def test_member_defaults_to_member_and_cannot_become_leader(self):
  r=self.s.post('/api/auth/register',{'name':'member-demo','pin':'member-password-123','pin_confirm':'member-password-123'});self.assertTrue(r['ok'])
  r=self.s.post('/api/auth/login',{'username':'member-demo','pin':'member-password-123'});self.assertTrue(r['ok']);self.assertEqual(r['person']['role'],'both')
  self.assertFalse(self.s.post('/api/auth/login',{'username':'member-demo','pin':'member-password-123','portal':'lead'})['ok'])
 def test_activation_word_does_not_authenticate(self):
  for name in ['组长','组ZHNG']:
   r=self.s.post('/api/auth/login',{'username':name,'pin':'not-a-real-password','portal':'lead'});self.assertFalse(r['ok'])
 def test_wrong_password_still_rejected(self):
  self.assertFalse(self.s.post('/api/auth/login',{'username':'owner','pin':'wrong-password','portal':'lead'})['ok'])
 def test_portal_keywords_are_not_registered_as_member_accounts(self):
  for name in ['组长','组ZHNG','zuzhang']:
   self.assertFalse(self.s.post('/api/auth/register',{'name':name,'pin':'member-password-123','pin_confirm':'member-password-123'})['ok'])
 def test_fresh_forms_have_name_password_and_different_portals(self):
  def get(path):return urllib.request.urlopen(f'http://127.0.0.1:{self.s.port}'+path).read().decode()
  normal=get('/login');leader=get('/leader-login');self.assertIn('组员登录',normal);self.assertNotIn('<select',normal)
  self.assertIn('value="member"',normal);self.assertIn('href="/leader-login"',normal);self.assertNotIn('user.addEventListener("input"',normal);self.assertIn('value="lead"',leader);self.assertIn('value="owner"',leader)
 def test_existing_owner_cannot_be_overwritten_by_setup(self):
  r=self.s.post('/api/auth/bootstrap',{'username':'intruder','pin':'different-password','pin_confirm':'different-password'});self.assertFalse(r['ok'])
  self.assertEqual(self.creds,(self.fixture.root/'data/credentials.json').read_bytes())
 def test_task_assignment_permission_is_based_on_authenticated_identity(self):
  self.fixture.adopt();s=self.s.get('/api/snapshot',self.token)
  self.assertTrue(all(t['my_role']=='owner' for t in s['plan']['tasks']))
  self.assertTrue(all(t['owner']=='owner' for t in s['plan']['tasks']))
  self.assertEqual(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{self.s.port}/api/version')).status,200)
  with self.assertRaises(urllib.error.HTTPError) as cm:self.s.get('/api/snapshot')
  self.assertEqual(cm.exception.code,401)  # 3.1 前置登录：未登录不能读取项目

class FirstSetup(unittest.TestCase):
 def test_blank_desktop_can_set_one_owner_once(self):
  with tempfile.TemporaryDirectory(prefix='helix-login-empty-') as tmp:
   root=Path(tmp)/'shell';shutil.copytree(Path(__file__).resolve().parent.parent,root,ignore=shutil.ignore_patterns('data','docs','__pycache__','*.zip'))
   (root/'data').mkdir();(root/'helix.config.json').write_text(json.dumps({'project_id':'EMPTY_LOGIN','project_name':'Empty fixture','workbench_app':'','ledger_root':''}))
   s=Srv(root);s.start()
   try:
    r=s.post('/api/auth/bootstrap',{'username':'lead-demo','pin':'setup-password-123','pin_confirm':'setup-password-123'});self.assertTrue(r['ok'],r);self.assertEqual(r['person']['role'],'lead')
    self.assertFalse(s.post('/api/auth/bootstrap',{'username':'second-lead','pin':'setup-password-123','pin_confirm':'setup-password-123'})['ok'])
    self.assertTrue(s.post('/api/auth/login',{'username':'lead-demo','pin':'setup-password-123','portal':'lead'})['ok'])
   finally:s.stop()

if __name__=='__main__':unittest.main(verbosity=2)
