"""最小垫片：与 Workbench 2.00 的 core.review_session.assert_human_actor 行为一致的子集。

真人写入（配对、交叉核、检查点）必须带一个真实的人名；空值、"ai"、"agent"、"system"、"harness"
以及 ai-/agent- 前缀一律拒绝。
"""
from __future__ import annotations

_MACHINE = {"ai", "agent", "system", "harness", "bot", "model", "llm"}


def assert_human_actor(actor: str) -> str:
    name = str(actor or "").strip()
    low = name.lower()
    if not name:
        raise PermissionError("真人写入必须填写你的名字。")
    if low in _MACHINE or low.startswith(("ai-", "ai:", "agent-", "agent:", "bot-", "model-")):
        raise PermissionError(f"“{name}”看起来不是真人；配对和交叉核只能由真人完成。")
    if len(name) > 64:
        raise PermissionError("名字太长（最多 64 个字符）。")
    return name
