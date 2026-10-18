"""Actual headcount, independent review, membership changes and preservation of work."""
import copy, json, unittest
from pathlib import Path
import team
import test_source_project as fixture


class BalanceRules(unittest.TestCase):
    def members(self, count):
        return [{'name': 'leader' if i == 0 else f'member{i}', 'role': 'lead' if i == 0 else 'both'} for i in range(count)]

    def tasks(self):
        return [{'task_id': f'T{i}', 'week': i//10+1, 'deliverable': 'Run and retain evidence', 'status': 'PENDING'} for i in range(30)]

    def test_three_five_and_six_people_each_do_real_work(self):
        for count in (3, 5, 6):
            r = team.balanced(self.members(count), self.tasks())
            self.assertEqual(r['member_count'], count)
            self.assertTrue(all(x['primary'] > 0 and x['cross'] > 0 for x in r['load'].values()))
            self.assertLessEqual(max(v['primary'] for v in r['load'].values())-min(v['primary'] for v in r['load'].values()),1)
            self.assertTrue(all(x['owner'] != x['cross_reviewer'] for x in r['rows']))

    def test_observers_inactive_duplicates_and_empty_names_do_not_count(self):
        m = self.members(5) + [{'name':'guest','role':'observer'}, {'name':'disabled','active':False}, {'name':'leader'}, {'name':''}]
        r=team.balanced(m,self.tasks())
        self.assertEqual(r['member_count'],5)
        self.assertEqual(set(r['load']),{p['name'] for p in self.members(5)})

    def test_two_people_and_role_restrictions(self):
        m=[{'name':'a','role':'primary'},{'name':'b','role':'cross'}]
        r=team.balanced(m,self.tasks())
        self.assertTrue(all(x['owner']=='a' and x['cross_reviewer']=='b' for x in r['rows']))
        with self.assertRaises(ValueError):team.balanced([{'name':'alone'}],self.tasks())
        with self.assertRaises(ValueError):team.balanced([{'name':'a','role':'primary'},{'name':'b','role':'primary'}],self.tasks())

    def test_specialist_work_stays_with_capable_member(self):
        m=self.members(5);m[2]['duties']=['dock']
        t=self.tasks();t[0]['duty']='dock'
        self.assertEqual(team.balanced(m,t)['rows'][0]['owner'],'member2')

    def test_started_completed_and_ledger_locked_work_stays_put(self):
        t=self.tasks()
        for i,status in enumerate(('IN_PROGRESS','COMPLETED','PENDING')):
            t[i].update(owner='departed',cross_reviewer='leader',status=status)
        r=team.balanced(self.members(5),t,{'T2'})
        self.assertEqual(r['protected_count'],3)
        self.assertTrue(all(r['rows'][i]['owner']=='departed' for i in range(3)))
        self.assertTrue(r['notes'])

    def test_dependency_order_and_input_are_preserved(self):
        t=self.tasks();t[0]['depends_on']=['T1'];before=copy.deepcopy(t)
        r=team.balanced(self.members(5),t)
        self.assertEqual(t,before);self.assertEqual([x['i'] for x in r['rows']],list(range(30)))


class MembershipAPI(unittest.TestCase):
    def setUp(self):
        self.f=fixture.SourceProjectAPI();self.f.setUp();self.s=self.f.s;self.token=self.f.token
        self.members=[{'name':n,'role':'lead' if n=='owner' else 'both'} for n in ['owner','peer','third','fourth','fifth']]
        tasks=[{'task_id':f'T{i}','week':1 if i==0 else 2,'owner':'owner' if i==0 else '',
                'cross_reviewer':'peer' if i==0 else '', 'deliverable':'Practice with real evidence','acceptance':'Save actual input and output'} for i in range(16)]
        r=self.s.post('/api/projects',{'name':'Dynamic team fixture','members':self.members,'start':'2026-10-12','n_weeks':2,'tasks':tasks},self.token)
        self.assertTrue(r['ok'],r);self.pid=r['project']['id']
        self.path=self.f.root/'data/projects'/self.pid/'project.json'

    def tearDown(self):self.f.tearDown()

    def project(self):return json.loads(self.path.read_text(encoding='utf-8'))

    def test_membership_five_to_four_to_six_recalculates_without_changing_task_contracts(self):
        before=self.project()
        for members in (self.members[:4],self.members+[{'name':'sixth','role':'both'}]):
            r=self.s.post('/api/projects/update',{'members':members},self.token);self.assertTrue(r['ok'],r)
            p=self.project();self.assertEqual(p['team_assignment']['member_count'],len(members))
            names={m['name'] for m in members}
            self.assertTrue(all(t['owner'] in names and t['cross_reviewer'] in names and t['owner']!=t['cross_reviewer'] for t in p['tasks']))
            self.assertTrue(all(v['primary'] for v in p['team_assignment']['load'].values()))
            for a,b in zip(before['tasks'],p['tasks']):
                self.assertEqual({k:v for k,v in a.items() if k not in ('owner','cross_reviewer')},{k:v for k,v in b.items() if k not in ('owner','cross_reviewer')})

    def test_checkin_and_seeded_task_assignments_and_ledger_are_preserved(self):
        p=self.project();t=next(t for t in p['tasks'] if 'owner' in (t['owner'],t['cross_reviewer']))
        r=self.s.post('/api/checkin',{'task_id':t['task_id'],'state':'doing','progress':20,'note':'Actual fixture practice'},self.token)
        self.assertTrue(r['ok'],r)
        r=self.s.post('/api/seed',{'week':1},self.token);self.assertTrue(r['ok'],r)
        ledger=self.path.parent/'helix'/self.pid/'ledger.jsonl';prior=ledger.read_bytes()
        r=self.s.post('/api/projects/update',{'members':self.members[:4]},self.token);self.assertTrue(r['ok'],r)
        after={t['task_id']:t for t in self.project()['tasks']}
        for a in (p['tasks'][0],t):
            self.assertEqual((a['owner'],a['cross_reviewer']),(after[a['task_id']]['owner'],after[a['task_id']]['cross_reviewer']))
        self.assertEqual(ledger.read_bytes(),prior)
        self.assertEqual((self.f.root/'data/credentials.json').read_bytes(),self.f.creds)

    def test_new_account_alone_does_not_enter_project_or_change_assignments(self):
        before=self.path.read_bytes()
        r=self.s.post('/api/auth/register',{'name':'unrelated','pin':'member-password-123','pin_confirm':'member-password-123'})
        self.assertTrue(r['ok'],r);self.assertEqual(self.path.read_bytes(),before)

    def test_member_cannot_reallocate_the_project(self):
        r=self.s.post('/api/auth/register',{'name':'unrelated','pin':'member-password-123','pin_confirm':'member-password-123'})
        r=self.s.post('/api/auth/login',{'username':'unrelated','pin':'member-password-123'});self.assertTrue(r['ok'])
        self.assertFalse(self.s.post('/api/projects/update',{'members':self.members[:3]},r['token'])['ok'])


if __name__=='__main__':unittest.main(verbosity=2)
