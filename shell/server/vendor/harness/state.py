"""把螺旋账本折叠成当前状态：碱基、配对、修复记忆、松紧度与健康度。

状态只从账本推导（事件溯源），页面不保存平行状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any, Iterable, Mapping

from .bonds import AT, DEFAULT_AUTONOMY, GC, sampled_for_audit

VERDICTS = {
    "complement": {"label": "互补", "desc": "接受，AI 碱基与真人判断一致", "score": 1.0},
    "revise": {"label": "修正", "desc": "方向对，但需要按真人意见改写", "score": 0.5},
    "mismatch": {"label": "错配", "desc": "否决，并写下原因供 AI 修复", "score": 0.0},
    "defer": {"label": "暂缓", "desc": "现在不判断，进入暂缓区", "score": None},
}

STATUS_LABELS = {
    "open": "待配对 · G–C",
    "audit": "待抽检 · A–T",
    "auto": "已先行 · A–T",
    "deferred": "暂缓",
    "complement": "互补",
    "revise": "已修正",
    "mismatch": "错配",
    "disputed": "交叉核不一致",
}


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


@dataclass
class HelixState:
    project_id: str
    autonomy: str = DEFAULT_AUTONOMY
    bases: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    lessons: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    imported_v1: dict[str, Any] | None = None
    blocked_turns: int = 0
    events: int = 0
    requests: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, Any] | None = None
    exports: list[dict[str, Any]] = field(default_factory=list)

    # ---------------------------------------------------------------- views
    def ordered_bases(self) -> list[dict[str, Any]]:
        return [self.bases[base_id] for base_id in self.order if base_id in self.bases]

    def queue(self, status: str) -> list[dict[str, Any]]:
        rows = [b for b in self.ordered_bases() if b["status"] == status]
        return sorted(rows, key=lambda b: (b.get("confidence", 0.5), b.get("seq", 0)))

    @property
    def open_gc(self) -> list[dict[str, Any]]:
        """AI / 1.00 工作包提出、尚未配对的 G–C 碱基（会阻滞复制叉）。"""
        return [b for b in self.queue("open") if b.get("kind") != "task"]

    @property
    def open_tasks(self) -> list[dict[str, Any]]:
        """真实任务碱基：周期长，由主核按自己的节奏配对，不阻滞 AI。"""
        return [b for b in self.queue("open") if b.get("kind") == "task"]

    @property
    def fork_stalled(self) -> bool:
        """复制叉阻滞：还有未配对的 AI G–C 碱基时，AI 不得开启新一轮。"""
        return bool(self.open_gc)

    def cross_pending(self) -> list[dict[str, Any]]:
        """双人互补链：已由主核配对、但还缺“另一位真人”交叉核的 G–C 碱基。"""
        return [
            b for b in self.ordered_bases()
            if b.get("needs_cross") and b.get("verdict") and not b.get("cross") and b["status"] != "disputed"
        ]

    @property
    def outcome_unknown(self) -> list[dict[str, Any]]:
        """非幂等模型调用：已发出请求、但没有结果也没有失败回执的回合。"""
        return [r for r in self.requests.values() if r.get("state") == "requested"]

    def task_bases(self, task_id: str) -> list[dict[str, Any]]:
        return [b for b in self.ordered_bases() if b.get("task_id") == task_id]

    def active_lessons(self, stage: str | None = None) -> list[dict[str, Any]]:
        rows = [l for l in self.lessons.values() if l.get("active")]
        if stage:
            rows = [l for l in rows if not l.get("stage") or l.get("stage") == stage] or rows
        return sorted(rows, key=lambda l: l.get("seq", 0), reverse=True)

    def mismatch_texts(self) -> list[dict[str, Any]]:
        return [l for l in self.lessons.values() if l.get("active") and l.get("verdict") == "mismatch"]

    def metrics(self) -> dict[str, Any]:
        bases = self.ordered_bases()
        decided = [b for b in bases if b.get("verdict") in {"complement", "revise", "mismatch"}]
        counts = {key: sum(1 for b in bases if b.get("verdict") == key) for key in VERDICTS}
        latencies = []
        for b in decided:
            start, end = _parse(b.get("proposed_at")), _parse(b.get("paired_at"))
            if start and end:
                latencies.append(max(0.0, (end - start).total_seconds() / 60.0))
        brier = None
        if decided:
            brier = sum((b.get("confidence", 0.5) - VERDICTS[b["verdict"]]["score"]) ** 2 for b in decided) / len(decided)

        def _mean(rows: Iterable[dict[str, Any]]) -> float | None:
            values = [float(r.get("confidence", 0.5)) for r in rows]
            return round(sum(values) / len(values), 3) if values else None

        total = len(bases)
        need_human = [b for b in bases if b["bond"] == GC or b.get("sampled")]
        paired_need = [b for b in need_human if b.get("verdict")]
        return {
            "total": total,
            "gc": sum(1 for b in bases if b["bond"] == GC),
            "at": sum(1 for b in bases if b["bond"] == AT),
            "open_gc": len(self.open_gc),
            "open_tasks": len(self.open_tasks),
            "audit_pending": len(self.queue("audit")),
            "auto": len(self.queue("auto")),
            "deferred": len(self.queue("deferred")),
            "counts": counts,
            "pairing_rate": round(len(paired_need) / len(need_human), 3) if need_human else None,
            "acceptance_rate": round(counts["complement"] / len(decided), 3) if decided else None,
            "mismatch_rate": round(counts["mismatch"] / len(decided), 3) if decided else None,
            "brier": round(brier, 3) if brier is not None else None,
            "conf_complement": _mean(b for b in decided if b["verdict"] == "complement"),
            "conf_mismatch": _mean(b for b in decided if b["verdict"] == "mismatch"),
            "median_wait_min": round(median(latencies), 1) if latencies else None,
            "lessons_active": len(self.active_lessons()),
            "proofread_blocked": sum(1 for b in bases if b.get("proofread_block")),
            "turns": len(self.turns),
            "blocked_turns": self.blocked_turns,
            "cross_pending": len(self.cross_pending()),
            "duplex": sum(1 for b in bases if (b.get("cross") or {}).get("agree")),
            "disputed": sum(1 for b in bases if b["status"] == "disputed"),
            "outcome_unknown": len(self.outcome_unknown),
            "seeded": sum(1 for b in bases if b.get("source") == "sprint_seed"),
        }


def fold_state(project_id: str, entries: Iterable[Mapping[str, Any]]) -> HelixState:
    state = HelixState(project_id=project_id)
    for entry in entries:
        state.events += 1
        kind = entry.get("type")
        payload = dict(entry.get("payload") or {})
        if kind == "autonomy.set":
            state.autonomy = str(payload.get("level") or DEFAULT_AUTONOMY)
        elif kind == "import.v1":
            state.imported_v1 = {**payload, "ts": entry.get("ts")}
        elif kind == "turn.opened":
            state.turns.append({**payload, "ts": entry.get("ts"), "actor": entry.get("actor"), "seq": entry.get("seq")})
            req = state.requests.get(str(payload.get("request_id") or ""))
            if req is not None:
                req.update({"state": "opened", "turn_id": payload.get("turn_id")})
        elif kind == "turn.requested":
            rid = str(payload.get("request_id"))
            state.requests[rid] = {**payload, "state": "requested", "ts": entry.get("ts"), "actor": entry.get("actor")}
        elif kind == "turn.failed":
            req = state.requests.get(str(payload.get("request_id")))
            if req is not None:
                req.update({"state": "failed", "error": payload.get("error") or "", "failed_at": entry.get("ts")})
        elif kind == "turn.acknowledged":
            req = state.requests.get(str(payload.get("request_id")))
            if req is not None:
                req.update({"state": "acknowledged", "ack_by": entry.get("actor"), "ack_note": payload.get("note") or "", "ack_at": entry.get("ts")})
        elif kind == "sources.witnessed":
            state.sources = {**payload, "ts": entry.get("ts")}
        elif kind == "export.made":
            state.exports.append({**payload, "ts": entry.get("ts"), "actor": entry.get("actor")})
        elif kind == "base.crossed":
            base = state.bases.get(str(payload.get("base_id")))
            if base is None:
                continue
            agree = bool(payload.get("agree"))
            base["cross"] = {"reviewer": entry.get("actor"), "agree": agree, "note": payload.get("note") or "", "at": entry.get("ts")}
            if not agree:
                base["status"] = "disputed"
        elif kind == "turn.blocked":
            state.blocked_turns += 1
        elif kind == "checkpoint":
            state.checkpoints.append({**payload, "ts": entry.get("ts"), "actor": entry.get("actor")})
        elif kind == "base.proposed":
            base_id = str(payload.get("base_id"))
            bond = str(payload.get("bond") or GC)
            sampled = bond == GC or bool(payload.get("sampled"))
            status = "open" if bond == GC else ("audit" if sampled else "auto")
            state.bases[base_id] = {
                **payload,
                "seq": entry.get("seq"),
                "proposed_at": entry.get("ts"),
                "sampled": sampled if bond == AT else False,
                "status": status,
                "verdict": None,
                "needs_cross": bool(payload.get("needs_cross")),
            }
            state.order.append(base_id)
        elif kind == "base.paired":
            base = state.bases.get(str(payload.get("base_id")))
            if base is None:
                continue
            verdict = str(payload.get("verdict"))
            base.update({
                "verdict": verdict if verdict != "defer" else None,
                "status": "deferred" if verdict == "defer" else verdict,
                "paired_at": entry.get("ts"),
                "reviewer": entry.get("actor"),
                "note": payload.get("note") or "",
                "revised_text": payload.get("revised_text") or "",
                "decision": payload.get("decision") or "",
                "source_refs": list(payload.get("source_refs") or []),
                "is_designated_owner": payload.get("is_designated_owner"),
            })
        elif kind == "repair.learned":
            lesson_id = str(payload.get("lesson_id"))
            state.lessons[lesson_id] = {**payload, "active": True, "seq": entry.get("seq"), "ts": entry.get("ts"), "actor": entry.get("actor")}
        elif kind == "repair.retired":
            lesson = state.lessons.get(str(payload.get("lesson_id")))
            if lesson:
                lesson.update({"active": False, "retired_by": entry.get("actor"), "retired_reason": payload.get("reason") or ""})
    return state


def resample(base_id: str, autonomy: str) -> bool:
    return sampled_for_audit(base_id, autonomy)


__all__ = ["HelixState", "STATUS_LABELS", "VERDICTS", "fold_state"]
