"""Exercise the actual row builder with isolated dependencies; no network or accounts."""
import ast,types,unittest
from pathlib import Path

class CloudIdentity(unittest.TestCase):
 def rows(self,metadata):
  tree=ast.parse(Path(__file__).with_name('serve.py').read_text(encoding='utf-8'))
  function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='cloud_rows')
  task={'task_id':'T1','owner':'owner','deliverable':'Actual task','end':'2026-10-25'}
  ctx=types.SimpleNamespace(entries=lambda:[],tasks=lambda:[task])
  g={'Ctx':lambda _:ctx,'TEAM':types.SimpleNamespace(fold_checkins=lambda _: {},STATES={}),
     'KERNEL':None,'P':types.SimpleNamespace(is_local=lambda _:True,load=lambda _:metadata),
     '_project_id':lambda:'new-id','display_map':lambda:{},'_cfg':lambda:{},'REVIEW_LABEL':{},'apply_map':lambda v,m:v}
  exec(compile(ast.Module(body=[function],type_ignores=[]),'serve.cloud_rows','exec'),g)
  return g['cloud_rows']('new-id')
 def test_generic_project_keeps_own_id(self):
  self.assertEqual(self.rows({'name':'Generic'})[0]['helix_id'],'new-id:T1')
 def test_explicit_source_alias_reuses_original_mirror(self):
  row=self.rows({'name':'Adopted','notion_project_id':'original-id'})[0]
  self.assertEqual(row['helix_id'],'original-id:T1');self.assertEqual(row['due'],'2026-10-25')
  self.assertFalse({'parameters','human_runs','inputs','token'}&set(row))

if __name__=='__main__':unittest.main(verbosity=2)
