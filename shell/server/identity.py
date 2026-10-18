"""0.5 真人身份：每位成员一个本机口令（PBKDF2-SHA256 加盐散列），登录后得到会话令牌。

- 所有真人写入（配对、交叉核、检查点、播种、冻结计划、启动/确认智能体运行）只认会话里的人，
  不再相信表单里填的名字；
- 口令只存散列（data/credentials.json），不存明文；连续输错 5 次锁 5 分钟；
- 管理操作（改名册、改计划、配模型）需要组长；还没有任何人设口令时处于“引导模式”，界面会提示。
局限（如实说明）：这是本机口令，不是外部可信签名；能登录这台电脑并知道口令的人仍可操作。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import unicodedata
from functools import wraps
import time
from pathlib import Path

ITER = 200_000
SESSION_TTL = 12 * 3600          # 未勾选“保持登录”
IDLE_TTL = 4 * 3600
REMEMBER_TTL = 30 * 24 * 3600    # 勾选“保持登录”（默认）：30 天
REMEMBER_IDLE = 14 * 24 * 3600
_SESS_PATH: Path | None = None
_SESS_FLUSH = [0.0]
MAX_FAIL, LOCK_SEC = 5, 300
_LOCK = threading.RLock()
_SESSIONS: dict[str, dict] = {}
_FAILS: dict[str, list] = {}


class AuthError(PermissionError):
    pass


def anonymous_name(people, role='both'):
    used={p.get('name') for p in people}
    if role=='lead' and '组长' not in used:return '组长'
    for i in range(1,1000):
        name='组员'+(chr(64+i) if i<=26 else str(i))
        if name not in used:return name


def _serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return call


class Identity:
    def __init__(self, data_dir: Path, *, member_mode: bool = False, account_scope: str = "local"):
        self.path = Path(data_dir) / "credentials.json"
        self.member_mode = member_mode
        self.account_scope = account_scope
        self.store_lock = _LOCK
        global _SESS_PATH
        _SESS_PATH = Path(data_dir) / "sessions.json"
        _load_sessions()

    # ---------------------------------------------------------------- store
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("creds", {})
        except FileNotFoundError:
            return {}
        except Exception as exc:
            raise AuthError("口令文件损坏或不可读，已停止身份写入；请恢复备份，不要删除文件绕过校验。") from exc

    def _save(self, creds: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": "helix.credentials.v1", "creds": creds}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def has_pin(self, pid: str) -> bool:
        return pid in self._load()

    def any_pin(self) -> bool:
        return bool(self._load())

    def status(self, people: list[dict]) -> dict:
        creds = self._load()
        leads = [p["id"] for p in people if p.get("role") == "lead" and p["id"] in creds]
        return {"bootstrap": not creds and not self.member_mode, "with_pin": sorted(creds), "lead_with_pin": leads,
                "registration": True, "account_scope": self.account_scope, "member_mode": self.member_mode}

    def login_name(self, people: list[dict], name: str, pin: str, portal: str = "", remember: bool = True) -> dict:
        key = unicodedata.normalize("NFKC", str(name or "")).strip().casefold()
        person = next((p for p in people if p.get('active',True) and unicodedata.normalize("NFKC",p.get('username') or p.get('name','')).strip().casefold()==key),None)
        if not person:raise AuthError('账号或密码不正确')
        if portal not in ('','member','lead'):raise AuthError('登录入口无效，请重新打开登录页')
        if portal=='lead' and person.get('role')!='lead':raise AuthError('这是组员账号，请从普通登录进入')
        return self.login(people,person['id'],pin,remember=remember)

    # ---------------------------------------------------------------- pin
    @staticmethod
    def _hash(pin: str, salt: bytes, it: int = ITER) -> str:
        return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, it).hex()

    def _check(self, pid: str, pin: str) -> bool:
        with _LOCK:
            f = [t for t in _FAILS.get(pid, []) if time.time() - t < LOCK_SEC]
            _FAILS[pid] = f
            if len(f) >= MAX_FAIL:
                raise AuthError(f"口令连续输错 {MAX_FAIL} 次，已锁定 {LOCK_SEC // 60} 分钟。")
        c = self._load().get(pid)
        ok = bool(c) and hmac.compare_digest(self._hash(str(pin or ""), bytes.fromhex(c["salt"]), int(c.get("iter", ITER))), c["hash"])
        if not ok:
            with _LOCK:
                _FAILS.setdefault(pid, []).append(time.time())
        else:
            _FAILS.pop(pid, None)
        return ok

    @_serialized
    def set_pin(self, people: list[dict], pid: str, new_pin: str, *, current_pin: str = "", admin_id: str = "", admin_pin: str = "") -> str:
        person = next((p for p in people if p.get("id") == pid), None)
        if person is None:
            raise AuthError("名册里没有这个人；先在成员页登记。")
        new_pin = str(new_pin or "")
        if len(new_pin) < 6:
            raise AuthError("口令至少 6 位。")
        creds = self._load()
        lead_pins = [p["id"] for p in people if p.get("role") == "lead" and p["id"] in creds]
        how = ""
        if pid in creds:  # 改口令：本人旧口令，或一位组长批准
            if current_pin and self._check(pid, current_pin):
                how = "self"
            elif admin_id and admin_id in lead_pins and admin_id != pid and self._check(admin_id, admin_pin):
                how = "lead:" + admin_id
            else:
                raise AuthError("改口令需要输入原口令，或由一位组长输入组长口令批准（重置）。")
        elif lead_pins:  # 首次设置：已有组长时，必须组长在场批准
            if admin_id in lead_pins and admin_id != pid and self._check(admin_id, admin_pin):
                how = "lead:" + admin_id
            elif admin_id == pid:
                raise AuthError("组长不能给自己批准。")
            else:
                raise AuthError("已有组长设置了口令：新成员首次设口令需要组长输入组长口令批准。")
        elif self.member_mode:
            raise AuthError("组员版不能认领预置身份；请使用“注册新账号”，或输入本人原口令。")
        else:
            how = "bootstrap"  # 还没有组长口令：谁先设就是谁，界面会提示尽快给组长设口令
        salt = secrets.token_bytes(16)
        creds[pid] = {"salt": salt.hex(), "hash": self._hash(new_pin, salt), "iter": ITER, "set_at": int(time.time()), "set_by": how}
        self._save(creds)
        self.logout_person(pid)
        return how

    @_serialized
    def clear(self, pid: str) -> None:
        creds = self._load()
        if creds.pop(pid, None) is not None:
            self._save(creds)
        self.logout_person(pid)

    @_serialized
    def register(self, people: list[dict], name: str, pin: str, save_people, *, reserved_names=(), anonymous=False) -> dict:
        """只创建新的普通成员；不认领已有名册身份，不接受角色/指纹/人员 ID。"""
        name = unicodedata.normalize("NFKC", str(name or "")).strip()
        key = name.casefold()
        if not 2 <= len(name) <= 24 or not all(c.isalnum() or c in " _-." for c in name):
            raise AuthError("账号名须为 2–24 个字，只能用文字、数字、空格、下划线、点或连字符。")
        if key in {"helix", "admin", "administrator", "root", "owner", "组长", "组zhng", "组zhang", "zuzhang", "管理员", "统筹"}:
            raise AuthError("该名称为管理身份保留，请换一个账号名。")
        if any(unicodedata.normalize("NFKC",str(n or '')).strip().casefold()==key for p in people for n in (p.get('name'),p.get('username'))):
            raise AuthError("账号名已存在（含停用账号），请登录原账号或换一个名称；不能认领已有成员。")
        if any(unicodedata.normalize("NFKC", str(n)).strip().casefold() == key for n in reserved_names):
            raise AuthError("该名称已用于项目身份，请换一个账号名；既有任务身份需组长确认关联。")
        pin = str(pin or "")
        if not 8 <= len(pin) <= 128:
            raise AuthError("注册密码须为 8–128 位。")
        if len(people) >= 60:
            raise AuthError("本机名册已达 60 人，需由组长整理后再注册。")
        creds = self._load()
        pid = "p" + secrets.token_hex(12)
        while pid in creds or any(p.get("id") == pid for p in people):
            pid = "p" + secrets.token_hex(12)
        person = {"id": pid, "name": name, "role": "both", "keys": [], "active": True,
                  "color": "#7dd3fc", "note": "自行注册；尚未关联既有任务身份"}
        if anonymous:person.update(name=anonymous_name(people),username=name)
        salt = secrets.token_bytes(16)
        creds[pid] = {"salt": salt.hex(), "hash": self._hash(pin, salt), "iter": ITER,
                      "set_at": int(time.time()), "set_by": "self-register"}
        self._save(creds)
        try:
            save_people([*people, person])
        except Exception:
            creds.pop(pid, None)
            self._save(creds)
            raise
        return {"person": {k: person[k] for k in ("id", "name", "role")}, "account_scope": self.account_scope}

    @_serialized
    def issue_member_invite(self, people, pid):
        person=next((p for p in people if p.get('id')==pid and p.get('active',True)),None)
        if not person or person.get('role')=='lead' or self.has_pin(pid):
            raise AuthError('只能为尚未设置账号的组员生成设置链接')
        path=self.path.with_name('member_invites.json')
        saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        code=secrets.token_urlsafe(32)
        saved[pid]={'hash':hashlib.sha256(code.encode()).hexdigest(),'expires':int(time.time())+7*86400}
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(saved),encoding='utf-8');tmp.replace(path)
        return {'path':'/register?join='+code,'person_id':pid,'name':person['name'],'expires_in':7*86400}

    @_serialized
    def claim_member(self, people, code, name, pin, save_people):
        path=self.path.with_name('member_invites.json')
        saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        digest=hashlib.sha256(str(code or '').encode()).hexdigest()
        pid=next((pid for pid,v in saved.items() if v.get('expires',0)>time.time() and hmac.compare_digest(digest,v.get('hash',''))),None)
        person=next((p for p in people if p.get('id')==pid and p.get('active',True)),None)
        if not person or person.get('role')=='lead' or self.has_pin(pid):
            raise AuthError('设置链接无效、已过期或已经使用，请使用自己的账号登录')
        name=unicodedata.normalize('NFKC',str(name or '')).strip()
        if not 2<=len(name)<=24 or not all(c.isalnum() or c in ' _-.' for c in name):
            raise AuthError('账号须为2至24个文字、数字或下划线')
        if name.casefold() in {'helix','admin','administrator','root','owner','组长','组zhng','组zhang','zuzhang','管理员','统筹'}:
            raise AuthError('该账号名为管理身份保留')
        if any(unicodedata.normalize('NFKC',str(n or '')).strip().casefold()==name.casefold()
               for p in people for n in (p.get('username'),p.get('name')) if p['id']!=pid):
            raise AuthError('账号已存在，请换一个账号名')
        if not isinstance(pin,str) or not 8<=len(pin)<=128:raise AuthError('密码须为8至128位')
        updated=[{**p,'username':name,'note':'本人设置账号；保留原有任务身份'} if p['id']==pid else p for p in people]
        creds=self._load();salt=secrets.token_bytes(16)
        creds[pid]={'salt':salt.hex(),'hash':self._hash(pin,salt),'iter':ITER,'set_at':int(time.time()),'set_by':'self-invite'}
        self._save(creds)
        try:save_people(updated)
        except Exception:
            creds.pop(pid,None);self._save(creds);raise
        return {'person':{k:person[k] for k in ('id','name','role')},'account_scope':self.account_scope}

    # ---------------------------------------------------------------- sessions
    def login(self, people: list[dict], pid: str, pin: str, remember: bool = True) -> dict:
        person = next((p for p in people if p.get("id") == pid and p.get("active", True)), None)
        if person is None:
            raise AuthError("名册里没有这个人，或已停用。")
        if not self.has_pin(pid):
            raise AuthError(f"{person['name']} 还没有设置口令。")
        if not self._check(pid, pin):
            raise AuthError("账号或密码不正确。")
        token = secrets.token_urlsafe(32)
        now = time.time()
        with _LOCK:
            _SESSIONS[_th(token)] = {"pid": pid, "start": now, "last": now, "remember": bool(remember)}
            _save_sessions()
        return {"token": token, "person": {"id": pid, "name": person["name"], "role": person.get("role")}, "expires_in": REMEMBER_TTL if remember else SESSION_TTL, "remember": bool(remember)}

    def resolve(self, token: str, people: list[dict]) -> dict | None:
        if not token:
            return None
        key, now = _th(token), time.time()
        with _LOCK:
            s = _SESSIONS.get(key)
            if not s:
                return None
            ttl, idle = (REMEMBER_TTL, REMEMBER_IDLE) if s.get("remember") else (SESSION_TTL, IDLE_TTL)
            if now - s["start"] > ttl or now - s["last"] > idle:
                _SESSIONS.pop(key, None)
                _save_sessions()
                return None
            s["last"] = now
            if now - _SESS_FLUSH[0] > 300:
                _save_sessions()
        person = next((p for p in people if p.get("id") == s["pid"] and p.get("active", True)), None)
        if person is None or not self.has_pin(s["pid"]):
            return None
        return {**person, "session": key[:12]}

    def logout(self, token: str) -> None:
        with _LOCK:
            if _SESSIONS.pop(_th(token or ""), None):
                _save_sessions()

    def logout_person(self, pid: str) -> None:
        with _LOCK:
            for k in [k for k, v in _SESSIONS.items() if v["pid"] == pid]:
                _SESSIONS.pop(k, None)
            _save_sessions()


def _load_sessions() -> None:
    """会话持久化：只存令牌的散列，重启 Helix 不再把所有人踢下线。"""
    with _LOCK:
        _SESSIONS.clear()
        try:
            raw = json.loads(_SESS_PATH.read_text(encoding="utf-8")) if _SESS_PATH and _SESS_PATH.exists() else {}
        except Exception:
            raw = {}
        now = time.time()
        for k, v in (raw.get("sessions") or {}).items():
            if isinstance(v, dict) and v.get("pid") and now - float(v.get("start", 0)) < (REMEMBER_TTL if v.get("remember") else SESSION_TTL):
                _SESSIONS[k] = {"pid": v["pid"], "start": float(v["start"]), "last": float(v.get("last", v["start"])), "remember": bool(v.get("remember"))}


def _save_sessions() -> None:
    if not _SESS_PATH:
        return
    _SESS_FLUSH[0] = time.time()
    try:
        _SESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SESS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": "helix.sessions.v1", "sessions": _SESSIONS}), encoding="utf-8")
        tmp.replace(_SESS_PATH)
    except OSError:
        pass  # 写不进去时会话仍在内存里有效


def _th(token: str) -> str:
    return hashlib.sha256(("helix-session:" + token).encode()).hexdigest()


def is_admin(person: dict | None, people: list[dict], ident: Identity) -> tuple[bool, str]:
    """管理操作：引导模式（没人设口令）放行；否则需要登录，且有组长口令时必须是组长。"""
    st = ident.status(people)
    if ident.member_mode:
        return False, "这是组员版：管理设置保留在组长端。"
    if st["bootstrap"]:
        return True, "bootstrap"
    if not person:
        return False, "这个操作需要先登录（右上角“登录”）。"
    if person.get("role") != "lead":
        return False, "这个操作需要组长登录。"
    return True, person.get("role") or ""


def matches(person: dict, raw: str, fingerprint) -> bool:
    """会话里的人是否就是数据里的这个原名（按名册指纹或显示名）。"""
    raw = str(raw or "").strip()
    if not raw or not person:
        return False
    return raw == person.get("name") or fingerprint(raw) in (person.get("keys") or [])


__all__ = ["Identity", "AuthError", "is_admin", "matches"]
