"""1.00 → 2.00 数据保留：先备份、再只读登记，绝不改写 1.00 数据。

- 首次打开 2.00 时，把 ``.destoop/store.json`` 与项目目录下的 SQLite/JSON
  记录打包备份到 ``archives/helix_upgrade/``（只做一次，带 SHA-256 清单）；
- 为每个项目在螺旋账本写入一条 ``import.v1`` 事件，记录 1.00 已有的
  AI 协同工作包、人工核验记录与日志数量，作为螺旋的“亲代链”；
- 1.00 的 store.json、SQLite、V4 bundle 全部保持原样，1.00 页面照常可用。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .ledger import HelixLedger, utc_now

_BACKUP_SUFFIXES = {".json", ".db", ".sqlite", ".sqlite3", ".jsonl", ".yaml", ".yml", ".csv"}
_MAX_BACKUP_BYTES = 400 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_v1_data(state_dir: Path | None = None, projects_dir: Path | None = None, archives_dir: Path | None = None) -> dict[str, Any]:
    """One-time, idempotent backup of 1.00 state before Helix writes anything."""
    import runtime_paths
    state_dir = Path(state_dir or runtime_paths.STATE_STORE_DIR)
    projects_dir = Path(projects_dir or runtime_paths.PROJECTS_ROOT)
    archives_dir = Path(archives_dir or runtime_paths.ARCHIVES_ROOT) / "helix_upgrade"
    marker = state_dir / "helix" / "_v1_backup.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    candidates: list[tuple[Path, str]] = []
    store = state_dir / "store.json"
    if store.exists():
        candidates.append((store, "destoop/store.json"))
    if projects_dir.exists():
        for path in sorted(projects_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in _BACKUP_SUFFIXES:
                candidates.append((path, "projects/" + path.relative_to(projects_dir).as_posix()))
    total, files, skipped = 0, [], []
    for path, arc in candidates:
        size = path.stat().st_size
        if total + size > _MAX_BACKUP_BYTES:
            skipped.append(arc)
            continue
        total += size
        files.append((path, arc, size))
    result: dict[str, Any] = {"created_at": utc_now(), "files": len(files), "bytes": total, "skipped": skipped[:50], "archive": None}
    if files:
        archives_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = archives_dir / f"v1.00-before-helix-{stamp}.zip"
        manifest = []
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path, arc, size in files:
                zf.write(path, arc)
                manifest.append({"path": arc, "bytes": size, "sha256": _sha256(path)})
            zf.writestr("MANIFEST.json", json.dumps({"source_version": "1.00", "files": manifest}, ensure_ascii=False, indent=2))
        result["archive"] = str(archive)
        result["archive_sha256"] = _sha256(archive)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def summarize_v1(store: Mapping[str, Any], project_id: str) -> dict[str, Any]:
    def _mine(rows: Any) -> list[Mapping[str, Any]]:
        return [r for r in (rows or []) if isinstance(r, Mapping) and str(r.get("project_id") or "") == project_id]

    ai_runs = _mine(store.get("ai_collaboration_runs"))
    reviews = _mine(store.get("human_reviews")) + _mine(store.get("reviews"))
    logs = _mine(store.get("logs"))
    return {
        "source_version": "1.00",
        "ai_collaboration_runs": len(ai_runs),
        "latest_ai_run": (ai_runs[-1].get("id") if ai_runs else None),
        "human_review_records": len(reviews),
        "log_records": len(logs),
        "store_updated_at": store.get("updated_at"),
        "policy": "只读登记；1.00 数据原样保留，未复制、未改写。",
    }


def ensure_migrated(ledger: HelixLedger, store: Mapping[str, Any]) -> dict[str, Any] | None:
    """Write the one-time ``import.v1`` event for this project if missing."""
    for entry in ledger.entries():
        if entry.get("type") == "import.v1":
            return None
    backup = backup_v1_data()
    payload = summarize_v1(store, ledger.project_id)
    payload["backup_archive"] = backup.get("archive")
    payload["backup_files"] = backup.get("files")
    ledger.append("import.v1", payload, actor="harness", actor_kind="system")
    return payload


__all__ = ["backup_v1_data", "ensure_migrated", "summarize_v1"]
