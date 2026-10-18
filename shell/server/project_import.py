"""Portable, transactional project material import. Never executes source code."""
from __future__ import annotations
import csv,hashlib,io,json,os,re,secrets,shutil,stat,threading,zipfile
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone

LOCK=threading.RLock()
MAX_ZIP=256*1024*1024
MAX_TOTAL=512*1024*1024
MAX_FILES=10000
PRIVATE={'credentials.json','people.json','cloud.json','secrets.json','access.json','encryption.key','join.json','member_invites.json','seat_claims.json','install.json','python.txt','sessions.json','update-signing.json','update-key.json'}
IGNORED={'.git','.venv','venv','node_modules','__pycache__','runtime','.pytest_cache'}
PLAN_NAMES=('project.helix.json','项目.helix.json','project.json')
TASK_NAMES=('tasks.csv','任务表.csv','CURRENT_SPRINT_TASKS_20261012.csv','tasks.xlsx','任务表.xlsx')
SECRET=re.compile(rb'(?i)(?:ntn_|secret_|sk-)[A-Za-z0-9_-]{20,}')

def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

def safe_rel(name):
 name=str(name).replace('\\','/')
 p=PurePosixPath(name)
 if not name or p.is_absolute() or any(x in ('','..','.') or ':' in x or re.search(r'[\x00-\x1f<>"|?*]',x) or x.endswith((' ','.')) for x in name.split('/')):
  raise ValueError('项目包含不安全的相对路径：'+name[:120])
 for x in p.parts:
  if x.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:raise ValueError('项目包含Windows保留文件名')
 return p.as_posix()

def skip_reason(rel):
 parts=PurePosixPath(rel).parts
 if any(x.lower() in IGNORED for x in parts):return '运行环境或缓存'
 if parts[-1].lower() in PRIVATE or parts[-1].lower().endswith(('.dpapi.json','.pem','.key','.pfx','.p12')) or parts[-1].lower() in ('.env','models.json'):return '账号或连接配置'
 if 'ledger.jsonl'==parts[-1].lower():return '历史签署账本不作为新项目记录导入'
 return ''

def _stage(P,actor):
 token=secrets.token_hex(16);root=P.DATA/'imports'/token
 (root/'content').mkdir(parents=True)
 return token,root

def _plan(P,folder,label,actor):
 source={};manifest={};warnings=[]
 for name in PLAN_NAMES:
  path=folder/name
  if path.is_file():
   manifest=json.loads(path.read_text(encoding='utf-8-sig'))
   source=manifest.get('project',manifest);break
 if not isinstance(source,dict):raise ValueError('项目说明必须是JSON对象')
 tasks=source.get('tasks') or []
 if not tasks:
  for name in TASK_NAMES:
   if (folder/name).is_file():tasks=P.parse_tasks(name,(folder/name).read_bytes())['rows'];break
 if not tasks:warnings.append('未找到任务表；先作为资料项目导入，可稍后编辑计划')
 members=[dict(m) for m in source.get('members',[]) if isinstance(m,dict) and m.get('name')]
 names=list(dict.fromkeys([*(m['name'] for m in members),*(t.get(k,'') for t in tasks for k in ('owner','cross_reviewer') if t.get(k))]))
 lead_names={m['name'] for m in members if m.get('role')=='lead'}
 if not lead_names and '组长' in names:lead_names={'组长'}
 remap={name:actor['name'] for name in lead_names}
 for t in tasks:
  for key in ('owner','cross_reviewer'):t[key]=remap.get(t.get(key,''),t.get(key,''))
  t['status']='PENDING'
 names=list(dict.fromkeys([actor['name'],*(remap.get(n,n) for n in names if n)]))
 # Register explicit anonymous labels before generating labels for named sources.
 # Otherwise a source ordered C,A,B can make the generated A shadow the real A slot.
 rest=[n for n in names if n!=actor['name']]
 names=[actor['name'],*sorted(rest,key=lambda n:(0,n) if re.fullmatch('组员[A-Z]+|组员[0-9]+',n) else (1,rest.index(n)))]
 if len(names)<2:names.append('协作组员')
 members=[{'name':n,'role':'lead' if n==actor['name'] else 'both','duties':next((m.get('duties',[]) for m in members if remap.get(m['name'],m['name'])==n),[])} for n in names]
 n=max([int(str(t.get('week') or 1).strip().lstrip('Ww第').rstrip('周')) for t in tasks]+[1])
 data={k:source[k] for k in ('name','target','question','duties','red_lines','weeks','status') if k in source}
 data.setdefault('name',label or '导入项目');data['members']=members;data['tasks']=tasks;data['claim_ceiling']='EXPLORATORY_ONLY'
 if not data.get('weeks'):
  data.update(start=next((t.get('start') for t in tasks if t.get('start')),datetime.now().date().isoformat()),n_weeks=n)
 clean,warn=P.clean_project(data);warnings+=warn
 return clean,warnings,manifest

