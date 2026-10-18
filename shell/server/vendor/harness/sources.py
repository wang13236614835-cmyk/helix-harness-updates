"""真实科研数据只读适配器：把三库的权威文件投影成 Helix 可用的“科研骨架”。

只读、可降级、可复核：
- 只打开登记过的文件（V4 authority、本轮四周任务表、打卡映射、人审 Git 草稿模板）；
- 每读一个文件都记录 SHA-256 / 字节数 / 修改时间（来源见证 witness）；
- 所有见证再合成 ``snapshot_hash``，写进每一轮 AI 回合的输入哈希；
- 找不到文件只会显示“未找到”，绝不伪造或回填数据；
- 永远不写这些目录，Gate 和计数也不会因为 Helix 而改变。

根目录可通过以下方式覆盖（优先级从高到低）：
1. 环境变量 ``MASH_AIDD_RESEARCH_ROOTS``（JSON：{"v4_authority": "...", "sprint_dir": "...", "gnn_repo": "..."}）；
2. ``<STATE_STORE_DIR>/helix/sources.json``（同样的键）；
3. 内置默认（本机三库的标准位置，以及源码模式下仓库内的 research_v4）。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SOURCES_VERSION = "helix-sources.v1"

DEFAULT_ROOTS: dict[str, list[str]] = {
    "v4_authority": [
        "D:/aidd destoop/research_v4/current/authority",
    ],
    "sprint_dir": [
        "D:/zcode-workspace/aidd-repo-work/00-当前研究",
    ],
    "gnn_repo": [
        "D:/zcode-workspace/hepato-gnn-screening",
    ],
}

SPRINT_TASKS = "CURRENT_SPRINT_TASKS_20261012.csv"
SPRINT_PLAN = "CURRENT_SPRINT_20261012.md"
SPRINT_CHECKIN = "CURRENT_CHECKIN_W6_W9_MAP_20261012.csv"
W1_TEMPLATE = "W1_SCORING_TEMPLATE_20261012.csv"
REVIEW_TEMPLATE = "human_review/adora2a_mcule_20260927/review_result.template.json"

TRACK_LABELS = {
    "EXTERNAL_LIBRARY": "MCULE 外部库",
    "NATURAL_PRODUCTS": "天然产物 / 中药",
    "DRUG_REPURPOSING": "老药再利用",
}
RESEARCH_TRACK_LABELS = {
    "ONBOARDING": "工作台上手与分工",
    "SCORING": "打分",
    "DRUG_REPURPOSING": "老药反向筛选",
    "TCM_NATURAL_PRODUCTS": "中药反向筛选",
    "CLOSEOUT": "收尾",
}
DISCOVERY_GATES = ("D0", "D1", "D2", "D3", "D4", "D5", "D6")
MAX_BYTES = 4 * 1024 * 1024

# 本轮四周计划中的“红线”，原文摘录自 CURRENT_SPRINT_20261012.md；校对器按这些规则检查 AI 碱基。
SPRINT_RED_LINES = [
    "缺失=UNKNOWN 而非 0",
    "不跨不同靶点/assay 直接比较 pIC50 或 Docking 分数",
    "Docking 只是支撑，不能与活性等价",
    "不事后调权选赢家；加权总分需事先登记权重、校准和敏感性",
    "不捏造绝对疗效或正式“Top”",
    "不能把相似性/预测打成“老药治疗 MASH 已验证”",
    "药材来源 ≠ 标准品可购",
    "未通过当前数据/模型资格的预测只可标 EXPLORATORY_ONLY",
    "主核与交叉核不同人；不按日历强行 PASS",
    "W4 项目收尾 ≠ H1 Candidate Freeze、EP0 预注册、采购或湿实验已执行",
]
ALLOWED_SPRINT_DECISIONS = ["UNKNOWN", "OUT_OF_DOMAIN", "NEEDS_EVIDENCE", "HOLD", "REJECTED", "PASS"]


def _state_dir() -> Path | None:
    try:
        import runtime_paths
        return Path(runtime_paths.STATE_STORE_DIR) / "helix"
    except Exception:
        return None


def _app_root() -> Path:
    return Path(__file__).resolve().parents[1]


def configured_roots() -> dict[str, list[str]]:
    roots = {key: list(values) for key, values in DEFAULT_ROOTS.items()}
    # 便携包独立读取随包快照；既有桌面安装仍优先原始权威库。
    deployment = Path(__file__).resolve().parents[4]
    bundle = deployment / "research"
    bundled = {
        "v4_authority": [str(bundle / "v4" / "current" / "authority")],
        "sprint_dir": [str(bundle / "sprint")],
        "gnn_repo": [str(bundle / "gnn")],
    }
    if os.environ.get("HELIX_RESEARCH_ONLY_BUNDLED") == "1":
        return bundled
    installed_here = (deployment / "shell" / "launch" / "install.json").is_file()
    for key, paths in bundled.items():
        if Path(paths[0]).is_dir():
            roots[key] = roots[key] + paths if installed_here else paths + roots[key]
    # 源码模式：仓库内 research_v4 优先于默认绝对路径（同一份数据，不同挂载）。
    local_v4 = _app_root() / "research_v4" / "current" / "authority"
    roots["v4_authority"].insert(0, str(local_v4))
    overrides: dict[str, Any] = {}
    state_dir = _state_dir()
    if state_dir is not None:
        cfg = state_dir / "sources.json"
        try:
            overrides.update(json.loads(cfg.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    env = os.environ.get("MASH_AIDD_RESEARCH_ROOTS")
    if env:
        try:
            overrides.update(json.loads(env))
        except ValueError:
            pass
    for key, value in overrides.items():
        if key in roots and value:
            values = value if isinstance(value, list) else [value]
            roots[key] = [str(v) for v in values] + roots[key]
    return roots


@dataclass
class Witness:
    label: str
    path: str
    sha256: str = ""
    bytes: int = 0
    mtime: str = ""
    found: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ResearchSnapshot:
    generated_at: str
    today: str
    witnesses: list[Witness] = field(default_factory=list)
    v4: dict[str, Any] = field(default_factory=dict)
    sprint: dict[str, Any] = field(default_factory=dict)
    review_template: dict[str, Any] | None = None

    @property
    def found_any(self) -> bool:
        return any(w.found for w in self.witnesses)

    @property
    def snapshot_hash(self) -> str:
        canon = json.dumps([[w.label, w.sha256] for w in self.witnesses if w.found], ensure_ascii=False)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def witness(self, label: str) -> Witness | None:
        return next((w for w in self.witnesses if w.label == label), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources_version": SOURCES_VERSION,
            "generated_at": self.generated_at,
            "today": self.today,
            "snapshot_hash": self.snapshot_hash,
            "witnesses": [w.as_dict() for w in self.witnesses],
            "v4": self.v4,
            "sprint": self.sprint,
        }


def _read(label: str, candidates: list[Path], witnesses: list[Witness]) -> str | None:
    tried = [str(p) for p in candidates]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            raw = path.read_bytes()[: MAX_BYTES + 1]
            if len(raw) > MAX_BYTES:
                witnesses.append(Witness(label, str(path), error="文件过大，未读取"))
                return None
            stat = path.stat()
            witnesses.append(Witness(
                label, str(path), hashlib.sha256(raw).hexdigest(), len(raw),
                datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat(), True,
            ))
            return raw.decode("utf-8-sig")
        except OSError as exc:
            witnesses.append(Witness(label, str(path), error=str(exc)[:200]))
            return None
    witnesses.append(Witness(label, tried[0] if tried else "", error="未找到"))
    return None


def _json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _paths(roots: list[str], name: str) -> list[Path]:
    return [Path(root) / name for root in roots]


# ------------------------------------------------------------------ parsers
def parse_v4(discovery: Mapping[str, Any], validation: Mapping[str, Any], freeze: Mapping[str, Any]) -> dict[str, Any]:
    if not discovery:
        return {}
    gates = {}
    for gate in DISCOVERY_GATES:
        row = (discovery.get("gates") or {}).get(gate) or {}
        gates[gate] = {
            "raw_status": str(row.get("raw_status") or "NOT_EVALUATED"),
            "migration_status": str(row.get("migration_status") or ""),
            "evidence_count": len(row.get("evidence_ids") or []),
        }
    candidates = []
    for cid, row in (discovery.get("candidates") or {}).items():
        identity = row.get("identity") or {}
        tracks = row.get("source_tracks") or []
        candidates.append({
            "candidate_id": cid,
            "name": str(row.get("name") or cid),
            "track": str(tracks[0] if tracks else ""),
            "smiles": str(identity.get("smiles") or ""),
            "inchikey": str(identity.get("inchikey") or ""),
            "identity_status": str(identity.get("legacy_identity_status") or "UNKNOWN"),
            "qualification": str((row.get("qualification") or {}).get("raw_status") or "NOT_EVALUATED"),
        })
    tracks: dict[str, int] = {}
    for c in candidates:
        tracks[c["track"]] = tracks.get(c["track"], 0) + 1
    exceptions = []
    for row in discovery.get("exception_queue") or []:
        legacy = row.get("legacy_record") or {}
        exceptions.append({
            "task_id": str(row.get("task_id") or legacy.get("task_id") or ""),
            "category": str(row.get("category") or ""),
            "target": str(row.get("target") or ""),
            "status": str(row.get("status") or ""),
            "priority": str(legacy.get("priority") or ""),
            "object": str(legacy.get("object") or ""),
            "required_action": str(legacy.get("required_action") or "")[:800],
            "allowed_result": list(legacy.get("allowed_result") or []),
            "legacy_gate": str(legacy.get("gate") or ""),
            "primary_reviewer": str(legacy.get("primary_reviewer") or ""),
            "cross_reviewer": str(legacy.get("cross_reviewer") or ""),
            "git_result_path": str(legacy.get("git_result_path") or ""),
            "notes": str(legacy.get("notes") or "")[:400],
            "reason": str(row.get("reason") or "")[:400],
        })
    ctx = validation.get("execution_context") or {}
    ep0 = validation.get("ep0") or {}
    passed = [g for g, row in gates.items() if row["raw_status"] == "PASS"]
    model_ready = all(gates[g]["raw_status"] == "PASS" for g in ("D1", "D2"))
    return {
        "project_id": str(discovery.get("project_id") or ""),
        "schema_version": str(discovery.get("schema_version") or ""),
        "question": str((discovery.get("research_question") or {}).get("text") or "")[:600],
        "target": str((discovery.get("target") or {}).get("target_id") or ""),
        "target_direction": str((discovery.get("target") or {}).get("direction") or ""),
        "discovery_status": str(discovery.get("discovery_status") or ""),
        "candidate_freeze_ready": bool(discovery.get("candidate_freeze_ready")),
        "gates": gates,
        "gates_passed": passed,
        "human_nodes": dict(discovery.get("human_nodes") or {}),
        "candidates": candidates,
        "tracks": tracks,
        "shortlist": list(discovery.get("shortlist") or []),
        "exceptions": exceptions,
        "freeze": {
            "status": str(freeze.get("status") or "UNKNOWN"),
            "version": freeze.get("version"),
            "candidates": len(freeze.get("candidates") or []),
            "human_receipt": bool(freeze.get("human_receipt")),
        },
        "validation": {
            "status": str(validation.get("validation_status") or ""),
            "ep0": str(ep0.get("status") or ""),
            "executor": str(ctx.get("executor") or ""),
            "material_release": str(ctx.get("material_release") or ""),
            "actual_data_count": ctx.get("actual_data_count"),
            "sequence": list(ctx.get("sequence") or []),
        },
        "claim_ceiling": "MODEL_QUALIFIED" if model_ready else "EXPLORATORY_ONLY",
        "updated_at": str(discovery.get("updated_at") or ""),
    }


def parse_sprint(tasks_csv: str | None, checkin_csv: str | None, today: date) -> dict[str, Any]:
    if not tasks_csv:
        return {}
    tasks = []
    for row in csv.DictReader(io.StringIO(tasks_csv)):
        if not row.get("task_id"):
            continue
        tasks.append({
            "task_id": row["task_id"].strip(),
            "week": int(row.get("week") or 0),
            "start": (row.get("start") or "").strip(),
            "end": (row.get("end") or "").strip(),
            "owner": (row.get("owner") or "").strip(),
            "cross_reviewer": (row.get("cross_reviewer") or "").strip(),
            "track": (row.get("research_track") or "").strip(),
            "track_label": RESEARCH_TRACK_LABELS.get((row.get("research_track") or "").strip(), row.get("research_track") or ""),
            "deliverable": (row.get("deliverable") or "").strip(),
            "acceptance": (row.get("acceptance") or "").strip(),
            "status": (row.get("status") or "").strip() or "PENDING",
        })
    checkin = {}
    extras = {r.get('task_id','').strip():r for r in csv.DictReader(io.StringIO(tasks_csv))}
    for task in tasks:
        original = extras.get(task['task_id'],{})
        task.update({k:original[k] for k in ('execution_mode','human_work') if original.get(k)})
    for row in csv.DictReader(io.StringIO(checkin_csv or "")):
        if row.get("canonical_task_id"):
            checkin[row["canonical_task_id"].strip()] = {
                "html_week": row.get("html_week"),
                "items": [row.get(k) for k in ("html_task1", "html_task2", "html_weekly_report", "html_check1", "html_check2", "html_check3") if row.get(k)],
            }
    for task in tasks:
        task["checkin"] = checkin.get(task["task_id"], {})
    weeks: dict[int, dict[str, Any]] = {}
    for task in tasks:
        w = weeks.setdefault(task["week"], {"week": task["week"], "start": task["start"], "end": task["end"], "track": task["track"], "track_label": task["track_label"], "tasks": 0})
        w["tasks"] += 1
    current = None
    for w in sorted(weeks):
        try:
            if date.fromisoformat(weeks[w]["start"]) <= today <= date.fromisoformat(weeks[w]["end"]):
                current = w
        except ValueError:
            continue
    if current is None and weeks:
        first = min(weeks)
        try:
            current = first if today < date.fromisoformat(weeks[first]["start"]) else max(weeks)
        except ValueError:
            current = first
    days_left = None
    if current in weeks:
        try:
            days_left = (date.fromisoformat(weeks[current]["end"]) - today).days
        except ValueError:
            days_left = None
    members = sorted({t["owner"] for t in tasks} | {t["cross_reviewer"] for t in tasks if t["cross_reviewer"]})
    same_person = [t["task_id"] for t in tasks if t["owner"] and t["owner"] == t["cross_reviewer"]]
    return {
        "tasks": tasks,
        "weeks": [weeks[w] for w in sorted(weeks)],
        "current_week": current,
        "days_left": days_left,
        "members": members,
        "status_counts": {s: sum(1 for t in tasks if t["status"] == s) for s in sorted({t["status"] for t in tasks})},
        "same_person_violations": same_person,
        "red_lines": list(SPRINT_RED_LINES),
        "allowed_decisions": list(ALLOWED_SPRINT_DECISIONS),
    }


# --------------------------------------------------------------------- load
def load_research_snapshot(*, today: date | None = None, roots: Mapping[str, list[str]] | None = None) -> ResearchSnapshot:
    today = today or date.today()
    roots = dict(roots or configured_roots())
    witnesses: list[Witness] = []
    v4_roots = roots.get("v4_authority") or []
    discovery = _json(_read("V4 · DISCOVERY_STATE_V4.json", _paths(v4_roots, "DISCOVERY_STATE_V4.json"), witnesses))
    validation = _json(_read("V4 · VALIDATION_STATE_V4.json", _paths(v4_roots, "VALIDATION_STATE_V4.json"), witnesses))
    freeze = _json(_read("V4 · CANDIDATE_FREEZE.json", _paths(v4_roots, "CANDIDATE_FREEZE.json"), witnesses))
    sprint_roots = roots.get("sprint_dir") or []
    tasks_csv = _read("四周任务表 · " + SPRINT_TASKS, _paths(sprint_roots, SPRINT_TASKS), witnesses)
    checkin_csv = _read("打卡映射 · " + SPRINT_CHECKIN, _paths(sprint_roots, SPRINT_CHECKIN), witnesses)
    template = _json(_read("人审 Git 草稿模板", _paths(roots.get("gnn_repo") or [], REVIEW_TEMPLATE), witnesses)) or None
    return ResearchSnapshot(
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        today=today.isoformat(),
        witnesses=witnesses,
        v4=parse_v4(discovery, validation, freeze),
        sprint=parse_sprint(tasks_csv, checkin_csv, today),
        review_template=template,
    )


def ai_context(snap: ResearchSnapshot, *, max_candidates: int = 12) -> dict[str, Any]:
    """压缩后的科研骨架，进入 AI 回合快照（键名即可被 evidence_keys 引用）。"""
    v4, sprint = snap.v4, snap.sprint
    week = sprint.get("current_week")
    this_week = [t for t in sprint.get("tasks") or [] if t["week"] == week]
    return {
        "snapshot_hash": snap.snapshot_hash,
        "v4_gates": {g: row["raw_status"] for g, row in (v4.get("gates") or {}).items()},
        "v4_human_nodes": v4.get("human_nodes") or {},
        "v4_discovery_status": v4.get("discovery_status"),
        "v4_claim_ceiling": v4.get("claim_ceiling"),
        "v4_freeze": v4.get("freeze"),
        "v4_validation": v4.get("validation"),
        "v4_tracks": v4.get("tracks"),
        "v4_external_candidates": [
            {k: c[k] for k in ("candidate_id", "name", "identity_status")}
            for c in (v4.get("candidates") or []) if c["track"] == "EXTERNAL_LIBRARY"
        ][:max_candidates],
        "v4_exceptions": [
            {k: e[k] for k in ("task_id", "object", "target", "status", "priority", "primary_reviewer", "cross_reviewer")}
            for e in v4.get("exceptions") or []
        ],
        "sprint_week": week,
        "sprint_days_left": sprint.get("days_left"),
        "sprint_tasks_this_week": [
            {k: t[k] for k in ("task_id", "owner", "cross_reviewer", "track_label", "deliverable", "status")}
            for t in this_week
        ],
        "sprint_red_lines": sprint.get("red_lines") or [],
        "sprint_allowed_decisions": sprint.get("allowed_decisions") or [],
    }


__all__ = [
    "ALLOWED_SPRINT_DECISIONS",
    "DEFAULT_ROOTS",
    "ResearchSnapshot",
    "SPRINT_RED_LINES",
    "TRACK_LABELS",
    "Witness",
    "ai_context",
    "configured_roots",
    "load_research_snapshot",
    "parse_sprint",
    "parse_v4",
]
