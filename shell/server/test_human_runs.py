"""Human execution API acceptance, with fake identities and isolated project data."""
import io,json,os,sys,unittest
from pathlib import Path
from test_source_project import SourceProjectAPI
import human_runs as HR

class HumanAPI(unittest.TestCase):
    login=SourceProjectAPI.login
    adopt=SourceProjectAPI.adopt
    tearDown=SourceProjectAPI.tearDown
    def setUp(self):
        runtime=Path(__file__).resolve().parents[2]/'runtime/science'
        previous=os.environ.get('HELIX_SCIENCE_ROOT')
        if runtime.is_dir():os.environ['HELIX_SCIENCE_ROOT']=str(runtime)
        try:SourceProjectAPI.setUp(self)
        finally:
            if previous is None:os.environ.pop('HELIX_SCIENCE_ROOT',None)
            else:os.environ['HELIX_SCIENCE_ROOT']=previous
        self.adopt()
    def begin(self,**extra):
        r=self.s.post('/api/human/start',{'task_id':'T1','method':'本人亲自运行数据检查并保存工件',**extra},self.token)
        self.assertTrue(r['ok'],r);return r['run_id']
    def artifact(self):
        return self.s.post('/api/files?name=operation-log.txt',token=self.token,raw=b'actual isolated test operation log')['file']['sha256']
    def end(self,rid,**extra):
        return self.s.post('/api/human/finish',{'run_id':rid,'status':'completed','steps':'本人打开原始文件，执行检查，保存运行日志。','observation':'观察到结果可重复。',**extra},self.token)
    def test_01_external_operation_record_and_review_separation(self):
        rid=self.begin();self.assertFalse(self.end(rid)['ok'])
        sha=self.artifact();r=self.end(rid,outputs=[sha]);self.assertTrue(r['ok'],r)
        s=self.s.get('/api/snapshot',self.token);run=s['human_runs'][0]
        self.assertEqual(run['status'],'completed');self.assertEqual(run['record_kind'],'human_reported')
        self.assertEqual(s['gates']['tasks']['T1']['state'],'unseeded');self.assertEqual(s['team']['summary']['done'],0)
        self.assertTrue(s['ledger']['ok']);self.assertFalse(self.end(rid,outputs=[sha])['ok'])
    def test_02_login_and_task_identity_required(self):
        self.assertFalse(self.s.post('/api/human/start',{'task_id':'T1','method':'不能代为操作'})['ok'])
        reg=self.s.post('/api/auth/register',{'name':'outsider','pin':'outsider-password','pin_confirm':'outsider-password'})
        tok=self.s.post('/api/auth/login',{'person_id':reg['person']['id'],'pin':'outsider-password'})['token']
        self.assertFalse(self.s.post('/api/human/start',{'task_id':'T1','method':'不能冒领任务'},tok)['ok'])
    def test_03_cross_reviewer_can_operate_but_cannot_continue_owner_record(self):
        rid=self.begin()
        r=self.s.post('/api/auth/set-pin',{'person_id':'peer-id','new_pin':'peer-password-123','admin_id':'owner-id','admin_pin':'owner-password-123'},self.token);self.assertTrue(r['ok'],r)
        tok=self.s.post('/api/auth/login',{'person_id':'peer-id','pin':'peer-password-123'})['token']
        self.assertFalse(self.s.post('/api/human/finish',{'run_id':rid,'status':'blocked','steps':'不能修改他人实操记录','observation':'仅本人操作'},tok)['ok'])
        own=self.s.post('/api/human/start',{'task_id':'T1','method':'交叉核亲自独立运行一个样例'},tok);self.assertTrue(own['ok'],own)
        run=next(r for r in self.s.get('/api/snapshot',tok)['human_runs'] if r['run_id']==own['run_id']);self.assertEqual(run['as'],'cross')
    def test_04_cannot_spoof_actor_or_artifacts(self):
        self.assertFalse(self.s.post('/api/human/start',{'task_id':'T1','method':'本人操作','person_id':'peer-id'},self.token)['ok'])
        self.assertFalse(self.s.post('/api/human/start',{'task_id':'T1','method':'本人操作','inputs':['f'*64]},self.token)['ok'])
        rid=self.begin();self.assertFalse(self.end(rid,outputs=['f'*64])['ok'])
    def test_05_tool_failure_is_recorded_without_false_completion(self):
        rid=self.begin();r=self.s.post('/api/tools/run',{'tool':'uninstalled-tool','human_run_id':rid},self.token)
        self.assertFalse(r['ok']);run=self.s.get('/api/snapshot',self.token)['human_runs'][0]
        self.assertEqual(run['status'],'running');self.assertFalse(run['tools'][0]['ok']);self.assertTrue(run['outputs'])
        self.assertTrue(self.end(rid,status='blocked',observation='工具没有安装，已保留原始错误。')['ok'])
    def test_06_secret_redaction(self):
        secret='ntn_'+'x'*40;rid=self.begin(method='本人操作 '+secret,parameters={'api_key':'private-value','note':'password=private-value'})
        run=self.s.get('/api/snapshot',self.token)['human_runs'][0];text=json.dumps(run)
        self.assertNotIn(secret,text);self.assertNotIn('private-value',text);self.assertIn('REDACTED',text)
    def test_07_incomplete_notes_not_just_signature(self):
        rid=self.begin();sha=self.artifact()
        self.assertFalse(self.end(rid,steps='已签字',outputs=[sha])['ok'])
        self.assertFalse(self.end(rid,observation='',outputs=[sha])['ok'])
    def test_08_human_task_fields_survive_edit_and_change_plan_hash(self):
        snap=self.s.get('/api/snapshot',self.token);tasks=snap['plan']['tasks'];tasks[0].update(execution_mode='human',human_work='本人亲自复算并保留工件')
        self.assertTrue(self.s.post('/api/projects/update',{'tasks':tasks},self.token)['ok'])
        self.assertTrue(self.s.post('/api/plan/freeze',{},self.token)['ok'])
        snap=self.s.get('/api/snapshot',self.token);self.assertEqual(snap['plan']['tasks'][0]['execution_mode'],'human')
        tasks=snap['plan']['tasks'];tasks[0]['human_work']='本人亲自独立复算，增加一个反例'
        self.s.post('/api/projects/update',{'tasks':tasks},self.token)
        self.assertTrue(self.s.get('/api/snapshot',self.token)['gates']['plan']['changed'])
    @unittest.skipUnless(sys.platform=='win32','Real bundled Windows RDKit execution is validated on Windows')
    def test_09_real_rdkit_output_is_captured(self):
        rid=self.begin();r=self.s.post('/api/tools/run',{'tool':'rdkit','smiles':'CCO','human_run_id':rid},self.token)
        self.assertTrue(r['ok'],r);self.assertEqual(r['human_execution']['record_kind'],'server_observed')
        self.assertTrue(self.end(rid)['ok']);run=self.s.get('/api/snapshot',self.token)['human_runs'][0]
        self.assertTrue(run['tools'][0]['ok']);self.assertGreaterEqual(len(run['outputs']),2)
        self.assertEqual(run['record_kind'],'server_observed_tool_and_human_report')
    def test_10_machine_actor_cannot_write_human_events(self):
        sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
        from harness import ledger
        HR.register_events(ledger);led=ledger.HelixLedger('unit-human',root=self.root/'unit-ledger');led.ensure_genesis()
        with self.assertRaises(ledger.LedgerPermissionError):led.append('human.execution.started',{'run_id':'invalid'},actor='ai:test',actor_kind='ai')

if __name__=='__main__':unittest.main(verbosity=2)
