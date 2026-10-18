"""Authenticated human execution records, separate from AI drafts and scientific approval."""
from __future__ import annotations
import json,re,secrets
from pathlib import Path
import io

EVENTS={'human.execution.started','human.execution.tool','human.execution.finished'}
_SECRET=re.compile(r'(?i)(?:ntn_|secret_|sk-)[A-Za-z0-9_-]{16,}|(?:Bearer\s+)[A-Za-z0-9._-]{16,}')

def register_events(mod):
    mod.EVENT_TYPES=frozenset(set(mod.EVENT_TYPES)|EVENTS)
    mod.HUMAN_ONLY=frozenset(set(mod.HUMAN_ONLY)|EVENTS)

def clean(value,n=3000):
    text=_SECRET.sub('[REDACTED]',str(value or ''))
    text=re.sub(r'(?i)\b(password|token|api_key|secret)\s*[:=]\s*[^\s,;]+',r'\1=[REDACTED]',text)
    return text.strip()[:n]

def safe(value):
    if isinstance(value,dict):
        return {k:('[REDACTED]' if re.search(r'(?i)token|password|secret|authorization|api.?key',k) else safe(v)) for k,v in value.items()}
    if isinstance(value,list):return [safe(v) for v in value]
    return clean(value,4_000_000) if isinstance(value,str) else value

def fold(entries):
    runs={}
    for e in entries:
        p=e.get('payload') or {};rid=p.get('run_id');kind=e.get('type')
        if kind not in EVENTS or not rid:continue
        if kind=='human.execution.started':
            runs[rid]={**p,'started_by':e['actor'],'started_at':e['ts'],'status':'running','tools':[],'outputs':[]}
        elif rid in runs and kind=='human.execution.tool':
            runs[rid]['tools'].append({**p,'ts':e['ts']})
            runs[rid]['outputs'] += p.get('outputs') or []
        elif rid in runs and kind=='human.execution.finished':
            runs[rid].update(p,finished_at=e['ts'])
    return sorted(runs.values(),key=lambda r:r['started_at'],reverse=True)

def participant(ctx,person,tid,matches,fingerprint):
    task=ctx.task(tid)
    if not task:raise ValueError('找不到这个任务')
    for field,role in [('owner','owner'),('cross_reviewer','cross')]:
        raw=task.get(field)
        if raw and matches(person,raw,fingerprint):return task,raw,role
    raise PermissionError('只有任务主核或交叉核本人能登记实操，不能代替他人操作。')

def current(ctx,person,rid,matches,fingerprint):
    run=next((r for r in fold(ctx.entries()) if r['run_id']==rid),None)
    if not run:raise ValueError('找不到当前项目的真人执行记录')
    if run['person_id']!=person['id']:raise PermissionError('只能继续本人的实操记录')
    participant(ctx,person,run['task_id'],matches,fingerprint)
    if run['status']!='running':raise ValueError('这次实操已结束；后续操作请新建一条记录。')
    return run

def references(ctx,values):
    known={f['sha256']:f for f in ctx.files()};out=[]
    for value in list(values or [])[:20]:
        f=known.get(str(value))
        if not f:raise ValueError('请选择当前项目已登记的文件')
        out.append({'sha256':f['sha256'],'name':f['name']})
    return out

def start(ctx,person,data,matches,fingerprint):
    if set(data)-{'task_id','method','inputs','parameters'}:raise ValueError('实操身份由当前登录账号确定，不能指定代办人。')
    task,actor,role=participant(ctx,person,str(data.get('task_id') or ''),matches,fingerprint)
    method=clean(data.get('method'),500)
    if len(method)<4:raise ValueError('请写明准备亲自做什么（至少4个字）')
    payload={'run_id':'human_'+secrets.token_hex(8),'task_id':task['task_id'],'person_id':person['id'],'as':role,
             'method':method,'inputs':references(ctx,data.get('inputs')),'parameters':safe(data.get('parameters') or {}),
             'record_kind':'human_reported'}
    return ctx.ledger().append('human.execution.started',payload,actor=actor,actor_kind='human')

def finish(ctx,person,data,matches,fingerprint):
    if set(data)-{'run_id','status','steps','observation','outputs','parameters'}:raise ValueError('不接受代办人或额外审核结论字段')
    run=current(ctx,person,str(data.get('run_id') or ''),matches,fingerprint)
    status=str(data.get('status') or '')
    if status not in ('completed','blocked'):raise ValueError('请选择实操完成或受阻')
    steps=clean(data.get('steps'));observation=clean(data.get('observation'),1500)
    outputs=references(ctx,data.get('outputs'))
    observed=[t for t in run['tools'] if t.get('ok')]
    if len(steps)<8 or len(observation)<4:raise ValueError('请记录本人实际步骤和观察，不能只签字确认。')
    if status=='completed' and not outputs and not observed:raise ValueError('完成实操需附成果/操作日志文件，或在本记录中实际运行工具。')
    return ctx.ledger().append('human.execution.finished',{'run_id':run['run_id'],'status':status,'steps':steps,
        'observation':observation,'outputs':outputs or run['outputs'],'parameters':safe(data.get('parameters') or run['parameters']),
        'record_kind':'server_observed_tool_and_human_report' if observed else 'human_reported'},actor=run['started_by'],actor_kind='human')

def tool(ctx,person,data,matches,fingerprint,projects,science):
    run=current(ctx,person,str(data.get('human_run_id') or ''),matches,fingerprint)
    request={k:v for k,v in data.items() if k!='human_run_id'}
    result=None;error=None
    try:result=science.run(request)
    except Exception as exc:error=clean(str(exc),1000)
    outputs=[]
    for suffix,obj in [('input',safe(request)),('output',safe(result if result is not None else {'ok':False,'error':error}))]:
        raw=json.dumps(obj,ensure_ascii=False,indent=2).encode()
        f=projects.add_file(ctx.pid,run['run_id']+'-'+suffix+'.json',io.BytesIO(raw),len(raw),by=person['name'],note='真人发起的工具调用 · '+suffix)
        outputs.append({'sha256':f['sha256'],'name':f['name']})
    if result and request.get('tool')=='vina':
        import vina_adapter
        from urllib.parse import urlparse,parse_qs
        for name,url in (result.get('artifacts') or {}).items():
            query=parse_qs(urlparse(url).query)
            artifact=vina_adapter.result_file(query.get('run_id',[''])[0],name)
            raw=artifact.read_bytes();f=projects.add_file(ctx.pid,run['run_id']+'-'+name,io.BytesIO(raw),len(raw),by=person['name'],note='真人Vina实操输出')
            outputs.append({'sha256':f['sha256'],'name':f['name']})
    ctx.ledger().append('human.execution.tool',{'run_id':run['run_id'],'tool':str(request.get('tool') or ''),'ok':error is None,
        'error':error,'outputs':outputs,'record_kind':'server_observed','tool_versions':science.registry()},actor=run['started_by'],actor_kind='human')
    if error:raise ValueError(error+'（本次原始错误与参数已登记在实操记录中）')
    return {**result,'human_execution':{'run_id':run['run_id'],'outputs':outputs,'record_kind':'server_observed'}}