def _finish_preview(P,token,root,label,actor,skipped):
 rows=[];total=0;seen=set()
 for file in sorted((root/'content').rglob('*')):
  if not file.is_file():continue
  rel=safe_rel(file.relative_to(root/'content').as_posix())
  if rel.casefold() in seen:raise ValueError('项目有大小写冲突的文件：'+rel)
  seen.add(rel.casefold());size=file.stat().st_size;total+=size
  if len(rows)>=MAX_FILES or size>MAX_ZIP or total>MAX_TOTAL:raise ValueError('项目过大：最多10000文件、单文件256MB、总计512MB')
  # Text connection keys are never copied into a distributable project.
  if size<4*1024*1024 and file.suffix.lower() in {'.json','.env','.ini','.cfg','.txt','.yaml','.yml'} and SECRET.search(file.read_bytes()):raise ValueError('资料含连接Token，请先移除该配置文件：'+rel)
  rows.append({'path':rel,'bytes':size,'sha256':digest(file)})
 if not rows:raise ValueError('没有可导入的项目资料')
 project,warnings,manifest=_plan(P,root/'content',label,actor)
 declared=manifest.get('files',{})
 if isinstance(declared,dict):
  actual={r['path']:r['sha256'] for r in rows}
  for rel,sha in declared.items():
   if actual.get(safe_rel(rel))!=sha:raise ValueError('项目原件缺失或SHA不一致：'+rel)
 workspace=manifest.get('local_workspace')
 if workspace:validate_workspace(workspace,project,rows)
 record={'owner_id':actor['id'],'token':token,'project':project,'files':rows,'warnings':warnings,'skipped':skipped,'bytes':total,'local_workspace':workspace}
 fingerprint=hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest();record['snapshot_hash']=fingerprint
 P._write(root/'preview.json',record)
 return {'token':token,'snapshot_hash':fingerprint,'name':project['name'],'task_count':len(project['tasks']),'file_count':len(rows),'bytes':total,'files':rows,'warnings':warnings,'skipped':skipped}

def preview_zip(P,data,label,actor):
 if len(data)>MAX_ZIP:raise ValueError('ZIP文件上限256MB')
 token,root=_stage(P,actor);skipped=[]
 try:
  with zipfile.ZipFile(io.BytesIO(data)) as z:
   infos=[i for i in z.infolist() if not i.is_dir()]
   if len(infos)>MAX_FILES or sum(i.file_size for i in infos)>MAX_TOTAL:raise ValueError('项目ZIP解压后过大')
   names=[safe_rel(i.filename) for i in infos]
   prefix=names[0].split('/')[0]+'/' if names and all('/' in n and n.split('/')[0]==names[0].split('/')[0] for n in names) else ''
   seen=set()
   for info,name in zip(infos,names):
    if stat.S_ISLNK(info.external_attr>>16) or info.flag_bits&1:raise ValueError('项目ZIP不支持符号链接或加密条目')
    rel=name[len(prefix):] if prefix else name
    if rel.casefold() in seen:raise ValueError('ZIP存在重复路径：'+rel)
    seen.add(rel.casefold());reason=skip_reason(rel)
    if reason:skipped.append({'path':rel,'reason':reason});continue
    path=root/'content'/rel;path.parent.mkdir(parents=True,exist_ok=True)
    with z.open(info) as src,path.open('xb') as dst:shutil.copyfileobj(src,dst)
  return _finish_preview(P,token,root,label,actor,skipped)
 except Exception:shutil.rmtree(root);raise

def preview_folder(P,source,actor):
 if os.environ.get('HELIX_CLOUD_MODE')=='1':raise ValueError('云端请上传项目ZIP；本机路径只在本机读取')
 source=Path(source).expanduser().resolve()
 if not source.is_dir():raise ValueError('找不到这个项目文件夹')
 if source==source.parent or source==P.ROOT or P.DATA.resolve().is_relative_to(source):raise ValueError('请选择具体的项目资料文件夹，不要选择磁盘或Helix安装目录')
 token,root=_stage(P,actor);skipped=[];count=total=0
 try:
  for parent,dirs,files in os.walk(source,followlinks=False):
   dirs[:]=[d for d in dirs if d.lower() not in IGNORED and not (Path(parent)/d).is_symlink() and not ((Path(parent)/d).lstat().st_file_attributes&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0) if os.name=='nt' else False)]
   for name in files:
    file=Path(parent)/name;rel=safe_rel(file.relative_to(source).as_posix());reason=skip_reason(rel)
    info=file.lstat()
    if reason or file.is_symlink() or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):skipped.append({'path':rel,'reason':reason or '链接'});continue
    count+=1;total+=info.st_size
    if count>MAX_FILES or total>MAX_TOTAL or info.st_size>MAX_ZIP:raise ValueError('项目资料超过导入容量，请选择项目子目录或拆分资料')
    dst=root/'content'/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,dst)
  return _finish_preview(P,token,root,source.name,actor,skipped)
 except Exception:shutil.rmtree(root);raise

