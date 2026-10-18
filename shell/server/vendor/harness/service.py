"""Helix Harness 服务层：螺旋回合、配对、修复记忆、端粒检查点与接力卡。

页面只调用这里的函数；所有写操作都落在螺旋账本，且遵守：
- AI 只能写 ``turn.opened`` / ``base.proposed``；
- 只有真人能写 ``base.paired`` / ``repair.retired`` / ``autonomy.set`` / ``checkpoint``；
- 存在未配对的 G–C 碱基时（复制叉阻滞），不允许开启新的 AI 回合。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from . import HARNESS_VERSION
from .bonds import AUTONOMY_LEVELS, GC, KIND_LABELS, clamp_confidence, classify_bond, sampled_for_audit
from .ledger import HelixLedger, new_id, utc_now
from .migrate import ensure_migrated
from .proofread import proofread_base
from .state import STATUS_LABELS, VERDICTS, HelixState, fold_state

TURN_PROMPT_VERSION = "helix-turn.v1"


class HarnessError(RuntimeError):
    """可向用户展示的 Harness 操作错误。"""


# --------------------------------------------------------------------- open
def open_helix(project_id: str, store: Mapping[str, Any] | None = None, *, root: Any = None, migrate: bool = True) -> tuple[HelixLedger, HelixState]:
    ledger = HelixLedger(project_id, root=root)
    ledger.ensure_genesis()
    if migrate and store is not None:
        ensure_migrated(ledger, store)
    return ledger, fold_state(project_id, ledger.entries())


def load_state(ledger: HelixLedger) -> HelixState:
    return fold_state(ledger.project_id, ledger.entries())


# -------------------------------------------------------------------- bases
def _normalize_raw_bases(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        kind = str(item.get("kind") or "action").strip().lower()
        row = {
            "kind": kind if kind in KIND_LABELS else "action",
            "text": text[:1500],
            "rationale": str(item.get("rationale") or "").strip()[:1500],
            "confidence": clamp_confidence(item.get("confidence")),
            "evidence_keys": [str(k).strip()[:120] for k in (item.get("evidence_keys") or []) if str(k).strip()][:12],
        }
        for key in _TASK_FIELDS:
            value = item.get(key)
            if value not in (None, "", []):
                row[key] = value if isinstance(value, (int, float, list)) else str(value)[:300]
        rows.append(row)
    return rows[:40]


# 真实任务相关字段：AI 或播种都可以把碱基挂到某个真实任务上（挂上即 G–C + 双人互补）。
_TASK_FIELDS = ("task_id", "owner", "cross_reviewer", "track", "seed_week", "task_status", "legacy_gate", "allowed_result", "candidate_id")


def propose_turn(
    ledger: HelixLedger,
    *,
    raw_bases: Sequence[Any],
    gate_id: str,
    phase: str,
    summary: str,
    source: str,
    snapshot: Mapping[str, Any] | None,
    model: str = "",
    prompt_version: str = TURN_PROMPT_VERSION,
    input_hash: str = "",
    lesson_ids: Sequence[str] = (),
    actor: str = "ai",
    turn_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = load_state(ledger)
    if state.fork_stalled and source != "sprint_seed":
        ledger.append("turn.blocked", {"reason": "复制叉阻滞：仍有未配对的 G–C 碱基", "open_gc": [b["base_id"] for b in state.open_gc][:20]}, actor="harness", actor_kind="system")
        raise HarnessError(f"还有 {len(state.open_gc)} 个 G–C 碱基等待真人配对；先完成配对，AI 才能开始下一轮。")
    bases = _normalize_raw_bases(raw_bases)
    if not bases:
        raise HarnessError("这一轮没有可配对的碱基（AI 输出为空或无法解析）。")
    turn_id = new_id("turn")
    ledger.append(
        "turn.opened",
        {
            "turn_id": turn_id,
            "gate_id": gate_id,
            "phase": phase,
            "summary": str(summary or "")[:2000],
            "source": source,
            "model": model,
            "prompt_version": prompt_version,
            "input_hash": input_hash,
            "lesson_ids": list(lesson_ids),
            "autonomy": state.autonomy,
            "harness_version": HARNESS_VERSION,
            **dict(turn_meta or {}),
        },
        actor=actor,
        actor_kind="ai" if actor != "harness" else "system",
    )
    mismatches = state.mismatch_texts()
    created = []
    for index, base in enumerate(bases, start=1):
        base_id = f"{turn_id}.b{index:02d}"
        if source == "sprint_seed":
            # 播种文本逐字来自权威任务表 / V4 异常队列，不是 AI 主张；不做越界拦截。
            check = {"notes": [], "block": False, "repeat": None}
        else:
            check = proofread_base(base, snapshot=snapshot, mismatches=mismatches)
        base["proofread_notes"] = check["notes"]
        base["proofread_block"] = check["block"]
        base["repeat_mismatch"] = check["repeat"]
        bond, reasons = classify_bond(base, phase=phase, autonomy=state.autonomy)
        payload = {
            **base,
            "base_id": base_id,
            "turn_id": turn_id,
            "gate_id": gate_id,
            "stage": gate_id,
            "bond": bond,
            "bond_reasons": reasons,
            "sampled": bond == GC or sampled_for_audit(base_id, state.autonomy),
            "source": source,
            "needs_cross": bond == GC and bool(base.get("task_id")),
        }
        ledger.append("base.proposed", payload, actor=actor, actor_kind="ai" if actor != "harness" else "system")
        created.append(payload)
    return {"turn_id": turn_id, "bases": created}


# ------------------------------------------------------------------ AI turn
_TURN_SCHEMA = """严格输出一个 JSON 对象：
{
  "summary": "一句话说明这一轮最重要的问题",
  "bases": [
    {
      "kind": "action | gap | conflict | tool | question",
      "text": "一个可以被真人单独判断的最小单元（一句话）",
      "rationale": "为什么提出；必须能追溯到输入字段",
      "confidence": 0.0,
      "evidence_keys": ["引用的输入字段名，如 current_task.why / gate_chain"]
    }
  ]
}
要求：
- 每个碱基只说一件事，3–10 个；confidence 是你对“真人会接受这一条”的真实估计（0–1），不要都写高分；
- 不确定就写 gap 或 question，不要猜；
- 绝不宣布 Gate 通过、替真人签字、批准发布、把计算预测说成实验事实；
- 必须遵守“修复记忆”中真人给出的纠正，不要重复已被否决的内容。"""


_TASK_HINT = """
真实科研骨架（research_context）规则：
- research_context 来自本机三库的权威文件（V4 authority + 本轮四周任务表），带 SHA-256 见证；
- 如果一个碱基是在帮某个真实任务（如 W1-Y、HR-ADORA2A-001），请加字段 "task_id"，它会自动成为 G–C 并需要主核 + 交叉核两位真人；
- 必须遵守 research_context.sprint_red_lines；V4 主张上限是 research_context.v4_claim_ceiling，EXPLORATORY_ONLY 时任何评分/排序都要写明“探索性”；
- 缺失写 UNKNOWN，不要写 0；不要输出正式 Top 或榜单。"""


def ai_snapshot_for(workflow_snapshot: Mapping[str, Any] | None, research: Any = None) -> dict[str, Any]:
    """AI 回合的唯一事实来源：1.00 工作快照 + 真实科研骨架（若读到）。"""
    snap = dict(workflow_snapshot or {})
    if research is not None and getattr(research, "found_any", False):
        from .sources import ai_context
        snap["research_context"] = ai_context(research)
    return snap


def build_turn_prompt(snapshot: Mapping[str, Any], lessons: Sequence[Mapping[str, Any]], deferred: Sequence[Mapping[str, Any]]) -> str:
    memory = [
        f"- [{'否决' if l.get('verdict') == 'mismatch' else '修正'}] 原提议：{str(l.get('base_text') or '')[:200]} → 真人意见：{str(l.get('lesson') or '')[:300]}"
        for l in lessons[:12]
    ]
    parked = [f"- {str(b.get('text') or '')[:200]}" for b in deferred[:8]]
    parts = [
        _TURN_SCHEMA + (_TASK_HINT if "research_context" in snapshot else ""),
        "\n修复记忆（真人此前的纠正，优先级高于你的判断）：\n" + ("\n".join(memory) if memory else "- 暂无"),
        "\n暂缓区（真人暂不处理，除非有新证据否则不要重复提出）：\n" + ("\n".join(parked) if parked else "- 暂无"),
        "\n当前工作快照（唯一允许使用的事实来源）：\n" + json.dumps(snapshot, ensure_ascii=False, indent=2),
    ]
    return "\n".join(parts)


def run_ai_turn(ledger: HelixLedger, workflow: Mapping[str, Any], action: Mapping[str, Any], *, provider: Any = None, research: Any = None) -> dict[str, Any]:
    """一次 AI 回合。模型调用是非幂等的：先记 turn.requested，再调用；
    失败记 turn.failed；进程中断则留下“结果未知”，必须由真人确认后才能再发起。"""
    state = load_state(ledger)
    if state.fork_stalled:
        return propose_turn(ledger, raw_bases=[], gate_id="", phase="", summary="", source="ai", snapshot=None)  # raises with ledger receipt
    if state.outcome_unknown:
        raise HarnessError("上一次 AI 请求结果未知（可能已计费/已生成但未落账）。请先在“账本与端粒”里由真人确认，再发起新回合；不会自动重试。")
    from core.ai_collaboration import _SYSTEM, _extract_json, _safe_snapshot
    from literature.ai import AIProvider, AIProviderConfig
    client = provider or AIProvider(AIProviderConfig.load())
    if not client.config.configured:
        raise HarnessError("AI Provider 未配置；请到 系统设置 → 全局 AI/API 配置。也可以先“播种本周真实任务”或“导入 1.00 工作包”开始配对。")
    snapshot = ai_snapshot_for(_safe_snapshot(workflow, action), research)
    gate_id = str(action.get("gate_id") or snapshot.get("current_phase") or "")
    lessons = state.active_lessons(gate_id)
    canonical = json.dumps({"snapshot": snapshot, "lessons": [l["lesson_id"] for l in lessons]}, ensure_ascii=False, sort_keys=True)
    input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if research is not None and getattr(research, "found_any", False):
        from .sprint import record_sources
        record_sources(ledger, research)
    request_id = new_id("req")
    model = str(getattr(client.config, "model", ""))
    ledger.append("turn.requested", {"request_id": request_id, "gate_id": gate_id, "input_hash": input_hash, "model": model, "idempotent": False}, actor="harness", actor_kind="system")
    try:
        raw = client.complete(
            [
                {"role": "system", "content": _SYSTEM + "\n你现在运行在 Helix Harness 中：把工作拆成可被真人逐条配对的“碱基”。"},
                {"role": "user", "content": build_turn_prompt(snapshot, lessons, state.queue("deferred"))},
            ],
            json_mode=True,
        )
        payload = _extract_json(raw)
    except Exception as exc:
        ledger.append("turn.failed", {"request_id": request_id, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}, actor="harness", actor_kind="system")
        raise
    return propose_turn(
        ledger,
        raw_bases=payload.get("bases") or [],
        gate_id=gate_id,
        phase=str(snapshot.get("current_phase") or ""),
        summary=str(payload.get("summary") or ""),
        source="ai",
        snapshot=snapshot,
        model=model,
        input_hash=input_hash,
        lesson_ids=[l["lesson_id"] for l in lessons],
        actor=f"ai:{model or 'model'}",
        turn_meta={"request_id": request_id, "research_snapshot_hash": snapshot.get("research_context", {}).get("snapshot_hash", "")},
    )


def acknowledge_request(ledger: HelixLedger, request_id: str, *, reviewer: str, note: str) -> dict[str, Any]:
    if len(str(note or "").strip()) < 4:
        raise HarnessError("请写明你核对了什么（例如：已在模型后台确认无输出 / 已手动导入），至少 4 个字。")
    return ledger.append("turn.acknowledged", {"request_id": request_id, "note": note.strip()[:1000]}, actor=reviewer, actor_kind="human")


def import_v1_packet(ledger: HelixLedger, record: Mapping[str, Any]) -> dict[str, Any]:
    """把 1.00 的 AI 协同工作包拆成碱基（置信度未知按 0.5，校对照常执行）。"""
    state = load_state(ledger)
    imported = {t.get("v1_record_id") for t in state.turns if t.get("v1_record_id")}
    if record.get("id") and record.get("id") in imported:
        raise HarnessError("这个 1.00 工作包已经导入过了。")
    packet = dict(record.get("packet") or {})
    keys = list(packet.get("source_keys") or [])
    mapping = (("proposed_actions", "action"), ("evidence_gaps", "gap"), ("conflicts", "conflict"), ("tool_requests", "tool"), ("human_questions", "question"))
    raw = [
        {"kind": kind, "text": text, "confidence": 0.5, "evidence_keys": keys, "rationale": "来自 1.00 AI 协同工作包（原包未给出逐条置信度）"}
        for field, kind in mapping
        for text in (packet.get(field) or [])
    ]
    result = propose_turn(
        ledger,
        raw_bases=raw,
        gate_id=str(record.get("gate_id") or ""),
        phase=str((record.get("input_snapshot") or {}).get("current_phase") or ""),
        summary=str(packet.get("summary") or ""),
        source="v1_packet",
        snapshot=record.get("input_snapshot") if isinstance(record.get("input_snapshot"), Mapping) else None,
        model=str(record.get("model") or ""),
        prompt_version=str(record.get("prompt_version") or ""),
        input_hash=str(record.get("input_hash") or ""),
        actor="harness",
        turn_meta={"v1_record_id": record.get("id"), "v1_created_at": record.get("created_at")},
    )
    return result


# ------------------------------------------------------------------ pairing
def pair_base(
    ledger: HelixLedger,
    base_id: str,
    *,
    verdict: str,
    reviewer: str,
    confirmed: bool,
    note: str = "",
    revised_text: str = "",
    decision: str = "",
    source_refs: Sequence[str] = (),
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise HarnessError("配对结论只能是 互补 / 修正 / 错配 / 暂缓。")
    if not confirmed:
        raise HarnessError("请勾选“我本人已阅读来源并作出判断”。")
    state = load_state(ledger)
    base = state.bases.get(base_id)
    if base is None:
        raise HarnessError("找不到这个碱基。")
    if base.get("verdict"):
        raise HarnessError("这个碱基已经配对过；如需改判，请在新一轮中提出新的碱基。")
    note = str(note or "").strip()
    revised_text = str(revised_text or "").strip()
    if verdict == "mismatch" and len(note) < 4:
        raise HarnessError("错配必须写明原因（至少 4 个字），它会成为 AI 的修复记忆。")
    if verdict == "revise" and not (revised_text or note):
        raise HarnessError("修正需要给出改写后的内容或修正意见。")
    if verdict == "defer" and len(note) < 2:
        raise HarnessError("暂缓请简单写一下原因或等待什么。")
    refs = [str(r).strip()[:500] for r in source_refs or [] if str(r).strip()][:20]
    decision = str(decision or "").strip().upper()
    extra: dict[str, Any] = {}
    if base.get("kind") == "task" and verdict != "defer":
        from .sources import ALLOWED_SPRINT_DECISIONS
        if decision not in ALLOWED_SPRINT_DECISIONS:
            raise HarnessError("任务碱基需要给出四周计划允许的结论：" + " / ".join(ALLOWED_SPRINT_DECISIONS))
        if decision in {"PASS", "REJECTED", "OUT_OF_DOMAIN"} and not refs:
            raise HarnessError(f"结论 {decision} 必须附上原始工件路径 / DOI / PMID / SHA 至少一条。")
        owner = str(base.get("owner") or "")
        extra = {"decision": decision, "source_refs": refs, "is_designated_owner": bool(owner) and owner.casefold() == str(reviewer).strip().casefold()}
    elif refs or decision:
        extra = {"decision": decision, "source_refs": refs}
    entry = ledger.append(
        "base.paired",
        {"base_id": base_id, "verdict": verdict, "note": note[:2000], "revised_text": revised_text[:2000], "bond": base.get("bond"), **extra},
        actor=reviewer,
        actor_kind="human",
    )
    if verdict in {"mismatch", "revise"}:
        ledger.append(
            "repair.learned",
            {
                "lesson_id": new_id("lesson"),
                "base_id": base_id,
                "verdict": verdict,
                "stage": base.get("stage") or "",
                "kind": base.get("kind"),
                "base_text": base.get("text"),
                "lesson": note or f"改写为：{revised_text}",
                "revised_text": revised_text,
            },
            actor=reviewer,
            actor_kind="human",
        )
    return entry


def retire_lesson(ledger: HelixLedger, lesson_id: str, *, reviewer: str, reason: str) -> dict[str, Any]:
    if len(str(reason or "").strip()) < 2:
        raise HarnessError("退役修复记忆需要写明原因。")
    return ledger.append("repair.retired", {"lesson_id": lesson_id, "reason": reason.strip()[:1000]}, actor=reviewer, actor_kind="human")


def set_autonomy(ledger: HelixLedger, level: str, *, reviewer: str, reason: str = "") -> dict[str, Any]:
    if level not in AUTONOMY_LEVELS:
        raise HarnessError("未知的螺旋松紧度。")
    return ledger.append("autonomy.set", {"level": level, "reason": str(reason or "")[:500]}, actor=reviewer, actor_kind="human")


def telomere_checkpoint(ledger: HelixLedger, *, label: str, reviewer: str, note: str = "") -> dict[str, Any]:
    """端粒检查点：真人给当前螺旋状态盖一个可复核的时间戳与链头哈希。"""
    state = load_state(ledger)
    if state.fork_stalled:
        raise HarnessError("还有未配对的 G–C 碱基，不能在复制叉阻滞时设置端粒检查点。")
    report = ledger.verify()
    if not report.ok:
        raise HarnessError(f"账本校验失败：{report.problem}")
    return ledger.append(
        "checkpoint",
        {"label": str(label or "阶段检查点")[:120], "note": str(note or "")[:1000], "chain_head": report.head, "events": report.count, "metrics": state.metrics()},
        actor=reviewer,
        actor_kind="human",
    )


# --------------------------------------------------------------- relay card
def relay_card(state: HelixState, verify: Mapping[str, Any] | None = None, research: Any = None) -> dict[str, Any]:
    m = state.metrics()
    recent = [b for b in state.ordered_bases() if b.get("paired_at")]
    recent.sort(key=lambda b: str(b.get("paired_at")), reverse=True)
    if state.fork_stalled:
        next_human = f"先配对 {m['open_gc']} 个 G–C 碱基（从置信度最低的开始）。"
        next_ai = "等待：复制叉阻滞中，AI 不会开启新一轮。"
    elif m["audit_pending"]:
        next_human = f"抽检 {m['audit_pending']} 个已先行的 A–T 碱基。"
        next_ai = "可以开启下一轮螺旋回合。"
    elif state.open_tasks:
        next_human = f"本轮还有 {len(state.open_tasks)} 个真实任务待主核（按各自主核人认领，不阻滞 AI）。"
        next_ai = "可以开启下一轮螺旋回合，为本周任务起草核验要点（挂 task_id 即需双人互补）。"
    else:
        next_human = "当前没有待配对碱基；可以设置端粒检查点，或让 AI 开始下一轮。"
        next_ai = "可以开启下一轮螺旋回合。"
    if state.outcome_unknown:
        next_ai = "暂停：上一次 AI 请求结果未知，需真人在账本页确认后才能继续（不自动重试）。"
    cross = state.cross_pending()
    if cross and not state.fork_stalled:
        next_human = f"{len(cross)} 个已主核的碱基等待“另一位真人”交叉核；" + next_human
    sprint_info = None
    if research is not None and getattr(research, "sprint", None):
        sp = research.sprint
        week = sp.get("current_week")
        rows = [t for t in sp.get("tasks") or [] if t["week"] == week]
        sprint_info = {
            "week": week,
            "days_left": sp.get("days_left"),
            "track_label": rows[0]["track_label"] if rows else "",
            "tasks": [
                {
                    "task_id": t["task_id"], "owner": t["owner"], "cross_reviewer": t["cross_reviewer"], "status": t["status"],
                    "helix": _task_progress(state, t["task_id"]),
                }
                for t in rows
            ],
            "snapshot_hash": research.snapshot_hash,
        }
        v4 = research.v4 or {}
        if v4:
            sprint_info["v4"] = {
                "discovery_status": v4.get("discovery_status"),
                "claim_ceiling": v4.get("claim_ceiling"),
                "freeze": (v4.get("freeze") or {}).get("status"),
                "validation": (v4.get("validation") or {}).get("status"),
                "actual_data_count": (v4.get("validation") or {}).get("actual_data_count"),
            }
    last_turn = state.turns[-1] if state.turns else {}
    return {
        "sprint": sprint_info,
        "outcome_unknown": [r.get("request_id") for r in state.outcome_unknown],
        "generated_at": utc_now(),
        "autonomy": state.autonomy,
        "autonomy_label": AUTONOMY_LEVELS.get(state.autonomy, {}).get("label", state.autonomy),
        "fork_stalled": state.fork_stalled,
        "next_human": next_human,
        "next_ai": next_ai,
        "last_turn": {k: last_turn.get(k) for k in ("turn_id", "gate_id", "summary", "source", "model", "ts")},
        "recent_pairs": [
            {"text": b.get("text"), "verdict": VERDICTS.get(b.get("verdict") or "", {}).get("label", STATUS_LABELS.get(b.get("status"), "")), "reviewer": b.get("reviewer"), "at": b.get("paired_at"), "note": b.get("note")}
            for b in recent[:5]
        ],
        "lessons": [{"lesson": l.get("lesson"), "stage": l.get("stage")} for l in state.active_lessons()[:6]],
        "metrics": m,
        "chain": dict(verify or {}),
        "imported_v1": state.imported_v1,
        "last_checkpoint": state.checkpoints[-1] if state.checkpoints else None,
    }


def _task_progress(state: HelixState, task_id: str) -> str:
    rows = state.task_bases(task_id)
    if not rows:
        return "未播种"
    if any(b["status"] == "disputed" for b in rows):
        return "交叉核不一致"
    if all((b.get("cross") or {}).get("agree") for b in rows if b.get("needs_cross")) and all(b.get("verdict") for b in rows):
        return "双链闭合（草稿，未正式导入）"
    if any(b.get("verdict") for b in rows):
        return "已主核 · 待交叉核"
    if any(b["status"] == "deferred" for b in rows):
        return "暂缓"
    return "待主核"


def write_progress_file(ledger: HelixLedger, card: Mapping[str, Any]) -> str:
    """把接力卡写成 HELIX_PROGRESS.md：下次会话（人或 AI）都从这里接着做。"""
    m = card.get("metrics") or {}
    lines = [
        f"# Helix 接力卡 · {ledger.project_id}",
        "",
        f"- 生成时间：{card.get('generated_at')}",
        f"- 螺旋松紧度：{card.get('autonomy_label')}",
        f"- 账本链头：{(card.get('chain') or {}).get('head', '')[:16]} · 校验 {'通过' if (card.get('chain') or {}).get('ok') else '未通过/未执行'}",
        "",
        "## 下一步",
        f"- 真人：{card.get('next_human')}",
        f"- AI：{card.get('next_ai')}",
        "",
        "## 当前碱基",
        f"- 合计 {m.get('total', 0)}；G–C 待配对 {m.get('open_gc', 0)}；A–T 待抽检 {m.get('audit_pending', 0)}；暂缓 {m.get('deferred', 0)}",
        f"- 互补 {m.get('counts', {}).get('complement', 0)} · 修正 {m.get('counts', {}).get('revise', 0)} · 错配 {m.get('counts', {}).get('mismatch', 0)}",
        "",
        "## 修复记忆（AI 下一轮必须遵守）",
    ]
    lines += [f"- [{l.get('stage') or '全局'}] {l.get('lesson')}" for l in card.get("lessons") or []] or ["- 暂无"]
    sp = card.get("sprint") or {}
    if sp:
        lines += ["", f"## 本轮第 {sp.get('week')} 周 · {sp.get('track_label')}（剩 {sp.get('days_left')} 天）"]
        lines += [f"- {t['task_id']} · 主核 {t['owner']} ⇄ 交叉核 {t['cross_reviewer']} · 任务表 {t['status']} · Helix：{t['helix']}" for t in sp.get("tasks") or []]
        v4 = sp.get("v4") or {}
        if v4:
            lines += [f"- V4：{v4.get('discovery_status')} · 主张上限 {v4.get('claim_ceiling')} · 冻结 {v4.get('freeze')} · 验证 {v4.get('validation')} · 实际实验数据 {v4.get('actual_data_count')}"]
        lines += [f"- 来源快照哈希：{str(sp.get('snapshot_hash') or '')[:16]}"]
    if card.get("outcome_unknown"):
        lines += ["", "## ⚠ 结果未知的 AI 请求（需真人确认，不自动重试）"] + [f"- {r}" for r in card["outcome_unknown"]]
    lines += ["", "## 最近配对"]
    lines += [f"- {p.get('at')} · {p.get('reviewer')} · {p.get('verdict')}：{str(p.get('text') or '')[:120]}" for p in card.get("recent_pairs") or []] or ["- 暂无"]
    lines += ["", "> 边界：本文件是协同进度，不是科研结论；Gate、签字与发布仍以 1.00 原有流程为准。", ""]
    path = ledger.dir / "HELIX_PROGRESS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


__all__ = [
    "HarnessError",
    "acknowledge_request",
    "ai_snapshot_for",
    "TURN_PROMPT_VERSION",
    "build_turn_prompt",
    "import_v1_packet",
    "load_state",
    "open_helix",
    "pair_base",
    "propose_turn",
    "relay_card",
    "retire_lesson",
    "run_ai_turn",
    "set_autonomy",
    "telomere_checkpoint",
    "write_progress_file",
]


def sidebar_badge(project_id: str | None) -> str:
    """侧栏入口的小字：不触发迁移、不写账本，读失败时静默降级。"""
    if not project_id:
        return "进入人机配对"
    try:
        ledger = HelixLedger(str(project_id))
        if not ledger.exists():
            return "首次进入会自动备份 1.00 数据"
        state = load_state(ledger)
        if state.outcome_unknown:
            return "AI 请求结果未知 · 待真人确认"
        if state.fork_stalled:
            return f"{len(state.open_gc)} 个 G–C 等你配对 · 复制叉阻滞"
        cross = len(state.cross_pending())
        if cross:
            return f"{cross} 个待交叉核 · 双人互补链"
        audit = len(state.queue("audit"))
        return f"{audit} 个 A–T 待抽检" if audit else "复制叉前进中"
    except Exception:
        return "进入人机配对"
