"""Portable project import acceptance: real APIs, isolated state, no live credentials."""
import io,json,zipfile,urllib.request,unittest,hashlib
from version import VERSION
from pathlib import Path
from test_anonymous_join import AnonymousJoin

class ProjectImport(unittest.TestCase):
 def setUp(self):
  self.fx=AnonymousJoin();self.fx.setUp();self.s=self.fx.s;self.token=self.fx.token;self.root=self.fx.root
  self.plan={'name':'通用独立项目','members':[{'name':'原组长','role':'lead'},{'name':'科研甲','role':'both'}],'start':'2026-10-19','n_weeks':2,'tasks':[{'task_id':'T-A','week':1,'owner':'原组长','cross_reviewer':'科研甲','deliverable':'实际读取输入','acceptance':'保留输出','status':'COMPLETED'},{'task_id':'T-B','week':2,'owner':'科研甲','cross_reviewer':'原组长','deliverable':'独立复核','acceptance':'验证来源','depends_on':['T-A']}]}
  self.creds=(self.root/'data/credentials.json').read_bytes()
 def tearDown(self):self.fx.tearDown()
 def archive(self,extra=None):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('Example/project.helix.json',json.dumps({'schema':'helix.portable-project.v1','project':self.plan}))
   z.writestr('Example/资料/真实输入.txt','原始内容，未经改写。')
   for k,v in (extra or {}).items():z.writestr(k,v)
  return b.getvalue()
 def preview(self,data=None,token=None):return self.s.post('/api/project-import/zip?name=Example',token=self.token if token is None else token,raw=data or self.archive())
 def commit(self,p,**extra):return self.s.post('/api/project-import/commit',{'token':p['token'],'snapshot_hash':p['snapshot_hash'],**extra},self.token)
 def test_complete_import_preserves_bytes_links_tasks_and_does_not_import_approvals(self):
  p=self.preview();self.assertTrue(p['ok'],p);self.assertEqual((p['file_count'],p['task_count']),(2,2))
  r=self.commit(p);self.assertTrue(r['ok'],r);snap=self.s.get('/api/snapshot',self.token)
  self.assertEqual(snap['project']['id'],r['id']);self.assertEqual(len(snap['files']),2)
  self.assertEqual(snap['plan']['tasks'][0]['status'],'PENDING');self.assertEqual(snap['plan']['tasks'][1]['depends_on'],['T-A'])
  self.assertEqual(self.creds,(self.root/'data/credentials.json').read_bytes());self.assertTrue(snap['ledger']['ok'])
  self.assertTrue(self.commit(p)['existing']);self.assertEqual(len(self.s.get('/api/snapshot',self.token)['files']),2)
  req=urllib.request.Request(f'http://127.0.0.1:{self.s.port}/api/research/file?path=source/%E8%B5%84%E6%96%99/%E7%9C%9F%E5%AE%9E%E8%BE%93%E5%85%A5.txt',headers={'X-Helix-Token':self.token})
  self.assertEqual(urllib.request.urlopen(req).read(),'原始内容，未经改写。'.encode())
 def test_traversal_duplicates_and_bad_task_graph_rejected(self):
  self.assertFalse(self.preview(self.archive({'../outside.txt':'no'}))['ok'])
  self.assertFalse(self.preview(self.archive({'Example/资料/真实输入.txt':'duplicate'}))['ok'])
  self.plan['tasks'][0]['depends_on']=['MISSING'];self.assertFalse(self.preview()['ok'])
 def test_private_files_excluded_and_real_secret_config_rejected(self):
  p=self.preview(self.archive({'Example/credentials.json':'{"fake":"fixture"}','Example/.env':'fixture'}));self.assertTrue(p['ok'],p);self.assertEqual(len(p['skipped']),2)
  self.assertFalse(self.preview(self.archive({'Example/config.json':'{"token":"ntn_'+'x'*32+'"}'}))['ok'])
 def test_non_admin_cannot_import_and_preview_tamper_is_detected(self):
  self.assertFalse(self.preview(token='')['ok']);p=self.preview();self.assertTrue(p['ok'])
  path=self.root/'data/imports'/p['token']/'content/资料/真实输入.txt';path.write_text('被改动',encoding='utf-8');self.assertFalse(self.commit(p)['ok'])
 def test_folder_without_task_table_imports_all_materials(self):
  folder=self.root.parent/'Generic Folder 中文';folder.mkdir();(folder/'data.csv').write_text('a,b\n1,2\n',encoding='utf-8');(folder/'说明.md').write_text('任何项目资料',encoding='utf-8')
  p=self.s.post('/api/project-import/folder',{'path':str(folder)},self.token);self.assertTrue(p['ok'],p);self.assertEqual(p['file_count'],2);self.assertEqual(p['task_count'],0)
  self.assertTrue(self.commit(p)['ok']);self.assertEqual(self.s.get('/api/snapshot',self.token)['research_bundle']['file_count'],2)
 def test_export_can_be_reimported_with_same_material_bytes(self):
  p=self.preview();self.assertTrue(self.commit(p)['ok'])
  req=urllib.request.Request(f'http://127.0.0.1:{self.s.port}/api/projects/export',headers={'X-Helix-Token':self.token});raw=urllib.request.urlopen(req).read()
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   self.assertIn('project.helix.json',z.namelist());self.assertFalse(any('credentials' in n for n in z.namelist()))
  p2=self.preview(raw);self.assertTrue(p2['ok'],p2);self.assertTrue(self.commit(p2)['ok'])
 def test_generic_blank_install_never_discovers_workstation_data(self):
  cfgpath=self.root/'helix.config.json';cfg=json.loads(cfgpath.read_text());cfg['generic_mode']=True;cfgpath.write_text(json.dumps(cfg));(self.root/'data/active.json').unlink()
  snap=self.s.get('/api/snapshot',self.token);self.assertTrue(snap['generic_mode']);self.assertEqual(snap['items'],[]);self.assertEqual(snap['release']['version'],VERSION)
 def test_out_of_order_anonymous_names_do_not_collapse_member_identities(self):
  roster=self.root/'data/people.json';saved=json.loads(roster.read_text(encoding='utf-8'));saved['people']=[p for p in saved['people'] if p['role']=='lead'];roster.write_text(json.dumps(saved,ensure_ascii=False),encoding='utf-8')
  names=['组员C','组员A','组员D','组员B'];self.plan['members']=[{'name':'原组长','role':'lead'}]+[{'name':n,'role':'both'} for n in names]
  self.plan['tasks']=[{'task_id':'R'+str(i),'week':1,'owner':n,'cross_reviewer':names[(i+1)%4],'deliverable':'本人任务','acceptance':'独立核对'} for i,n in enumerate(names)]
  p=self.preview();self.assertTrue(self.commit(p)['ok'])
  people=json.loads((self.root/'data/people.json').read_text(encoding='utf-8'))['people'];ids=[]
  for name in names:
   fp=hashlib.sha256(('helix-person:'+name).encode()).hexdigest()[:12];ids.append(next(x['id'] for x in people if fp in x.get('keys',[])))
  self.assertEqual(len(set(ids)),4)

if __name__=='__main__':unittest.main(verbosity=2)
