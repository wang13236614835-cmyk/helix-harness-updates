"""0.6 组内协作：任务打卡 + 统筹模式重分配。

打卡（task.checkin）是真人账本事件：
- 只有这条任务的主核 / 交叉核本人（口令会话）能打卡，组长也不能代打；
- 每次打卡记：状态（已开始 / 进行中 / 已完成 / 受阻）、进度 0–100、简短说明、可选的“成果文件”（必须是本项目已登记文件的 SHA）；
- 打卡只说明“我做到哪了”，不改变审核结论：任务是否通过仍由主核判定 + 交叉核复核决定。

统筹模式重分配（suggest）：组长做统筹，尽量少打扰组员——
- 主核：优先有该职能的人；同一条依赖链尽量交给同一人（少交接）；同一周的任务尽量分散；
- 交叉核：默认由统筹（组长）兜底；任务主核就是组长时，交给同职能 / 负载最低的另一人；
- 只给建议，不直接改计划；保存计划后需要组长重新冻结。
"""
from __future__ import annotations

import re
from typing import Any

STATES = {"started": "已开始", "doing": "进行中", "done": "已完成", "blocked": "受阻"}
EVENTS = {"task.checkin": True}
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def register_events(ledger_mod) -> None:
    ledger_mod.EVENT_TYPES = frozenset(set(ledger_mod.EVENT_TYPES) | set(EVENTS))
    ledger_mod.HUMAN_ONLY = frozenset(set(ledger_mod.HUMAN_ONLY) | {k for k, h in EVENTS.items() if h})


def _txt(v, n: int) -> str:
    return _CTRL.sub("", str(v or "")).strip()[:n]


def build_checkin(data: dict, task: dict, files: list[dict], role: str) -> dict:
    """校验并整理一次打卡（不写账本）。role = owner / cross。"""
    state = str(data.get("state") or "").strip()
    if state not in STATES:
        raise ValueError("请选择打卡状态：已开始 / 进行中 / 已完成 / 受阻")
    try:
        progress = int(round(float(data.get("progress") if data.get("progress") not in (None, "") else (100 if state == "done" else 0))))
    except (TypeError, ValueError):
        raise ValueError("进度要填 0–100 的数字")
    progress = max(0, min(100, progress))
    if state == "done":
        progress = 100
    elif progress >= 100:
        progress = 95  # 没选“已完成”就不显示 100%
    note = _txt(data.get("note"), 400)
    if state == "blocked" and len(note) < 4:
        raise ValueError("受阻时请写清楚卡在哪里（至少 4 个字），方便统筹协调")
    known = {f.get("sha256"): f for f in files or []}
    outs = []
    for raw in list(data.get("files") or [])[:10]:
        sha = str(raw or "").lower().removeprefix("sha256:").strip().split(" ")[0]
        f = known.get(sha) or next((x for s, x in known.items() if sha and len(sha) >= 12 and str(s).startswith(sha)), None)
        if not f:
            raise ValueError("成果文件要先在“数据与文件”页登记（对不上：" + sha[:16] + "）")
        outs.append({"sha256": f["sha256"], "name": f.get("name") or ""})
    return {"task_id": task["task_id"], "state": state, "progress": progress, "note": note, "files": outs, "as": role,
            "week": task.get("week"), "blocker": state == "blocked"}


def fold_checkins(entries: list[dict], keep: int = 12) -> dict[str, dict]:
    """每个任务：最新一次打卡、主核/交叉核各自最新、历史（新→旧，最多 keep 条）。"""
    out: dict[str, dict] = {}
    for e in entries:
        if e.get("type") != "task.checkin":
            continue
        p = e.get("payload") or {}
        tid = str(p.get("task_id") or "")
        if not tid:
            continue
        row = {"seq": e.get("seq"), "ts": e.get("ts"), "actor": e.get("actor"), "state": p.get("state"), "progress": p.get("progress"),
               "note": p.get("note") or "", "files": p.get("files") or [], "as": p.get("as") or "owner"}
        r = out.setdefault(tid, {"latest": None, "owner": None, "cross": None, "history": [], "count": 0})
        r["latest"] = row
        r["owner" if row["as"] == "owner" else "cross"] = row
        r["history"].insert(0, row)
        del r["history"][keep:]
        r["count"] += 1
    return out


def summary(checkins: dict, tasks: list[dict], week: int | None = None) -> dict:
    ts = [t for t in tasks if week is None or t.get("week") == week]
    c = {k: 0 for k in STATES}
    c["none"] = 0
    prog = []
    for t in ts:
        l = (checkins.get(t.get("task_id")) or {}).get("owner") or (checkins.get(t.get("task_id")) or {}).get("latest")
        if not l:
            c["none"] += 1
            prog.append(0)
        else:
            c[l["state"]] = c.get(l["state"], 0) + 1
            prog.append(int(l.get("progress") or 0))
    return {"tasks": len(ts), **c, "progress": round(sum(prog) / len(prog)) if prog else 0}


