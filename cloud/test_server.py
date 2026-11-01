"""Isolated Linux cloud acceptance: auth, encryption, check-in, restart reconciliation."""
import base64
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'shell/server'))
from test_helix05 import MockNotion

tmp = Path(tempfile.mkdtemp(prefix='helix-cloud-acceptance-'))
shutil.copytree(ROOT / 'shell', tmp / 'shell', ignore=shutil.ignore_patterns('data', '__pycache__'))
shutil.copytree(ROOT / 'cloud',tmp / 'cloud',ignore=shutil.ignore_patterns('state','__pycache__','*.tar.gz'))
(tmp / 'shell/data').mkdir()
# Compatibility acceptance explicitly uses named displays; anonymous joining is
# validated separately with the default configuration enabled.
cfgpath=tmp/'shell/helix.config.json'
cfg=json.loads(cfgpath.read_text()) if cfgpath.exists() else {}
cfg['anonymous_members']=False
cfgpath.write_text(json.dumps(cfg))
keyfile = tmp / 'key'
keyfile.write_bytes(Fernet.generate_key())
accessfile = tmp / 'access.json'
code = 'isolated-test-setup-code'
accessfile.write_text(json.dumps({'bootstrap_hash': hashlib.sha256(code.encode()).hexdigest()}))
sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
base = f'http://127.0.0.1:{port}'
mock = MockNotion()
auth = 'Basic ' + base64.b64encode(b'testteam:cloud-password-1234').decode()
env = {**os.environ, 'HELIX_PUBLIC_ORIGIN':base, 'HELIX_PORT':str(port), 'HELIX_BIND':'127.0.0.1',
       'HELIX_SECRET_KEY_FILE':str(keyfile), 'HELIX_ACCESS_FILE':str(accessfile), 'HELIX_CLOUD_BASE':mock.url, 'HELIX_TEST':'1'}
log = (tmp / 'server.log').open('wb')
proc = None
checks = []
LAST_HEADERS = {}


def check(name, condition):
    assert condition, name
    checks.append(name); print('PASS', name, flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(path, body=None, authenticated=True, member='', method=None, extra=None, form=False):
    global LAST_HEADERS
    headers = {'X-Helix':'1'}
    if authenticated: headers['Authorization'] = auth
    if member: headers['X-Helix-Token'] = member
    if extra: headers.update(extra)
    data = None
    if body is not None:
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({})).open(req, timeout=20)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        LAST_HEADERS = dict(response.headers)
        content = response.read()
        try: content = json.loads(content)
        except (ValueError, UnicodeError): pass
        return response.code, content


def start():
    global proc
    proc = subprocess.Popen([sys.executable, str(tmp / 'cloud/server.py')], env=env, stdout=log, stderr=log)
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError((tmp / 'server.log').read_text())
        try:
            request('/api/health'); return
        except OSError: time.sleep(.1)
    raise RuntimeError('startup timeout')


def stop():
    if proc and proc.poll() is None:
        proc.terminate(); proc.wait(timeout=15)


