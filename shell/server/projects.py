"""Helix Shell 0.4：本机项目（多项目、计划、数据文件、账本位置）。

只写 Helix Shell 自己的 data/ 目录；永远不写 research_v4 或 Workbench 2.00 的账本。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import team as TEAM
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROJ = DATA / "projects"
ACTIVE = DATA / "active.json"
MAX_FILE = 64 * 1024 * 1024
CEILINGS = ["EXPLORATORY_ONLY", "HYPOTHESIS_SUPPORTED", "VALIDATED_FINDING"]
DEFAULT_RED_LINES = [
    "缺失 = UNKNOWN，不能当 0",
    "主核与交叉核必须是两位不同的真人",
    "AI 写的内容必须经真人配对才算数",
    "结论不能超过本项目设定的结论上限",
    "PASS / REJECTED / OUT_OF_DOMAIN 必须附原始文件、DOI、PMID 或 SHA",
    "不事后改标准挑赢家；权重和阈值要先登记",
]
DEFAULT_DUTIES = [
    {"key": "lead", "label": "主导", "desc": "定方向、拍板、对外汇报"},
    {"key": "data", "label": "数据", "desc": "整理数据、清洗、统计与作图"},
    {"key": "lit", "label": "文献", "desc": "检索文献、读原文、核对出处"},
    {"key": "dock", "label": "对接", "desc": "分子对接 / 计算建模（可改名，例如“联络”）"},
    {"key": "liaison", "label": "外联", "desc": "联系老师、合作方、实验平台"},
    {"key": "write", "label": "写作", "desc": "写报告、论文、申请材料"},
]
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _txt(v, n=400) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v or "")).strip()[:n]


def _person(v) -> str:
    return re.sub(r'["\\<>\x00-\x1f]', "", str(v or "")).strip()[:24]


def safe_id(pid: str) -> str:
    return _SAFE_ID.sub("_", str(pid or ""))[:64]


def pdir(pid: str) -> Path:
    return PROJ / safe_id(pid)


def is_local(pid: str) -> bool:
    return (pdir(pid) / "project.json").is_file()


def load(pid: str) -> dict | None:
    try:
        return json.loads((pdir(pid) / "project.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def list_local() -> list[dict]:
    out = []
    if PROJ.is_dir():
        for d in sorted(PROJ.iterdir()):
            p = load(d.name) if d.is_dir() else None
            if p:
                out.append({"id": p["id"], "name": p.get("name") or p["id"], "kind": "local", "created_at": p.get("created_at")})
    return out


def active_id(default: str) -> str:
    try:
        pid = json.loads(ACTIVE.read_text(encoding="utf-8")).get("id") or default
    except Exception:
        return default
    return pid if pid == default or is_local(pid) else default


def set_active(pid: str) -> None:
    _write(ACTIVE, {"id": pid, "at": now()})


def ledger_root(pid: str) -> Path:
    """Helix Shell 自己管理的账本位置：data/projects/<id>/helix/<id>/ledger.jsonl"""
    return pdir(pid) / "helix"


# ------------------------------------------------------------------ 计划
def _iso(v) -> str:
    s = str(v or "").strip()[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return ""


def clean_weeks(rows, start: str = "", n: int = 0) -> list[dict]:
    out = []
    for i, w in enumerate((rows or [])[:26]):
        if not isinstance(w, dict):
            continue
        out.append({"week": i + 1, "start": _iso(w.get("start")), "end": _iso(w.get("end")), "theme": _txt(w.get("theme"), 40) or f"第 {i + 1} 周"})
    if not out and n:
        out = [{"week": i + 1, "start": "", "end": "", "theme": f"第 {i + 1} 周"} for i in range(min(int(n), 26))]
    base = _iso(start)
    for w in out:  # 缺日期时按开始日期顺排，每周 7 天
        if base and not w["start"]:
            w["start"] = (date.fromisoformat(base) + timedelta(days=7 * (w["week"] - 1))).isoformat()
        if w["start"] and not w["end"]:
            w["end"] = (date.fromisoformat(w["start"]) + timedelta(days=6)).isoformat()
    return out


def clean_duties(rows) -> list[dict]:
    """职能目录：每项 key（英文小写，内部用）+ label（显示名）+ desc。None = 默认目录。"""
    if rows is None:
        return [dict(d) for d in DEFAULT_DUTIES]
    out, keys, labels = [], set(), set()
    for i, r in enumerate(rows if isinstance(rows, (list, tuple)) else []):
        if isinstance(r, str):
            r = {"label": r}
        if not isinstance(r, dict):
            continue
        label = _txt(r.get("label") or r.get("name") or r.get("key"), 12)
        if not label or label in labels:
            continue
        key = str(r.get("key") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_]{1,16}", key):
            key = f"c{i + 1}"
        while key in keys:
            key += "x"
        keys.add(key); labels.add(label)
        out.append({"key": key, "label": label, "desc": _txt(r.get("desc"), 80)})
        if len(out) >= 16:
            break
    return out


def duty_key(v, duties: list[dict]) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    for d in duties or []:
        if s.lower() == d["key"] or s == d["label"]:
            return d["key"]
    return ""


def duty_problems(p: dict) -> list[str]:
    """分工提醒：职能有任务没人负责 / 主核不具备该职能 / 成员没有分配职能。只提醒，不阻断。"""
    duties = p.get("duties") or []
    if not duties:
        return []
    label = {d["key"]: d["label"] for d in duties}
    has = {m["name"]: set(m.get("duties") or []) for m in p.get("members") or []}
    out = []
    used = {t.get("duty") for t in p.get("tasks") or [] if t.get("duty")}
    for k in [d["key"] for d in duties if d["key"] in used]:
        if not any(k in s for s in has.values()):
            out.append(f"「{label[k]}」有任务但没有成员负责")
    # 组长的统筹/审计职能允许按周轮值给组员（例如 W1-Q…W4-Q 轮流），不算分工错误
    lead_duties = {k for m in p.get("members") or [] if m.get("role") == "lead" for k in (m.get("duties") or [])}
    for t in p.get("tasks") or []:
        k, o = t.get("duty"), t.get("owner")
        if k and o in has and k not in has[o] and k not in lead_duties:
            out.append(f"{t['task_id']} 属于「{label.get(k, k)}」，主核 {o} 没有这项职能")
    for n, s in has.items():
        if not s:
            out.append(f"{n} 还没有分配职能")
    return out[:20]


def clean_tasks(rows, weeks: list[dict], duties: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    by_week = {w["week"]: w for w in weeks}
    out, seen, warn = [], set(), []
    counters: dict[int, int] = {}
    for r in (rows or [])[:2000]:
        if not isinstance(r, dict):
            continue
        deliverable = _txt(r.get("deliverable"), 300)
        if not deliverable:
            continue
        try:
            wk = int(str(r.get("week") or "1").strip().lstrip("Ww第").rstrip("周") or 1)
        except ValueError:
            wk = 1
        wk = max(1, min(wk, len(weeks) or 1))
        counters[wk] = counters.get(wk, 0) + 1
        tid = _SAFE_ID.sub("-", _txt(r.get("task_id"), 32)) or f"W{wk}-{counters[wk]:02d}"
        while tid in seen:
            tid += "b"
        seen.add(tid)
        w = by_week.get(wk, {})
        t = {"task_id": tid, "week": wk, "start": _iso(r.get("start")) or w.get("start", ""), "end": _iso(r.get("end")) or w.get("end", ""),
             "owner": _person(r.get("owner")), "cross_reviewer": _person(r.get("cross_reviewer")), "track": _txt(r.get("track"), 40) or w.get("theme", ""),
             "deliverable": deliverable, "acceptance": _txt(r.get("acceptance"), 300), "status": _txt(r.get("status"), 24).upper() or "PENDING",
             "depends_on": _deps(r.get("depends_on")), "duty": duty_key(r.get("duty"), duties or [])}
        if 'execution_mode' in r:
            t['execution_mode'] = r['execution_mode'] if r['execution_mode'] in ('human','assisted','agent') else 'assisted'
        if 'human_work' in r:
            t['human_work'] = _txt(r.get('human_work'), 500)
        for k in ('workspace_slot','task_kind'):
            if k in r:t[k]=_txt(r[k],32)
        if 'estimated_minutes' in r:t['estimated_minutes']=max(0,min(600,int(r['estimated_minutes'])))
        for k in ('support_slots','evidence_refs'):
            if k in r:t[k]=[_txt(v,80) for v in r[k]][:20]
        if r.get("duty") and not t["duty"] and duties is not None:
            warn.append(f"{tid} 的职能“{_txt(r.get('duty'), 12)}”不在职能目录里，已留空")
        if not t["owner"]:
            warn.append(f"{tid} 没有主核")
        elif t["owner"] == t["cross_reviewer"]:
            warn.append(f"{tid} 主核与交叉核是同一人")
        out.append(t)
    problems = dep_problems(out)
    if problems:  # P0：不再静默删除科研前置条件；整份计划不保存，逐条告诉用户
        raise ValueError("计划没有保存，前置任务有问题：" + "；".join(problems) + "。请改正后再保存。")
    return out, warn


def dep_problems(tasks: list[dict]) -> list[str]:
    """检查依赖：指向不存在的任务、指向自己、成环。只报告，不修改。"""
    ids = {t["task_id"] for t in tasks}
    out = []
    for t in tasks:
        for d in t.get("depends_on") or []:
            if d == t["task_id"]:
                out.append(f"{t['task_id']} 依赖了它自己")
            elif d not in ids:
                out.append(f"{t['task_id']} 的前置任务 {d} 不在本计划里")
    graph = {t["task_id"]: [d for d in t.get("depends_on") or [] if d in ids and d != t["task_id"]] for t in tasks}
    color: dict[str, int] = {}
    cycles: list[str] = []

    def visit(n: str, path: list[str]) -> None:
        color[n] = 1
        for d in graph.get(n, []):
            if color.get(d) == 1:
                cyc = path[path.index(d):] + [d] if d in path else [n, d]
                cycles.append(" → ".join(cyc))
            elif not color.get(d):
                visit(d, path + [d])
        color[n] = 2

    for n in graph:
        if not color.get(n):
            visit(n, [n])
    out += [f"依赖成环：{c}" for c in cycles]
    return out


def _deps(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        items = v
    else:
        items = re.split(r"[,，;；、\s]+", str(v or ""))
    return [_SAFE_ID.sub("-", str(x).strip())[:32] for x in items if str(x).strip()][:12]


def clean_project(data: dict, old: dict | None = None) -> tuple[dict, list[str]]:
    p = dict(old or {})
    for k, n in (("name", 60), ("target", 120), ("question", 600), ("status", 40)):
        if k in data:
            p[k] = _txt(data.get(k), n)
    if not p.get("name"):
        raise ValueError("请填写项目名称")
    if "claim_ceiling" in data or not p.get("claim_ceiling"):
        c = str(data.get("claim_ceiling") or "EXPLORATORY_ONLY").upper()
        p["claim_ceiling"] = c if c in CEILINGS else "EXPLORATORY_ONLY"
    if "red_lines" in data or "red_lines" not in p:
        lines = data.get("red_lines")
        if isinstance(lines, str):
            lines = lines.splitlines()
        p["red_lines"] = [_txt(x, 200) for x in (lines if lines is not None else DEFAULT_RED_LINES) if _txt(x, 200)][:30]
    if "duties" in data or "duties" not in p:
        p["duties"] = clean_duties(data.get("duties"))
    dkeys = {d["key"] for d in p["duties"]}
    if "duties" in data:  # 删掉的职能，同时从成员和任务里清掉
        p["members"] = [{**m, "duties": [k for k in m.get("duties") or [] if k in dkeys]} for m in p.get("members") or []]
        p["tasks"] = [{**t, "duty": t.get("duty") if t.get("duty") in dkeys else ""} for t in p.get("tasks") or []]
    if "members" in data:
        mem = []
        for m in data.get("members") or []:
            name = _person(m.get("name") if isinstance(m, dict) else m)
            if name and name not in [x["name"] for x in mem]:
                du = []
                for x in (m.get("duties") if isinstance(m, dict) else None) or []:
                    k = duty_key(x, p["duties"])
                    if k and k not in du:
                        du.append(k)
                mem.append({"name": name, "role": (m.get("role") if isinstance(m, dict) else "") or "both", "duties": du[:8],
                            **({'active': bool(m['active'])} if isinstance(m,dict) and 'active' in m else {})})
        p["members"] = mem[:60]
    if "weeks" in data or "start" in data or "n_weeks" in data:
        p["weeks"] = clean_weeks(data.get("weeks"), data.get("start") or "", int(data.get("n_weeks") or 0))
    warn: list[str] = []
    if "tasks" in data or "weeks" in data:
        p["tasks"], warn = clean_tasks(data.get("tasks", p.get("tasks")), p.get("weeks") or [], p["duties"])
    if len([m for m in p.get("members", [])]) < 2:
        warn.append("人机协同需要至少 2 位真人（主核与交叉核不能是同一人）")
    warn += duty_problems(p)
    p.setdefault("status", "进行中")
    p.setdefault("weeks", [])
    p.setdefault("tasks", [])
    p["updated_at"] = now()
    return p, warn


def create(data: dict) -> tuple[dict, list[str]]:
    p, warn = clean_project(data)
    warn += rebalance(p, data=data)
    stamp = datetime.now().strftime("%Y%m%d")
    p["id"] = f"P{stamp}-{secrets.token_hex(2)}"
    p["created_at"] = now()
    p["schema"] = "helix.project.v1"
    _write(pdir(p["id"]) / "project.json", p)
    return p, warn


def update(pid: str, data: dict) -> tuple[dict, list[str]]:
    old = load(pid)
    if not old:
        raise ValueError("这个项目不是本机项目，不能在这里编辑计划")
    p, warn = clean_project(data, old)
    warn += rebalance(p, old, data)
    p["id"] = old["id"]
    _write(pdir(pid) / "project.json", p)
    return p, warn


def assignment_locks(pid: str) -> set[str]:
    """已播种、打卡或实操的任务保持原负责人；损坏的账本不能按无记录处理。"""
    path = ledger_root(pid) / safe_id(pid) / 'ledger.jsonl'
    if not path.exists():
        return set()
    locked = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            event = json.loads(line)
            tid = (event.get('payload') or {}).get('task_id')
            if tid:
                locked.add(str(tid))
    return locked


def rebalance(p: dict, old: dict | None = None, data: dict | None = None) -> list[str]:
    old, data = old or {}, data or {}
    # A local project with explicit seats keeps role ownership when people join.
    # Ordinary projects and cloud retain the generic balancing behavior.
    if (p.get('local_team') or {}).get('enabled') and os.environ.get('HELIX_CLOUD_MODE')!='1':
        slots={s['id']:s for s in p['local_team']['seats']}
        for t in p.get('tasks',[]):
            s=slots.get(t.get('workspace_slot'))
            if s:
                t['owner']=s['member_ref']
                t['cross_reviewer']=slots[s['reviewer_slot']]['member_ref']
        return ['本机岗位分工保持不变；加入账号只关联岗位身份']
    def signature(members):
        return sorted((m['name'],m.get('role') or 'both',tuple(sorted(m.get('duties') or []))) for m in TEAM.participants(members))
    members = p.get('members') or []
    names = {m['name'] for m in TEAM.participants(members)}
    membership_changed = bool(old) and signature(old.get('members')) != signature(members)
    incomplete = any(t.get('owner') not in names or t.get('cross_reviewer') not in names or t.get('owner') == t.get('cross_reviewer') for t in p.get('tasks', []))
    if not (membership_changed or data.get('rebalance_team') is True or incomplete):
        return []
    locks = assignment_locks(p['id']) if p.get('id') else set()
    original = {t['task_id']: t for t in old.get('tasks', [])}
    for t in p.get('tasks', []):
        prev = original.get(t['task_id'], {})
        if t['task_id'] in locks or str(prev.get('status') or 'PENDING').upper() not in {'PENDING','TODO','NOT_STARTED',''}:
            locks.add(t['task_id'])
            for key in ('owner', 'cross_reviewer'):
                if key in prev:
                    t[key] = prev[key]
    result = TEAM.balanced(members, p.get('tasks') or [], locks)
    for row in result['rows']:
        t = p['tasks'][row['i']]
        t.update(owner=row['owner'], cross_reviewer=row['cross_reviewer'])
    p['team_assignment'] = {k: result[k] for k in ('mode','member_count','load','protected_count','notes')}
    return [f"已按 {result['member_count']} 人（含组长）调整未开始任务；保留 {result['protected_count']} 条已有工作"] + result['notes']


def tasks_csv(p: dict) -> str:
    """转成与 CURRENT_SPRINT_TASKS 同列的 CSV，交给内核 sources.parse_sprint 解析。"""
    buf = io.StringIO()
    cols = ["task_id", "week", "start", "end", "owner", "cross_reviewer", "research_track", "deliverable", "acceptance", "status"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for t in p.get("tasks") or []:
        w.writerow({**{k: t.get(k, "") for k in cols if k != "research_track"}, "research_track": t.get("track", "")})
    return buf.getvalue()


def sprint_view(p: dict, parsed: dict, today: date) -> dict:
    """以项目自己的周安排为准（没有任务的周也显示），补上当前周与剩余天数。"""
    for task in parsed.get('tasks') or []:
        original = next((t for t in p.get('tasks') or [] if t['task_id']==task['task_id']),{})
        task.update({k:original[k] for k in ('execution_mode','human_work') if k in original})
    weeks = []
    for w in p.get("weeks") or []:
        n = sum(1 for t in p.get("tasks") or [] if t["week"] == w["week"])
        weeks.append({"week": w["week"], "start": w["start"], "end": w["end"], "track": w["theme"], "track_label": w["theme"], "tasks": n})
    cur = None
    for w in weeks:
        try:
            if date.fromisoformat(w["start"]) <= today <= date.fromisoformat(w["end"]):
                cur = w["week"]
        except ValueError:
            pass
    if cur is None and weeks:
        try:
            cur = weeks[0]["week"] if today < date.fromisoformat(weeks[0]["start"]) else weeks[-1]["week"]
        except ValueError:
            cur = weeks[0]["week"]
    left = None
    w = next((x for x in weeks if x["week"] == cur), None)
    if w:
        try:
            left = max((date.fromisoformat(w["end"]) - today).days, 0)
        except ValueError:
            pass
    return {**parsed, "weeks": weeks or parsed.get("weeks") or [], "current_week": cur, "days_left": left,
            "red_lines": p.get("red_lines") or [], "members": sorted(set(parsed.get("members") or []) | {m["name"] for m in p.get("members") or []})}


# ------------------------------------------------------------------ 数据文件
def files(pid: str) -> list[dict]:
    try:
        owned = json.loads((pdir(pid) / "files.json").read_text(encoding="utf-8")).get("files", [])
    except Exception:
        owned = []
    import source_project
    linked = source_project.files(load(pid) or {})
    seen = {f['sha256'] for f in owned}
    return owned + [f for f in linked if f['sha256'] not in seen]


def clean_filename(name: str) -> str:
    name = Path(str(name or "").replace("\\", "/")).name
    name = _BAD_NAME.sub("_", name).strip(" .")[:120]
    return name or "file"


def add_file(pid: str, name: str, stream, length: int, by: str = "", note: str = "") -> dict:
    if length <= 0:
        raise ValueError("文件是空的")
    if length > MAX_FILE:
        raise ValueError(f"文件太大（上限 {MAX_FILE // 1024 // 1024} MB）；大文件请放在原处，只登记路径和 SHA")
    name = clean_filename(name)
    d = pdir(pid) / "files"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (".upload-" + secrets.token_hex(4))
    h, left = hashlib.sha256(), length
    with tmp.open("wb") as f:
        while left > 0:
            chunk = stream.read(min(1 << 20, left))
            if not chunk:
                break
            h.update(chunk)
            f.write(chunk)
            left -= len(chunk)
    if left:
        tmp.unlink(missing_ok=True)
        raise ValueError("上传中断，请重试")
    sha = h.hexdigest()
    lst = files(pid)
    hit = next((x for x in lst if x["sha256"] == sha), None)
    if hit:
        tmp.unlink(missing_ok=True)
        return {**hit, "duplicate": True}
    stored = f"{sha[:12]}-{name}"
    tmp.replace(d / stored)
    row = {"name": name, "stored": stored, "sha256": sha, "bytes": length, "added_at": now(), "added_by": _person(by), "note": _txt(note, 200)}
    lst = [f for f in lst if not f.get('source_path')]
    lst.append(row)
    _write(pdir(pid) / "files.json", {"schema": "helix.files.v1", "files": lst})
    return row


def file_path(pid: str, sha: str) -> Path | None:
    row = next((x for x in files(pid) if x["sha256"].startswith(str(sha)) and len(str(sha)) >= 12), None)
    if not row:
        return None
    if row.get('source_path'):
        import source_project
        return source_project.file_path(load(pid) or {}, row)
    p = pdir(pid) / "files" / row["stored"]
    return p if p.is_file() else None


# ------------------------------------------------------------------ 文件预览（只读，给“看原文”用）
INLINE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm"}
PLAIN_EXT = {".txt", ".md", ".json", ".jsonl", ".sdf", ".smi", ".smiles", ".mol", ".mol2", ".pdb", ".fasta", ".fa", ".log", ".yaml", ".yml", ".xml", ".html", ".htm", ".tex", ".bib", ".ris", ".py", ".r", ".ini", ".cfg"}


def _decode(data: bytes) -> str:
    """文本解码：BOM 优先（UTF-8 / UTF-16），再试 UTF-8、GB18030；都不行才替换坏字节。"""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _cell(c) -> str:
    """单元格转文字：保留日期里的时间、整数不带 .0、长小数不丢精度。"""
    if c is None:
        return ""
    if isinstance(c, bool):
        return "TRUE" if c else "FALSE"
    if hasattr(c, "hour") and hasattr(c, "date"):  # datetime
        return c.isoformat(sep=" ")[:19] if (c.hour or c.minute or c.second) else c.date().isoformat()
    if hasattr(c, "isoformat"):  # date / time
        return c.isoformat()
    if isinstance(c, float):
        return str(int(c)) if c.is_integer() and abs(c) < 1e15 else format(c, ".15g")  # 与 Excel 显示的 15 位有效数字一致
    return str(c)


def _trim(rows: list[list[str]]) -> list[list[str]]:
    """去掉每行末尾的空单元格和表格末尾的空行（Excel 常把格式化过的空白区域也算进来）。"""
    out = []
    for r in rows:
        r = list(r)
        while r and r[-1] == "":
            r.pop()
        out.append(r)
    while out and not out[-1]:
        out.pop()
    return out


def _csv_rows(text: str, ext: str = ".csv") -> list[list[str]]:
    if ext == ".tsv":
        delim = "\t"
    else:
        head = text[:8192]
        try:
            delim = csv.Sniffer().sniff(head, delimiters=",\t;|").delimiter
        except csv.Error:
            delim = ","
        if delim != "," and head.count(",") > head.count(delim) * 2:
            delim = ","
    return list(csv.reader(io.StringIO(text), delimiter=delim))


def _xlsx_sheets(src, *, full: bool, max_rows: int, max_sheets: int = 8) -> tuple[list[dict], bool, int]:
    """读取 Excel：显示计算后的值；只有文件里没有缓存值（例如程序生成、未在 Excel 里打开过）时才显示公式本身。"""
    import openpyxl  # noqa: PLC0415
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    fwb = None
    sheets, truncated = [], False
    try:
        n_all = len(wb.worksheets)
        for idx, ws in enumerate(wb.worksheets if full else wb.worksheets[:max_sheets]):
            try:
                ws.reset_dimensions()  # 有些软件导出的表格尺寸记录错误，会导致只读到一部分
            except Exception:  # noqa: BLE001
                pass
            rows, total, missing = [], 0, False
            for row in ws.iter_rows(values_only=True):
                total += 1
                if len(rows) < max_rows:
                    cells = [_cell(c) for c in row]
                    rows.append(cells)
                    missing = missing or any(c is None for c in row)
            if missing:
                if fwb is None:
                    if hasattr(src, "seek"):
                        src.seek(0)
                    fwb = openpyxl.load_workbook(src, read_only=True, data_only=False)
                fws = fwb.worksheets[idx]
                try:
                    fws.reset_dimensions()
                except Exception:  # noqa: BLE001
                    pass
                for r_i, frow in enumerate(fws.iter_rows(values_only=True)):
                    if r_i >= len(rows):
                        break
                    for c_i, v in enumerate(frow):
                        if isinstance(v, str) and v.startswith("=") and c_i < len(rows[r_i]) and rows[r_i][c_i] == "":
                            rows[r_i][c_i] = v
            rows = _trim(rows)
            sheets.append({"name": ws.title, "rows": rows, "total": len(rows) if total <= max_rows else total})
            truncated = truncated or total > max_rows
        return sheets, truncated or (not full and n_all > max_sheets), n_all
    finally:
        wb.close()
        if fwb is not None:
            fwb.close()


def _docx_text(path: Path, full: bool) -> str:
    """按文档顺序读取 Word：段落与表格交替保留；制表符、换行保留；表格每行用“ | ”分隔单元格。"""
    import zipfile  # noqa: PLC0415
    from xml.etree import ElementTree as ET  # noqa: PLC0415
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def para(p) -> str:
        buf = []
        for el in p.iter():
            tag = el.tag
            if tag == W + "t":
                buf.append(el.text or "")
            elif tag == W + "tab":
                buf.append("\t")
            elif tag in (W + "br", W + "cr"):
                buf.append("\n")
        return "".join(buf)

    def block(el, out: list[str]) -> None:
        for ch in el:
            if ch.tag == W + "p":
                out.append(para(ch))
            elif ch.tag == W + "tbl":
                for tr in ch.iter(W + "tr"):
                    cells = []
                    for tc in tr.findall(W + "tc"):
                        sub: list[str] = []
                        block(tc, sub)
                        cells.append(" ".join(x.strip() for x in sub if x.strip()))
                    out.append(" | ".join(cells))
                out.append("")
            elif ch.tag in (W + "sdt", W + "sdtContent", W + "customXml", W + "body"):
                block(ch, out)

    out: list[str] = []
    with zipfile.ZipFile(str(path)) as z:
        names = z.namelist()
        parts = ["word/document.xml"]
        if full:
            parts += sorted(n for n in names if re.match(r"word/(header|footer|footnotes|endnotes|comments)\d*\.xml$", n))
        for part in parts:
            if part not in names:
                continue
            root = ET.fromstring(z.read(part))
            body = root.find(W + "body")
            block(body if body is not None else root, out)
    return "\n".join(out).strip("\n")


def preview(path: Path, max_rows: int = 200, max_chars: int = 200000, *, full: bool = False) -> dict:
    """把已登记文件转成可在页面里阅读的内容。只读；读不出来就如实说明。"""
    path = Path(path)
    if full:
        max_rows, max_chars = 2**31 - 1, 2**31 - 1
    name = re.sub(r"^[0-9a-f]{12,16}-(?=.)", "", path.name)
    ext = path.suffix.lower()
    size = path.stat().st_size
    out = {"name": name, "ext": ext, "bytes": size, "inline": ext in INLINE_TYPES, "kind": "binary", "readable": False, "truncated": False, "parser": ""}
    try:
        if ext in (".csv", ".tsv"):
            rows = _trim(_csv_rows(_decode(path.read_bytes()), ext))
            out.update(kind="table", readable=True, parser="csv", sheets=[{"name": name, "rows": rows[:max_rows], "total": len(rows)}], truncated=len(rows) > max_rows)
        elif ext in (".xlsx", ".xlsm"):
            sheets, trunc, n_all = _xlsx_sheets(str(path), full=full, max_rows=max_rows)
            out.update(kind="table", readable=True, parser="openpyxl", sheets=sheets, truncated=trunc, sheet_count=n_all)
        elif ext == ".pdf":
            out.update(kind="pdf", parser="pypdf")
            from pypdf import PdfReader  # noqa: PLC0415
            rd = PdfReader(str(path))
            pages, n = [], 0
            for pg in rd.pages:
                try:
                    t = pg.extract_text() or ""
                except Exception:  # noqa: BLE001  单页损坏不影响其他页
                    t = ""
                if n + len(t) > max_chars:
                    out["truncated"] = True
                    t = t[: max(0, max_chars - n)]
                pages.append(t); n += len(t)
                if out["truncated"]:
                    break
            out.update(pages=pages, page_count=len(rd.pages), readable=any(x.strip() for x in pages))
            if not out["readable"]:
                out["error"] = "这份 PDF 没有文字层（可能是扫描件），只能看原版页面，不能逐字核验"
        elif ext == ".docx":
            text = _docx_text(path, full)
            out.update(kind="text", readable=bool(text.strip()), parser="docx", text=text[:max_chars], truncated=len(text) > max_chars)
        elif ext in INLINE_TYPES:
            out.update(kind="image", parser="image")
        elif ext in PLAIN_EXT or size <= 2 * 1024 * 1024:
            data = path.read_bytes()[: max_chars * 4]
            utf16 = data[:2] in (b"\xff\xfe", b"\xfe\xff")
            if b"\x00" in data[:4096] and ext not in PLAIN_EXT and not utf16:
                return out
            text = _decode(data)
            out.update(kind="text", readable=bool(text.strip()), parser="text", text=text[:max_chars], truncated=len(text) > max_chars or size > len(data))
    except Exception as exc:  # noqa: BLE001
        out.update(readable=False, error=f"读取失败：{type(exc).__name__}: {str(exc)[:160]}")
    out['full_read'] = bool(full and out['readable'] and not out['truncated'])
    return out


# ------------------------------------------------------------------ 导入任务表
ALIASES = {
    "task_id": ["task_id", "id", "编号", "任务编号", "任务id"],
    "week": ["week", "周", "周次", "第几周"],
    "start": ["start", "开始", "开始日期"],
    "end": ["end", "结束", "截止", "截止日期", "due"],
    "owner": ["owner", "主核", "负责人", "primary", "primary_reviewer"],
    "cross_reviewer": ["cross_reviewer", "交叉核", "复核", "复核人", "cross"],
    "track": ["research_track", "track", "主题", "方向", "track_label"],
    "deliverable": ["deliverable", "交付物", "任务", "内容", "task", "工作内容"],
    "acceptance": ["acceptance", "验收", "验收标准"],
    "depends_on": ["depends_on", "depends", "依赖", "前置", "前置任务", "上游"],
    "status": ["status", "状态"],
    "duty": ["duty", "职能", "分工", "工种", "类别", "duty_label"],
}


def _norm_head(h) -> str:
    h = str(h or "").replace("\ufeff", "").strip().lower()
    h = re.sub(r"[\s_\-]+", "_", h)
    return h.strip("_*：:")


def _match_col(h: str, names: list[str]) -> bool:
    if h in names:
        return True
    base = re.sub(r"[（(\[【].*?[）)\]】]", "", h).strip("_")  # “交付物（deliverable）”“截止(日期)”
    if base in names:
        return True
    inner = re.findall(r"[（(\[【](.*?)[）)\]】]", h)
    return any(x.strip().lower() in names for x in inner)


def _all_sheets_from_bytes(name: str, data: bytes) -> list[list[list[str]]]:
    if name.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl  # noqa: PLC0415,F401
        except ImportError as exc:
            raise ValueError("这台电脑的 Python 没有 openpyxl，读不了 Excel；请在 Excel 里“另存为 CSV（UTF-8）”再导入") from exc
        sheets, _, _ = _xlsx_sheets(io.BytesIO(data), full=True, max_rows=20000)
        return [sh["rows"] for sh in sheets]
    if data[:2] not in (b"\xff\xfe", b"\xfe\xff"):
        for enc in ("utf-8-sig", "gb18030"):
            try:
                data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("无法识别文件编码，请另存为 UTF-8 CSV")
    return [_csv_rows(_decode(data), ".tsv" if name.lower().endswith(".tsv") else ".csv")]


def _rows_from_bytes(name: str, data: bytes) -> list[list[str]]:
    return _all_sheets_from_bytes(name, data)[0]


MAX_TASK_ROWS = 2000


def parse_tasks(name: str, data: bytes) -> dict:
    """读取任务表：自动找含“交付物/任务”列的工作表和表头行（前 10 行内），表头允许带括号说明和空格。"""
    best = None
    for rows in _all_sheets_from_bytes(name, data):
        rows = [r for r in rows if any(str(c).strip() for c in r)]
        for hi, raw in enumerate(rows[:10]):
            header = [_norm_head(c) for c in raw]
            col = {}
            for key, names in ALIASES.items():
                for i, h in enumerate(header):
                    if h and key not in col and i not in col.values() and _match_col(h, names):
                        col[key] = i
            if "deliverable" in col:
                if best is None or len(col) > len(best[2]):
                    best = (rows, hi, col)
                break
    if best is None:
        raise ValueError("没找到“交付物/任务/内容（deliverable）”这一列；请确认表头在前 10 行内")
    rows, hi, col = best
    head = rows[hi]
    body = rows[hi + 1:]
    out = []
    for r in body[:MAX_TASK_ROWS]:
        rec = {k: (str(r[i]).strip() if i < len(r) else "") for k, i in col.items()}
        if any(rec.values()):
            out.append(rec)
    unknown = [head[i] for i in range(len(head)) if i not in col.values() and str(head[i]).strip()]
    res = {"rows": out, "columns": {k: head[i] for k, i in col.items()}, "ignored": unknown}
    if len(body) > MAX_TASK_ROWS:
        res["warning"] = f"任务表有 {len(body)} 行，只读取了前 {MAX_TASK_ROWS} 行"
    return res