# ------------------------------------------------------------------ 统筹模式重分配
def _deps(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x for x in re.split(r"[,，;；、\s]+", str(v or "")) if x]


def suggest(members: list[dict], tasks: list[dict], lead: str = "", duties_list: list[dict] | None = None) -> dict:
    """返回 {"lead": 名字, "rows": [{i, task_id, owner, cross_reviewer, why}], "load": {名字: {primary, cross}}, "notes": [...]}"""
    mem = [m for m in members or [] if str(m.get("name") or "").strip() and (m.get("role") or "both") != "observer"]
    names = [str(m["name"]).strip() for m in mem]
    if len(set(names)) < 2:
        raise ValueError("至少需要 2 位真人（主核与交叉核不能是同一人）")
    label = {d.get("key"): d.get("label") or d.get("key") for d in duties_list or []}
    duties = {str(m["name"]).strip(): set(m.get("duties") or []) for m in mem}
    role = {str(m["name"]).strip(): m.get("role") or "both" for m in mem}
    if not lead or lead not in names:
        lead = next((n for n in names if role[n] == "lead"), "") or next((n for n in names if "lead" in duties[n]), "") or names[0]
    can_primary = [n for n in names if role[n] != "cross"] or names
    can_cross = [n for n in names if role[n] != "primary"] or names
    load = {n: {"primary": 0.0, "cross": 0.0} for n in names}
    week_load: dict[tuple, int] = {}
    owner_of: dict[str, str] = {}
    idx = {str(t.get("task_id") or ""): i for i, t in enumerate(tasks) if t.get("task_id")}

    def order_key(i):
        t = tasks[i]
        depth, seen, stack = 0, set(), list(_deps(t.get("depends_on")))
        while stack:  # 依赖越深越靠后，保证先排上游
            d = stack.pop()
            if d in seen or d not in idx:
                continue
            seen.add(d)
            depth += 1
            stack.extend(_deps(tasks[idx[d]].get("depends_on")))
        return (int(t.get("week") or 1), depth, i)

    rows, notes = [], []
    for i in sorted(range(len(tasks)), key=order_key):
        t = tasks[i]
        if not str(t.get("deliverable") or "").strip():
            continue
        duty, wk = t.get("duty") or "", int(t.get("week") or 1)
        with_duty = [n for n in can_primary if duty and duty in duties[n]]
        pool = with_duty or can_primary
        # 统筹本人尽量不当主核（除非只有他有这项职能，或这是“主导”类任务）
        if lead in pool and len(pool) > 1 and duty != "lead":
            pool = [n for n in pool if n != lead]
        ups = [owner_of[d] for d in _deps(t.get("depends_on")) if d in owner_of]

        def score(n):
            s = load[n]["primary"] + 0.5 * load[n]["cross"] + 1.5 * week_load.get((n, wk), 0)
            if n in ups:
                s -= 1.2  # 同一条依赖链交给同一人，少一次交接
            return (s, names.index(n))

        owner = min(pool, key=score)
        why = []
        why.append(f"有「{label.get(duty, duty)}」职能" if duty and owner in with_duty else ("没有人有这项职能，按负载分" if duty else "未定职能，按负载分"))
        if owner in ups:
            why.append("接上游任务，少一次交接")
        if lead in can_cross and lead != owner:
            cross = lead
            why.append("统筹兜底复核")
        else:
            rest = [n for n in can_cross if n != owner] or [n for n in names if n != owner]
            same = [n for n in rest if duty and duty in duties[n]]
            cross = min(same or rest, key=lambda n: (load[n]["cross"] + 0.5 * load[n]["primary"], names.index(n)))
            why.append("同职能互核" if cross in same else "负载最低的人复核")
        load[owner]["primary"] += 1
        load[cross]["cross"] += 1
        week_load[(owner, wk)] = week_load.get((owner, wk), 0) + 1
        if t.get("task_id"):
            owner_of[str(t["task_id"])] = owner
        rows.append({"i": i, "task_id": t.get("task_id") or "", "owner": owner, "cross_reviewer": cross, "why": "；".join(why),
                     "changed": owner != t.get("owner") or cross != t.get("cross_reviewer")})
    heavy = [n for n in names if load[n]["primary"] > max(2, 1.6 * (sum(v["primary"] for v in load.values()) / max(1, len(names))))]
    if heavy:
        notes.append("负载偏高：" + "、".join(heavy) + "（可在表里手动再调）")
    if load[lead]["cross"] > 6:
        notes.append(f"统筹 {lead} 复核 {int(load[lead]['cross'])} 条，偏多；可把部分交叉核改给同职能的人")
    return {"lead": lead, "rows": sorted(rows, key=lambda r: r["i"]), "load": {n: {k: int(v) for k, v in load[n].items()} for n in names}, "notes": notes}


def participants(members: list[dict]) -> list[dict]:
    """项目参与者包含组长；观察者、停用者和重复姓名不计入人数。"""
    out, seen = [], set()
    for m in members or []:
        name = str(m.get('name') or '').strip()
        if name and name not in seen and m.get('active', True) and m.get('role') != 'observer':
            out.append({**m, 'name': name})
            seen.add(name)
    return out