try:
    start()
    status, body = request('/api/snapshot', authenticated=False)
    check('initial setup never exposes project API data', status == 200 and isinstance(body, bytes) and b'<form' in body)
    check('wrong setup code rejected', request('/setup', {'code':'wrong'}, form=True)[0] == 403)
    check('owner sets their own entry account', request('/setup', {'code':code,'username':'testteam','password':'cloud-password-1234','confirm':'cloud-password-1234'}, form=True)[0] == 303)
    access = json.loads(accessfile.read_text())
    check('bootstrap removed and password stored as salted hash', 'bootstrap_hash' not in access and 'cloud-password' not in accessfile.read_text())
    check('stale setup form redirects to login without changing account', request('/setup', {'code':code}, form=True)[0] == 303)
    check('unauthenticated API denied', request('/api/snapshot', authenticated=False)[0] == 401)
    check('unauthenticated static HEAD denied', request('/', authenticated=False, method='HEAD')[0] == 401)
    check('bad password denied', request('/api/health', extra={'Authorization':'Basic ' + base64.b64encode(b'testteam:wrong').decode()})[0] == 401)
    check('hostile Origin denied', request('/api/health', extra={'Origin':'https://evil.invalid'})[0] == 403)
    check('hostile Host denied', request('/api/health', extra={'Host':'evil.invalid'})[0] == 403)
    status, body = request('/', authenticated=False)
    check('browser receives a normal login page', status == 200 and b'action="/login"' in body)
    check('browser forms retain same-origin referrer policy', LAST_HEADERS.get('Referrer-Policy') == 'strict-origin-when-cross-origin')
    check('normal login defaults to members without roster selection',b'value="member"' in body and b'<select' not in body)
    leader_status,leader_body=request('/leader-login',authenticated=False)
    check('leader has a separate password page',leader_status==200 and b'value="lead"' in leader_body and b'value="testteam"' in leader_body)
    check('owner cannot use explicit member portal',request('/login',{'username':'testteam','password':'cloud-password-1234','portal':'member'},authenticated=False,form=True)[0]==403)
    check('wrong form password rejected', request('/login', {'username':'testteam','password':'wrong'}, authenticated=False, form=True)[0] == 403)
    check('form login succeeds', request('/login', {'username':'testteam','password':'cloud-password-1234','portal':'lead'}, authenticated=False, form=True)[0] == 303)
    cookie = LAST_HEADERS['Set-Cookie'].split(';',1)[0]
    check('session cookie prevents script access and cross-site use', 'HttpOnly' in LAST_HEADERS['Set-Cookie'] and 'SameSite=Strict' in LAST_HEADERS['Set-Cookie'])
    check('session cookie authorizes API access', request('/api/health', authenticated=False, extra={'Cookie':cookie})[0] == 200)
    check('hostile Origin rejected with a valid cookie', request('/api/health', authenticated=False, extra={'Cookie':cookie,'Origin':'https://evil.invalid'})[0] == 403)
    check('logout clears session', request('/logout', {}, authenticated=False, form=True, extra={'Cookie':cookie})[0] == 303 and request('/api/health', authenticated=False, extra={'Cookie':cookie})[0] == 401)
    status, body = request('/api/health')
    check('authenticated cloud loads bundled kernel', status == 200 and body['ok'] and not body['error'])
    status, result = request('/api/projects', {'name':'云端验收副本','start':date.today().isoformat(),'n_weeks':1,
        'members':[{'name':'甲','role':'lead'},{'name':'乙','role':'both'}],
        'tasks':[{'task_id':'T1','week':1,'deliverable':'验收任务','acceptance':'记录进度','owner':'乙','cross_reviewer':'甲'}]})
    check('fresh server can create its own project', status == 200 and result['ok'])
    people = request('/api/people')[1]['roster']
    ids = {p['name']:p['id'] for p in people}
    for name in ('甲','乙'):
        result = request('/api/auth/set-pin', {'person_id':ids[name], 'new_pin':'member-password-123', 'admin_id':ids['testteam'], 'admin_pin':'cloud-password-1234'})[1]
        assert result['ok'], result
    tokens = {name:request('/api/auth/login', {'person_id':ids[name], 'pin':'member-password-123'})[1]['token'] for name in ('甲','乙')}
    check('each member has a distinct authenticated Helix session', tokens['甲'] != tokens['乙'])
    check('shared entry password alone cannot submit check-in', not request('/api/checkin', {'task_id':'T1','state':'doing','progress':30})[1]['ok'])
    result = request('/api/cloud/config', {'database':mock.db,'token':'ntn_' + 't'*40}, member=tokens['甲'])[1]
    check('leader configures Notion through protected cloud API', result['ok'])
    stored = json.loads((tmp / 'shell/data/cloud.json').read_text())
    check('Linux Notion token uses authenticated encryption', stored['token']['kind'] == 'fernet' and 'ntn_' not in json.dumps(stored))
    check('separate key decrypts token correctly', Fernet(keyfile.read_bytes()).decrypt(stored['token']['blob'].encode()).decode() == 'ntn_' + 't'*40)
    check('member check-in accepted', request('/api/checkin', {'task_id':'T1','state':'doing','progress':45,'note':'cloud acceptance'}, member=tokens['乙'])[1]['ok'])
    for _ in range(100):
        if any(r.get('进度') == .45 for r in mock.rows.values()): break
        time.sleep(.1)
    check('check-in automatically reaches remote Notion API', any(r.get('进度') == .45 for r in mock.rows.values()))
    result = request('/api/cloud/sync', {}, member=tokens['乙'])[1]
    check('unchanged sync skips existing row', result['ok'] and result['pushed'] == 0 and result['skipped'] == 1 and len(mock.rows) == 1)
    mock.down = True
    check('check-in persists while Notion is down', request('/api/checkin', {'task_id':'T1','state':'doing','progress':70,'note':'after restart'}, member=tokens['乙'])[1]['ok'])
    stop(); mock.down = False; start()
    check('entry account survives restart', request('/api/health')[0] == 200)
    for _ in range(100):
        if any(r.get('进度') == .7 for r in mock.rows.values()): break
        time.sleep(.1)
    check('restart recovers committed progress and pushes without desktop', any(r.get('进度') == .7 for r in mock.rows.values()) and len(mock.rows) == 1)
    snap = request('/api/snapshot')[1]
    check('ledger remains valid after server restart', snap['ledger']['ok'])
    check('cloud identity scope is explicit',snap['auth']['account_scope']=='cloud')
    access_before=accessfile.read_bytes()
    cred_before=json.loads((tmp/'shell/data/credentials.json').read_text())['creds'][ids['testteam']]
    status,invited=request('/api/cloud/invite',{})
    check('leader can get a team join link',status==200 and invited['ok'] and '/register?invite=' in invited['url'])
    invite=urllib.parse.parse_qs(urllib.parse.urlsplit(invited['url']).query)['invite'][0]
    member_form={'invite':invite,'name':'云端组员A','password':'member-password-a-123','confirm':'member-password-a-123'}
    check('anonymous registration without valid invitation is rejected',request('/register',{**member_form,'invite':'wrong'},authenticated=False,form=True)[0]==403)
    check('registration cannot request leader role',request('/register',{**member_form,'role':'lead'},authenticated=False,form=True)[0]==400)
    check('new member registers without leader password',request('/register',member_form,authenticated=False,form=True)[0]==303)
    member_cookie=LAST_HEADERS['Set-Cookie'].split(';',1)[0]
    member_snap=request('/api/snapshot',authenticated=False,extra={'Cookie':member_cookie})[1]
    check('cloud signup is automatically logged in as that member',member_snap['auth']['me']['name']=='云端组员A' and member_snap['auth']['me']['role']=='both')
    check('new member cannot administer team',not member_snap['auth']['can_admin'] and not member_snap['people']['editable'])
    check('new member cannot change Notion settings',request('/api/cloud/config',{},authenticated=False,extra={'Cookie':member_cookie})[0]==401)
    check('new member cannot create invitation',request('/api/cloud/invite',{},authenticated=False,extra={'Cookie':member_cookie})[0]==403)
    fresh_lead_token=request('/api/auth/login',{'person_id':ids['甲'],'pin':'member-password-123'})[1]['token']
    forged=request('/api/snapshot',authenticated=False,member=fresh_lead_token,extra={'Cookie':member_cookie})[1]
    check('cookie identity cannot be replaced by stale or forged client token',forged['auth']['me']['name']=='云端组员A')
    check('duplicate account cannot claim an existing identity',request('/register',member_form,authenticated=False,form=True)[0]==400)
    second={**member_form,'name':'云端组员B','password':'member-password-b-123','confirm':'member-password-b-123'}
    check('second independent member can register',request('/register',second,authenticated=False,form=True)[0]==303)
    second_cookie=LAST_HEADERS['Set-Cookie'].split(';',1)[0]
    second_snap=request('/api/snapshot',authenticated=False,extra={'Cookie':second_cookie})[1]
    check('independent browser sessions keep independent identities',second_snap['auth']['me']['name']=='云端组员B' and request('/api/snapshot',authenticated=False,extra={'Cookie':member_cookie})[1]['auth']['me']['name']=='云端组员A')
    assigned=request('/api/projects/update',{'members':[{'name':'testteam','role':'lead'},{'name':'云端组员A','role':'both'},{'name':'云端组员B','role':'both'}],
        'tasks':[{'task_id':'TEAM1','week':1,'deliverable':'云端协作验收','acceptance':'实际记录进度','owner':'云端组员A','cross_reviewer':'云端组员B'}]})[1]
    check('leader can assign work to newly registered members',assigned['ok'])
    posted=request('/api/checkin',{'task_id':'TEAM1','state':'doing','progress':25,'note':'independent member check-in'},authenticated=False,extra={'Cookie':member_cookie})[1]
    check('newly registered owner can check in with their own session',posted['ok'])
    for _ in range(100):
        if any(r.get('编号')=='TEAM1' and r.get('进度')==.25 for r in mock.rows.values()):break
        time.sleep(.1)
    check('member check-in automatically reaches Notion without member Token',any(r.get('编号')=='TEAM1' and r.get('进度')==.25 for r in mock.rows.values()))
    crossed=request('/api/checkin',{'task_id':'TEAM1','state':'started','progress':5,'note':'cross reviewer started'},authenticated=False,extra={'Cookie':second_cookie})[1]
    check('newly registered cross reviewer can record their own progress',crossed['ok'])
    check('ordinary member cannot edit assignments',request('/api/projects/update',{'name':'unauthorized'},authenticated=False,extra={'Cookie':second_cookie})[0]==401)
    check('invitation is reused without regenerating a shared secret',request('/api/cloud/invite',{})[1]['url']==invited['url'])
    check('invitation secret is encrypted at rest',invite not in (tmp/'join.json').read_text())
    owner_after=json.loads((tmp/'shell/data/credentials.json').read_text())['creds'][ids['testteam']]
    check('owner credentials and gateway settings remain unchanged',access_before==accessfile.read_bytes() and cred_before==owner_after)
    stop();start()
    check('new member password works after restart',request('/login',{'username':'云端组员A','password':'member-password-a-123'},authenticated=False,form=True)[0]==303)
    new_cookie=LAST_HEADERS['Set-Cookie'].split(';',1)[0]
    check('new login remains member after restart',request('/api/snapshot',authenticated=False,extra={'Cookie':new_cookie})[1]['auth']['me']['name']=='云端组员A')
    check('old cookie is revoked by server restart',request('/api/health',authenticated=False,extra={'Cookie':member_cookie})[0]==401)
    rows=request('/api/snapshot')[1]['people']['roster']
    for p in rows:
        if p['name']=='云端组员A':p['active']=False
    assert request('/api/people',{'people':rows})[1]['ok']
    check('disabled member immediately loses their cloud session',request('/api/health',authenticated=False,extra={'Cookie':new_cookie})[0]==401)
    # Wrong and missing encryption keys never silently downgrade to base64.
    stop()
    keyfile.write_bytes(Fernet.generate_key())
    os.environ['HELIX_SECRET_KEY_FILE'] = str(keyfile)
    import secure_store
    try:
        secure_store.unprotect(stored['token']['blob'])
        raise AssertionError('wrong key accepted')
    except Exception as exc:
        check('wrong key cannot decrypt previous credential', type(exc).__name__ == 'InvalidToken')
    os.environ['HELIX_SECRET_KEY_FILE'] = str(tmp / 'missing.key')
    try:
        secure_store.protect(b'test')
        raise AssertionError('missing key accepted')
    except FileNotFoundError:
        check('missing encryption key fails closed', True)
    print(json.dumps({'passed':len(checks),'checks':checks}, ensure_ascii=False))
finally:
    stop(); log.close()
