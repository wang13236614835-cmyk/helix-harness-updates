"""氢键分级：决定一个 AI 碱基是“先延伸后抽检（A–T）”还是“必须先配对（G–C）”。

规则刻意保守：凡触及 Gate、签字、冻结、发布、排除、停止、湿实验或
真人决策点 H0–H3/EP0 的内容，永远是 G–C，任何松紧度都不能放松。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

AT = "AT"  # 2 个氢键：低风险，AI 可先行，按比例事后抽检
GC = "GC"  # 3 个氢键：高风险，真人先配对，复制叉才前进

AUTONOMY_LEVELS: dict[str, dict[str, Any]] = {
    "tight": {"label": "紧螺旋", "desc": "所有碱基都按 G–C 处理，每步都由真人先配对。适合课题刚起步、方向未定。", "sample_rate": 1.0, "min_confidence": 1.01},
    "standard": {"label": "标准螺旋", "desc": "高风险或低置信先配对；常规检索、整理、计算类动作先行，按 25% 抽检。", "sample_rate": 0.25, "min_confidence": 0.55},
    "loose": {"label": "松螺旋", "desc": "只有硬边界与很低置信需要先配对；其余 AI 先行，按 10% 抽检。适合重复性批处理。", "sample_rate": 0.10, "min_confidence": 0.35},
}
DEFAULT_AUTONOMY = "standard"

HUMAN_DECISION_PHASES = frozenset({"H0", "H1", "H2", "H3", "EP0"})
_HARD_BOUNDARY = re.compile(
    r"(gate|签字|签署|冻结|freeze|发布|release|排除|剔除|淘汰|停止|终止|stop|湿实验|wet ?lab|动物实验|预注册|"
    r"总验收|主张层级|冻结主张|最终结论|下结论|定稿|批准|approve|sign[- ]?off)",
    re.IGNORECASE,
)
_ROUTINE = re.compile(r"(检索|搜索|下载|整理|汇总|核对|去重|标准化|计算|运行|对齐|提取|解析|统计|绘图|refresh|sync)", re.IGNORECASE)

KIND_LABELS = {
    "action": "建议动作",
    "gap": "证据缺口",
    "conflict": "冲突 / 风险",
    "tool": "工具调用",
    "question": "真人问题",
    "task": "本轮任务主件",
}


def clamp_confidence(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    return max(0.0, min(1.0, number))


def classify_bond(
    base: Mapping[str, Any],
    *,
    phase: str = "",
    autonomy: str = DEFAULT_AUTONOMY,
) -> tuple[str, list[str]]:
    """Return ``(bond, reasons)`` for one base."""
    level = AUTONOMY_LEVELS.get(autonomy, AUTONOMY_LEVELS[DEFAULT_AUTONOMY])
    text = str(base.get("text") or "")
    kind = str(base.get("kind") or "action")
    confidence = clamp_confidence(base.get("confidence"))
    reasons: list[str] = []
    if kind == "task" or base.get("task_id"):
        reasons.append("本轮真实任务：真人主核配对 + 另一位真人交叉核（双人互补链）")
    if str(phase).upper() in HUMAN_DECISION_PHASES:
        reasons.append(f"当前处于真人决策点 {phase}")
    if kind in {"question", "conflict"}:
        reasons.append("真人问题 / 冲突必须由真人裁决" if kind == "question" else "冲突需要真人判断取舍")
    if _HARD_BOUNDARY.search(text):
        reasons.append("触及 Gate / 冻结 / 发布 / 排除 / 结论等硬边界")
    if base.get("proofread_block"):
        reasons.append("机器校对发现越界表述")
    if base.get("repeat_mismatch"):
        reasons.append("与已记录的错配高度相似")
    if confidence < level["min_confidence"]:
        reasons.append(f"AI 置信度 {confidence:.2f} 低于本松紧度阈值 {level['min_confidence']:.2f}" if level["min_confidence"] <= 1 else "紧螺旋：全部先配对")
    if reasons:
        return GC, reasons
    if kind == "tool" or _ROUTINE.search(text):
        return AT, ["常规检索 / 整理 / 计算类动作，可先行并事后抽检"]
    return AT, ["未触及硬边界，按抽检处理"]


def sampled_for_audit(base_id: str, autonomy: str = DEFAULT_AUTONOMY) -> bool:
    """Deterministic audit sampling: same base always gets the same decision."""
    rate = AUTONOMY_LEVELS.get(autonomy, AUTONOMY_LEVELS[DEFAULT_AUTONOMY])["sample_rate"]
    if rate >= 1.0:
        return True
    bucket = int(hashlib.sha256(str(base_id).encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < rate


__all__ = [
    "AT",
    "AUTONOMY_LEVELS",
    "DEFAULT_AUTONOMY",
    "GC",
    "HUMAN_DECISION_PHASES",
    "KIND_LABELS",
    "clamp_confidence",
    "classify_bond",
    "sampled_for_audit",
]
