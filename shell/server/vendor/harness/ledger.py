"""螺旋账本：按项目隔离、只追加、哈希链接的 JSONL 审计账本。

每一条记录都包含 ``prev``（上一条哈希）与 ``hash``（本条规范化内容的
SHA-256）。任何离线篡改、删行或乱序都会让 :meth:`HelixLedger.verify` 失败。
账本只记录 Harness 自己的协同事件，不复制、不改写 1.00 的 store.json /
SQLite 数据。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

GENESIS = "0" * 64
_LOCK = threading.Lock()
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")

# 只有这些事件类型可以写入；新增类型必须显式登记，防止账本语义漂移。
EVENT_TYPES = frozenset({
    "genesis",
    "import.v1",
    "turn.opened",
    "turn.blocked",
    "base.proposed",
    "base.paired",
    "repair.learned",
    "repair.retired",
    "autonomy.set",
    "checkpoint",
    # v2：真实科研骨架、双人互补链与非幂等模型调用
    "sources.witnessed",
    "turn.requested",
    "turn.failed",
    "turn.acknowledged",
    "base.crossed",
    "export.made",
})
HUMAN_ONLY = frozenset({
    "base.paired", "repair.retired", "autonomy.set", "checkpoint",
    "base.crossed", "turn.acknowledged", "export.made",
})


class LedgerIntegrityError(RuntimeError):
    """账本哈希链断裂或内容被改动。"""


class LedgerPermissionError(PermissionError):
    """机器身份试图写入只允许真人写入的事件。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def entry_hash(entry: Mapping[str, Any]) -> str:
    body = {key: value for key, value in entry.items() if key != "hash"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _is_machine(actor: str) -> bool:
    lowered = str(actor or "").strip().lower()
    return lowered in {"ai", "agent", "system", "harness"} or lowered.startswith(("ai-", "ai:", "agent-", "agent:"))


@dataclass
class VerifyReport:
    ok: bool
    count: int
    head: str
    problem: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "count": self.count, "head": self.head, "problem": self.problem}


class HelixLedger:
    """One project's helix ledger under ``<STATE_STORE_DIR>/helix/<project>/``."""

    def __init__(self, project_id: str, root: str | Path | None = None) -> None:
        if not str(project_id or "").strip():
            raise ValueError("螺旋账本必须绑定项目 ID")
        self.project_id = str(project_id)
        if root is None:
            import runtime_paths
            root = Path(runtime_paths.STATE_STORE_DIR) / "helix"
        self.dir = Path(root) / (_SAFE.sub("_", self.project_id) or "project")
        self.path = self.dir / "ledger.jsonl"

    # ------------------------------------------------------------------ read
    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def entries(self) -> list[dict[str, Any]]:
        return list(self._iter())

    def _iter(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LedgerIntegrityError(f"账本第 {line_no} 行无法解析：{exc}") from exc

    def head(self) -> str:
        last = GENESIS
        for entry in self._iter():
            last = str(entry.get("hash") or last)
        return last

    def verify(self) -> VerifyReport:
        prev = GENESIS
        count = 0
        try:
            for count, entry in enumerate(self._iter(), start=1):
                if entry.get("prev") != prev:
                    return VerifyReport(False, count, prev, f"第 {count} 条 prev 不连续")
                if entry_hash(entry) != entry.get("hash"):
                    return VerifyReport(False, count, prev, f"第 {count} 条内容哈希不符（疑似被改动）")
                if entry.get("seq") != count:
                    return VerifyReport(False, count, prev, f"第 {count} 条序号异常")
                prev = str(entry["hash"])
        except LedgerIntegrityError as exc:
            return VerifyReport(False, count, prev, str(exc))
        return VerifyReport(True, count, prev)

    # ----------------------------------------------------------------- write
    def append(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
        actor_kind: str,
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"未登记的螺旋事件类型：{event_type}")
        if actor_kind not in {"ai", "human", "system"}:
            raise ValueError("actor_kind 只能是 ai / human / system")
        if event_type in HUMAN_ONLY:
            if actor_kind != "human" or _is_machine(actor):
                raise LedgerPermissionError(f"{event_type} 只能由真人写入；AI/系统不能配对、退役记忆或改松紧度。")
        if actor_kind == "human":
            from core.review_session import assert_human_actor
            actor = assert_human_actor(actor)
        with _LOCK:
            self.dir.mkdir(parents=True, exist_ok=True)
            prev, seq = GENESIS, 0
            for entry in self._iter():
                prev, seq = str(entry.get("hash") or prev), int(entry.get("seq") or seq)
            entry = {
                "seq": seq + 1,
                "ts": utc_now(),
                "project_id": self.project_id,
                "type": event_type,
                "actor": str(actor),
                "actor_kind": actor_kind,
                "payload": json.loads(_canonical(dict(payload))),
                "prev": prev,
            }
            entry["hash"] = entry_hash(entry)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(_canonical(entry) + "\n")
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        return entry

    def ensure_genesis(self) -> None:
        if not self.exists():
            from . import HARNESS_VERSION
            self.append(
                "genesis",
                {"harness_version": HARNESS_VERSION, "note": "螺旋起点：从这里开始，AI 每个碱基都需要真人互补配对。"},
                actor="harness",
                actor_kind="system",
            )


__all__ = [
    "EVENT_TYPES",
    "GENESIS",
    "HUMAN_ONLY",
    "HelixLedger",
    "LedgerIntegrityError",
    "LedgerPermissionError",
    "VerifyReport",
    "entry_hash",
    "new_id",
    "utc_now",
]
