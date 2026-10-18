"""Project-scoped local seats, native GNN learning and evidence-backed process logs."""
from __future__ import annotations
import csv, hashlib, html, io, json, os, re
from datetime import datetime, timezone
from pathlib import Path
import human_runs as HR
import identity as ID

EVENTS={'learning.checkin','team.support'}

def register_events(mod):
    mod.EVENT_TYPES=frozenset(set(mod.EVENT_TYPES)|EVENTS)
    mod.HUMAN_ONLY=frozenset(set(mod.HUMAN_ONLY)|EVENTS)

def enabled(project):
    return os.environ.get('HELIX_CLOUD_MODE')!='1' and bool((project or {}).get('local_team',{}).get('enabled'))

def config(P,pid):
    project=P.load(pid)
    if not enabled(project):raise ValueError('当前项目未启用本机五人工作台')
    return project,project['local_team']

def fingerprint(name):return hashlib.sha256(('helix-person:'+name.strip()).encode()).hexdigest()[:12]

def claims(P,pid):
    p=P.pdir(pid)/'seat_claims.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

def curriculum(P,pid):
    p=P.pdir(pid)/'curriculum.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

def seat_people(cfg,chosen,people,ident):
    out={}
    for slot in cfg['seats']:
        person=next((p for p in people if p['id']==chosen.get(slot['id'],{}).get('person_id',slot['person_id']) and p.get('active',True)),None)
        occupied=bool(person and ident.has_pin(person['id']))
        out[slot['id']]={'person':person,'occupied':occupied or slot['id']=='coordinator'}
    return out

def view(P,pid,people,ident,person,entries):
    project,cfg=config(P,pid);chosen=claims(P,pid);holders=seat_people(cfg,chosen,people,ident)
    my=next((sid for sid,h in holders.items() if h['person'] and h['occupied'] and person and h['person']['id']==person['id']),None)
    seats=[]
    for slot in cfg['seats']:
        h=holders[slot['id']]
        tasks=[t for t in project['tasks'] if t.get('workspace_slot')==slot['id']]
        seats.append({k:slot[k] for k in ('id','title','summary','alias','hours','reviewer_slot','skills')}|
                     {'occupied':h['occupied'],'is_mine':my==slot['id'],'task_count':len(tasks),
                      'research_count':sum(t.get('task_kind')=='research' for t in tasks),
                      'holder_name':h['person']['name'] if h['person'] and h['occupied'] else '',
                      'tasks':[{k:t.get(k) for k in ('task_id','week','deliverable','human_work','acceptance','owner','cross_reviewer','workspace_slot','task_kind','estimated_minutes','evidence_refs')} for t in tasks]})
    learning={};support=[]
    for event in entries:
        p=event.get('payload') or {}
        if event.get('type')=='learning.checkin':learning[f"{p['slot']}:{p['learning_week']}"]={**p,'actor':event['actor'],'ts':event['ts']}
        if event.get('type')=='team.support':support.append({**p,'actor':event['actor'],'ts':event['ts']})
    data=curriculum(P,pid)
    lessons=data.get('roles',{})
    assets=[{k:a.get(k) for k in ('id','title','kind','classification','exists','scaffold')}|
            {'url':f'/api/local-team/resource?project={pid}&asset={a["id"]}'} for a in cfg.get('assets',[])]
    return {'enabled':True,'project_id':pid,'my_slot':my,'must_select':bool(person and person.get('role')!='lead' and not my),
            'seats':seats,'participant_count':len(seats),'occupied_count':sum(s['occupied'] for s in seats),
            'learning':lessons,'learning_progress':learning,'support_logs':support[-20:][::-1],
            'resources':assets,'external_resources':data.get('external_resources',[]),'audit':audit(P,pid),
            'guide':['用自己的账号登录，选择一个空岗位；统筹岗位固定给组长。',
                     '查看本周任务、输入资料和交付要求；打开对应学习卡与代码，亲手操作。',
                     '上传自己的输出或原始报错，再记录学习与任务进度；另一位成员独立复核。',
                     '遇到问题点“申请统筹协助”；进度记录与科研结论分别验收。'],
            'current_learning_week':int(cfg.get('learning_week_offset',5))+next((w['week'] for w in project['weeks'] if w.get('start','')<=datetime.now().date().isoformat()<=w.get('end','')),1)}

