"""真实四周任务 × 双螺旋：播种、双人互补链（主核 ⇄ 交叉核）与可交接导出。

- 播种：把本周真实任务（CURRENT_SPRINT_TASKS_20261012.csv）和 V4 异常队列，确定性地
  转成“任务碱基”（kind=task，永远 G–C，需双人互补）。播种不调用模型，不写任何科研目录。
- 双人互补链：主核在协同台配对（给出四周计划允许的结论 + 原始工件来源），
  再由“另一位真人”交叉核；同一人不能既主核又交叉核。
- 导出：
  * human_review_git_draft.v1 草稿（与 hepato-gnn-screening 的人审模板同构），由本人提交到自己的 Git；
    official_import 永远是 NOT_APPLIED —— Git 留痕不等于正式人审完成；
  * W1 打分卡预填 CSV（列名与 W1_SCORING_TEMPLATE_20261012.csv 一致，评分全部 UNKNOWN）；
  * 复现包：账本全文 + 来源见证 + 链头，离线可用 verify_bundle() 复核。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any, Mapping, Sequence

from . import HARNESS_VERSION
from .ledger import GENESIS, HelixLedger, entry_hash, utc_now
from .sources import ALLOWED_SPRINT_DECISIONS, ResearchSnapshot, TRACK_LABELS

W1_TEMPLATE_HEADER = [
    "candidate_id", "source_track", "compound_name", "structure_key", "target_id", "assay_type", "endpoint",
    "raw_model_score", "model_id", "dataset_hash", "model_qualification", "AD_status", "UQ_status",
    "ADMET_support", "docking_support", "primary_literature_ref", "negative_evidence", "score_dimension",
    "score_value", "score_rule_version", "missingness", "reviewer", "review_version", "decision", "notes",
]
W1_DIMENSIONS = [
    "compound_identity", "target_assay_source", "model_domain_uncertainty", "ADMET_PAINS",
    "structure_support", "direct_pharmacology_disease_evidence", "precedent", "purchasability",
]
DECISION_LABELS = {
    "PASS": "PASS · 本项满足",
    "NEEDS_EVIDENCE": "NEEDS_EVIDENCE · 需补证",
    "HOLD": "HOLD · 暂挂",
    "UNKNOWN": "UNKNOWN · 未知",
    "OUT_OF_DOMAIN": "OUT_OF_DOMAIN · 超出适用域",
    "REJECTED": "REJECTED · 拒判",
}


class SprintError(RuntimeError):
    """可向用户展示的任务链错误。"""


def week_label(snap: ResearchSnapshot, week: int | None = None) -> str:
    week = week if week is not None else snap.sprint.get("current_week")
    row = next((w for w in snap.sprint.get("weeks") or [] if w["week"] == week), None)
    if not row:
        return f"W{week}" if week else ""
    return f"W{week}_{row['start']}_to_{row['end'][5:]}"


def _seeded_keys(state: Any) -> set[str]:
    return {f"{b.get('task_id')}@{b.get('seed_week')}" for b in state.ordered_bases() if b.get("source") == "sprint_seed"}


def seed_rows(snap: ResearchSnapshot, *, week: int | None = None, include_exceptions: bool = False) -> list[dict[str, Any]]:
    """纯函数：从真实任务生成待播种的碱基（不落账本，便于预览与测试）。"""
    week = week if week is not None else snap.sprint.get("current_week")
    rows: list[dict[str, Any]] = []
    for task in snap.sprint.get("tasks") or []:
        if task["week"] != week:
            continue
        rows.append({
            "kind": "task",
            "text": f"{task['task_id']}｜{task['owner']} 主核：{task['deliverable']}",
            "rationale": f"来自四周任务表（{task['track_label']}，{task['start']}–{task['end']}）。验收：{task['acceptance']}；交叉核：{task['cross_reviewer']}。",
            "confidence": 0.5,
            "evidence_keys": ["research_context.sprint_tasks_this_week"],
            "task_id": task["task_id"],
            "owner": task["owner"],
            "cross_reviewer": task["cross_reviewer"],
            "track": task["track"],
            "seed_week": week,
            "task_status": task["status"],
        })
    if include_exceptions:
        for exc in (snap.v4.get("exceptions") or []):
            if exc.get("status") in {"CLOSED", "RESOLVED"}:
                continue
            rows.append({
                "kind": "task",
                "text": f"{exc['task_id']}｜{exc['primary_reviewer'] or '未指定'} 主核：{exc['object']}（{exc['target']}）",
                "rationale": f"V4 异常队列 {exc['category']} · {exc['status']} · {exc['priority']}。要求：{exc['required_action'][:300]} 备注：{exc['notes'][:160]}",
                "confidence": 0.5,
                "evidence_keys": ["research_context.v4_exceptions"],
                "task_id": exc["task_id"],
                "owner": exc["primary_reviewer"],
                "cross_reviewer": exc["cross_reviewer"],
                "track": "V4_EXCEPTION",
                "legacy_gate": exc["legacy_gate"],
                "allowed_result": exc["allowed_result"],
                "seed_week": week,
                "task_status": exc["status"],
            })
    return rows


def seed_from_sprint(ledger: HelixLedger, snap: ResearchSnapshot, *, week: int | None = None, include_exceptions: bool = False) -> dict[str, Any]:
    from .service import ai_snapshot_for, load_state, propose_turn
    if not snap.sprint.get("tasks"):
        raise SprintError("没有读到四周任务表（CURRENT_SPRINT_TASKS_20261012.csv）；请在“真实科研骨架”里检查来源路径。")
    week = week if week is not None else snap.sprint.get("current_week")
    state = load_state(ledger)
    done = _seeded_keys(state)
    rows = [r for r in seed_rows(snap, week=week, include_exceptions=include_exceptions) if f"{r['task_id']}@{week}" not in done]
    if not rows:
        raise SprintError(f"第 {week} 周的任务已经播种过了。")
    record_sources(ledger, snap)
    return propose_turn(
        ledger,
        raw_bases=rows,
        gate_id="SPRINT",
        phase="",
        summary=f"播种本轮第 {week} 周真实任务（{len(rows)} 条）：主核配对 → 另一位真人交叉核",
        source="sprint_seed",
        snapshot=ai_snapshot_for(None, snap),
        actor="harness",
        input_hash=snap.snapshot_hash,
        turn_meta={"seed_week": week, "snapshot_hash": snap.snapshot_hash},
    )


def record_sources(ledger: HelixLedger, snap: ResearchSnapshot) -> dict[str, Any] | None:
    from .service import load_state
    state = load_state(ledger)
    if state.sources and state.sources.get("snapshot_hash") == snap.snapshot_hash:
        return None
    return ledger.append(
        "sources.witnessed",
        {"snapshot_hash": snap.snapshot_hash, "witnesses": [w.as_dict() for w in snap.witnesses if w.found], "today": snap.today},
        actor="harness",
        actor_kind="system",
    )


# -------------------------------------------------------------- cross review
def cross_review(ledger: HelixLedger, base_id: str, *, reviewer: str, agree: bool, note: str, confirmed: bool) -> dict[str, Any]:
    from .service import HarnessError, load_state
    if not confirmed:
        raise HarnessError("请勾选“我本人独立核查了主核材料”。")
    state = load_state(ledger)
    base = state.bases.get(base_id)
    if base is None:
        raise HarnessError("找不到这个碱基。")
    if not base.get("verdict"):
        raise HarnessError("主核还没有配对，交叉核需要在主核之后进行。")
    if base.get("cross"):
        raise HarnessError("这个碱基已经交叉核过了。")
    primary = str(base.get("reviewer") or "").strip()
    if primary and primary.casefold() == str(reviewer or "").strip().casefold():
        raise HarnessError("主核与交叉核必须是不同的人（四周计划规则）。")
    note = str(note or "").strip()
    if not agree and len(note) < 4:
        raise HarnessError("交叉核不一致时必须写明理由（至少 4 个字）。")
    designated = str(base.get("cross_reviewer") or "")
    return ledger.append(
        "base.crossed",
        {
            "base_id": base_id,
            "agree": bool(agree),
            "note": note[:2000],
            "task_id": base.get("task_id") or "",
            "designated_cross_reviewer": designated,
            "is_designated": bool(designated) and designated.casefold() == str(reviewer).strip().casefold(),
        },
        actor=reviewer,
        actor_kind="human",
    )


# ------------------------------------------------------------------ exports
def _source_refs(lines: Sequence[str]) -> list[dict[str, str]]:
    refs = []
    for line in lines:
        line = str(line).strip()
        if not line:
            continue
        kind = "DOI" if line.lower().startswith(("10.", "doi:")) else "PMID" if line.lower().startswith("pmid") else "URL" if line.startswith("http") else "FILE_OR_RECORD"
        refs.append({"source_id": line[:120], "source_type": kind, "url_or_record": line[:500], "what_was_checked": "", "quote_or_location": ""})
    return refs or [{"source_id": "", "source_type": "", "url_or_record": "", "what_was_checked": "", "quote_or_location": ""}]


def review_draft(base: Mapping[str, Any], snap: ResearchSnapshot, *, chain_head: str) -> dict[str, Any]:
    if not base.get("verdict"):
        raise SprintError("只有已主核配对的碱基才能导出人审草稿。")
    template = dict(snap.review_template or {})
    cross = base.get("cross") or {}
    draft = {
        **{k: v for k, v in template.items() if k not in {"source_refs", "cross_review", "official_import"}},
        "schema_version": str(template.get("schema_version") or "human_review_git_draft.v1"),
        "week": week_label(snap, base.get("seed_week")),
        "task_id": str(base.get("task_id") or base.get("base_id")),
        "gate": str(base.get("legacy_gate") or base.get("track") or base.get("gate_id") or ""),
        "reviewer_name": str(base.get("reviewer") or ""),
        "reviewer_git_account": "",
        "decision": str(base.get("decision") or "UNKNOWN"),
        "reviewed_at": str(base.get("paired_at") or ""),
        "reason": str(base.get("note") or base.get("revised_text") or ""),
        "human_value": None,
        "source_refs": _source_refs(base.get("source_refs") or []),
        "limitations": "由 Helix Harness 从协同台导出的草稿；reviewer_git_account 与 source_refs 细节需本人补全后用本人 Git 账号提交。",
        "cross_review": {
            "reviewer_name": str(cross.get("reviewer") or base.get("cross_reviewer") or ""),
            "reviewer_git_account": "",
            "status": "AGREE" if cross.get("agree") else ("DISAGREE" if cross else "PENDING"),
            "comment": str(cross.get("note") or ""),
        },
        "official_import": {
            "status": "NOT_APPLIED",
            "note": "Git留痕不等于AIDD正式人审完成；仅受控导入后计数。",
        },
        "helix_provenance": {
            "harness_version": HARNESS_VERSION,
            "base_id": base.get("base_id"),
            "ledger_head": chain_head,
            "research_snapshot_hash": snap.snapshot_hash,
            "ai_or_harness_origin": base.get("source"),
        },
    }
    return draft


def export_review_draft(ledger: HelixLedger, base_id: str, snap: ResearchSnapshot, *, reviewer: str) -> tuple[str, str]:
    from .service import load_state
    state = load_state(ledger)
    base = state.bases.get(base_id)
    if base is None:
        raise SprintError("找不到这个碱基。")
    draft = review_draft(base, snap, chain_head=ledger.head())
    text = json.dumps(draft, ensure_ascii=False, indent=2) + "\n"
    name = f"{draft['task_id']}__{draft['reviewer_name'] or 'reviewer'}.json".replace("/", "_")
    out = ledger.dir / "exports" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    ledger.append(
        "export.made",
        {"kind": "human_review_git_draft.v1", "base_id": base_id, "path": str(out), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
        actor=reviewer,
        actor_kind="human",
    )
    return name, text


def scorecard_csv(snap: ResearchSnapshot, *, track: str = "EXTERNAL_LIBRARY") -> str:
    """W1 打分卡预填：只填 V4 已有的身份字段，评分一律 UNKNOWN（缺失≠0）。"""
    v4 = snap.v4
    disc = snap.witness("V4 · DISCOVERY_STATE_V4.json")
    sha = disc.sha256[:16] if disc and disc.found else "未读取"
    gates = v4.get("gates") or {}
    qual = "NOT_QUALIFIED(D1=%s,D2=%s)" % ((gates.get("D1") or {}).get("raw_status", "?"), (gates.get("D2") or {}).get("raw_status", "?"))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=W1_TEMPLATE_HEADER, lineterminator="\n")
    writer.writeheader()
    for cand in v4.get("candidates") or []:
        if cand["track"] != track:
            continue
        for dim in W1_DIMENSIONS:
            writer.writerow({
                "candidate_id": cand["candidate_id"],
                "source_track": cand["track"],
                "compound_name": cand["name"],
                "structure_key": cand["inchikey"],
                "target_id": v4.get("target") or "",
                "assay_type": "UNKNOWN", "endpoint": "UNKNOWN", "raw_model_score": "UNKNOWN",
                "model_id": "UNKNOWN", "dataset_hash": "UNKNOWN", "model_qualification": qual,
                "AD_status": "UNKNOWN", "UQ_status": "UNKNOWN", "ADMET_support": "UNKNOWN",
                "docking_support": "UNKNOWN", "primary_literature_ref": "", "negative_evidence": "",
                "score_dimension": dim, "score_value": "UNKNOWN", "score_rule_version": "W1_SCORING_PROTOCOL(未冻结)",
                "missingness": "UNKNOWN_NOT_ZERO", "reviewer": "", "review_version": "", "decision": "HOLD",
                "notes": f"Helix 预填：身份字段来自 DISCOVERY_STATE_V4 sha256:{sha}（{cand['identity_status']}）；评分未填写，不含任何伪造数值；{TRACK_LABELS.get(track, track)}",
            })
    return buf.getvalue()


def bundle(ledger: HelixLedger, snap: ResearchSnapshot | None) -> dict[str, Any]:
    entries = ledger.entries()
    report = ledger.verify()
    return {
        "bundle_version": "helix-bundle.v1",
        "harness_version": HARNESS_VERSION,
        "project_id": ledger.project_id,
        "exported_at": utc_now(),
        "ledger_head": report.head,
        "ledger_ok": report.ok,
        "entries": entries,
        "research_snapshot": ({"snapshot_hash": snap.snapshot_hash, "witnesses": [w.as_dict() for w in snap.witnesses]} if snap else None),
        "limitations": [
            "复现包证明的是协同过程的完整性（哈希链），不是科学结论的有效性。",
            "不含原始科研文件本身，只含其 SHA-256 见证；复核时需对照原路径重新计算。",
            "本机单写者、无外部签名；不能防止有意的整体重算。",
        ],
    }


def verify_bundle(data: Mapping[str, Any]) -> dict[str, Any]:
    prev = GENESIS
    entries = list(data.get("entries") or [])
    for index, entry in enumerate(entries, start=1):
        if entry.get("prev") != prev:
            return {"ok": False, "count": index, "problem": f"第 {index} 条 prev 不连续"}
        if entry_hash(entry) != entry.get("hash"):
            return {"ok": False, "count": index, "problem": f"第 {index} 条内容与哈希不符"}
        prev = str(entry.get("hash"))
    ok = prev == data.get("ledger_head") or not entries
    return {"ok": ok, "count": len(entries), "head": prev, "problem": "" if ok else "链头与包内记录不一致"}


__all__ = [
    "ALLOWED_SPRINT_DECISIONS",
    "DECISION_LABELS",
    "SprintError",
    "W1_DIMENSIONS",
    "W1_TEMPLATE_HEADER",
    "bundle",
    "cross_review",
    "export_review_draft",
    "record_sources",
    "review_draft",
    "scorecard_csv",
    "seed_from_sprint",
    "seed_rows",
    "verify_bundle",
    "week_label",
]