def balanced(members: list[dict], tasks: list[dict], locked_task_ids=()) -> dict:
    """按实际人数、职能和每周负载分配。组长同样执行，复核轮换；已开始的保持原分工。"""
    mem = participants(members)
    names = [m['name'] for m in mem]
    if len(names) < 2:
        raise ValueError('至少需要 2 位参与者，才能安排独立复核；请先补充成员')
    primary = [m['name'] for m in mem if m.get('role') != 'cross']
    cross_pool = [m['name'] for m in mem if m.get('role') != 'primary']
    if not primary or not cross_pool or not any(a != b for a in primary for b in cross_pool):
        raise ValueError('当前角色无法组成执行人与另一位复核人，请调整成员角色')
    duties = {m['name']: set(m.get('duties') or []) for m in mem}
    load = {n: {'primary': 0, 'cross': 0} for n in names}
    weeks, owner_of, rows, notes = {}, {}, [], []
    locked = set(locked_task_ids)
    pending = {'', 'PENDING', 'TODO', 'NOT_STARTED'}

    def week_load(n, wk):
        return weeks.setdefault(wk, {}).setdefault(n, {'primary': 0, 'cross': 0})

    def count(t, owner, cross):
        wk = int(t.get('week') or 1)
        for n, role in ((owner, 'primary'), (cross, 'cross')):
            if n in load:
                load[n][role] += 1
                week_load(n, wk)[role] += 1
        owner_of[t.get('task_id')] = owner

    def is_locked(t):
        return t.get('task_id') in locked or str(t.get('status') or '').upper() not in pending

    for i, t in enumerate(tasks):
        if is_locked(t):
            count(t, t.get('owner'), t.get('cross_reviewer'))
            rows.append({'i': i, 'task_id': t.get('task_id', ''), 'owner': t.get('owner', ''),
                         'cross_reviewer': t.get('cross_reviewer', ''), 'changed': False,
                         'locked': True, 'why': '已开始、已播种或已有记录，保留原分工'})
            if t.get('owner') not in names or t.get('cross_reviewer') not in names:
                notes.append(f"{t.get('task_id')} 的原负责人已离开项目，请单独办理交接")

    # 同周按前置关系先分上游；职能优先，再比较本周和全程负载。
    indexed = {t.get('task_id'): t for t in tasks}
    def depth(t, seen=None):
        seen = set(seen or ()) | {t.get('task_id')}
        return max([1 + depth(indexed[d], seen) for d in _deps(t.get('depends_on')) if d in indexed and d not in seen] or [0])

    for i in sorted(range(len(tasks)), key=lambda i: (int(tasks[i].get('week') or 1), depth(tasks[i]), i)):
        t = tasks[i]
        if is_locked(t) or not str(t.get('deliverable') or '').strip():
            continue
        wk, duty = int(t.get('week') or 1), t.get('duty') or ''
        pool = [n for n in primary if duty and duty in duties[n]] or primary
        upstream = [owner_of[d] for d in _deps(t.get('depends_on')) if d in owner_of]
        owner = min(pool, key=lambda n: (week_load(n, wk)['primary'], load[n]['primary'],
                         .25 * week_load(n, wk)['cross'] - .4 * (n == t.get('owner')) - .3 * (n in upstream), names.index(n)))
        rest = [n for n in cross_pool if n != owner]
        if not rest:
            # 执行角色优先限制不能破坏独立复核；换一个能被独立复核的执行人。
            pool = [n for n in pool if any(c != n for c in cross_pool)]
            if not pool:
                raise ValueError(f"{t.get('task_id')} 的职能和角色无法安排独立复核")
            owner = min(pool, key=lambda n: (week_load(n,wk)['primary'], load[n]['primary'], names.index(n)))
            rest = [n for n in cross_pool if n != owner]
        same = [n for n in rest if duty and duty in duties[n]]
        cross = min(same or rest, key=lambda n: (week_load(n,wk)['cross'], load[n]['cross'],
                        .25 * week_load(n,wk)['primary'] - .4 * (n == t.get('cross_reviewer')), names.index(n)))
        count(t, owner, cross)
        rows.append({'i': i, 'task_id': t.get('task_id', ''), 'owner': owner, 'cross_reviewer': cross,
                     'changed': owner != t.get('owner') or cross != t.get('cross_reviewer'), 'locked': False,
                     'why': f'按 {len(names)} 人的职能与每周负载分配，另一位成员独立复核'})
    return {'mode': 'balanced', 'member_count': len(names), 'lead': next((m['name'] for m in mem if m.get('role') == 'lead'), ''),
            'rows': sorted(rows,key=lambda r:r['i']), 'load': load, 'week_load': weeks, 'notes': notes,
            'protected_count': sum(r.get('locked',False) for r in rows)}


__all__ = ["STATES", "register_events", "build_checkin", "fold_checkins", "summary", "suggest", "balanced", "participants"]
