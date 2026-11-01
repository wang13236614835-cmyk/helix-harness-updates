"""0.5 智能体运行时：六个角色真正执行，账本是唯一事实来源，可阻断、可恢复、可复现。

一次“运行”= 对一个真实任务依次执行：
  plan（解旋酶）→ extract（抽取者，只读已登记文件，摘录逐字核验）→ draft（聚合酶）
  → critique（校对酶，必须异源）→ propose（写成碱基，等真人主核 + 交叉核）
每一步：先记 run.step.requested → 调模型 → 输出存 runs/<run_id>/<step>.json 并把 SHA 记入 run.step.done。
- 明确失败（限流/5xx/断网未发出）→ run.step.failed，可自动重试 2 次；
- 超时 / 中途断电 = 结果未知（可能已计费）→ 不自动重试，必须由真人“确认”后才能恢复；
- 进程重启后从账本折叠出每一步状态，已完成的步骤直接复用（校验 SHA），从断点继续；
- 运行前强制检查：计划已冻结且未改动、上游任务 Gate 已通过、预算未超、批评者与起草者不同家族、
  没有未配对的 AI 碱基（复制叉阻滞）。任何一条不满足都拒绝运行，并说明原因。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evidence import quote_in, read_text
from models import ModelError, ROLES, digest, family

STEPS = ["plan", "extract", "draft", "critique", "propose"]
STEP_ROLE = {"plan": "helicase", "extract": "extractor", "draft": "drafter", "critique": "critic", "summary": "ligase"}
STEP_LABEL = {"plan": "解旋 · 规划", "extract": "抽取 · 只读文件", "draft": "起草", "critique": "异源互审", "propose": "写成碱基 · 等真人", "summary": "连接 · 周汇总"}
RUN_EVENTS = {"run.started": True, "run.step.requested": False, "run.step.done": False, "run.step.failed": False, "run.completed": False,
              "run.blocked": False, "run.acknowledged": True, "run.cancelled": True, "plan.frozen": True, "write.attested": True}
PROMPT_VERSION = "helix-agents.v1"
_ACTIVE: set[str] = set()
_ALOCK = threading.Lock()


class RunError(RuntimeError):
    pass


def register_events(ledger_mod) -> None:
    """把 0.5 事件类型登记进内核账本（不改 vendor 文件）。"""
    ledger_mod.EVENT_TYPES = frozenset(set(ledger_mod.EVENT_TYPES) | set(RUN_EVENTS))
    ledger_mod.HUMAN_ONLY = frozenset(set(ledger_mod.HUMAN_ONLY) | {k for k, h in RUN_EVENTS.items() if h})


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ------------------------------------------------------------------ plan / gates
def plan_hash(tasks: list[dict], red_lines: list, ceiling: str) -> str:
    rows = [{k: t.get(k) for k in ("task_id", "week", "deliverable", "acceptance", "owner", "cross_reviewer", "depends_on")} for t in tasks]
    for row, task in zip(rows,tasks):
        row.update({k:task[k] for k in ('execution_mode','human_work') if k in task})
    return digest({"tasks": sorted(rows, key=lambda r: str(r["task_id"])), "red_lines": list(red_lines or []), "ceiling": ceiling or ""})


def frozen_plan(entries: list[dict]) -> dict | None:
    rows = [e for e in entries if e.get("type") == "plan.frozen"]
    return {**rows[-1]["payload"], "by": rows[-1]["actor"], "ts": rows[-1]["ts"], "seq": rows[-1]["seq"]} if rows else None


def task_states(state, tasks: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for t in tasks:
        tid = t["task_id"]
        tb = state.task_bases(tid)
        seeds = [b for b in tb if b.get("kind") == "task"]
        crossed = lambda b: (b.get("cross") or {}).get("agree") is True  # noqa: E731
        if any(b.get("decision") == "PASS" and crossed(b) for b in seeds):
            st, why = "passed", "主核 PASS + 交叉核一致"
        elif any(b.get("decision") in ("REJECTED", "OUT_OF_DOMAIN") and crossed(b) for b in seeds):
            st, why = "failed", "Gate 失败：" + next(b.get("decision") for b in seeds if b.get("decision") in ("REJECTED", "OUT_OF_DOMAIN"))
        elif any(b.get("status") == "disputed" for b in tb):
            st, why = "disputed", "交叉核不一致，等两人对齐"
        elif not seeds:
            st, why = "unseeded", "还没有播种到账本"
        else:
            st, why = "open", "进行中"
        ai = [b for b in tb if b.get("kind") != "task"]
        out[tid] = {"state": st, "why": why, "bases": len(tb), "ai_open": sum(1 for b in ai if b.get("status") == "open"), "deps": list(t.get("depends_on") or [])}
    for tid, row in out.items():
        bad = [d for d in row["deps"] if (out.get(d) or {}).get("state") != "passed"]
        row["blocked_by"] = bad
        if bad and row["state"] not in ("passed", "failed"):
            fails = [d for d in bad if (out.get(d) or {}).get("state") == "failed"]
            row["state"], row["why"] = "blocked", ("上游 Gate 失败：" if fails else "等上游 Gate 通过：") + "、".join(bad)
    return out


# ------------------------------------------------------------------ fold
def fold_runs(entries: list[dict]) -> dict[str, dict]:
    runs: dict[str, dict] = {}
    for e in entries:
        t, p = e.get("type"), e.get("payload") or {}
        if not str(t).startswith("run."):
            continue
        rid = str(p.get("run_id") or "")
        if t == "run.started":
            runs[rid] = {"run_id": rid, "kind": p.get("kind", "task"), "task_id": p.get("task_id"), "week": p.get("week"), "started_by": e.get("actor"), "ts": e.get("ts"),
                         "steps": {}, "status": "running", "plan_hash": p.get("plan_hash"), "roles": p.get("roles") or {}, "events": 0, "usage": {"input": 0, "output": 0}, "calls": 0}
            continue
        r = runs.get(rid)
        if r is None:
            continue
        r["events"] += 1
        step = p.get("step")
        if t == "run.step.requested":
            if r["status"] != "cancelled":
                r["status"] = "running"; r.pop("blocked", None)
            r["steps"][step] = {"state": "requested", "model": p.get("model"), "provider": p.get("provider"), "attempt": p.get("attempt", 1), "ts": e.get("ts"), "input_hash": p.get("input_hash")}
        elif t == "run.step.done":
            r["steps"].setdefault(step, {}).update({"state": "done", "output_sha": p.get("output_sha"), "usage": p.get("usage") or {}, "latency_ms": p.get("latency_ms"), "notes": p.get("notes") or [], "done_at": e.get("ts")})
            u = p.get("usage") or {}
            r["usage"]["input"] += int(u.get("input") or 0); r["usage"]["output"] += int(u.get("output") or 0)
            r["calls"] += 1 if p.get("model") or u else 0
        elif t == "run.step.failed":
            r["steps"].setdefault(step, {}).update({"state": "unknown" if p.get("outcome_unknown") else "failed", "error": p.get("error"), "kind": p.get("kind"), "failed_at": e.get("ts")})
            r["calls"] += 1 if p.get("sent") else 0
        elif t == "run.acknowledged":
            if step in r["steps"]:
                r["steps"][step].update({"state": "acknowledged", "ack_by": e.get("actor"), "ack_note": p.get("note")})
        elif t == "run.blocked":
            r["status"], r["blocked"] = "blocked", p.get("reason")
        elif t == "run.cancelled":
            r["status"], r["cancel_note"] = "cancelled", p.get("note")
        elif t == "run.completed":
            r.update(status="completed", turn_id=p.get("turn_id"), base_ids=p.get("base_ids") or [], refs=p.get("refs") or {}, output=p.get("output"), output_sha=p.get("output_sha"))
    with _ALOCK:
        active = set(_ACTIVE)
    for r in runs.values():
        if r["status"] in ("completed", "cancelled"):
            continue
        st = [s.get("state") for s in r["steps"].values()]
        if r["run_id"] in active:
            r["status"] = "running"
        elif "requested" in st or "unknown" in st:
            r["status"] = "unknown"  # 进程中断或超时：结果未知，等真人确认
        elif r["status"] == "blocked":
            pass
        elif "failed" in st:
            r["status"] = "failed"
        else:
            r["status"] = "interrupted"
    return runs


def usage_today(runs: dict[str, dict], entries: list[dict]) -> dict:
    day = _today()
    tok = calls = 0
    for e in entries:
        if e.get("type") in ("run.step.done", "run.step.failed") and str(e.get("ts", "")).startswith(day):
            u = (e.get("payload") or {}).get("usage") or {}
            tok += int(u.get("input") or 0) + int(u.get("output") or 0)
            calls += 1 if (e["type"] == "run.step.done" and (e["payload"].get("model"))) or (e["type"] == "run.step.failed" and e["payload"].get("sent")) else 0
    return {"day": day, "tokens": tok, "calls": calls}


# ------------------------------------------------------------------ runtime
_SYS = """你是 Helix Harness 里的一个科研协作角色。硬规则（违反即作废）：
- 只能使用本消息给出的材料；没有依据就写 UNKNOWN 或提出问题，绝不编造数据、文献或数值；
- 绝不宣布 Gate 通过、替真人签字、把计算预测说成实验事实、输出正式 Top / 榜单；
- 主张上限：{ceiling}；红线：
{red}
- 只输出一个 JSON 对象，不要多余文字。"""

_STEP_PROMPT = {
    "plan": """STEP=plan
