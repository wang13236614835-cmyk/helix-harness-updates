"""MASH-AIDD Helix Harness（v2.00）——人机协同双螺旋 Harness 层。

设计隐喻（个人特色）：科研过程是一条正在复制的 DNA 双链。
- AI 链（青绿）负责“合成”：拆任务、找证据缺口、找冲突、提出工具动作；
- 真人链（琥珀）负责“配对”：每个 AI 碱基必须得到真人的互补、修正、错配或暂缓；
- 氢键分级：A–T（2 键）= 低风险，AI 可先延伸、事后抽检；
            G–C（3 键）= 高风险，必须真人先配对，复制叉才能前进；
- 校对（proofreading）：AI 输出在到达真人前先过机器规则校对；
- 错配修复（mismatch repair）：真人的否决与修正沉淀为“修复记忆”，注入下一轮 AI；
- 接力卡（relay card）：每次会话从同一份进度文件接着做，不靠聊天记忆。

硬边界（沿用 1.00，不放松）：Harness 只追加写入自己的螺旋账本；
不修改 V4 Gate、不代签人工核验、不发布候选、不把计算预测写成实验事实。
"""
from __future__ import annotations

HARNESS_VERSION = "helix-harness.v2"

from .ledger import HelixLedger, LedgerIntegrityError  # noqa: E402
from .bonds import AUTONOMY_LEVELS, classify_bond  # noqa: E402
from .state import HelixState, fold_state  # noqa: E402

__all__ = [
    "AUTONOMY_LEVELS",
    "HARNESS_VERSION",
    "HelixLedger",
    "HelixState",
    "LedgerIntegrityError",
    "classify_bond",
    "fold_state",
]
