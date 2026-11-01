"""Cloud membership uses the same identity store as Helix; invitations never grant admin."""
import hashlib
import hmac
import json
import secrets
import unicodedata
from pathlib import Path
from html import escape

STYLE = '<style>body{background:#111;color:#fcfcfc;font:17px system-ui;max-width:500px;margin:7vh auto;padding:24px}input,button,textarea{box-sizing:border-box;width:100%;padding:13px;margin:8px 0 18px;background:#222;color:#fff;border:1px solid #666;border-radius:8px}p{line-height:1.7}a{color:#7dd3fc}</style>'


def document(title, body):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title>'+STYLE+body+'</html>').encode()


def registration_form(code='', message='', join=''):
    return document('加入 Helix', '<h1>注册新账号</h1><p>通过组长的加入链接注册。使用自己的账号和密码，无需组长口令或 Notion Token。</p><p role="alert">'+escape(message)+'</p><form method="post" action="/register">'+
        ('<input type="hidden" name="join" value="'+escape(join,quote=True)+'"><p>设置后沿用原分工，团队只显示匿名代号。</p>' if join else '<label>团队邀请码<input name="invite" required value="'+escape(code,quote=True)+'" autocomplete="off"></label>')+
        '<label>账号名<input name="name" required minlength="2" maxlength="24" autocomplete="username"></label>'
        '<label>密码<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>'
        '<label>确认密码<input name="confirm" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>'
        '<button>注册并进入</button></form><a href="/">已有账号，返回登录</a>')


def invite_page():
    return document('邀请组员', '''<h1>邀请组员</h1><p>把加入链接发给组员，对方自行设置账号和密码。无需共享你的口令或 Notion Token。</p><button id="create">获取加入链接</button><textarea id="link" readonly rows="4" aria-label="加入链接"></textarea><button id="copy">复制加入链接</button><p role="status" id="status"></p><a href="/#/people">返回成员页</a><script>
const out=document.querySelector('#link'),status=document.querySelector('#status');
document.querySelector('#create').onclick=async()=>{try{const r=await fetch('/api/cloud/invite',{method:'POST',headers:{'X-Helix':'1'}}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'获取失败');out.value=j.url;status.textContent='加入链接已生成，可以发给组员。';}catch(e){status.textContent=e.message;}};
document.querySelector('#copy').onclick=async()=>{if(!out.value)return;try{await navigator.clipboard.writeText(out.value);status.textContent='已复制加入链接';}catch{out.select();status.textContent='请按 Ctrl+C 复制';}};
</script>''')


def normalize(name):
    return unicodedata.normalize('NFKC',str(name)).strip().casefold()


def authenticate(serve, name, password):
    person=next((p for p in serve.load_people() if normalize(p.get('username') or p.get('name',''))==normalize(name) and p.get('active',True)),None)
    if not person:
        raise serve.ID.AuthError('账号或密码不正确')
    return serve.IDENT.login(serve.load_people(),person['id'],password)


def bootstrap_owner(serve, name, password):
    """Only called after a valid first-use setup code; preserves any existing credentials."""
    people=serve.load_people()
    person=next((p for p in people if normalize(p.get('username') or p.get('name',''))==normalize(name)),None)
    if person and (person.get('role')!='lead' or not person.get('active',True)):
        raise ValueError('已有身份不能被首次设置覆盖')
    if not person:
        if serve.IDENT.any_pin():
            raise ValueError('已有口令不能被首次设置替换')
        serve.register_members([{'name':name,'role':'lead'}])
        person=next(p for p in serve.load_people() if (p.get('username') or p['name'])==name)
    if not serve.IDENT.has_pin(person['id']):
        serve.IDENT.set_pin(serve.load_people(),person['id'],password)
    else:
        authenticate(serve,name,password)
    if not serve.P.list_local():
        from datetime import date
        project,_=serve.P.create({'name':'Helix 团队项目','start':date.today().isoformat(),'n_weeks':4,'members':[{'name':name,'role':'lead'}],'tasks':[]})
        serve.P.set_active(project['id'])
        serve.HelixLedger(project['id'],root=serve.P.ledger_root(project['id'])).ensure_genesis()


def issue_invite(path, origin, secure_store):
    path=Path(path)
    if path.exists():
        saved=json.loads(path.read_text())
        code=secure_store.unprotect(saved['blob']).decode()
    else:
        code=secrets.token_urlsafe(32)
        _,blob=secure_store.protect(code.encode())
        saved={'hash':hashlib.sha256(code.encode()).hexdigest(),'blob':blob}
        with path.open('x',encoding='utf-8') as f: json.dump(saved,f)
        if __import__('os').name!='nt':path.chmod(0o600)
    return origin+'/register?invite='+code


def valid_invite(path, code):
    try:
        saved=json.loads(Path(path).read_text())
        return bool(code) and hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(),saved['hash'])
    except (OSError,ValueError,KeyError):return False