任务：{task}
请把这个任务拆成 2–6 个需要证据回答的小问题，并指出风险。
输出：{{"questions":[{{"id":"Q1","text":"...","why":"..."}}],"risks":["..."]}}""",
    "extract": """STEP=extract
小问题：{questions}
下面是本项目已登记的原始文件（只读，每个文件以 <<<FILE sha256 文件名 开头）。
请为每个小问题找出“逐字摘录”（必须原样复制文件里的连续文字，≤200 字；找不到就不要写）。
输出：{{"cards":[{{"id":"E1","file_sha":"文件的 sha256","quote":"逐字摘录","supports":"Q1","note":"这段说明了什么"}}]}}
{files}""",
    "draft": """STEP=draft
任务：{task}
小问题：{questions}
已核验的证据卡（摘录已逐字比对原文）：{cards}
请起草 3–8 条“碱基”：每条只说一件可被真人单独判断的事；有依据的写 evidence（证据卡 id），没有依据的写成 gap 或 question。
confidence 是你对“真人会接受”的真实估计（0–1），不要都给高分。
输出：{{"summary":"一句话","bases":[{{"kind":"action|gap|conflict|question","text":"...","rationale":"...","confidence":0.5,"evidence":["E1"]}}]}}""",
    "critique": """STEP=critique
