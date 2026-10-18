"""机器校对（类比 DNA 聚合酶 3'→5' 校对）：AI 碱基到达真人前先过规则检查。

校对不替真人判断科学对错，只拦截三类机器可判定的问题：
1. 越界表述：把计算预测说成已验证、宣布 Gate 通过、替真人签字等；
   以及本轮四周计划的红线（缺失记 0、Docking=活性、正式 Top、老药已验证……）；
2. 无来源：引用的输入字段在当前快照里不存在；
3. 重复错配：与真人已否决过的内容高度相似（防止 AI 反复提同一个错误）。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

_OVERCLAIM = re.compile(
    r"(gate\s*(已)?通过|已通过\s*gate|已(被)?(实验)?验证|实验证实|证实了|确证|已批准|批准发布|可以发布|"
    r"代(替)?真人|自动签|已签字|一定有效|必然|毫无疑问|guarantee|proven)",
    re.IGNORECASE,
)
# 本轮四周计划红线（CURRENT_SPRINT_20261012.md），命中即拦截到真人面前。
_RED_LINES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(缺失|未知|missing|空值|无数据).{0,8}(记|设|填|按|视为|当作)\s*(为|成)?\s*0(?![.\d])", re.I), "红线：缺失必须记 UNKNOWN，不能记 0"),
    (re.compile(r"(docking|对接|vina|打分).{0,14}(证明|说明|即|等于|等同|代表|就是).{0,8}(活性|疗效|有效|结合力)", re.I), "红线：Docking 只是支撑，不能与活性等价"),
    (re.compile(r"(老药|已上市药|drug).{0,12}(治疗|改善|逆转).{0,6}MASH.{0,6}(已|被)?(验证|证实|有效)", re.I), "红线：不能把相似性/预测写成“老药治疗 MASH 已验证”"),
    (re.compile(r"(正式\s*(榜|排名|候选)|(?<![A-Za-z])Top\s*-?\s*\d+|冠军|赢家)", re.I), "红线：不捏造正式 Top / 榜单；只输出可解释排序或拒判"),
    (re.compile(r"(事后|重新|调整).{0,4}(调权|权重).{0,12}(选|让|使).{0,24}(赢|第一|胜出|靠前)", re.I), "红线：不事后调权选赢家"),
    (re.compile(r"(跨|不同)\s*(靶点|assay|测定).{0,12}(直接)?(比较|排序|比大小).{0,6}(pIC50|IC50|Ki|docking|对接|分数)", re.I), "红线：不跨靶点/assay 直接比较 pIC50 或 Docking 分数"),
    (re.compile(r"(药材|植物来源|中药来源).{0,10}(即|等于|说明|代表).{0,6}(可购|可采购|标准品)", re.I), "红线：药材来源 ≠ 标准品可购"),
]
_SCORE_CLAIM = re.compile(r"(评分|打分|得分|score|排序|排名|优先级|rank)", re.I)
_EXPLORATORY = re.compile(r"(探索|exploratory|EXPLORATORY_ONLY|假说|待核|未经资格)", re.I)
_TOKEN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+")


def _tokens(text: str) -> set[str]:
    raw = _TOKEN.findall(str(text or "").lower())
    grams = set(raw)
    grams.update(a + b for a, b in zip(raw, raw[1:]))
    return grams


def similarity(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def snapshot_keys(snapshot: Mapping[str, Any] | None, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if not isinstance(snapshot, Mapping):
        return keys
    for key, value in snapshot.items():
        name = f"{prefix}{key}"
        keys.add(str(key))
        keys.add(name)
        if isinstance(value, Mapping):
            keys |= snapshot_keys(value, f"{name}.")
    return keys


def proofread_base(
    base: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any] | None,
    mismatches: Iterable[Mapping[str, Any]] = (),
    threshold: float = 0.55,
) -> dict[str, Any]:
    notes: list[str] = []
    text = str(base.get("text") or "")
    block = False
    if _OVERCLAIM.search(text) or _OVERCLAIM.search(str(base.get("rationale") or "")):
        notes.append("越界表述：出现“已验证 / Gate 通过 / 批准”等 AI 无权给出的结论")
        block = True
    joined = text + "\n" + str(base.get("rationale") or "")
    for pattern, label in _RED_LINES:
        if pattern.search(joined):
            notes.append(label)
            block = True
    ceiling = ""
    if isinstance(snapshot, Mapping):
        ctx = snapshot.get("research_context")
        if isinstance(ctx, Mapping):
            ceiling = str(ctx.get("v4_claim_ceiling") or "")
    if ceiling == "EXPLORATORY_ONLY" and _SCORE_CLAIM.search(text) and not _EXPLORATORY.search(joined):
        notes.append("主张上限：V4 D1/D2 未通过，评分/排序类内容必须标明 EXPLORATORY_ONLY（探索性）")
    keys = [str(k) for k in base.get("evidence_keys") or [] if str(k).strip()]
    if snapshot is not None:
        known = snapshot_keys(snapshot)
        missing = [k for k in keys if k not in known]
        if not keys:
            notes.append("无来源：没有指出依据的输入字段")
        elif missing:
            notes.append("来源字段不在当前快照中：" + "、".join(missing[:5]))
    repeat = None
    for lesson in mismatches:
        score = similarity(text, str(lesson.get("base_text") or ""))
        if score >= threshold:
            repeat = {"lesson_id": lesson.get("lesson_id"), "score": round(score, 2)}
            notes.append(f"与已否决内容相似度 {score:.0%}，请对照修复记忆")
            break
    return {"notes": notes, "block": block, "repeat": repeat}


__all__ = ["proofread_base", "similarity", "snapshot_keys"]
