"""Authenticated cloud entry point. Do not expose the desktop serve.py directly."""
import base64
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'shell/server'))
os.environ['HELIX_RESEARCH_ONLY_BUNDLED'] = '1'
os.environ['HELIX_CLOUD_MODE'] = '1'
os.environ.setdefault('HELIX_SECRET_KEY_FILE', '/secrets/encryption.key')
ACCESS = Path(os.environ.get('HELIX_ACCESS_FILE', '/secrets/access.json'))
ORIGIN = os.environ.get('HELIX_PUBLIC_ORIGIN', '').rstrip('/')
if not ORIGIN or urlsplit(ORIGIN).scheme not in ('http', 'https'):
    raise RuntimeError('HELIX_PUBLIC_ORIGIN must be the exact HTTPS origin')
if urlsplit(ORIGIN).scheme != 'https' and urlsplit(ORIGIN).hostname not in ('localhost', '127.0.0.1'):
    raise RuntimeError('Public cloud entry requires HTTPS')
import secure_store
secure_store.cipher()  # Missing/wrong key fails before opening the listening socket.
import serve
import accounts
import login_ui
JOIN = ACCESS.parent / 'join.json'

LOCK = threading.RLock()
FAILURES = {}
SESSIONS = {}
COOKIE = 'helix_cloud_session'
ITERATIONS = 600_000


def password_record(password):
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return {'salt': salt.hex(), 'hash': digest.hex(), 'iterations': ITERATIONS}


def matches(password, record):
    try:
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), record['iterations'])
        return hmac.compare_digest(digest.hex(), record['hash'])
    except (KeyError, ValueError, TypeError):
        return False


def load_access():
    # Fail closed on missing/corrupt access settings; never fall back to open access.
    return json.loads(ACCESS.read_text(encoding='utf-8'))


def save_access(obj):
    tmp = ACCESS.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(obj, f)
    if os.name != 'nt':
        tmp.chmod(0o600)
    tmp.replace(ACCESS)