你是独立的批评者（与起草者不同模型家族）。逐条找错、找反证、查越界与红线。
任务：{task}
证据卡：{cards}
待审要点（index 从 1 开始）：{drafts}
verdict 只能是 support / weak / contradict / overclaim。
输出：{{"reviews":[{{"index":1,"verdict":"support","issue":"","suggest":""}}],"missing":["还缺什么证据"]}}""",
    "summary": """STEP=summary
请把下面已由真人配对的结果整理成一份周汇总草稿（Markdown，中文，≤800 字）。只能整理，不得新增结论；
每条后面保留碱基编号；开头写明“草稿，未经真人确认不得外发”。
周次：{week}
已配对：{pairs}""",
}


class Runtime:
    """ctx 提供：ledger() / entries() / state() / tasks() / task(tid) / files() / file_abspath(row) /
    ceiling() / red_lines() / run_dir(run_id) / registry / service / proofread"""

    def __init__(self, ctx):
        self.ctx = ctx

    # -------------------------------------------------------------- checks
    def preflight(self, task_id: str, *, kind: str = "task") -> tuple[list[str], dict]:
        c, why = self.ctx, []
        try:
            c.ledger()
        except Exception as exc:
            return [str(exc)], {}
        entries = c.entries()
        state = c.state()
        tasks = c.tasks()
        runs = fold_runs(entries)
        if kind == "task":
            t = c.task(task_id)
            if not t:
                return ["找不到这个任务。"], {}
            ts = task_states(state, tasks).get(task_id) or {}
            if ts.get("state") in ("passed", "failed"):
                why.append(f"这个任务已经{'通过' if ts['state'] == 'passed' else '判定失败'}，不再起草。")
            if ts.get("blocked_by"):
                why.append(ts["why"] + "（Gate 阻断：上游没通过，下游不能开工）")
            fz = frozen_plan(entries)
            cur = plan_hash(tasks, c.red_lines(), c.ceiling())
            if not fz:
                why.append("计划还没有冻结：请组长在“运行”页点“冻结计划”（把任务表的指纹写进账本），之后才能让智能体执行。")
            elif fz.get("plan_hash") != cur:
                why.append("计划在冻结后被改动过：请确认改动并重新冻结计划。")
            if state.fork_stalled:
                why.append(f"还有 {len(state.open_gc)} 条 AI 碱基等真人配对（复制叉阻滞）：先在收件箱配对。")
        busy = [r for r in runs.values() if r["status"] in ("running", "unknown")]
        if any(r["status"] == "unknown" for r in busy):
            why.append("有一次运行结果未知（超时或中断，可能已计费）：先在“运行”页由真人确认。")
        elif busy:
            why.append("已有一个运行在进行中，等它结束。")
        roles = {}
        steps = ["summary"] if kind == "summary" else STEPS[:4]
        for step in steps:
            role = STEP_ROLE[step]
            try:
                p, m = c.registry.resolve_role(role)
                roles[role] = {"provider": p["id"], "model": m, "family": family(m), "mock": p.get("style") == "mock"}
            except ModelError as exc:
                why.append(str(exc))
        same = kind == "task" and roles.get("drafter") and roles.get("critic") and roles["drafter"]["family"] == roles["critic"]["family"]
        if same and not c.registry.load()["rules"].get("allow_same_family"):
            why.append(f"批评者与起草者是同一模型家族（{roles['critic']['family']}）：异源互审要求不同家族。请在模型工坊给批评者换一家。")
        b = c.registry.load()["budget"]
        u = usage_today(runs, entries)
        if u["calls"] >= b["max_calls_per_day"] or u["tokens"] >= b["max_tokens_per_day"]:
            why.append(f"今日预算已用完（{u['calls']} 次调用 / {u['tokens']} tokens）：到模型工坊调整预算，或明天再跑。不会偷偷换便宜模型。")
        return why, roles

    # -------------------------------------------------------------- lifecycle
    def start(self, person_raw: str, task_id: str = "", *, kind: str = "task", week: int | None = None, background: bool = True) -> str:
        why, roles = self.preflight(task_id, kind=kind)
        if why:
            raise RunError("；".join(why))
        led = self.ctx.ledger()
        rid = "run_" + time.strftime("%Y%m%d%H%M%S") + "_" + hashlib.sha1(f"{task_id}{time.time()}".encode()).hexdigest()[:6]
        tasks = self.ctx.tasks()
        led.append("run.started", {"run_id": rid, "kind": kind, "task_id": task_id, "week": week, "roles": roles, "prompt_version": PROMPT_VERSION,
                                   "plan_hash": plan_hash(tasks, self.ctx.red_lines(), self.ctx.ceiling())}, actor=person_raw, actor_kind="human")
        self._launch(rid, background)
        return rid

    def resume(self, rid: str, background: bool = True) -> None:
        r = fold_runs(self.ctx.entries()).get(rid)
        if not r:
            raise RunError("找不到这个运行。")
        if r["status"] == "unknown":
            raise RunError("这次运行有一步结果未知：请先由真人确认（写明你核对了什么），再恢复。")
        if r["status"] not in ("failed", "interrupted", "blocked"):
            raise RunError(f"当前状态“{r['status']}”不能恢复。")
        if r["kind"] == "task":
            why, _ = self.preflight(r["task_id"])
            why = [w for w in why if "已有一个运行" not in w]
            if why:
                raise RunError("；".join(why))
        self._launch(rid, background)

    def acknowledge(self, person_raw: str, rid: str, note: str) -> None:
        if len(str(note or "").strip()) < 4:
            raise RunError("请写明你核对了什么（例如：已在模型后台确认这次请求没有生成 / 已计费但结果丢失），至少 4 个字。")
        r = fold_runs(self.ctx.entries()).get(rid)
        if not r:
            raise RunError("找不到这个运行。")
        steps = [s for s, v in r["steps"].items() if v.get("state") in ("requested", "unknown")]
        if not steps:
            raise RunError("这个运行没有结果未知的步骤。")
        for s in steps:
            self.ctx.ledger().append("run.acknowledged", {"run_id": rid, "step": s, "note": note.strip()[:600]}, actor=person_raw, actor_kind="human")

    def cancel(self, person_raw: str, rid: str, note: str = "") -> None:
        r = fold_runs(self.ctx.entries()).get(rid)
        if not r or r["status"] in ("completed", "cancelled"):
            raise RunError("这个运行已经结束。")
        if r["status"] == "running":
            raise RunError("运行正在调用模型，等这一步结束后再取消。")
        self.ctx.ledger().append("run.cancelled", {"run_id": rid, "note": str(note or "")[:400]}, actor=person_raw, actor_kind="human")

    def _launch(self, rid: str, background: bool) -> None:
        with _ALOCK:
            if rid in _ACTIVE:
                raise RunError("这个运行已经在执行。")
            _ACTIVE.add(rid)
        if background:
            threading.Thread(target=self._safe_exec, args=(rid,), daemon=True, name="helix-run-" + rid).start()
        else:
            self._safe_exec(rid)

    def _safe_exec(self, rid: str) -> None:
        try:
            self._exec(rid)
        except Exception as exc:  # 记下来，绝不吞掉
            try:
                self.ctx.ledger().append("run.step.failed", {"run_id": rid, "step": "harness", "error": f"{type(exc).__name__}: {str(exc)[:400]}", "kind": "harness"}, actor="harness", actor_kind="system")
            except Exception:
                pass
        finally:
            with _ALOCK:
                _ACTIVE.discard(rid)

    # -------------------------------------------------------------- execution
    def _exec(self, rid: str) -> None:
        r = fold_runs(self.ctx.entries())[rid]
        steps = ["summary"] if r["kind"] == "summary" else STEPS
        out: dict[str, Any] = {}
        for step in steps:
            st = r["steps"].get(step) or {}
            if st.get("state") == "done":
                out[step] = self._load_output(rid, step, st.get("output_sha"))
                continue
            if step == "propose":
                self._propose(rid, r, out)
                return
            res = self._model_step(rid, r, step, out)
            if res is None:
                return
            out[step] = res
        if r["kind"] == "summary":
            self._finish_summary(rid, r, out["summary"])

    def _load_output(self, rid: str, step: str, sha: str | None):
        path = self.ctx.run_dir(rid) / f"{step}.json"
        raw = path.read_bytes() if path.is_file() else b""
        if hashlib.sha256(raw).hexdigest() != sha:
            raise RunError(f"中间结果 {step}.json 与账本记录的 SHA 不一致（被改动或丢失），不能复用；请取消后重新运行。")
        return json.loads(raw.decode("utf-8"))

    def _save_output(self, rid: str, step: str, obj) -> str:
        d = self.ctx.run_dir(rid)
        d.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        (d / f"{step}.json").write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def _task_text(self, r: dict) -> str:
        t = self.ctx.task(r.get("task_id")) or {}
        task = {k: t.get(k) for k in ("task_id", "week", "track_label", "deliverable", "acceptance") if t.get(k)}
        learning = getattr(self.ctx, "learning_context", None)
        if callable(learning):
            support = learning(t)
            if support:
                task["learning_support"] = support
        return json.dumps(task, ensure_ascii=False)

    def _messages(self, r: dict, step: str, out: dict) -> tuple[list[dict], dict]:
        red = "\n".join(f"  · {x}" for x in (self.ctx.red_lines() or [])[:20]) or "  · （本项目未写红线）"
        system = _SYS.format(ceiling=self.ctx.ceiling() or "未设置（按探索性处理）", red=red)
        meta: dict = {}
        if step == "plan":
            user = _STEP_PROMPT["plan"].format(task=self._task_text(r))
        elif step == "extract":
            blocks, total, used = [], 0, []
            send = self.ctx.registry.load()["rules"].get("send_file_text", True)
            meta["send_file_text"] = send
            for f in (list(self.ctx.files())[-8:][::-1] if send else []):
                text = read_text(self.ctx.file_abspath(f))
                if not text:
                    continue
                chunk = text[:6000]
                if total + len(chunk) > 40000:
                    break
                total += len(chunk)
                used.append({"sha256": f["sha256"], "name": f["name"], "chars": len(chunk), "truncated": len(text) > len(chunk)})
                blocks.append(f"<<<FILE {f['sha256']} {f['name']}\n{chunk}\n>>>")
            meta["files"] = used
            user = _STEP_PROMPT["extract"].format(questions=json.dumps(out["plan"].get("questions") or [], ensure_ascii=False), files="\n".join(blocks) or "（本项目没有可读的文本类文件：请不要编造摘录，直接输出空 cards）")
        elif step == "draft":
            user = _STEP_PROMPT["draft"].format(task=self._task_text(r), questions=json.dumps(out["plan"].get("questions") or [], ensure_ascii=False),
                                                cards=json.dumps(out["extract"].get("cards") or [], ensure_ascii=False, indent=0))
        elif step == "critique":
            user = _STEP_PROMPT["critique"].format(task=self._task_text(r), cards=json.dumps(out["extract"].get("cards") or [], ensure_ascii=False),
                                                   drafts=json.dumps([{"index": i, **{k: b.get(k) for k in ("kind", "text", "rationale", "evidence")}} for i, b in enumerate(out["draft"].get("bases") or [], 1)], ensure_ascii=False))
        else:  # summary
            st = self.ctx.state()
            week = r.get("week")
            pairs = [{"base_id": b["base_id"], "text": b.get("revised_text") or b.get("text"), "verdict": b.get("status"), "decision": b.get("decision"), "cross": (b.get("cross") or {}).get("agree")}
                     for b in st.ordered_bases() if b.get("verdict") and (not week or str(b.get("seed_week") or (self.ctx.task(b.get("task_id")) or {}).get("week")) == str(week))]
            meta["pairs"] = len(pairs)
            user = _STEP_PROMPT["summary"].format(week=week or "全部", pairs=json.dumps(pairs[:80], ensure_ascii=False))
        return [{"role": "system", "content": system}, {"role": "user", "content": user}], meta

    def _model_step(self, rid: str, r: dict, step: str, out: dict):
        c = self.ctx
        role = STEP_ROLE[step]
        try:
            prov, model = c.registry.resolve_role(role)
        except ModelError as exc:
            c.ledger().append("run.step.failed", {"run_id": rid, "step": step, "error": str(exc), "kind": "config", "sent": False}, actor="harness", actor_kind="system")
            return None
        if step == "critique":
            dfam = family((r["roles"].get("drafter") or {}).get("model") or "")
            if family(model) == dfam and not c.registry.load()["rules"].get("allow_same_family"):
                c.ledger().append("run.blocked", {"run_id": rid, "step": step, "reason": f"批评者与起草者同一家族（{dfam}），异源互审不成立"}, actor="harness", actor_kind="system")
                return None
        msgs, meta = self._messages(r, step, out)
        budget = c.registry.load()["budget"]
        for attempt in (1, 2, 3):
            entries = c.entries()
            runs = fold_runs(entries)
            u = usage_today(runs, entries)
            run_tok = runs[rid]["usage"]["input"] + runs[rid]["usage"]["output"]
            if u["calls"] >= budget["max_calls_per_day"] or u["tokens"] >= budget["max_tokens_per_day"] or run_tok >= budget["max_tokens_per_run"]:
                c.ledger().append("run.blocked", {"run_id": rid, "step": step, "reason": f"预算已到上限（今日 {u['calls']} 次 / {u['tokens']} tokens；本次运行 {run_tok} tokens）"}, actor="harness", actor_kind="system")
                return None
            ih = digest(msgs)
            c.ledger().append("run.step.requested", {"run_id": rid, "step": step, "role": role, "provider": prov["id"], "model": model, "attempt": attempt, "input_hash": ih, "idempotent": False},
                              actor="harness", actor_kind="system")
            try:
                if getattr(c, "fault", None):
                    c.fault(step, attempt)
                res = c.registry.chat(prov["id"], model, msgs, json_mode=step != "summary", max_tokens=1600 if step == "summary" else 3000, timeout=int(budget.get("timeout_s", 120)))
                parsed = res["text"] if step == "summary" else _json(res["text"])
                notes: list[str] = []
                if step == "extract":
                    parsed, notes = self._verify_cards(parsed)
                    parsed["files"] = meta.get("files", [])
                elif step == "draft":
                    parsed, notes = self._clean_drafts(parsed, out["extract"])
                elif step == "critique":
                    parsed = {"reviews": [x for x in parsed.get("reviews") or [] if isinstance(x, dict)][:40], "missing": [str(x)[:200] for x in parsed.get("missing") or []][:10]}
                elif step == "plan":
                    parsed = {"questions": [q for q in parsed.get("questions") or [] if isinstance(q, dict) and q.get("text")][:8], "risks": [str(x)[:200] for x in parsed.get("risks") or []][:8]}
                payload = {"model": model, "provider": prov["id"], "output": parsed, "input_hash": ih, "meta": meta}
                sha = self._save_output(rid, step, payload["output"] if step != "summary" else {"markdown": parsed, "meta": meta})
                c.ledger().append("run.step.done", {"run_id": rid, "step": step, "model": model, "output_sha": sha, "usage": res["usage"], "latency_ms": res["latency_ms"], "notes": notes},
                                  actor="ai:" + model, actor_kind="ai")
                return payload["output"] if step != "summary" else {"markdown": parsed, "meta": meta}
            except ModelError as exc:
                c.ledger().append("run.step.failed", {"run_id": rid, "step": step, "error": str(exc)[:400], "kind": exc.kind, "sent": exc.sent, "outcome_unknown": exc.outcome_unknown, "attempt": attempt},
                                  actor="harness", actor_kind="system")
                if exc.outcome_unknown or not exc.retriable or attempt == 3:
                    return None
                time.sleep(getattr(c, "backoff", 2.0) * attempt)
            except (ValueError, KeyError, TypeError) as exc:
                c.ledger().append("run.step.failed", {"run_id": rid, "step": step, "error": "模型输出无法解析：" + str(exc)[:300], "kind": "parse", "sent": True, "attempt": attempt},
                                  actor="harness", actor_kind="system")
                if attempt == 2:
                    return None
        return None

    def _verify_cards(self, parsed: dict) -> tuple[dict, list[str]]:
        files = {f["sha256"]: f for f in self.ctx.files()}
        ok, notes, texts = [], [], {}
        for card in parsed.get("cards") or []:
            if not isinstance(card, dict):
                continue
            sha = str(card.get("file_sha") or "").lower()
            f = files.get(sha) or next((v for k, v in files.items() if sha and len(sha) >= 12 and k.startswith(sha)), None)
            if not f:
                notes.append(f"丢弃 {card.get('id')}：文件指纹对不上")
                continue
            if f["sha256"] not in texts:
                texts[f["sha256"]] = read_text(self.ctx.file_abspath(f))
            if not quote_in(str(card.get("quote") or ""), texts[f["sha256"]]):
                notes.append(f"丢弃 {card.get('id')}：摘录在 {f['name']} 里找不到（疑似编造）")
                continue
            ok.append({"id": str(card.get("id") or f"E{len(ok) + 1}")[:8], "file_sha": f["sha256"], "file": f["name"], "quote": str(card["quote"])[:400],
                       "supports": str(card.get("supports") or "")[:20], "note": str(card.get("note") or "")[:300], "verified": True})
        return {"cards": ok, "dropped": len(notes)}, notes

    def _clean_drafts(self, parsed: dict, extract: dict) -> tuple[dict, list[str]]:
        ids = {c["id"] for c in extract.get("cards") or []}
        bases, notes = [], []
        for b in parsed.get("bases") or []:
            if not isinstance(b, dict) or not str(b.get("text") or "").strip():
                continue
            ev = [e for e in b.get("evidence") or [] if e in ids]
            if len(ev) < len(b.get("evidence") or []):
                notes.append(f"“{str(b['text'])[:20]}…”引用了不存在的证据卡，已去掉")
            bases.append({"kind": str(b.get("kind") or "action"), "text": str(b["text"])[:1200], "rationale": str(b.get("rationale") or "")[:1000],
                          "confidence": b.get("confidence", 0.5), "evidence": ev})
        return {"summary": str(parsed.get("summary") or "")[:400], "bases": bases[:10]}, notes

    def _propose(self, rid: str, r: dict, out: dict) -> None:
        c = self.ctx
        t = c.task(r.get("task_id")) or {}
        cards = {x["id"]: x for x in out["extract"].get("cards") or []}
        reviews = {int(x.get("index") or 0): x for x in out["critique"].get("reviews") or [] if str(x.get("index") or "").isdigit() or isinstance(x.get("index"), int)}
        critic = (r["roles"].get("critic") or {}).get("model", "")
        drafter = (r["roles"].get("drafter") or {}).get("model", "")
        raw, refs = [], []
        for i, b in enumerate(out["draft"].get("bases") or [], 1):
            rv = reviews.get(i) or {}
            verdict = str(rv.get("verdict") or "unreviewed")
            conf = float(b.get("confidence") or 0.5)
            if verdict in ("weak",):
                conf = min(conf, 0.45)
            if verdict in ("contradict", "overclaim"):
                conf = min(conf, 0.2)
            same = family(critic) == family(drafter)
            if same:
                conf = min(conf, 0.4)
            crit = ("\n【同源互审：批评者与起草者同一家族，可信度降低】" if same else "") + f"\n【校对酶 · {critic}】{ {'support': '支持', 'weak': '证据弱', 'contradict': '有反证', 'overclaim': '越界', 'unreviewed': '未审'}.get(verdict, verdict) }" + (f"：{rv.get('issue')}" if rv.get("issue") else "") + (f"；建议：{rv.get('suggest')}" if rv.get("suggest") else "")
            ev = [cards[e] for e in b.get("evidence") or [] if e in cards]
            evl = "".join(f"\n【证据 {e['id']} · {e['file']} · sha256:{e['file_sha'][:12]}】“{e['quote'][:160]}”" for e in ev)
            kind = "conflict" if verdict == "contradict" else b.get("kind")
            raw.append({"kind": kind, "text": b["text"], "rationale": (b.get("rationale") or "") + evl + crit, "confidence": conf,
                        "evidence_keys": [f"evidence.{e['id']}" for e in ev] or ["task.acceptance"],
                        "task_id": t.get("task_id"), "owner": t.get("owner"), "cross_reviewer": t.get("cross_reviewer"), "track": t.get("track_label") or t.get("track")})
            refs.append([f"sha256:{e['file_sha']} {e['file']}" for e in ev])
        if not raw:
            c.ledger().append("run.blocked", {"run_id": rid, "step": "propose", "reason": "起草结果为空"}, actor="harness", actor_kind="system")
            return
        snapshot = {"research_context": {"v4_claim_ceiling": c.ceiling()}, "evidence": {k: {"file": v["file"], "quote": v["quote"]} for k, v in cards.items()},
                    "task": {"acceptance": t.get("acceptance"), "deliverable": t.get("deliverable")}}
        try:
            res = c.service.propose_turn(c.ledger(), raw_bases=raw, gate_id=str(t.get("task_id") or ""), phase="", summary=out["draft"].get("summary") or "",
                                         source="agent", snapshot=snapshot, model=drafter, prompt_version=PROMPT_VERSION, input_hash=digest(out),
                                         actor="ai:" + (drafter or "model"), turn_meta={"run_id": rid, "critic_model": critic, "missing": out["critique"].get("missing") or []})
        except Exception as exc:
            c.ledger().append("run.blocked", {"run_id": rid, "step": "propose", "reason": str(exc)[:300]}, actor="harness", actor_kind="system")
            return
        ids = [b["base_id"] for b in res["bases"]]
        c.ledger().append("run.completed", {"run_id": rid, "turn_id": res["turn_id"], "base_ids": ids, "refs": {bid: refs[i] for i, bid in enumerate(ids)},
                                            "blocked_by_proofread": sum(1 for b in res["bases"] if b.get("proofread_block"))}, actor="harness", actor_kind="system")

    def _finish_summary(self, rid: str, r: dict, out: dict) -> None:
        d = self.ctx.run_dir(rid)
        md = str(out.get("markdown") or "")
        if "草稿" not in md[:200]:
            md = "> 草稿，未经真人确认不得外发。\n\n" + md
        path = d / f"W{r.get('week') or 'all'}-周汇总草稿.md"
        path.write_text(md, encoding="utf-8")
        self.ctx.ledger().append("run.completed", {"run_id": rid, "output": str(path.name), "output_sha": hashlib.sha256(md.encode("utf-8")).hexdigest(), "status": "NOT_APPLIED"},
                                 actor="harness", actor_kind="system")

    # -------------------------------------------------------------- view
    def view(self) -> dict:
        c = self.ctx
        try:
            entries = c.entries()
        except Exception:
            entries = []
        runs = fold_runs(entries)
        out = []
        for r in sorted(runs.values(), key=lambda x: x["ts"] or "", reverse=True)[:30]:
            if r["status"] == "completed" and r["kind"] != "summary":
                r["steps"].setdefault("propose", {"state": "done", "model": f"{len(r.get('base_ids') or [])} 条碱基"})
            out.append({**r, "steps": [{"step": s, "label": STEP_LABEL.get(s, s), "role": STEP_ROLE.get(s), **(r["steps"].get(s) or {"state": "pending"})}
                                       for s in (["summary"] if r["kind"] == "summary" else STEPS)]})
        return {"runs": out, "usage": usage_today(runs, entries), "steps": STEPS, "labels": STEP_LABEL, "roles": ROLES}


def _json(text: str) -> dict:
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        v = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("没有找到 JSON 对象")
        v = json.loads(m.group(0))
    if not isinstance(v, dict):
        raise ValueError("输出不是 JSON 对象")
    return v


__all__ = ["Runtime", "RunError", "register_events", "task_states", "plan_hash", "frozen_plan", "fold_runs", "STEPS", "STEP_LABEL"]