def claim(P,pid,ident,load_people,save_people,person,slot_id,entries):
    with ident.store_lock:
        project,cfg=config(P,pid);people=load_people()
        current=next((p for p in people if p['id']==person['id'] and p.get('active',True)),None)
        if not current or current.get('role')=='lead':raise ID.AuthError('组长固定负责统筹与全程辅助；组员请用自己的账号落座')
        chosen=claims(P,pid);holders=seat_people(cfg,chosen,people,ident)
        mine=next((sid for sid,h in holders.items() if h['occupied'] and h['person'] and h['person']['id']==current['id']),None)
        if mine:
            if mine==slot_id:return {'slot':mine,'unchanged':True}
            raise ID.AuthError('你已经落座；更换分工请联系统筹处理交接')
        slot=next((s for s in cfg['seats'] if s['id']==slot_id and s['id']!='coordinator'),None)
        if not slot:raise ValueError('请选择一个组员岗位')
        if holders[slot_id]['occupied']:raise ID.AuthError('此岗位已有人落座，请选择其他空岗位')
        placeholder=holders[slot_id]['person']
        if not placeholder:raise ValueError('岗位身份缺失，请由统筹检查名册')
        if current.get('keys'):
            raise ID.AuthError('当前账号已有任务身份，不能自动合并；请由统筹核对关联')
        if any(e.get('actor')==slot['member_ref'] and e.get('actor_kind')=='human' for e in entries):
            raise ID.AuthError('这个岗位已有历史真人记录，请由统筹确认交接')
        updated=[]
        for p in people:
            if p['id']==placeholder['id']:
                updated.append({**p,'name':'已移交席位-'+p['id'][-6:],'active':False,'keys':[]})
            elif p['id']==current['id']:
                updated.append({**p,'username':p.get('username') or p['name'],'name':slot['alias'],
                                'keys':list(dict.fromkeys(placeholder.get('keys',[])+[fingerprint(slot['member_ref'])])),
                                'note':slot['title']+'；本人自主落座'})
            else:updated.append(p)
        chosen[slot_id]={'person_id':current['id'],'chosen_at':datetime.now(timezone.utc).isoformat()}
        try:
            save_people(updated)
            P._write(P.pdir(pid)/'seat_claims.json',chosen)
        except Exception:
            save_people(people)
            raise
        return {'slot':slot_id,'task_count':sum(t.get('workspace_slot')==slot_id for t in project['tasks'])}

def own_slot(P,pid,ident,people,person):
    _,cfg=config(P,pid);holders=seat_people(cfg,claims(P,pid),people,ident)
    slot=next((s for s in cfg['seats'] if holders[s['id']]['occupied'] and holders[s['id']]['person'] and holders[s['id']]['person']['id']==person['id']),None)
    if not slot:raise ID.AuthError('请先选择自己的岗位，再记录学习或申请协助')
    return slot

def checkin(P,pid,ident,people,person,data,ledger):
    slot=own_slot(P,pid,ident,people,person)
    if set(data)-{'learning_week','state','progress','note','files'}:raise ValueError('学习打卡参数无效')
    week=int(data.get('learning_week') or 0)
    if not any(w['week']==week for w in curriculum(P,pid).get('roles',{}).get(slot['id'],[])):raise ValueError('没有这周的学习卡')
    state=data.get('state')
    if state not in ('doing','done','blocked'):raise ValueError('请选择进行中、已完成或受阻')
    note=HR.clean(data.get('note'),1200)
    if len(note)<10:raise ValueError('请写明本人学了什么、实际做了什么或卡在哪里（至少10个字）')
    progress=max(0,min(100,int(data.get('progress') or 0)))
    known={f['sha256']:f for f in P.files(pid)};files=[]
    for sha in list(data.get('files') or [])[:10]:
        f=known.get(str(sha))
        if not f or f.get('source_path') or f.get('added_by') not in (person['name'],slot['member_ref']):
            raise ValueError('学习成果须为本人上传的项目文件，不能直接勾选来源资料或他人成果')
        files.append({'sha256':f['sha256'],'name':f['name']})
    if state=='done' and not files:raise ValueError('标记已完成前，请上传本人操作输出、笔记或原始报错记录')
    return ledger.append('learning.checkin',{'slot':slot['id'],'learning_week':week,'state':state,'progress':100 if state=='done' else min(progress,95),
        'note':note,'files':files,'person_id':person['id'],'scope':'LEARNING_PROCESS_NOT_SCIENTIFIC_APPROVAL'},actor=slot['member_ref'],actor_kind='human')

