"""Cloud form registration binds the existing anonymous slot, not a new member."""
import base64,json,os,shutil,socket,subprocess,sys,tempfile,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from cryptography.fernet import Fernet
ROOT=Path(__file__).resolve().parents[1]
tmp=Path(tempfile.mkdtemp(prefix='helix-cloud-anonymous-'))
shutil.copytree(ROOT/'shell',tmp/'shell',ignore=shutil.ignore_patterns('data','docs','__pycache__'))
shutil.copytree(ROOT/'cloud',tmp/'cloud',ignore=shutil.ignore_patterns('state','__pycache__','*.tar.gz'))
(tmp/'shell/data').mkdir()
(tmp/'shell/helix.config.json').write_text(json.dumps({'project_id':'ANON_CLOUD','workbench_app':'','ledger_root':''}))
(tmp/'key').write_bytes(Fernet.generate_key())
import hashlib
(tmp/'access.json').write_text(json.dumps({'bootstrap_hash':hashlib.sha256(b'fixture-setup-code').hexdigest()}))
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
base=f'http://127.0.0.1:{port}'
env={**os.environ,'HELIX_PUBLIC_ORIGIN':base,'HELIX_PORT':str(port),'HELIX_BIND':'127.0.0.1','HELIX_SECRET_KEY_FILE':str(tmp/'key'),'HELIX_ACCESS_FILE':str(tmp/'access.json'),'HELIX_TEST':'1'}
auth='Basic '+base64.b64encode(b'leader_login:leader-password-123').decode()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
op=urllib.request.build_opener(NoRedirect,urllib.request.ProxyHandler({}))
def request(path,body=None,owner=True,cookie='',form=False):
    headers={'X-Helix':'1'}
    if owner:headers['Authorization']=auth
    if cookie:headers['Cookie']=cookie
    data=None
    if body is not None:
        data=urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        headers['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
    try:r=op.open(urllib.request.Request(base+path,data=data,headers=headers),timeout=25)
    except urllib.error.HTTPError as e:r=e
    with r:
        raw=r.read();head=dict(r.headers);status=r.status
        try:content=json.loads(raw)
        except ValueError:content=raw.decode()
        return status,content,head
log=(tmp/'server.log').open('wb');proc=None;checks=[]
def start():
    global proc
    proc=subprocess.Popen([sys.executable,str(tmp/'cloud/server.py')],env=env,stdout=log,stderr=log)
    for i in range(120):
        if proc.poll() is not None:raise RuntimeError('Isolated cloud server failed to start')
        try:request('/login',owner=False);return
        except OSError:time.sleep(.1)
    raise RuntimeError('Isolated cloud startup timed out')
def stop():
    if proc and proc.poll() is None:proc.terminate();proc.wait(timeout=10)
def check(label,condition):
    assert condition,label
    checks.append(label);print('PASS '+label,flush=True)
try:
    start()
    check('Owner sets their own private login',request('/setup',{'code':'fixture-setup-code','username':'leader_login','password':'leader-password-123','confirm':'leader-password-123'},form=True)[0]==303)
    names=['leader_login','raw-one','raw-two','raw-three','raw-four']
    members=[{'name':n,'role':'lead' if i==0 else 'both'} for i,n in enumerate(names)]
    tasks=[{'task_id':'T'+str(i),'week':1,'owner':n,'cross_reviewer':names[(i+1)%5],'deliverable':'Practice and preserve evidence','acceptance':'Actual records'} for i,n in enumerate(names)]
    status,created,_=request('/api/projects',{'name':'Anonymous cloud fixture','members':members,'n_weeks':1,'tasks':tasks})
    check('Five participant project created',status==200 and created.get('ok'))
    pid=created['project']['id'];pfile=tmp/'shell/data/projects'/pid/'project.json';before=pfile.read_bytes()
    status,snap,_=request('/api/snapshot')
    check('Default anonymous names', {p['name'] for p in snap['people']['roster']}=={'组长','组员A','组员B','组员C','组员D'})
    check('Raw names and private username hidden from snapshot',all(n not in json.dumps(snap,ensure_ascii=False) for n in names))
    target=next(p for p in snap['people']['roster'] if p['name']=='组员A')
    status,invite,_=request('/api/auth/invite',{'person_id':target['id']})
    check('Owner issues bound setup link',status==200 and invite.get('ok'))
    code=urllib.parse.parse_qs(urllib.parse.urlsplit(invite['path']).query)['join'][0]
    status,html,_=request(invite['path'],owner=False)
    check('Member can open setup before login',status==200 and 'name="join"' in html and '沿用原分工' in html)
    status,_,head=request('/register',{'join':code,'name':'private-member','password':'personal-password-123','confirm':'personal-password-123'},owner=False,form=True)
    check('Member sets their own credentials',status==303 and 'Set-Cookie' in head)
    cookie=head['Set-Cookie'].split(';')[0]
    status,snap,_=request('/api/snapshot',owner=False,cookie=cookie)
    check('Same person and anonymous name retained',status==200 and snap['auth']['me']['id']==target['id'] and snap['auth']['me']['name']=='组员A')
    check('Existing assignments and count unchanged',len(snap['plan']['members'])==5 and pfile.read_bytes()==before and any(t['my_role']=='owner' for t in snap['plan']['tasks']))
    check('Private member login hidden from team snapshot','private-member' not in json.dumps(snap))
    check('Member cannot invite others',request('/api/auth/invite',{'person_id':snap['people']['roster'][-1]['id']},owner=False,cookie=cookie)[0]!=200)
    check('Setting link cannot be reused',request('/register',{'join':code,'name':'replacement','password':'replacement-password','confirm':'replacement-password'},owner=False,form=True)[0]==400)
    stop();start()
    status,_,_=request('/login',{'username':'private-member','password':'personal-password-123','portal':'member'},owner=False,form=True)
    check('Self chosen credentials survive restart',status==303)
    print('Passed '+str(len(checks))+' / '+str(len(checks)),flush=True)
finally:
    stop();log.close();shutil.rmtree(tmp)