FORM = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Helix 首次设置</title>
<style>body{background:#111;color:#fcfcfc;font:17px system-ui;max-width:470px;margin:8vh auto;padding:24px}input,button{box-sizing:border-box;width:100%;padding:13px;margin:8px 0 18px;background:#222;color:#fff;border:1px solid #666;border-radius:8px}p{line-height:1.7}</style>
<h1>Helix 首次设置</h1><p>填写部署者给你的初始化码，并设置组长账号。组员后续通过加入链接自行注册。</p>
<form method="post" action="/setup"><label>初始化码<input name="code" type="password" required autocomplete="off"></label><label>入口账号（英文、数字、下划线）<input name="username" required autocomplete="username" pattern="[A-Za-z0-9_]{3,32}"></label><label>入口密码（至少 12 位）<input name="password" type="password" required minlength="12" autocomplete="new-password"></label><label>再次填写密码<input name="confirm" type="password" required minlength="12" autocomplete="new-password"></label><button>保存并进入</button></form></html>'''.encode('utf-8')

LOGIN_FORM = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 Helix</title>
<style>body{background:#111;color:#fcfcfc;font:17px system-ui;max-width:470px;margin:8vh auto;padding:24px}input,button{box-sizing:border-box;width:100%;padding:13px;margin:8px 0 18px;background:#222;color:#fff;border:1px solid #666;border-radius:8px}p{line-height:1.7}</style>
<h1>登录 Helix</h1><p>使用自己的账号登录，直接进入团队项目并以本人身份打卡。</p><p role="status">__MESSAGE__</p>
<form method="post" action="/login"><label>账号<input name="username" required autocomplete="username"></label><label>密码<input name="password" type="password" required autocomplete="current-password"></label><button>登录</button></form><p><a style="color:#7dd3fc" href="/register">注册新账号</a> · 新成员使用组长分享的加入链接</p></html>'''


class H(serve.H):
    def end_headers(self):
        self.send_header('X-Frame-Options', 'DENY')
        # no-referrer can produce Origin: null for browser form navigation POSTs.
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def answer(self, status, body=b'', content_type='text/plain; charset=utf-8', challenge=False):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if challenge:
            self.send_header('WWW-Authenticate', 'Basic realm="Helix team", charset="UTF-8"')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def peer(self):
        # Never trust a client-supplied X-Forwarded-For.
        return self.client_address[0]

    def limited(self):
        with LOCK:
            times = [t for t in FAILURES.get(self.peer(), []) if t > time.monotonic() - 60]
            FAILURES[self.peer()] = times
            return len(times) >= 8

    def failed(self):
        with LOCK:
            if len(FAILURES) > 2048:
                FAILURES.clear()
            FAILURES.setdefault(self.peer(), []).append(time.monotonic())

    def allowed_origin(self):
        # The HTTPS proxy must preserve Host. Reject hostile Origin and Host.
        return self.headers.get('Host', '') == urlsplit(ORIGIN).netloc and self.headers.get('Origin', ORIGIN) == ORIGIN

    def login_form(self, status=200, message='', leader=False):
        leads=[p.get('username') or p['name'] for p in serve.load_people() if p.get('role')=='lead' and p.get('active',True)]
        return self.answer(status, login_ui.page(cloud=True,leader=leader,leader_name=leads[0] if len(leads)==1 else '',message=message), 'text/html; charset=utf-8')

    def redirect(self, cookie=None):
        self.send_response(303)
        self.send_header('Location', '/')
        self.send_header('Content-Length', '0')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()

    def session_id(self):
        try:
            c = SimpleCookie(self.headers.get('Cookie', ''))
            value = c[COOKIE].value if COOKIE in c else ''
            return hashlib.sha256(value.encode()).hexdigest() if value else ''
        except CookieError:
            return ''

    def bind_member(self, result, force=False):
        self._cloud_person = result['person']
        if force or not self.headers.get('X-Helix-Token'):
            if 'X-Helix-Token' in self.headers:
                self.headers.replace_header('X-Helix-Token', result['token'])
            else:
                self.headers['X-Helix-Token'] = result['token']

    def start_session(self, result):
        token=secrets.token_urlsafe(32)
        with LOCK:
            now=time.monotonic()
            for sid in list(SESSIONS):
                if SESSIONS[sid]['expires'] <= now: SESSIONS.pop(sid)
            if len(SESSIONS)>=4096:raise RuntimeError('登录会话已满')
            SESSIONS[hashlib.sha256(token.encode()).hexdigest()]={'expires':now+12*3600,'token':result['token'],'person':result['person']}
        flags='; Secure' if ORIGIN.startswith('https:') else ''
        return self.redirect(f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200'+flags)

    def guard(self):
        if not self.allowed_origin():
            self.answer(403, '来源地址不匹配'.encode()); return False
        if self.limited():
            self.answer(429, '尝试次数过多，请一分钟后重试'.encode()); return False
        try:
            access = load_access()
        except Exception:
            self.answer(503, '访问配置不可用，请联系部署者'.encode()); return False
        if not access.get('username'):
            self.answer(200, FORM, 'text/html; charset=utf-8'); return False
        with LOCK:
            session=SESSIONS.get(self.session_id())
            resolved=serve.IDENT.resolve(session['token'],serve.load_people()) if session else None
            if session and session['expires']>time.monotonic() and resolved:
                session['person']={k:resolved[k] for k in ('id','name','role')}
                self.bind_member(session,force=True); return True
            if session:
                SESSIONS.pop(self.session_id(),None)
                serve.IDENT.logout(session['token'])
        try:
            scheme, encoded = self.headers.get('Authorization', '').split(' ', 1)
            user, password = base64.b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
            result=accounts.authenticate(serve,user,password) if scheme=='Basic' else None
            valid=bool(result)
        except (ValueError, UnicodeError, KeyError, serve.ID.AuthError):
            valid = False
        if not valid:
            # The browser's initial challenge does not count as a password failure.
            if self.headers.get('Authorization'):
                self.failed()
            if self.command == 'GET' and urlsplit(self.path).path in ('/', '/login', '/setup'):
                self.login_form(); return False
            self.answer(401, '请登录团队入口'.encode(), challenge=True); return False
        self.bind_member(result)
        return True

    def do_GET(self):
        if urlsplit(self.path).path in ('/login','/leader-login'):
            if not self.allowed_origin():return self.answer(403)
            return self.login_form(leader=urlsplit(self.path).path=='/leader-login')
        if urlsplit(self.path).path == '/register':
            if not self.allowed_origin():return self.answer(403)
            query=parse_qs(urlsplit(self.path).query)
            code=query.get('invite',[''])[0]
            return self.answer(200,accounts.registration_form(code,join=query.get('join',[''])[0]),'text/html; charset=utf-8')
        if self.guard():
            if urlsplit(self.path).path == '/invite':
                if self._cloud_person.get('role')!='lead':return self.answer(403,'只有组长可以邀请组员'.encode())
                return self.answer(200,accounts.invite_page(),'text/html; charset=utf-8')
            super().do_GET()

    def do_HEAD(self):
        if self.guard():
            super().do_HEAD()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/register':
            if not self.allowed_origin():return self.answer(403)
            if self.limited():return self.answer(429)
            code=''
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:return self.answer(413)
                body=parse_qs(self.rfile.read(length).decode())
                code=body.get('invite',[''])[0]
                join=body.get('join',[''])[0]
                if set(body)-{'invite','name','password','confirm','join'}:
                    raise serve.ID.AuthError('注册不能指定角色或已有人员身份')
                if not join and not accounts.valid_invite(JOIN,code):
                    self.failed(); return self.answer(403,accounts.registration_form('','加入链接无效，请向组长获取加入链接'),'text/html; charset=utf-8')
                name,pw=body.get('name',[''])[0],body.get('password',[''])[0]
                result=serve.H._auth(self,'/api/auth/claim' if join else '/api/auth/register',{'name':name,'pin':pw,'pin_confirm':body.get('confirm',[''])[0],**({'join':join} if join else {})})
                logged=serve.IDENT.login(serve.load_people(),result['person']['id'],pw)
                return self.start_session(logged)
            except serve.ID.AuthError as exc:
                return self.answer(400,accounts.registration_form(code,str(exc),join=body.get('join',[''])[0]),'text/html; charset=utf-8')
            except Exception:
                return self.answer(503,'注册未完成，请稍后重试；不要重复提交已成功的注册'.encode())
        if path == '/login':
            if not self.allowed_origin():
                return self.answer(403, '请从 Helix 首页登录'.encode())
            if self.limited():
                return self.login_form(429, '尝试次数过多，请一分钟后重试')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    return self.answer(413)
                body = parse_qs(self.rfile.read(length).decode('utf-8'))
                access = load_access()
                username, password = body.get('username', [''])[0], body.get('password', [''])[0]
                if not access.get('username'):
                    return self.answer(200, FORM, 'text/html; charset=utf-8')
                portal=body.get('portal',[''])[0]
                try:result=serve.IDENT.login_name(serve.load_people(),username,password,portal)
                except serve.ID.AuthError:
                    self.failed();return self.login_form(403,'账号、密码或登录入口不正确，请检查后重试',leader=portal=='lead')
                return self.start_session(result)
            except Exception:
                return self.login_form(503, '登录暂不可用，请稍后重试')
        if path == '/logout':
            if not self.allowed_origin():
                return self.answer(403)
            with LOCK:
                prior=SESSIONS.pop(self.session_id(), None)
                if prior:serve.IDENT.logout(prior['token'])
            return self.redirect(f'{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
        if urlsplit(self.path).path == '/setup':
            # Stale first-use forms must lead to login, never replace an existing password.
            try:
                if load_access().get('username'):
                    return self.redirect()
            except Exception:
                return self.answer(503)
            if not self.allowed_origin():
                return self.answer(403)
            if self.limited():
                return self.answer(429)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    return self.answer(413)
                body = parse_qs(self.rfile.read(length).decode('utf-8'))
                with LOCK:
                    access = load_access()
                    if access.get('username'):
                        return self.redirect()
                    code = body.get('code', [''])[0]
                    if not hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), access.get('bootstrap_hash', '')):
                        self.failed(); return self.answer(403, '初始化码不正确'.encode())
                    user, pw = body.get('username', [''])[0], body.get('password', [''])[0]
                    if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', user) or len(pw) < 12 or pw != body.get('confirm', [''])[0]:
                        return self.answer(400, '账号或密码格式不正确，或两次密码不一致'.encode())
                    accounts.bootstrap_owner(serve,user,pw)
                    save_access({'username': user, **password_record(pw)})
                self.send_response(303); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers()
                return
            except Exception:
                return self.answer(503, '设置未完成，请联系部署者'.encode())
        # Resumable upload chunks authenticate with the 128-bit transfer token issued to an
        # already-authenticated lead; the login session itself is memory-only and would
        # otherwise abort a multi-GB transfer whenever the server restarts.
        if path.startswith('/api/desktop-transfer/') and path.rsplit('/', 1)[-1] in ('chunk', 'status', 'finish'):
            return super().do_POST()
        if self.guard():
            if path in ('/api/desktop-sync/metadata-preview', '/api/desktop-sync/metadata-apply'):
                if self._cloud_person.get('role') != 'lead':
                    return self._json({'ok': False, 'error': 'Authenticated cloud lead required'}, 403)
            if path == '/api/cloud/invite':
                try:
                    serve.need_admin(self)
                    if self.headers.get('X-Helix')!='1':return self.answer(403)
                    with LOCK:url=accounts.issue_invite(JOIN,ORIGIN,secure_store)
                    return self._json({'ok':True,'url':url})
                except serve.ID.AuthError:return self._json({'ok':False,'error':'只有组长可以邀请组员'},403)
            return super().do_POST()


def recover_sync():
    """Reconcile committed check-ins after crashes; Notion row hashes skip unchanged data."""
    while True:
        try:
            if serve.CLOUD.public()['enabled']:
                for p in serve.P.list_local():
                    serve.CLOUD.enqueue(p['id'])
        except Exception:
            pass
        time.sleep(60)


def main():
    load_access()
    threading.Thread(target=recover_sync, daemon=True).start()
    # Container listens privately; publish only on the host's loopback address.
    server = serve.ThreadingHTTPServer((os.environ.get('HELIX_BIND', '127.0.0.1'), int(os.environ.get('HELIX_PORT', '18773'))), H)
    server.serve_forever()


if __name__ == '__main__':
    main()
