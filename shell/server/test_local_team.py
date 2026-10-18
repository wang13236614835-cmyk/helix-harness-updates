"""Exercise real self-seating/evidence flows on isolated account/project copies."""
import hashlib,json,os,unittest,urllib.request
from unittest.mock import patch
from test_anonymous_join import AnonymousJoin
import local_team as LT

class LocalTeam(unittest.TestCase):
 def setUp(self):
  self.fx=AnonymousJoin();self.fx.setUp();self.s=self.fx.s;self.token=self.fx.token;self.pid=self.fx.pid
  self.root=self.fx.root;self.pfile=self.fx.pfile;p=json.loads(self.pfile.read_text(encoding='utf-8'))
  members=p['members'];people=json.loads((self.root/'data/people.json').read_text(encoding='utf-8'))['people']
  ids=['coordinator','model','data','structure','literature'];seats=[]
  for i,m in enumerate(members):
   fp=LT.fingerprint(m['name']);person=next(x for x in people if fp in x.get('keys',[]) or x['id']==self.fx.lead and i==0)
   seats.append(dict(id=ids[i],member_ref=m['name'],person_id=person['id'],alias=person['name'],title='岗位'+str(i),summary='任务测试',hours=4,reviewer_slot=ids[(i+1)%5],skills='practice'))
   p['tasks'][i].update(workspace_slot=ids[i],task_kind='coordination' if i==0 else 'research',evidence_refs=['safe'])
  source=self.root/'source';source.mkdir();(source/'safe.md').write_text('真实来源 <script>bad()</script>',encoding='utf-8')
  p['local_team']=dict(enabled=True,seats=seats,source_roots={'test':str(source)},assets=[dict(id='safe',root='test',path='safe.md',title='来源',sha256=hashlib.sha256((source/'safe.md').read_bytes()).hexdigest())])
  self.pfile.write_text(json.dumps(p,ensure_ascii=False),encoding='utf-8');self.pbytes=self.pfile.read_bytes()
  (self.pfile.parent/'curriculum.json').write_text(json.dumps({'roles':{sid:[{'week':6,'theme':'练习'}] for sid in ids}}))
  self.name= 'new-person';r=self.s.post('/api/auth/register',dict(name=self.name,pin='fixture-only-password',pin_confirm='fixture-only-password'));self.assertTrue(r['ok'],r)
  r=self.s.post('/api/auth/login',dict(username=self.name,pin='fixture-only-password'));self.assertTrue(r['ok'],r);self.member=r['token'];self.memberid=r['person']['id']
 def tearDown(self):self.fx.tearDown()
 def claim(self,slot='model',token=None):return self.s.post('/api/local-team/claim',{'slot':slot},token or self.member)
 def test_self_seating_links_tasks_keeps_identity_password_and_plan(self):
  self.assertTrue(self.s.get('/api/snapshot',self.member)['local_team']['must_select'])
  before=(self.root/'data/credentials.json').read_bytes();r=self.claim();self.assertTrue(r['ok'],r)
  snap=self.s.get('/api/snapshot',self.member);self.assertEqual(snap['local_team']['my_slot'],'model');self.assertEqual(snap['auth']['me']['id'],self.memberid)
  self.assertTrue(any(t['my_role']=='owner' for t in snap['plan']['tasks']))
  self.assertEqual(before,(self.root/'data/credentials.json').read_bytes());self.assertEqual(self.pbytes,self.pfile.read_bytes())
  self.assertTrue(self.claim()['unchanged']);self.assertFalse(self.claim('data')['ok']);self.assertFalse(self.claim('coordinator')['ok'])
  self.assertTrue(self.s.post('/api/auth/login',dict(username=self.name,pin='fixture-only-password'))['ok'])
  self.assertFalse(self.claim(token=self.token)['ok']);self.assertFalse(self.s.post('/api/local-team/claim',{'slot':'data'})['ok'])
 def test_occupied_seat_and_privilege_fields_rejected(self):
  self.assertTrue(self.claim()['ok'])
  self.s.post('/api/auth/register',dict(name='second-user',pin='fixture-only-password',pin_confirm='fixture-only-password'))
  token=self.s.post('/api/auth/login',dict(username='second-user',pin='fixture-only-password'))['token']
  self.assertFalse(self.claim(token=token)['ok']);self.assertTrue(self.claim('data',token)['ok'])
  self.assertFalse(self.s.post('/api/local-team/claim',{'slot':'structure','role':'lead'},token)['ok'])
 def test_learning_requires_own_real_output_and_does_not_approve_science(self):
  data=dict(learning_week=6,state='done',progress=100,note='本人实际运行了工具并保存输出记录')
  self.assertFalse(self.s.post('/api/local-team/checkin',data,self.member)['ok']);self.assertTrue(self.claim()['ok'])
  self.assertFalse(self.s.post('/api/local-team/checkin',data,self.member)['ok'])
  own=self.s.post('/api/files?name=output.txt',token=self.member,raw=b'actual fixture output')['file']['sha256']
  other=self.s.post('/api/files?name=other.txt',token=self.token,raw=b'other output')['file']['sha256']
  self.assertFalse(self.s.post('/api/local-team/checkin',{**data,'files':[other]},self.member)['ok'])
  self.assertFalse(self.s.post('/api/local-team/checkin',{**data,'files':[own],'learning_week':999},self.member)['ok'])
  r=self.s.post('/api/local-team/checkin',{**data,'files':[own]},self.member);self.assertTrue(r['ok'],r)
  snap=self.s.get('/api/snapshot',self.member);log=snap['local_team']['learning_progress']['model:6'];self.assertEqual(log['state'],'done')
  self.assertEqual(log['scope'],'LEARNING_PROCESS_NOT_SCIENTIFIC_APPROVAL');self.assertTrue(snap['ledger']['ok']);self.assertEqual(self.pfile.read_bytes(),self.pbytes)
  self.assertTrue(self.s.post('/api/local-team/support',{'note':'工具环境报错，已经尝试重启但仍未解决'},self.member)['ok'])
 def test_safe_resources_and_generic_cloud_isolation(self):
  body=urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{self.s.port}/api/local-team/resource?project={self.pid}&asset=safe',headers={'X-Helix-Token':self.token})).read().decode()
  self.assertIn('&lt;script&gt;',body);self.assertNotIn('<script>bad()',body)
  with patch.dict(os.environ,{'HELIX_CLOUD_MODE':'1'}):self.assertFalse(LT.enabled({'local_team':{'enabled':True}}))
  self.assertFalse(LT.enabled({}))

if __name__=='__main__':unittest.main(verbosity=2)