def support(P,pid,ident,people,person,data,ledger):
    slot=own_slot(P,pid,ident,people,person);_,cfg=config(P,pid)
    note=HR.clean(data.get('note'),1200)
    if len(note)<10:raise ValueError('请写清楚问题、已尝试的办法或实际协助内容（至少10个字）')
    target=data.get('slot') if slot['id']=='coordinator' else slot['id']
    if target not in {s['id'] for s in cfg['seats']}:raise ValueError('请选择需要协助的岗位')
    return ledger.append('team.support',{'slot':target,'kind':'assistance' if slot['id']=='coordinator' else 'request','note':note,
        'person_id':person['id'],'scope':'PROCESS_ONLY'},actor=slot['member_ref'],actor_kind='human')

def asset_path(cfg,asset):
    root=Path(cfg['source_roots'][asset['root']]).resolve()
    path=(root/asset['path']).resolve()
    if not path.is_relative_to(root):raise ValueError('资料路径不在登记来源中')
    return path

def audit(P,pid):
    project,cfg=config(P,pid);checks=[]
    for asset in cfg.get('assets',[]):
        path=asset_path(cfg,asset)
        exists=path.is_file()
        sha=hashlib.sha256(path.read_bytes()).hexdigest() if exists else ''
        checks.append({'id':asset['id'],'title':asset['title'],'exists':exists,'hash_matches':bool(exists and sha==asset.get('sha256')),
                       'classification':asset.get('classification'),'sha256':sha})
    unknown=[];slots={s['id']:s for s in cfg['seats']};tasks=project.get('tasks',[])
    for t in tasks:
        if t.get('workspace_slot') not in slots:unknown.append(t['task_id']+' 未关联岗位')
        if t.get('owner')==t.get('cross_reviewer'):unknown.append(t['task_id']+' 执行与复核为同一人')
        if t.get('task_kind')=='research' and t.get('workspace_slot')=='coordinator':unknown.append(t['task_id']+' 科研执行仍分给统筹')
        if any(a not in {x['id'] for x in cfg.get('assets',[])} for a in t.get('evidence_refs',[])):unknown.append(t['task_id']+' 有未登记输入链接')
    report=cfg.get('engineering_audit',{})
    return {**report,'assets':checks,'missing_assets':sum(not a['exists'] for a in checks),'changed_assets':sum(a['exists'] and not a['hash_matches'] for a in checks),
            'assignment_errors':unknown,'research_load':{s['id']:sum(t.get('task_kind')=='research' and t.get('workspace_slot')==s['id'] for t in tasks) for s in cfg['seats']},
            'scope':'LOCAL_SOURCE_CONSISTENCY_NOT_SCIENTIFIC_APPROVAL'}

def resource(P,pid,asset_id):
    _,cfg=config(P,pid)
    asset=next((a for a in cfg.get('assets',[]) if a['id']==asset_id),None)
    if not asset:raise ValueError('资料未登记')
    path=asset_path(cfg,asset)
    if not path.is_file():raise ValueError('原始资料尚未找到，请查看工程核对中的缺口')
    raw=path.read_bytes()
    if len(raw)>2_000_000:raise ValueError('资料过大，请在项目研究资料中查看原件')
    text=raw.decode('utf-8-sig',errors='replace')
    # Viewer is escaped text, never executes native-page scripts or alters its SKEY/history.
    for s in cfg['seats']:text=text.replace(s['member_ref'],s['title'])
    for raw,title in cfg.get('source_name_map',{}).items():text=text.replace(raw,title)
    if path.suffix.lower()=='.html':
        match=re.search(r'const WEEKS\s*=',text)
        if match:
            try:text=json.dumps(json.JSONDecoder().raw_decode(text[match.end():].lstrip())[0],ensure_ascii=False,indent=2)
            except ValueError:text='原生页内容暂无法解析，请查看原件；历史记录未修改。'
    esc=html.escape
    sha=hashlib.sha256(raw).hexdigest()
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(asset['title'])}</title><style>body{{background:#f3f6f5;color:#183a3b;font:16px/1.7 system-ui;max-width:1100px;margin:30px auto;padding:20px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:24px;border-radius:14px}}a{{color:#176955}}small{{color:#607572}}</style><h1>{esc(asset['title'])}</h1><p><a href="/#/team">返回本机工作台</a> · {esc(asset.get('classification','来源资料'))}</p><small>原件 SHA-256：{sha}；页面为匿名只读展示。原件和原生历史记录保留。</small><pre>{esc(text)}</pre></html>'''.encode('utf-8')
