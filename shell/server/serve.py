"""Helix Shell 本机服务（壳阶段）。

只读科研数据；唯一会写的是壳自己的成员名册 data/people.json（真人由你在界面里添加）。
- GET  /api/health      服务、数据端口、账本位置
- GET  /api/snapshot    helix.snapshot.v1（真名已按名册映射；未登记的人显示为代号“成员A/B/…”）
- GET  /api/events      SSE：账本 / 名册变化时推送（AG-UI 风格事件名）
- GET  /api/people      名册 + 数据里出现但未登记的人（只给代号与不可逆指纹，不给原名）
- POST /api/people      保存名册（需请求头 X-Helix: 1，防跨站写入）
- GET  /motion/helix-v2 2.00 原版双螺旋（服务端渲染，同样做名字映射）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP_DIR = ROOT / "app"
CONFIG = ROOT / "helix.config.json"
sys.path.insert(0, str(HERE))
import projects as P  # noqa: E402
import local_team as LT
import project_import as PI
from version import VERSION as RELEASE_VERSION, BUILD as RELEASE_BUILD, SHELL_VERSION
PEOPLE = ROOT / "data" / "people.json"
SCHEMA = "helix.snapshot.v1"
VERSION = SHELL_VERSION


def _cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


# 内核：默认用随壳附带的只读 harness（vendor/），也可指向 Workbench 2.00 的 app 目录
_override = os.environ.get("HELIX_WORKBENCH_APP") or _cfg().get("workbench_app")
KERNEL = Path(_override) if _override and (Path(_override) / "harness" / "sources.py").is_file() else HERE / "vendor"
sys.path.insert(0, str(KERNEL))
KERNEL_ERR = ""
try:
    from harness import sources as _sources  # noqa: E402
    from harness import sprint as _sprint  # noqa: E402
    from harness import state as _state  # noqa: E402
    from harness import service as _service  # noqa: E402
    from harness.ledger import HelixLedger  # noqa: E402
    from harness.sources import ResearchSnapshot, Witness  # noqa: E402
    from ui import helix_view as _hv  # noqa: E402
except Exception as exc:  # pragma: no cover
    KERNEL, KERNEL_ERR = None, f"{type(exc).__name__}: {exc}"

import identity as ID  # noqa: E402
import evidence as EV  # noqa: E402
import models as MD  # noqa: E402
import agents as AG  # noqa: E402
import science as SCI  # noqa: E402
import team as TEAM  # noqa: E402
import cloud as CL  # noqa: E402
import source_project as SP  # noqa: E402
import human_runs as HR  # noqa: E402
if KERNEL is not None:
    from harness import ledger as _ledger_mod  # noqa: E402
    from harness import proofread as _proof  # noqa: E402
    AG.register_events(_ledger_mod)
    TEAM.register_events(_ledger_mod)
    HR.register_events(_ledger_mod)
    LT.register_events(_ledger_mod)
DATA = ROOT / "data"
IDENT = ID.Identity(DATA, member_mode=bool(_cfg().get("member_mode")), account_scope="cloud" if os.environ.get("HELIX_CLOUD_MODE") == "1" else "local")
REG = MD.Registry(DATA)
CLOUD = CL.Cloud(DATA, lambda pid: cloud_rows(pid))
import updater as UPD  # noqa: E402

COOKIE = "helix_sid"
STARTED_AT = time.time()
PUBLIC_PATHS = {"/login", "/leader-login", "/register", "/api/health", "/api/version", "/api/auth/me", "/api/update/manifest",
                "/api/update/package", "/icon.svg", "/icon.ico", "/favicon.ico", "/manifest.webmanifest", "/sw.js", "/api/client-log"}
PUBLIC_POST = {"/api/auth/login", "/api/auth/register", "/api/auth/bootstrap", "/api/auth/claim", "/api/auth/logout", "/api/client-log"}


def req_token(handler) -> str:
    """会话令牌：HttpOnly Cookie（EventSource / 下载链接也能带上）与旧版请求头都接受，取有效的那个。"""
    if handler is None:
        return ""
    head = handler.headers.get("X-Helix-Token") or ""
    cookie = ""
    for part in (handler.headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            cookie = v.strip()
    if head and cookie and head != cookie:
        people = load_people()
        if IDENT.resolve(cookie, people):
            return cookie
    return head or cookie


def session_cookie(token: str, remember: bool = True) -> str:
    age = "; Max-Age=%d" % ID.REMEMBER_TTL if remember else ""
    return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict{age}" if token else f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"

try:  # 真人身份校验：优先用内核自带的 core.review_session；没有时用 server/helix_core_shim.py
    import core.review_session  # noqa: E402,F401
except ImportError:
    import types
    import helix_core_shim as _rs  # noqa: E402
    _pkg = types.ModuleType("core")
    _pkg.__path__ = []
    _pkg.review_session = _rs
    sys.modules.setdefault("core", _pkg)
    sys.modules["core.review_session"] = _rs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _builtin_id() -> str:
    return os.environ.get("HELIX_PROJECT_ID") or _cfg().get("project_id") or "MASH_2026_002"


def _project_id() -> str:
    """当前活动项目：内置项目（V4 只读来源）或 data/projects/ 下的本机项目。"""
    return P.active_id(_builtin_id())


def _ledger_root() -> Path | None:
    pid = _project_id()
    if P.is_local(pid):
        return P.ledger_root(pid)
    if _cfg().get('generic_mode'):
        return P.ledger_root(pid) if (P.ledger_root(pid)/pid/'ledger.jsonl').is_file() else None
    cands = [str(P.ledger_root(pid)), os.environ.get("HELIX_LEDGER_ROOT"), _cfg().get("ledger_root")]
    if os.environ.get("HELIX_STATE_DIR"):
        cands.append(str(Path(os.environ["HELIX_STATE_DIR"]) / ".destoop" / "helix"))
    cands += ["D:/aidd destoop/.destoop/helix"]
    if os.environ.get("LOCALAPPDATA"):
        cands.append(str(Path(os.environ["LOCALAPPDATA"]) / "MASH-AIDD-Workbench" / ".destoop" / "helix"))
    for c in cands:
        if c and (Path(c) / _project_id() / "ledger.jsonl").is_file():
            return Path(c)
    return None


def _writable() -> tuple[bool, str]:
    """只有 Helix Shell 自己 data/projects/ 下的账本可写；Workbench 2.00 的账本与 research_v4 一律只读。"""
    if KERNEL is None:
        return False, "内核未加载：" + KERNEL_ERR
    pid, root = _project_id(), _ledger_root()
    if root and Path(root).resolve() == P.ledger_root(pid).resolve():
        return True, ""
    if root:
        return False, "这个项目的账本由 Workbench 2.00 管理（只读显示）；请在 Workbench 页 29 配对，或在本机另建项目"
    return False, "这个项目还没有账本（预览模式）。点“在本机建立账本”后即可在这里播种、配对、交叉核（V4 原件仍只读）"


def _ledger(create: bool = False):
    ok, why = _writable()
    if not ok and not (create and not _ledger_root() and not P.is_local(_project_id())):
        raise PermissionError(why)
    led = HelixLedger(_project_id(), root=P.ledger_root(_project_id()))
    led.ensure_genesis()
    return led


def _local_research(p: dict):
    today = date.today()
    parsed = _sources.parse_sprint(P.tasks_csv(p), "", today) or {"tasks": [], "weeks": [], "members": [], "allowed_decisions": list(_sources.ALLOWED_SPRINT_DECISIONS)}
    sp = P.sprint_view(p, parsed, today)
    sp.setdefault("allowed_decisions", list(_sources.ALLOWED_SPRINT_DECISIONS))
    wit = [Witness(label=f["name"], path="files/" + f["stored"], sha256=f["sha256"], bytes=f["bytes"], mtime=f["added_at"], found=True) for f in P.files(p["id"])]
    roots = (p.get('source') or {}).get('roots')
    source = _sources.load_research_snapshot(roots=roots) if roots else None
    return ResearchSnapshot(generated_at=_now(), today=today.isoformat(), witnesses=wit,
                            v4=source.v4 if source else {}, sprint=sp)


def research_library(pid=None):
    pid = pid or _project_id()
    if P.is_local(pid):
        return SP.library(P.load(pid)) or P.pdir(pid) / 'research'
    return SCI.RESEARCH


def source_proposal():
    if P.is_local(_project_id()):
        raise ValueError('当前已经是普通项目，可直接编辑计划和运行任务。')
    source = _sources.load_research_snapshot()
    proposal = SP.preview(source, _cfg().get('project_name') or '', _builtin_id(), research_library())
    for member in proposal['project']['members']:
        person = next((p for p in load_people() if ID.matches(p, member['name'], fingerprint)), None)
        if person:
            member['role'] = person.get('role') or 'both'
    return proposal


def research() -> object:
    pid = _project_id()
    if P.is_local(pid):
        return _local_research(P.load(pid))
    if _cfg().get('generic_mode'):
        return ResearchSnapshot(generated_at=_now(),today=date.today().isoformat())
    return _sources.load_research_snapshot()


# ------------------------------------------------------------------ 名册与名字映射
ROLES = {"lead": "组长", "primary": "主核", "cross": "交叉核", "both": "主核 + 交叉核", "observer": "观察者"}
_HEX = re.compile(r"^[0-9a-f]{12}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def fingerprint(raw: str) -> str:
    """原名的不可逆指纹：名册只存它，不存原名。"""
    return hashlib.sha256(("helix-person:" + raw.strip()).encode("utf-8")).hexdigest()[:12]


def load_people() -> list[dict]:
    try:
        data = json.loads(PEOPLE.read_text(encoding="utf-8"))
        return [p for p in data.get("people", []) if isinstance(p, dict)]
    except Exception:
        return []


def clean_people(rows: list) -> list[dict]:
    out, seen = [], set()
    existing={p['id']:p for p in load_people()}
    for p in rows[:60]:
        if not isinstance(p, dict):
            continue
        name = re.sub(r'["\\<>\x00-\x1f]', "", str(p.get("name") or "")).strip()[:24]
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "id": re.sub(r"[^a-zA-Z0-9_-]", "", str(p.get("id") or "")) or "p" + hashlib.sha1(name.encode()).hexdigest()[:8],
            "name": name,
            "role": p.get("role") if p.get("role") in ROLES else "both",
            "color": p.get("color") if _COLOR.match(str(p.get("color") or "")) else "",
            "note": str(p.get("note") or "")[:80],
            "keys": [k for k in (p.get("keys") or []) if _HEX.match(str(k))][:6],
            "active": bool(p.get("active", True)),
        })
        username=existing.get(out[-1]['id'],{}).get('username') or p.get('username')
        if username:out[-1]['username']=str(username).strip()[:24]
    return out


def public_people(people):
    return [{k:v for k,v in p.items() if k!='username'} for p in people]


def raw_people(snap: dict) -> list[str]:
    seen: list[str] = []

    def add(v):
        v = str(v or "").strip()
        if v and v not in seen and len(v) <= 24 and not v.startswith(("harness", "system", "ai:", "未指定")):
            seen.append(v)
    sp = snap.get("sprint") or {}
    for m in sp.get("members") or []:
        add(m)
    for t in sp.get("pairs") or []:
        add(t.get("owner")); add(t.get("cross_reviewer"))
    for b in snap.get("items") or []:
        add(b.get("owner")); add(b.get("cross_reviewer")); add(b.get("reviewer"))
        add((b.get("cross") or {}).get("reviewer"))
    for e in (snap.get("ledger") or {}).get("tail") or []:
        if e.get("actor_kind") == "human":
            add(e.get("actor"))
    return seen


def name_map(raws: list[str], people: list[dict]) -> tuple[dict, list[dict]]:
    by_key = {k: p for p in people for k in p.get("keys", [])}
    mapping, discovered, n = {}, [], 0
    for raw in raws:
        fp = fingerprint(raw)
        p = by_key.get(fp) or next((person for person in people if person.get('name')==raw and person.get('active',True)),None)
        code = "成员" + (chr(65 + n) if n < 26 else str(n + 1))
        n += 1  # 代号按出现顺序固定，登记某人后其他人的代号不变
        if p:
            mapping[raw] = p["name"]
        else:
            mapping[raw] = code
            discovered.append({"code": code, "key": fp})
    return mapping, discovered


# 姓名映射（通用版）：按“哪些字段绝不能动”来保护，而不是只列哪些字段要换。
# - 受保护字段：ID、哈希、键、令牌、路径、链接、时间、序号、枚举（状态/结论/类型…）——一律不动；
# - 字符串看起来像不透明标识（哈希、路径、URL、时间戳）也不动；数字、布尔、null 永远不动；
# - 人员字段（owner/reviewer/members…或 *_by/*_reviewer…）：整段等于原名就换，包括“1”这类数字代号；
# - 其他任意文字字段（rationale、deliverable、将来新增的说明字段…）：像名字的原名（≥2 字、非纯数字）按子串替换；
# - 字典的键整段等于像名字的原名时也换（例如按成员统计的表）。
_PERSON_KEYS = {"owner", "cross_reviewer", "reviewer", "actor", "by", "who", "assignee", "member", "person",
                "claimed_by", "author", "operator", "holder", "claimant", "primary_reviewer", "signer"}
_PERSON_LISTS = {"members", "people", "reviewers", "owners", "actors", "assignees", "signers"}
_PROTECT_KEY = re.compile(r"(?:^|_)(?:id|ids|uid|pid|sha|sha256|hash|hashes|digest|fingerprint|key|keys|token|pin|secret|password|"
                          r"path|paths|stored|file|url|uri|href|ts|at|time|date|seq|count|kind|type|status|state|decision|verdict|"
                          r"level|mode|code|color|phase|track|bond|gate|turn|run|mime|ext|lang|version|role|duty|duties|parser)$", re.I)
_OPAQUE = re.compile(r"^(?:[0-9a-f]{12,}|sha256:\S+|[a-z][a-z0-9+.-]*://\S*|[A-Za-z]:[\\/].*|/\S*|\d{4}-\d{2}-\d{2}(?:[T ][\d:.+Z-]+)?|[^\s/\\]+\.[A-Za-z0-9]{1,6})$", re.I)  # 最后一项：文件名


def _person_key(k: str) -> bool:
    return k in _PERSON_KEYS or k.endswith(("_by", "_reviewer", "_owner", "_actor", "_person", "_member"))


def _textual(raw: str) -> bool:
    return len(raw) >= 2 and not raw.isdigit() and not (raw.isascii() and len(raw) < 3)


def _remap(obj, mapping: dict, first_wins: bool = False):
    """通用结构化替换；apply_map 用原名→显示名，unmap 用显示名→原名。"""
    if not mapping:
        return obj
    subs = sorted((r for r in mapping if _textual(r) and mapping[r] != r), key=len, reverse=True)

    def text(v: str) -> str:
        if _OPAQUE.match(v.strip()):
            return v
        if v.strip() in mapping and _textual(v.strip()):
            return mapping[v.strip()]
        for raw in subs:
            if raw in v:
                v = v.replace(raw, mapping[raw])
        return v

    def walk(o, key="", person=False):
        if isinstance(o, dict):
            return {(mapping[k] if isinstance(k, str) and _textual(k) and k in mapping else k): walk(v, str(k)) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(x, key, person or key in _PERSON_LISTS) for x in o]
        if not isinstance(o, str) or (_PROTECT_KEY.search(key) and not (person or _person_key(key))):
            return o
        if person or _person_key(key) or key in _PERSON_LISTS:
            return mapping.get(o.strip(), o)
        return text(o) if subs or o.strip() in mapping else o
    return walk(obj)


UNMAP_PATHS = {"/api/projects/update", "/api/pair", "/api/cross", "/api/checkpoint", "/api/seed"}


def apply_map(obj, mapping: dict):
    return _remap(obj, mapping)


def unmap(obj, mapping: dict):
    """写入前把界面上的显示名 / 代号换回数据里的原名，保证落盘的计划和账本始终是原名。"""
    inv: dict = {}
    for raw, shown in mapping.items():
        if shown != raw:
            inv.setdefault(shown, raw)  # 同一人多个原名时取最先出现的
    return _remap(obj, inv)


# ------------------------------------------------------------------ 快照
def _demo() -> dict:
    data = json.loads((APP_DIR / "data" / "demo.snapshot.json").read_text(encoding="utf-8"))
    data.update(mode="demo", port={"kind": "demo", "detail": KERNEL_ERR or "演示数据"})
    return data


def _preview_items(research) -> list[dict]:
    rows = _sprint.seed_rows(research, include_exceptions=True)
    out = []
    for i, r in enumerate(rows, 1):
        out.append({**r, "base_id": f"preview.{i:02d}", "status": "open", "bond": "GC", "needs_cross": True, "source": "preview",
                    "bond_reasons": ["本轮真实任务：真人主核 + 另一位真人交叉核", "预览：尚未写入账本（在 Workbench 页 29 播种后变为正式碱基）"]})
    return out


def build_raw() -> dict:
    if KERNEL is None:
        return _demo()
    pid = _project_id()
    local = P.load(pid) if P.is_local(pid) else None
    research_ = research()
    r = research_.as_dict()
    root = _ledger_root()
    led = HelixLedger(_project_id(), root=root) if root else None
    entries = led.entries() if led else []
    verify = led.verify().to_dict() if led else {"ok": True, "count": 0, "head": "", "problem": "未找到螺旋账本（预览模式）"}
    st = _state.fold_state(_project_id(), entries)
    bases = st.ordered_bases()
    preview = not bases and bool(research_.sprint.get("tasks"))
    if preview:
        bases = _preview_items(research_)
    payload = _hv.helix_payload(bases, stalled=bool(st.fork_stalled), autonomy_label=str(st.autonomy), limit=60)
    v4, sp = r.get("v4") or {}, r.get("sprint") or {}
    cur = sp.get("current_week")
    pairs = []
    for t in sp.get("tasks") or []:
        if t.get("week") != cur:
            continue
        tb = [b for b in bases if b.get("task_id") == t["task_id"]]
        pairs.append({**{k: t.get(k) for k in ("task_id", "owner", "cross_reviewer", "track_label", "deliverable", "acceptance", "status", "end")},
                      "bases": 0 if preview else len(tb), "open": sum(1 for b in tb if b.get("status") == "open"),
                      "crossed": sum(1 for b in tb if b.get("cross")), "disputed": sum(1 for b in tb if b.get("status") == "disputed")})
    found = sum(1 for w in r.get("witnesses") or [] if w.get("found"))
    writable, why = _writable()
    seeded_tasks = {b.get("task_id") for b in bases if not preview}
    week_tasks = [t["task_id"] for t in sp.get("tasks") or [] if t.get("week") == cur]
    harness = {"writable": writable, "why": why, "kind": "local" if local else "builtin", "can_takeover": not local and not root,
               "can_adopt": bool(not local and found and sp.get('tasks')),
               "seedable": bool(writable and week_tasks and not (set(week_tasks) & seeded_tasks)), "seed_week": cur,
               "allowed_decisions": list(_sources.ALLOWED_SPRINT_DECISIONS), "needs_refs": ["PASS", "REJECTED", "OUT_OF_DOMAIN"]}
    projects = [{"id": _builtin_id(), "name": _cfg().get("project_name") or _builtin_id(), "kind": "builtin"}] + P.list_local()
    for x in projects:
        x["active"] = x["id"] == pid
    if local:
        found = 1
        v4 = {**v4, "target": local.get("target"), "question": local.get("question"), "discovery_status": local.get("status"), "claim_ceiling": local.get("claim_ceiling"), "updated_at": local.get("updated_at")}
    return {
        "schema": SCHEMA, "generated_at": _now(), "mode": "live" if found else "empty", "preview": preview,
        "harness": harness, "projects": projects, "files": P.files(pid),
        "plan": {k: local.get(k) for k in ("weeks", "tasks", "members", "duties", "red_lines", "claim_ceiling", "name", "target", "question", "status", "team_assignment")} if local else None,
        "port": {"kind": "helix-shell-server", "detail": f"内核 {KERNEL.name if KERNEL else '-'} · 账本 {root or '未找到'}"},
        "project": {"id": pid, "kind": "local" if local else "builtin", "name": (local or {}).get("name") or _cfg().get("project_name") or v4.get("target") or pid, "v4_id": v4.get("project_id"),
                    "target": v4.get("target"), "question": v4.get("question"), "status": v4.get("discovery_status"),
                    "claim_ceiling": v4.get("claim_ceiling"), "direction": v4.get("target_direction"), "updated_at": v4.get("updated_at")},
        "skeleton": {"gates": [{"id": k, **(v if isinstance(v, dict) else {"raw_status": v})} for k, v in (v4.get("gates") or {}).items()],
                     "human_nodes": v4.get("human_nodes") or {}, "freeze": v4.get("freeze") or {}, "validation": v4.get("validation") or {},
                     "tracks": v4.get("tracks") or {}, "candidates": len(v4.get("candidates") or []),
                     "exceptions": [{k: e.get(k) for k in ("task_id", "category", "target", "status", "priority", "object")} for e in v4.get("exceptions") or []]},
        "sprint": {"today": r.get("today"), "current_week": cur, "days_left": sp.get("days_left"), "weeks": sp.get("weeks") or [], "pairs": pairs,
                   "members": sp.get("members") or [], "red_lines": sp.get("red_lines") or [], "allowed_decisions": sp.get("allowed_decisions") or [],
                   "violations": sp.get("same_person_violations") or []},
        "helix": payload,
        "items": [_item(b) for b in bases[-120:]],
        "metrics": {**st.metrics(), **({"open_tasks": len(bases), "preview": len(bases)} if preview else {})},
        "ledger": {**verify, "tail": [{k: e.get(k) for k in ("seq", "ts", "type", "actor", "actor_kind", "hash")} for e in entries[-40:]]},
        "witnesses": r.get("witnesses") or [], "snapshot_hash": r.get("snapshot_hash"),
        "agents": _cfg().get("agents") or [], "mcp": _cfg().get("mcp") or [], "models": _cfg().get("models") or {},
    }


def build_snapshot(handler=None) -> dict:
    raw = build_raw()
    people = load_people()
    mapping, discovered = name_map(raw_people(raw), people)
    snap = apply_map(raw, mapping)
    snap["sprint"]["members"] = list(dict.fromkeys(mapping[m] for m in raw_people(raw)))
    snap["people"] = {"roster": public_people(people), "discovered": discovered, "roles": ROLES, "editable": True}
    if KERNEL is not None:
        ctx = Ctx(_project_id())
        refs = agent_refs(ctx)
        for it in snap.get("items") or []:
            if it.get("base_id") in refs:
                it["agent_refs"] = refs[it["base_id"]]
        snap["gates"] = apply_map(gates_view(ctx), mapping)
        snap["agent_runs"] = apply_map(runtime(ctx.pid).view(), mapping)
        snap["team"] = apply_map(team_view(ctx, snap["sprint"].get("current_week")), mapping)
    snap["cloud"] = CLOUD.public()
    snap["auth"] = auth_view(handler)
    person=next((p for p in people if p['id']==(snap['auth'].get('me') or {}).get('id')),None)
    roles={t['task_id']:('owner' if ID.matches(person or {},t.get('owner'),fingerprint) else 'cross' if ID.matches(person or {},t.get('cross_reviewer'),fingerprint) else '') for t in Ctx(_project_id()).tasks()}
    for task in [*(snap.get('plan') or {}).get('tasks',[]),*snap['sprint'].get('pairs',[])]:task['my_role']=roles.get(task['task_id'],'')
    snap["people"]["editable"] = snap["auth"]["can_admin"]
    snap["models"] = {**REG.public(), "config": raw.get("models") or {}}
    snap["version"] = VERSION
    snap['release']={'version':RELEASE_VERSION,'build':RELEASE_BUILD}
    snap['generic_mode']=bool(_cfg().get('generic_mode'))
    snap["research_bundle"] = SCI.summary(research_library())
    snap["science_tools"] = SCI.registry()
    snap['human_runs'] = apply_map(HR.fold(Ctx(_project_id()).entries()), mapping)
    if LT.enabled(P.load(_project_id())):
        snap['local_team']=apply_map(LT.view(P,_project_id(),people,IDENT,person,Ctx(_project_id()).entries()),mapping)
    # 依赖已随包安装不等于已有 MCP 传输；明确展示实际接入方式。
    versions = {t["id"]: t for t in snap["science_tools"]}
    for entry in snap.get("mcp") or []:
        if entry.get("id") in versions:
            tool = versions[entry["id"]]
            entry.update(status=tool["status"], kind="随包开源工具 · " + (tool["version"] or "未安装") + " · 科学工具页调用")
    return snap


# 条目状态统一成内核词表（与前端 common.js 的 canon 一致）；别名、过去式、大小写都归一
_STATUS_ALIAS = {"pending": "open", "todo": "open", "new": "open", "defer": "deferred", "hold": "deferred", "held": "deferred", "paused": "deferred",
                 "postponed": "deferred", "complemented": "complement", "accepted": "complement", "accept": "complement", "agreed": "complement",
                 "revised": "revise", "mismatched": "mismatch", "rejected": "mismatch", "dispute": "disputed", "conflict": "disputed",
                 "audited": "audit", "sampled": "audit"}


def canon_status(st) -> str:
    k = str(st if st is not None else "").strip().lower()
    return _STATUS_ALIAS.get(k, k)


_ITEM_KEYS = ("base_id", "text", "kind", "bond", "status", "confidence", "rationale", "reviewer", "owner", "cross_reviewer",
              "task_id", "decision", "note", "source_refs", "sources", "gate_id", "phase", "needs_cross", "proposed_at", "paired_at", "turn_id",
              "bond_reasons", "track", "legacy_gate", "allowed_result", "evidence_keys", "source", "sampled", "proofread_notes", "proofread_block")


def _item(b: dict) -> dict:
    out = {k: b.get(k) for k in _ITEM_KEYS if b.get(k) not in (None, "", [], {})}
    if "status" in out:
        out["status"] = canon_status(out["status"])
    if b.get("cross"):
        out["cross"] = {k: b["cross"].get(k) for k in ("reviewer", "agree", "note", "ts", "at") if k in b["cross"]}
    return out


def _fingerprint() -> str:
    parts = []
    pid = _project_id()
    extra = [P.ACTIVE, P.pdir(pid) / "project.json", P.pdir(pid) / "files.json"]
    for p in [PEOPLE] + extra + ([(_ledger_root() or Path("-")) / P.safe_id(pid) / "ledger.jsonl"] if KERNEL else []):
        try:
            s = p.stat(); parts.append(f"{s.st_mtime_ns}:{s.st_size}")
        except OSError:
            parts.append("none")
    return "|".join(parts)


def save_people(people: list[dict]) -> list[dict]:
    PEOPLE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PEOPLE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": "helix.people.v1", "updated_at": _now(), "people": people}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PEOPLE)
    return people


@ID._serialized
def register_members(members: list[dict]) -> None:
    """向导里填的真人写进名册：显示名就是你填的名字，并关联它的指纹。"""
    people = load_people()
    colors = ["#fcd34d", "#6ee7b7", "#7dd3fc", "#f9a8d4", "#c4b5fd", "#fdba74"]
    changed = False
    for m in members:
        name, fp = m["name"], fingerprint(m["name"])
        # 先按指纹找（改过显示名的人仍是同一个人），再按名字找；避免改名后重复登记
        p = next((x for x in people if fp in (x.get("keys") or [])), None) or next((x for x in people if x["name"] == name), None)
        if p is None:
            role=m.get('role') if m.get('role') in ROLES else 'both'
            anonymous=_cfg().get('anonymous_members',True)
            people.append({"id": "p" + hashlib.sha1(name.encode()).hexdigest()[:8], "name": ID.anonymous_name(people,role) if anonymous else name, "role": role,
                           "color": colors[len(people) % len(colors)], "note": "", "keys": [fp], "active": True})
            if anonymous and role=='lead':people[-1]['username']=name
            changed = True
        elif fp not in p.get("keys", []):
            p.setdefault("keys", []).append(fp)
            changed = True
    if changed:
        save_people(clean_people(people))


def raw_name(display) -> str:
    """界面上显示的是名册名或代号；写账本前换回数据里的原名。"""
    display = str(display or "").strip()
    raw = build_raw()
    mapping, _ = name_map(raw_people(raw), load_people())
    inv = {v: k for k, v in mapping.items()}
    return inv.get(display, display)


def _witness_files() -> None:
    ok, _ = _writable()
    if not ok:
        return
    try:
        led = _ledger()
        _sprint.record_sources(led, research())
    except Exception:
        pass


# ------------------------------------------------------------------ 0.5：身份、证据、Gate、智能体
class Ctx:
    """绑定到一个项目的运行上下文（后台运行期间切换项目不会串账本）。"""
    registry = REG
    service = None
    backoff = 2.0

    def __init__(self, pid: str):
        self.pid = pid
        self.service = _service if KERNEL else None
        if os.environ.get("HELIX_TEST") == "1":
            self.backoff = 0.05

    def fault(self, step: str, attempt: int) -> None:
        """故障注入（只在 HELIX_TEST=1 时生效）：data/_fault.json = {"step": "extract", "kind": "timeout|server|crash", "times": 1}"""
        if os.environ.get("HELIX_TEST") != "1":
            return
        f = DATA / "_fault.json"
        try:
            spec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return
        if spec.get("step") != step or int(spec.get("times", 1)) <= 0:
            return
        spec["times"] = int(spec.get("times", 1)) - 1
        f.write_text(json.dumps(spec), encoding="utf-8")
        if spec.get("kind") == "crash":
            os._exit(3)  # 模拟断电：请求已记账、结果没回来
        raise MD.ModelError("注入故障：" + spec.get("kind", "server"), spec.get("kind", "server"), sent=True)

    def _root(self) -> Path:
        return P.ledger_root(self.pid)

    def ledger(self):
        led = HelixLedger(self.pid, root=self._root())
        if not led.path.is_file():
            raise PermissionError("这个项目还没有本机账本：先“在本机建立账本”。")
        return led

    def entries(self) -> list[dict]:
        if KERNEL is None:
            return []
        led = HelixLedger(self.pid, root=self._root())
        return led.entries() if led.path.is_file() else []

    def state(self):
        return _state.fold_state(self.pid, self.entries())

    def _research(self):
        return _local_research(P.load(self.pid)) if P.is_local(self.pid) else _sources.load_research_snapshot()

    def tasks(self) -> list[dict]:
        if P.is_local(self.pid):
            return [{**t, "track_label": t.get("track")} for t in (P.load(self.pid) or {}).get("tasks") or []]
        return [{**t, "depends_on": t.get("depends_on") or []} for t in self._research().sprint.get("tasks") or []]

    def task(self, tid) -> dict | None:
        return next((t for t in self.tasks() if t.get("task_id") == tid), None)

    def files(self) -> list[dict]:
        return P.files(self.pid)

    def file_abspath(self, f: dict) -> Path:
        path = P.file_path(self.pid, f['sha256'])
        if path is None:
            raise ValueError('项目来源文件不存在或已变化，请刷新后重新选择。')
        return path

    def ceiling(self) -> str:
        if P.is_local(self.pid):
            return str((P.load(self.pid) or {}).get("claim_ceiling") or "")
        return str((self._research().v4 or {}).get("claim_ceiling") or "")

    def red_lines(self) -> list:
        if P.is_local(self.pid):
            return list((P.load(self.pid) or {}).get("red_lines") or [])
        return list(self._research().sprint.get("red_lines") or [])

    def run_dir(self, rid: str) -> Path:
        return P.pdir(self.pid) / "runs" / re.sub(r"[^A-Za-z0-9_-]", "", rid)


_RUNTIMES: dict[str, AG.Runtime] = {}


def runtime(pid: str | None = None) -> AG.Runtime:
    pid = pid or _project_id()
    if pid not in _RUNTIMES:
        _RUNTIMES[pid] = AG.Runtime(Ctx(pid))
    return _RUNTIMES[pid]


def display_map() -> dict:
    mapping, _ = name_map(raw_people(build_raw()), load_people())
    return mapping


def actor_for(person: dict, *candidates: str) -> str:
    """写账本用的名字：优先用数据里的原名（与任务表一致），否则用名册显示名。"""
    for c in candidates:
        if c and ID.matches(person, c, fingerprint):
            return c
    for raw in raw_people(build_raw()):
        if ID.matches(person, raw, fingerprint):
            return raw
    return person["name"]


def gates_view(ctx: Ctx) -> dict:
    if KERNEL is None:
        return {}
    try:
        entries = ctx.entries()
        tasks = ctx.tasks()
        st = _state.fold_state(ctx.pid, entries)
        cur = AG.plan_hash(tasks, ctx.red_lines(), ctx.ceiling())
        fz = AG.frozen_plan(entries)
        return {"plan": {"hash": cur, "frozen": fz, "changed": bool(fz) and fz.get("plan_hash") != cur, "tasks": len(tasks)},
                "tasks": AG.task_states(st, tasks), "ceiling": ctx.ceiling()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def agent_refs(ctx: Ctx) -> dict:
    out = {}
    for e in ctx.entries():
        if e.get("type") == "run.completed":
            out.update((e.get("payload") or {}).get("refs") or {})
    return out


def need_person(handler) -> dict:
    p = IDENT.resolve(req_token(handler), load_people())
    if not p:
        if IDENT.status(load_people())["bootstrap"]:
            raise ID.AuthError("真人写入需要先登录。还没有人设置口令：请到“成员”页给自己设口令，再点右上角“登录”。")
        raise ID.AuthError("真人写入需要先登录（右上角“登录”）；登录已过期时请重新登录。")
    return p


def need_admin(handler) -> dict | None:
    p = IDENT.resolve(req_token(handler), load_people())
    ok, why = ID.is_admin(p, load_people(), IDENT)
    if not ok:
        raise ID.AuthError(why)
    return p


def attest(led, person: dict, target: dict, kind: str, extra: dict | None = None) -> None:
    led.append("write.attested", {"target_seq": target.get("seq"), "target_type": kind, "person_id": person["id"], "method": "pin-session",
                                  "session": person.get("session"), **(extra or {})}, actor=target.get("actor") or person["name"], actor_kind="human")


def do_pair(handler, data: dict) -> dict:
    person = need_person(handler)
    led = _ledger()
    ctx = Ctx(_project_id())
    st = _state.fold_state(ctx.pid, led.entries())
    bid = str(data.get("base_id") or "")
    b = st.bases.get(bid)
    if b is None:
        raise ValueError("找不到这个碱基。")
    dm = display_map()
    owner = str(b.get("owner") or "")
    if owner and not ID.matches(person, owner, fingerprint):
        raise ID.AuthError(f"这条的指定主核是 {dm.get(owner, owner)}；你（{person['name']}）不能代签。如需换人，请组长修改计划并重新冻结。")
    reviewer = actor_for(person, owner)
    decision = str(data.get("decision") or "")
    verdict = str(data.get("verdict") or "")
    refs = [str(x) for x in data.get("source_refs") or []][:20]
    checked = []
    if verdict != "defer":
        checked = EV.check_refs(refs, decision=decision if b.get("kind") == "task" else "", files=ctx.files(), witnesses=[w for w in research().as_dict().get("witnesses") or []],
                                quote=str(data.get("quote") or ""), file_text=lambda r: EV.read_text(ctx.file_abspath(r)) if r.get("kind") == "file" else None,
                                id_checked=bool(data.get("id_checked")))
    if b.get("kind") == "task" and decision in ("PASS",) and verdict in ("complement", "revise"):
        ts = AG.task_states(st, ctx.tasks()).get(b.get("task_id")) or {}
        if ts.get("blocked_by"):
            raise ValueError("Gate 阻断：" + ts["why"] + "。上游没有通过前，这个任务不能判 PASS。")
    blocked = EV.ceiling_guard(str(data.get("revised_text") or ""), ceiling=ctx.ceiling(), proofread=_proof.proofread_base)
    if blocked:
        raise ValueError("修正内容越过了主张上限 / 红线：" + "；".join(blocked))
    norm = [f"sha256:{r['sha256']} {r.get('name') or ''}".strip() if r.get("sha256") else r["raw"] for r in checked] or refs
    e = _service.pair_base(led, bid, verdict=verdict, reviewer=reviewer, confirmed=bool(data.get("confirmed")), note=str(data.get("note") or ""),
                           revised_text=str(data.get("revised_text") or ""), decision=decision, source_refs=norm)
    attest(led, person, e, "base.paired", {"base_id": bid, "designated": bool(owner), "evidence": [{k: r.get(k) for k in ("kind", "level", "checks", "sha256", "name", "id", "strong", "quote", "note")} for r in checked],
                                           "quote": str(data.get("quote") or "")[:400]})
    return e


def do_cross(handler, data: dict) -> dict:
    person = need_person(handler)
    led = _ledger()
    st = _state.fold_state(_project_id(), led.entries())
    bid = str(data.get("base_id") or "")
    b = st.bases.get(bid)
    if b is None:
        raise ValueError("找不到这个碱基。")
    dm = display_map()
    if b.get("reviewer") and ID.matches(person, str(b["reviewer"]), fingerprint):
        raise ID.AuthError("主核与交叉核必须是不同的人：这条的主核就是你。")
    designated = str(b.get("cross_reviewer") or "")
    if designated and not ID.matches(person, designated, fingerprint):
        raise ID.AuthError(f"这条的指定交叉核是 {dm.get(designated, designated)}；你（{person['name']}）不能代签。")
    e = _sprint.cross_review(led, bid, reviewer=actor_for(person, designated), agree=bool(data.get("agree")), note=str(data.get("note") or ""), confirmed=bool(data.get("confirmed")))
    attest(led, person, e, "base.crossed", {"base_id": bid, "designated": bool(designated)})
    return e


# ------------------------------------------------------------------ 0.6 组内打卡 + 云同步
REVIEW_LABEL = {"passed": "已通过", "failed": "未通过", "disputed": "有分歧", "blocked": "等上游", "unseeded": "未播种", "open": "待审核"}


def team_view(ctx: Ctx, week=None) -> dict:
    try:
        tasks = ctx.tasks()
        ck = TEAM.fold_checkins(ctx.entries())
        return {"checkins": ck, "states": TEAM.STATES, "summary": TEAM.summary(ck, tasks), "week": TEAM.summary(ck, tasks, week) if week else None}
    except Exception as exc:
        return {"checkins": {}, "states": TEAM.STATES, "error": f"{type(exc).__name__}: {exc}"}


def do_checkin(handler, data: dict) -> dict:
    person = need_person(handler)
    led = _ledger()
    ctx = Ctx(_project_id())
    tid = str(data.get("task_id") or "")
    t = ctx.task(tid)
    if not t:
        raise ValueError("找不到这个任务。")
    owner, cross = str(t.get("owner") or ""), str(t.get("cross_reviewer") or "")
    if owner and ID.matches(person, owner, fingerprint):
        role, raw = "owner", owner
    elif cross and ID.matches(person, cross, fingerprint):
        role, raw = "cross", cross
    else:
        dm = display_map()
        raise ID.AuthError(f"只有这条任务的主核 {dm.get(owner, owner)} 或交叉核 {dm.get(cross, cross)} 本人能打卡；你（{person['name']}）不能代打。")
    payload = TEAM.build_checkin(data, t, ctx.files(), role)
    e = led.append("task.checkin", payload, actor=raw, actor_kind="human")
    attest(led, person, e, "task.checkin", {"task_id": tid, "as": role})
    CLOUD.enqueue(ctx.pid)
    return e


def cloud_rows(pid: str) -> list[dict]:
    """推到云端的行（白名单字段；人名用名册显示名 / 代号，不推原名）。"""
    ctx = Ctx(pid)
    entries, tasks = ctx.entries(), ctx.tasks()
    ck = TEAM.fold_checkins(entries)
    ts = AG.task_states(_state.fold_state(pid, entries), tasks) if KERNEL is not None else {}
    local = P.load(pid) if P.is_local(pid) else None
    labels = {d.get("key"): d.get("label") for d in (local or {}).get("duties") or []}
    if pid == _project_id():
        mapping = display_map()
    else:
        raws = [x for t in tasks for x in (t.get("owner"), t.get("cross_reviewer")) if x]
        mapping, _ = name_map(list(dict.fromkeys(raws)), load_people())
    dn = lambda n: mapping.get(n, n) if n else ""  # noqa: E731
    name = (local or {}).get("name") or _cfg().get("project_name") or pid
    mirror_pid = (local or {}).get('notion_project_id') or pid
    out = []
    for t in tasks:
        tid = t["task_id"]
        c = ck.get(tid) or {}
        cur, last = c.get("owner") or c.get("latest") or {}, c.get("latest") or {}
        note = apply_map(last.get("note") or "", mapping) if last.get("note") else ""
        fl = next((h.get("files") for h in c.get("history") or [] if h.get("files")), [])  # 最近一次提交的成果
        out.append({"helix_id": f"{mirror_pid}:{tid}", "project": name, "task_id": tid, "title": t.get("deliverable") or "", "week": t.get("week"),
                    "duty": labels.get(t.get("duty") or "") or "", "owner": dn(t.get("owner")), "cross_reviewer": dn(t.get("cross_reviewer")),
                    "state": TEAM.STATES.get(cur.get("state"), "未打卡"), "progress": int(cur.get("progress") or 0), "checkin_at": last.get("ts") or "",
                    "checkin_by": dn(last.get("actor")), "note": note, "files": "；".join(f"{f.get('name')} ({str(f.get('sha256'))[:8]})" for f in fl),
                    "review": REVIEW_LABEL.get((ts.get(tid) or {}).get("state"), "未播种"), "acceptance": t.get("acceptance") or "", "due": t.get("end") or ""})
    return out


def freeze_plan(handler, data: dict, person: dict | None = None) -> dict:
    person = person or need_person(handler)
    ok, why = ID.is_admin(person, load_people(), IDENT)
    if not ok:
        raise ID.AuthError(why)
    ctx = Ctx(_project_id())
    led = _ledger()
    tasks = ctx.tasks()
    h = AG.plan_hash(tasks, ctx.red_lines(), ctx.ceiling())
    fz = AG.frozen_plan(led.entries())
    if fz and fz.get("plan_hash") == h:
        return {"seq": fz.get("seq"), "plan_hash": h, "unchanged": True}
    e = led.append("plan.frozen", {"plan_hash": h, "tasks": len(tasks), "previous": (fz or {}).get("plan_hash", ""), "note": str(data.get("note") or "")[:400],
                                   "ceiling": ctx.ceiling(), "red_lines": len(ctx.red_lines())}, actor=actor_for(person), actor_kind="human")
    return {"seq": e["seq"], "plan_hash": h}


def auth_view(handler=None) -> dict:
    people = load_people()
    st = IDENT.status(people)
    me = IDENT.resolve(req_token(handler), people) if handler else None
    return {**st, "can_admin": ID.is_admin(me, people, IDENT)[0],
            "me": {k: me.get(k) for k in ("id", "name", "role")} if me else None}


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(APP_DIR), **k)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        if not self.path.startswith("/api"):  # 页面文件每次都向服务确认，升级后不会新旧混用导致白屏
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _json(self, obj, code=200, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _gate(self, path: str) -> bool:
        """前置登录：除登录页、健康检查和签名更新清单外，所有页面、静态资源与接口都要先登录。"""
        if os.environ.get("HELIX_CLOUD_MODE") == "1" or path in PUBLIC_PATHS:
            return True
        people = load_people()
        if IDENT.resolve(req_token(self), people):
            return True
        boot = IDENT.status(people)["bootstrap"]
        if boot and path not in ("/", "/index.html"):
            return True  # 还没有任何账号：只有设置组长账号这一步，接口照常（与旧版一致）
        if path.startswith("/api/"):
            self._json({"ok": False, "auth": True, "error": "请先登录（登录已过期或尚未登录）"}, 401)
            return False
        if path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/leader-login" if boot else "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        body = "需要先登录".encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def do_GET(self):
        u = urlparse(self.path)
        if not self._gate(u.path):
            return
        try:
            if u.path == '/api/version':
                return self._json({'ok':True,'version':RELEASE_VERSION,'build':RELEASE_BUILD,'shell_version':VERSION,'started':STARTED_AT})
            if u.path == '/api/projects/export':
                need_admin(self)
                raw=PI.export_project(P,_project_id(),lambda p:apply_map(p,display_map()))
                self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Disposition','attachment; filename="Helix-Project.zip"');self.send_header('Content-Length',str(len(raw)));self.end_headers();return self.wfile.write(raw)
            if u.path in ('/login','/leader-login','/register') and os.environ.get('HELIX_CLOUD_MODE')!='1':
                import login_ui
                leads=[p.get('username') or p['name'] for p in load_people() if p.get('role')=='lead' and p.get('active',True)]
                body=login_ui.registration(parse_qs(u.query).get('join',[''])[0]) if u.path=='/register' else login_ui.page(leader=u.path=='/leader-login',leader_name=leads[0] if len(leads)==1 else '',bootstrap=IDENT.status(load_people())['bootstrap'])
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();return self.wfile.write(body)
            if u.path == "/api/update/manifest":
                pub = UPD.published()
                return self._json(pub[0] if pub else {"ok": False, "error": "本机没有发布更新"}, 200 if pub else 404)
            if u.path == "/api/update/package":
                pub = UPD.published()
                if not pub:
                    return self._json({"ok": False, "error": "本机没有发布更新"}, 404)
                raw = pub[1].read_bytes()
                self.send_response(200); self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % pub[0]["file"])
                self.send_header("Content-Length", str(len(raw))); self.end_headers()
                return self.wfile.write(raw)
            if u.path == "/api/update/status":
                return self._json({"ok": True, **UPD.status(), "can_publish": ID.is_admin(need_person(self), load_people(), IDENT)[0]})
            if u.path == "/api/update/bundle":
                need_admin(self)
                pub = UPD.published()
                if not pub:
                    return self._json({"ok": False, "error": "还没有发布过更新"}, 404)
                raw = UPD.bundle_for_hand(pub[0], pub[1].read_bytes())
                self.send_response(200); self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="Helix-Update-%s-handoff.zip"' % pub[0]["version"])
                self.send_header("Content-Length", str(len(raw))); self.end_headers()
                return self.wfile.write(raw)
            if u.path == "/api/health":
                return self._json({"ok": True, "version": VERSION, "release": RELEASE_VERSION, "instance_id":hashlib.sha256(str(ROOT.resolve()).casefold().encode()).hexdigest()[:16], "kernel": str(KERNEL or ""), "error": KERNEL_ERR,
                                   "ledger_root": str(_ledger_root() or ""), "people": len(load_people()), "ts": _now()})
            if u.path == "/api/snapshot":
                return self._json(build_snapshot(self))
            if u.path == '/api/local-team/resource':
                q=parse_qs(u.query)
                body=LT.resource(P,q.get('project',[_project_id()])[0],q.get('asset',[''])[0])
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
                self.send_header('Content-Length',str(len(body)));self.end_headers();return self.wfile.write(body)
            if u.path == "/api/research":
                q = parse_qs(u.query)
                return self._json({**SCI.summary(research_library()), **SCI.files(q.get("q", [""])[0], q.get("offset", [0])[0], root=research_library())})
            if u.path == "/api/research/papers":
                return self._json(SCI.papers(parse_qs(u.query).get("q", [""])[0], root=research_library()))
            if u.path == "/api/tools":
                return self._json({"ok": True, "tools": SCI.registry()})
            if u.path == "/api/tools/vina/example":
                import vina_adapter
                return self._json(vina_adapter.example())
            if u.path == "/api/tools/vina/file":
                import vina_adapter
                q = parse_qs(u.query)
                try:
                    fp = vina_adapter.result_file(q.get("run_id", [""])[0], q.get("name", [""])[0])
                except ValueError as exc:
                    return self._json({"ok": False, "error": str(exc)}, 404)
                body = fp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="' + fp.name + '"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/research/file":
                try:
                    fp = SCI.file_path(parse_qs(u.query).get("path", [""])[0], root=research_library())
                except ValueError as exc:
                    return self._json({"ok": False, "error": str(exc)}, 404)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + __import__("urllib.parse").parse.quote(fp.name))
                self.send_header("Content-Length", str(fp.stat().st_size))
                self.end_headers()
                with fp.open("rb") as stream:
                    __import__("shutil").copyfileobj(stream, self.wfile)
                return
            if u.path == "/api/auth/me":
                return self._json({"ok": True, **auth_view(self)})
            if u.path == "/api/models":
                return self._json({"ok": True, **REG.public()})
            if u.path == "/api/runs":
                return self._json({"ok": True, **apply_map(runtime().view(), display_map())})
            if u.path == "/api/preflight":
                why, roles = runtime().preflight(parse_qs(u.query).get("task_id", [""])[0], kind=parse_qs(u.query).get("kind", ["task"])[0])
                return self._json({"ok": True, "blockers": apply_map({"blockers": why}, display_map())["blockers"], "roles": roles})
            if u.path == "/api/people":
                s = build_snapshot()
                return self._json(s["people"])
            if u.path == "/motion/helix-v2":
                html = _hv.helix_html(build_snapshot()["helix"]) if KERNEL else "<body style='background:#0b1f1c;color:#9cc9bd;font:13px sans-serif;display:grid;place-items:center;height:100vh;margin:0'>2.00 原版动效需要内核</body>"
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/file/preview":
                fp = P.file_path(_project_id(), parse_qs(u.query).get("sha", [""])[0])
                if not fp:
                    return self._json({"ok": False, "error": "找不到文件"}, 404)
                return self._json({"ok": True, **apply_map(P.preview(fp,full=parse_qs(u.query).get('full',['0'])[0]=='1'), display_map())})
            if u.path == "/api/file":
                q = parse_qs(u.query)
                fp = P.file_path(_project_id(), q.get("sha", [""])[0])
                if not fp:
                    return self._json({"ok": False, "error": "找不到文件"}, 404)
                body = fp.read_bytes()
                mime = P.INLINE_TYPES.get(fp.suffix.lower())
                inline = q.get("inline", [""])[0] == "1" and bool(mime)
                self.send_response(200)
                self.send_header("Content-Type", mime if inline else "application/octet-stream")
                self.send_header("Content-Disposition", ("inline" if inline else "attachment") + "; filename*=UTF-8''" + __import__("urllib.parse").parse.quote(fp.name.split("-", 1)[-1]))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/events":
                return self._sse(float(parse_qs(u.query).get("interval", ["2"])[0]))
        except Exception as exc:
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
        if u.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def _body_json(self, limit: int = 2_000_000) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("请求太大")
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_POST(self):
        u = urlparse(self.path)
        if self.headers.get("X-Helix") != "1":
            return self._json({"ok": False, "error": "缺少 X-Helix 请求头"}, 403)
        LAST_WRITE[0] = time.time()
        if u.path not in PUBLIC_POST and not u.path.startswith("/api/auth/") and os.environ.get("HELIX_CLOUD_MODE") != "1" and not IDENT.status(load_people())["bootstrap"] \
                and not IDENT.resolve(req_token(self), load_people()):
            return self._json({"ok": False, "auth": True, "error": "请先登录（登录已过期或尚未登录）"}, 401)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path.startswith('/api/project-import/'):
                person=need_admin(self)
                # need_admin verifies privileges; resolve the actual caller for preview ownership.
                person=need_person(self)
                if u.path=='/api/project-import/zip':
                    n=int(self.headers.get('Content-Length') or 0)
                    if n<=0 or n>PI.MAX_ZIP:raise ValueError('项目ZIP不能为空，且不得超过256MB')
                    result=PI.preview_zip(P,self.rfile.read(n),q.get('name','导入项目'),person)
                elif u.path=='/api/project-import/folder':
                    body=self._body_json(20000);result=PI.preview_folder(P,str(body.get('path') or ''),person)
                elif u.path=='/api/project-import/commit':
                    body=self._body_json(20000)
                    with IDENT.store_lock:result=PI.commit(P,body.get('token'),body.get('snapshot_hash'),person,register_members,HelixLedger)
                elif u.path=='/api/project-import/cancel':
                    body=self._body_json(20000);result=PI.discard(P,body.get('token'),person)
                else:raise ValueError('项目读取操作不存在')
                return self._json({'ok':True,**result})
            if u.path == "/api/client-log":  # 界面启动自检上报，便于远程排查白屏
                data = self._body_json(100_000)
                log = ROOT / "data" / "client.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                if not log.exists() or log.stat().st_size < 500_000:
                    with log.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"ts": _now(), **{k: data.get(k) for k in ("kind", "msg", "extra", "ua", "url", "resources")}}, ensure_ascii=False) + "\n")
                return self._json({"ok": True})
            if u.path == "/api/tools/run":
                body=self._body_json(4_000_000)
                if body.get('human_run_id'):
                    person=need_person(self);ctx=Ctx(_project_id())
                    return self._json(HR.tool(ctx,person,body,ID.matches,fingerprint,P,SCI))
                return self._json(SCI.run(body))
            if u.path in ('/api/human/start','/api/human/finish'):
                person=need_person(self);ctx=Ctx(_project_id());body=self._body_json(30_000)
                event=(HR.start if u.path.endswith('/start') else HR.finish)(ctx,person,body,ID.matches,fingerprint)
                attest(ctx.ledger(),person,event,event['type'])
                return self._json({'ok':True,'run_id':event['payload']['run_id']})
            if u.path.startswith("/api/update/"):
                return self._json({"ok": True, **self._update(u.path)})
            if u.path.startswith("/api/auth/"):
                body = self._body_json(20_000)
                result = self._auth(u.path, body)
                cookie = None
                if u.path == "/api/auth/logout":
                    cookie = session_cookie("")
                elif isinstance(result, dict) and result.get("token"):
                    cookie = session_cookie(result["token"], result.get("remember", True))
                return self._json({"ok": True, **result}, cookie=cookie)
            if u.path.startswith('/api/local-team/'):
                person=need_person(self);data=self._body_json(50_000);pid=_project_id()
                LT.config(P,pid)
                if u.path=='/api/local-team/claim':
                    if set(data)-{'slot'}:raise ValueError('落座参数无效')
                    result=LT.claim(P,pid,IDENT,load_people,save_people,person,str(data.get('slot') or ''),Ctx(pid).entries())
                    CLOUD.enqueue(pid)
                    return self._json({'ok':True,**result})
                if u.path=='/api/local-team/checkin':
                    event=LT.checkin(P,pid,IDENT,load_people(),person,data,_ledger(create=True))
                elif u.path=='/api/local-team/support':
                    if set(data)-{'slot','note'}:raise ValueError('协助参数无效')
                    event=LT.support(P,pid,IDENT,load_people(),person,data,_ledger(create=True))
                else:raise ValueError('本机工作台操作不存在')
                attest(_ledger(),person,event,event['type'])
                return self._json({'ok':True,'seq':event['seq']})
            if u.path.startswith("/api/models/"):
                return self._json({"ok": True, **self._models(u.path, self._body_json(50_000))})
            if u.path.startswith("/api/runs/") or u.path == "/api/plan/freeze":
                return self._json({"ok": True, **self._runs(u.path, unmap(self._body_json(50_000), display_map()))})
            if u.path == "/api/people":
                data = self._body_json(200_000)
                with IDENT.store_lock:
                    self._people_guard(data.get("people") or [])
                    result = save_people(clean_people(data.get("people") or []))
                CLOUD.enqueue(_project_id())
                return self._json({"ok": True, "people": public_people(result)})
            if u.path == "/api/files":  # 原始 body：文件内容
                pid = _project_id()
                who = IDENT.resolve(req_token(self), load_people())
                if not who and not IDENT.status(load_people())["bootstrap"]:
                    raise ID.AuthError("添加数据文件需要先登录（文件会记在你名下）。")
                row = P.add_file(pid, q.get("name", "file"), self.rfile, int(self.headers.get("Content-Length") or 0), by=(who or {}).get("name") or q.get("by", ""), note=q.get("note", ""))
                _witness_files()
                return self._json({"ok": True, "file": row})
            if u.path == "/api/parse-tasks":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 8_000_000:
                    raise ValueError("表格太大（上限 8 MB）")
                return self._json({"ok": True, **P.parse_tasks(q.get("name", "tasks.csv"), self.rfile.read(n))})
            data = self._body_json()
            if u.path in ('/api/projects/source-preview', '/api/projects/adopt'):
                need_admin(self)
                proposal = source_proposal()
                if u.path.endswith('source-preview'):
                    return self._json({'ok': True, **{k: proposal[k] for k in ('name','task_count','source_file_count','snapshot_hash')}})
                if set(data) - {'snapshot_hash'}:
                    raise ValueError('接入只接受已读取来源，不能指定路径或账号。')
                result = SP.adopt(P, proposal, _sources.configured_roots(), str(data.get('snapshot_hash') or ''))
                p = result['project']
                HelixLedger(p['id'], root=P.ledger_root(p['id'])).ensure_genesis()
                P.set_active(p['id'])
                _witness_files()
                return self._json({'ok': True, 'id': p['id'], 'existing': result['existing'], 'warnings': apply_map(result['warnings'],display_map())})
            if u.path in UNMAP_PATHS:
                data = unmap(data, display_map())
            if u.path in ("/api/projects", "/api/projects/update"):
                need_admin(self)
            if u.path == "/api/projects":
                p, warn = P.create(data)
                register_members(p.get("members") or [])
                P.set_active(p["id"])
                HelixLedger(p["id"], root=P.ledger_root(p["id"])).ensure_genesis()
                return self._json({"ok": True, "project": apply_map(p,display_map()), "warnings": apply_map(warn,display_map())})
            if u.path == "/api/projects/update":
                p, warn = P.update(_project_id(), data)
                register_members(p.get("members") or [])
                CLOUD.enqueue(_project_id())
                return self._json({"ok": True, "project": apply_map(p,display_map()), "warnings": apply_map(warn,display_map())})
            if u.path == "/api/projects/active":
                pid = str(data.get("id") or "")
                if pid != _builtin_id() and not P.is_local(pid):
                    raise ValueError("找不到这个项目")
                P.set_active(pid)
                return self._json({"ok": True, "id": pid})
            if u.path == "/api/takeover":
                need_admin(self)
                led = _ledger(create=True)
                return self._json({"ok": True, "ledger": str(led.path)})
            if u.path == "/api/seed":
                person = need_person(self)
                led, res = _ledger(), research()
                if not AG.frozen_plan(led.entries()):
                    try:
                        freeze_plan(self, {"note": "播种时自动冻结"}, person)
                    except ID.AuthError:
                        raise ID.AuthError("第一次播种会冻结计划，需要组长登录。")
                week = int(data.get("week") or res.sprint.get("current_week") or 1)
                r = _sprint.seed_from_sprint(led, res, week=week, include_exceptions=not P.is_local(_project_id()) and bool(data.get("exceptions", True)))
                return self._json({"ok": True, "turn_id": r.get("turn_id"), "count": len(r.get("bases") or [])})
            if u.path == "/api/pair":
                e = do_pair(self, data)
                CLOUD.enqueue(_project_id())
                return self._json({"ok": True, "seq": e.get("seq")})
            if u.path == "/api/cross":
                e = do_cross(self, data)
                CLOUD.enqueue(_project_id())
                return self._json({"ok": True, "seq": e.get("seq")})
            if u.path == "/api/checkin":
                e = do_checkin(self, data)
                return self._json({"ok": True, "seq": e.get("seq"), "cloud": CLOUD.public()["enabled"]})
            if u.path == "/api/team/suggest":  # 纯计算，不写任何东西
                if data.get('mode') == 'balanced':
                    return self._json({'ok': True, **TEAM.balanced(data.get('members') or [], data.get('tasks') or [], P.assignment_locks(_project_id()) if P.is_local(_project_id()) else ())})
                return self._json({"ok": True, **TEAM.suggest(data.get("members") or [], data.get("tasks") or [], str(data.get("lead") or ""), data.get("duties") or [])})
            if u.path == "/api/cloud/config":
                need_admin(self)
                r = CLOUD.configure(str(data.get("database") or data.get("database_id") or ""), str(data.get("token") or ""), bool(data.get("enabled", True)))
                CLOUD.enqueue(_project_id())
                return self._json({"ok": True, "cloud": r})
            if u.path == "/api/cloud/disable":
                need_admin(self)
                CLOUD.disable(bool(data.get("forget")))
                return self._json({"ok": True, "cloud": CLOUD.public()})
            if u.path == "/api/cloud/sync":
                need_person(self)
                return self._json({"ok": True, **CLOUD.sync(_project_id(), force=bool(data.get("force"))), "cloud": CLOUD.public()})
            if u.path == "/api/checkpoint":
                person = need_person(self)
                led = _ledger()
                e = _service.telomere_checkpoint(led, label=str(data.get("label") or "检查点")[:80], reviewer=actor_for(person), note=str(data.get("note") or "")[:400])
                attest(led, person, e, "checkpoint")
                return self._json({"ok": True, "seq": e.get("seq")})
        except ID.AuthError as exc:
            return self._json({"ok": False, "error": str(exc), "auth": True}, 401)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc) or type(exc).__name__}, 400)
        return self._json({"ok": False, "error": "未知接口"}, 404)

    # ---------------------------------------------------------------- 0.5
    def _people_guard(self, rows: list) -> None:
        """改名册（尤其是指纹关联）等于改身份：有口令后只允许组长；引导模式放行。"""
        need_admin(self)

    def _update(self, path: str) -> dict:
        person = need_person(self)
        if path == "/api/update/check":
            r = UPD.check_now(CLOUD)
            return {**r, "status": UPD.status()}
        if path == "/api/update/apply":
            if not (UPD.UPD / "staged").exists():
                raise ValueError("没有已下载并校验的更新")
            return {"restarting": request_restart("manual"), "status": UPD.status()}
        if path == "/api/update/import":
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > UPD.MAX_PACKAGE + 1024 * 1024:
                raise ValueError("更新包为空或过大")
            staged = UPD.import_package(self.rfile.read(n))
            return {"staged": staged, "status": UPD.status()}
        d = self._body_json(20_000)
        ok, why = ID.is_admin(person, load_people(), IDENT)
        if path == '/api/update/github':
            import github_updates as GH
            r = GH.configure({k: v for k, v in d.items() if k in ('repository', 'token', 'proxy', 'use_saved_login', 'forget')})
            AUTO_UPDATE.poke()
            return {'github': r, 'status': UPD.status()}
        if path == "/api/update/settings":
            if not ok:  # 组员只能改本机的接收方式，发布相关设置只归组长
                d = {k: v for k, v in d.items() if k in ("auto", "interval_min", "feeds")}
            r = {"settings": UPD.save_settings(d), "status": UPD.status()}
            AUTO_UPDATE.poke()
            return r
        if not ok:
            raise ID.AuthError(why)
        if path == "/api/update/publish":
            rel = UPD.make_release(str(d.get("notes") or ""), str(d.get("version") or ""))
            m, raw, sent, errors = rel["manifest"], rel["raw"], ["本机"], []
            st = UPD.settings()
            if st["publish_dir"]:
                try:
                    UPD.publish_folder(st["publish_dir"], m, raw); sent.append("共享文件夹")
                except Exception as exc:
                    errors.append(f"共享文件夹：{exc}")
            if st["publish_github"]:
                try:
                    from github_updates import GitHubFeed
                    GitHubFeed().publish(m, raw); sent.append("GitHub")
                except Exception as exc:
                    errors.append(f"GitHub：{exc}")
            return {"manifest": {k: m[k] for k in ("version", "build", "file", "size", "sha256", "notes")}, "sent": sent, "errors": errors, "status": UPD.status()}
        raise ValueError("未知接口")

    def _auth(self, path: str, d: dict) -> dict:
        if path=='/api/auth/invite':
            need_admin(self)
            if set(d)-{'person_id'}:raise ID.AuthError('邀请参数无效')
            return IDENT.issue_member_invite(load_people(),str(d.get('person_id') or ''))
        if path=='/api/auth/claim':
            if set(d)-{'join','name','pin','pin_confirm'}:raise ID.AuthError('设置不能指定角色或其他人的身份')
            if not isinstance(d.get('pin'),str) or d.get('pin')!=d.get('pin_confirm'):raise ID.AuthError('两次输入的密码不一致')
            return IDENT.claim_member(load_people(),str(d.get('join') or ''),str(d.get('name') or ''),d['pin'],save_people)
        if path=='/api/auth/bootstrap':
            with IDENT.store_lock:
                if not IDENT.status(load_people())['bootstrap']:raise ID.AuthError('组长账号已设置，请使用原账号登录')
                if set(d)-{'username','pin','pin_confirm'}:raise ID.AuthError('设置字段无效')
                name=str(d.get('username') or '').strip();pin=d.get('pin')
                if not 1<=len(name)<=24 or not all(c.isalnum() or c in ' _-.' for c in name):raise ID.AuthError('账号请使用1至24个文字、数字或下划线')
                if not isinstance(pin,str) or not 8<=len(pin)<=128 or pin!=d.get('pin_confirm'):raise ID.AuthError('密码需8至128位，且两次输入一致')
                person=next((p for p in load_people() if (p.get('username') or p['name'])==name),None)
                if person and (person.get('role')!='lead' or not person.get('active',True)):raise ID.AuthError('已有成员身份不能改成组长，请换一个账号名')
                if not person:register_members([{'name':name,'role':'lead'}])
                person=next(p for p in load_people() if (p.get('username') or p['name'])==name)
                IDENT.set_pin(load_people(),person['id'],pin)
                return IDENT.login(load_people(),person['id'],pin)
        if path == "/api/auth/register":
            if set(d) - {"name", "pin", "pin_confirm"}:
                raise ID.AuthError("注册不能指定角色、人员 ID、指纹或其他管理字段。")
            if not isinstance(d.get("pin"), str) or d.get("pin") != d.get("pin_confirm"):
                raise ID.AuthError("两次输入的密码不一致。")
            with IDENT.store_lock:
                raw_names = raw_people(build_raw())
                for project in P.list_local():
                    p = P.load(project['id']) or {}
                    raw_names.extend(m.get('name','') for m in p.get('members',[]))
                    raw_names.extend(n for t in p.get('tasks',[]) for n in (t.get('owner'),t.get('cross_reviewer')) if n)
                mapping, _ = name_map(raw_names, load_people())
                return IDENT.register(load_people(), str(d.get("name") or ""), d["pin"], save_people,
                                      reserved_names=[*raw_names, *mapping.values()],anonymous=_cfg().get('anonymous_members',True))
        people = load_people()
        if path == "/api/auth/login":
            remember=d.get('remember',True) is not False
            result=IDENT.login_name(people,str(d.get('username') or ''),str(d.get('pin') or ''),str(d.get('portal') or 'member'),remember=remember) if 'username' in d else IDENT.login(people,str(d.get('person_id') or ''),str(d.get('pin') or ''),remember=remember)
            if LT.enabled(P.load(_project_id())):result['next']='/#/team'
            return result
        if path == "/api/auth/logout":
            IDENT.logout(req_token(self))
            return {}
        if path == "/api/auth/set-pin":
            how = IDENT.set_pin(people, str(d.get("person_id") or ""), str(d.get("new_pin") or ""), current_pin=str(d.get("current_pin") or ""),
                                admin_id=str(d.get("admin_id") or ""), admin_pin=str(d.get("admin_pin") or ""))
            return {"how": how}
        if path == "/api/auth/clear":
            need_admin(self)
            IDENT.clear(str(d.get("person_id") or ""))
            return {}
        raise ValueError("未知接口")

    def _models(self, path: str, d: dict) -> dict:
        need_admin(self)
        if path == "/api/models/provider":
            p = REG.upsert(d)
            return {"provider": {k: p.get(k) for k in ("id", "name", "base_url", "style", "models", "last_test")}}
        if path == "/api/models/remove":
            REG.remove(str(d.get("id") or ""))
            return {}
        if path == "/api/models/refresh":
            return {"models": REG.refresh_models(str(d.get("id") or ""))}
        if path == "/api/models/test":
            return {"test": REG.test(str(d.get("id") or ""), str(d.get("model") or ""))}
        if path == "/api/models/roles":
            REG.set_roles(d.get("roles") or {}, d.get("budget"), d.get("allow_mock"), d.get("rules"))
            return REG.public()
        raise ValueError("未知接口")

    def _runs(self, path: str, d: dict) -> dict:
        if path == "/api/plan/freeze":
            return freeze_plan(self, d)
        person = need_person(self)
        rt = runtime()
        who = actor_for(person)
        if path == "/api/runs/start":
            return {"run_id": rt.start(who, str(d.get("task_id") or ""))}
        if path == "/api/runs/summary":
            return {"run_id": rt.start(who, "", kind="summary", week=int(d.get("week") or 0) or None)}
        if path == "/api/runs/resume":
            rt.resume(str(d.get("run_id") or ""))
            return {}
        if path == "/api/runs/ack":
            rt.acknowledge(who, str(d.get("run_id") or ""), str(d.get("note") or ""))
            return {}
        if path == "/api/runs/cancel":
            rt.cancel(who, str(d.get("run_id") or ""), str(d.get("note") or ""))
            return {}
        raise ValueError("未知接口")

    def _sse(self, interval: float):
        """实时推送：心跳 + 数据变化 + 版本变化。客户端断开（含 Windows 10053）安静结束，不再刷错误日志。"""
        interval = min(max(interval, .5), 10)
        tok = req_token(self)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last, beat = None, 0.0
        try:
            hello = {"ts": _now(), "version": RELEASE_VERSION, "build": UPD.current_version().get("build") or RELEASE_BUILD}
            self.wfile.write(f"retry: 3000\nevent: RUN_STARTED\ndata: {json.dumps(hello)}\n\n".encode())
            self.wfile.flush()
            start = time.time()
            while time.time() - start < 3600 and not SHUTDOWN.is_set():
                fp = _fingerprint()
                if fp != last:
                    if last is not None:
                        self.wfile.write(f"event: STATE_SNAPSHOT\ndata: {json.dumps({'fp': fp, 'ts': _now()})}\n\n".encode())
                        self.wfile.flush()
                    last = fp
                if time.time() - beat > 15:
                    if tok and not IDENT.resolve(tok, load_people()):
                        self.wfile.write(b"event: AUTH_EXPIRED\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    self.wfile.write(f"event: HEARTBEAT\ndata: {json.dumps({'ts': _now()})}\n\n".encode())
                    self.wfile.flush()
                    beat = time.time()
                SHUTDOWN.wait(interval)
            if SHUTDOWN.is_set():
                self.wfile.write(b"event: RESTARTING\ndata: {}\n\n")
                self.wfile.flush()
        except (OSError, ValueError):
            return


import threading as _threading  # noqa: E402
SHUTDOWN = _threading.Event()
LAST_WRITE = [0.0]


def _staged_ready(info: dict) -> None:
    """后台下载并校验好更新后：空闲 90 秒（没人在写入）再自动重启应用。"""
    if not UPD.settings()["auto"] or _IDLE_WAIT.is_set():
        return
    _IDLE_WAIT.set()
    def wait_idle():
        while time.time() - LAST_WRITE[0] < 90:
            time.sleep(10)
        request_restart("auto-update")
    _threading.Thread(target=wait_idle, daemon=True).start()


_IDLE_WAIT = _threading.Event()


AUTO_UPDATE = UPD.AutoUpdater(lambda: CLOUD, _staged_ready)


class HelixServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)) or (isinstance(exc, OSError) and getattr(exc, "winerror", 0) in (10053, 10054)):
            return  # 浏览器关页或刷新导致的断开，属于正常情况
        super().handle_error(request, client_address)


def request_restart(reason: str = "update") -> bool:
    """有守护进程时：通知页面、稍后以退出码 75 退出，由守护进程应用更新并重启。"""
    if os.environ.get("HELIX_TEST") == "1":
        return False
    supervised = os.environ.get("HELIX_SUPERVISED") == "1"
    if supervised:
        # Reload the supervisor as well: old 3.1 processes keep the old update
        # allowlist in memory and otherwise cannot apply native desktop payloads.
        try:
            import subprocess
            launcher = Path(__file__).resolve().parent.parent.parent / 'portable_launcher.py'
            py = Path(sys.executable)
            if (py.parent / 'pythonw.exe').is_file():
                py = py.parent / 'pythonw.exe'
            log = UPD.DATA / 'supervisor-refresh.log'
            with log.open('ab') as out:
                subprocess.Popen([str(py), str(launcher), '--refresh-supervisor'], cwd=launcher.parent,
                                 creationflags=(0x08000000 | 0x00000008 | 0x00000200) if os.name == 'nt' else 0,
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True)
            return True
        except Exception as exc:
            _say(f"无法重启桌面更新服务：{exc}")
            return False
    if not supervised:
        # 没有守护进程（例如组员安装版用 helix.ps1 启动）：交给独立的小助手进程
        # 等本进程退出 → 应用更新 → 重新启动 → 45 秒内不健康就回滚。
        try:
            UPD.spawn_restart_helper(os.getpid(), int(os.environ.get("HELIX_PORT") or _cfg().get("port") or 18772),
                                     sys.executable, Path(__file__).resolve())
        except Exception as exc:  # noqa: BLE001
            _say(f"无法启动更新助手：{exc}")
            return False
    def later():
        SHUTDOWN.set()
        time.sleep(2.5)
        os._exit(UPD.RESTART_CODE if supervised else 0)
    _threading.Thread(target=later, daemon=True).start()
    return True


def main() -> None:
    port = int(os.environ.get("HELIX_PORT") or _cfg().get("port") or 18772)
    try:
        srv = HelixServer(("127.0.0.1", port), H)
    except OSError as exc:  # 端口被占用：通常是已经在运行
        _say(f"端口 {port} 已被占用（Helix 可能已在运行）：{exc}")
        sys.exit(0)
    _say(f"Helix Harness {RELEASE_VERSION} | http://127.0.0.1:{port} | 内核 {KERNEL or KERNEL_ERR} | 账本 {_ledger_root() or '未找到（预览模式）'} | 名册 {PEOPLE}")
    try:
        if CLOUD._load().get("enabled"):
            CLOUD.enqueue(_project_id())  # 启动时补推上次未完成的 Notion 同步
    except Exception:
        pass
    AUTO_UPDATE.start()
    try:
        if os.environ.get("HELIX_TEST") != "1" and os.environ.get("HELIX_SUPERVISED") != "1":
            st = UPD.state()
            if st.get("pending_verify"):  # 由更新助手重启后：能跑到这里就说明新版本可以启动
                pass
            elif (UPD.UPD / "staged" / "manifest.json").is_file():
                _staged_ready(st.get("staged") or {})
    except Exception:
        pass
    srv.serve_forever()


def _say(msg: str) -> None:
    try:
        print(msg, flush=True)
    except Exception:  # 无控制台 / 编码不支持时不影响服务
        pass


if __name__ == "__main__":
    main()