def commit(P,token,expected,actor,register,ledger):
 if not re.fullmatch('[0-9a-f]{32}',str(token)):raise ValueError('导入标识无效')
 with LOCK:
  root=P.DATA/'imports'/token;r=json.loads((root/'preview.json').read_text(encoding='utf-8'))
  if r['owner_id']!=actor['id']:raise PermissionError('此导入预览不属于当前账号')
  if r['snapshot_hash']!=expected:raise ValueError('导入预览已变化，请重新读取')
  checked={k:v for k,v in r.items() if k not in ('snapshot_hash','committed')}
  if hashlib.sha256(json.dumps(checked,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=expected:raise ValueError('导入预览文件损坏，请重新读取')
  if r.get('committed'):
   P.set_active(r['committed'])
   return {'id':r['committed'],'existing':True,'files':len(r['files'])}
  for f in r['files']:
   if digest(root/'content'/safe_rel(f['path']))!=f['sha256']:raise ValueError('预览后的原件发生变化，请重新读取')
  p=None;peoplefile=P.DATA/'people.json';people_before=peoplefile.read_bytes() if peoplefile.exists() else None
  try:
   p,warnings=P.create(r['project']);pd=P.pdir(p['id']);library=pd/'files';(library/'source').parent.mkdir(parents=True,exist_ok=True)
   shutil.copytree(root/'content',library/'source')
   rows=[{'name':Path(f['path']).name,'stored':'source/'+f['path'],'relative_path':f['path'],'sha256':f['sha256'],'bytes':f['bytes'],'added_at':P.now(),'added_by':'','note':'导入来源资料 · '+f['path']} for f in r['files']]
   P._write(pd/'files.json',{'schema':'helix.files.v1','files':rows})
   mfiles=[{'path':'source/'+f['path'],'bytes':f['bytes'],'sha256':f['sha256'],'group':'project'} for f in r['files']]
   P._write(library/'manifest.json',{'files':mfiles,'groups':{'project':{'files':len(rows)}},'papers':0,'pdfs':sum(f['path'].lower().endswith('.pdf') for f in r['files']),'created_at':P.now(),'authority_modified':False})
   p['source']={'kind':'portable','library':str(library),'snapshot_hash':r['snapshot_hash'],'witnesses':[]}
   p['import_summary']={'files':len(rows),'bytes':r['bytes'],'skipped':r['skipped'],'approval_history_imported':False}
   P._write(pd/'project.json',p)
   register(p['members']);ledger(p['id'],root=P.ledger_root(p['id'])).ensure_genesis()
   if r.get('local_workspace') and os.environ.get('HELIX_CLOUD_MODE')!='1':
    workspace=r['local_workspace'];people=json.loads((P.DATA/'people.json').read_text(encoding='utf-8'))['people'];seats=[]
    for seat in workspace['seats']:
     ref=actor['name'] if seat['id']=='coordinator' else seat['member_ref'];fp=hashlib.sha256(('helix-person:'+ref).encode()).hexdigest()[:12]
     person=next(x for x in people if (x['id']==actor['id'] if seat['id']=='coordinator' else fp in x.get('keys',[]) or x['name']==ref))
     seats.append({k:seat[k] for k in ('id','title','summary','alias','hours','reviewer_slot','skills')}|{'member_ref':ref,'person_id':person['id']})
    p['local_team']={'enabled':True,'schema':'helix.local-team.v1','seats':seats,'source_roots':{'portable':str(library/'source')},'assets':[{**a,'root':'portable','exists':True} for a in workspace['assets']],'engineering_audit':workspace.get('engineering_audit',{}),'source_name_map':workspace.get('source_name_map',{}),'learning_week_offset':5}
    P._write(pd/'curriculum.json',workspace['curriculum']);P._write(pd/'project.json',p)
   r['committed']=p['id'];P._write(root/'preview.json',r);P.set_active(p['id'])
   shutil.rmtree(root/'content')
   return {'id':p['id'],'files':len(rows),'tasks':len(p['tasks']),'warnings':warnings,'existing':False}
  except Exception:
   if p and P.pdir(p['id']).exists() and not r.get('committed'):shutil.rmtree(P.pdir(p['id']))
   if not r.get('committed') and people_before is not None:
    tmp=peoplefile.with_suffix('.restore.tmp');tmp.write_bytes(people_before);tmp.replace(peoplefile)
   raise

def discard(P,token,actor):
 if not re.fullmatch('[0-9a-f]{32}',str(token)):raise ValueError('导入标识无效')
 with LOCK:
  root=P.DATA/'imports'/token
  if not root.exists():return {'discarded':True}
  r=json.loads((root/'preview.json').read_text(encoding='utf-8'))
  if r['owner_id']!=actor['id']:raise PermissionError('此导入预览不属于当前账号')
  if not r.get('committed'):shutil.rmtree(root)
  return {'discarded':not bool(r.get('committed'))}

def validate_workspace(workspace,project,files):
 seats=workspace.get('seats',[]);ids={s.get('id') for s in seats};names={m['name'] for m in project['members']}
 if len(seats)!=5 or len(ids)!=5 or 'coordinator' not in ids:raise ValueError('项目分工模板必须包含5个不同岗位及统筹')
 for s in seats:
  if set(('id','title','summary','alias','hours','reviewer_slot','skills','member_ref'))-set(s):raise ValueError('岗位模板字段不完整')
  if s['reviewer_slot'] not in ids or s['reviewer_slot']==s['id']:raise ValueError('岗位复核人配置不正确')
  if s['id']!='coordinator' and s['member_ref'] not in names:raise ValueError('岗位成员不在计划中')
 known={f['path']:f['sha256'] for f in files};seen=set()
 for a in workspace.get('assets',[]):
  if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',a.get('id','')) or a['id'] in seen:raise ValueError('来源资料标识无效或重复')
  seen.add(a['id'])
  if known.get(safe_rel(a.get('path','')))!=a.get('sha256'):raise ValueError('岗位资料缺失或SHA不一致')
 curriculum=workspace.get('curriculum',{})
 for link in curriculum.get('external_resources',[]):
  if not str(link.get('url','')).startswith(('https://','http://')):raise ValueError('学习资源链接只支持HTTP/HTTPS')
 if any(t.get('workspace_slot') not in ids for t in project['tasks']):raise ValueError('任务与岗位模板不对应')

def export_project(P,pid,mapping):
 p=P.load(pid)
 if not p:raise ValueError('请先接入或新建普通项目，再导出模板')
 project={k:p[k] for k in ('name','target','question','members','weeks','tasks','duties','red_lines','claim_ceiling') if k in p}
 project=mapping(project)
 for t in project['tasks']:t['status']='PENDING'
 out=io.BytesIO();hashes={};seen=set();workspace=None;total=0
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  def add(rel,path):
   nonlocal total
   rel=safe_rel(rel);raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
   if rel in hashes:
    if hashes[rel]!=sha:raise ValueError('导出资料路径冲突：'+rel)
    return sha
   total+=len(raw)
   if total>MAX_TOTAL or len(hashes)>=MAX_FILES:raise ValueError('项目超过单包导入容量，请按资料项目拆分后导出')
   hashes[rel]=sha;seen.add(sha);z.writestr(rel,raw);return sha
  for i,f in enumerate(P.files(pid)):
   path=P.file_path(pid,f['sha256'])
   if not path:raise ValueError('原件缺失，未导出不完整项目：'+f['name'])
   rel='资料/'+(safe_rel(f['relative_path']) if f.get('relative_path') else str(i+1)+'-'+f['name']);add(rel,path)
  import science as SCI,source_project as SP
  library=SP.library(p)
  if library:
   for f in SCI.manifest(library).get('files',[]):
    if f['sha256'] not in seen:add('研究资料/'+safe_rel(f['path']),SCI.file_path(f['path'],root=library))
  team=p.get('local_team') or {}
  if team.get('enabled'):
   workspace={'seats':[],'assets':[],'curriculum':mapping(json.loads((P.pdir(pid)/'curriculum.json').read_text(encoding='utf-8'))),'engineering_audit':team.get('engineering_audit',{}),'source_name_map':{s['member_ref']:s['title'] for s in team['seats']}}
   for s in team['seats']:
    workspace['seats'].append({k:(mapping(s[k]) if k=='member_ref' else s[k]) for k in ('id','title','summary','alias','hours','reviewer_slot','skills','member_ref')})
   import local_team as LT
   for a in team['assets']:
    path=LT.asset_path(team,a);rel='岗位资料/'+a['id']+'-'+path.name;sha=add(rel,path)
    workspace['assets'].append({k:a.get(k) for k in ('id','title','kind','classification','scaffold')}|{'path':rel,'sha256':sha})
  manifest={'schema':'helix.portable-project.v1','project':project,'files':hashes,'approval_history_imported':False,'scope':'PLAN_AND_SOURCE_MATERIALS_NO_ACCOUNTS_OR_SIGNOFFS'}
  if workspace:manifest['local_workspace']=workspace
  z.writestr('project.helix.json',json.dumps(manifest,ensure_ascii=False,indent=2))
 return out.getvalue()
