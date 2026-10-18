"""0.6 云同步：把组内打卡进度推到 Notion 数据库，组员不用再手动上交进度。

- 只推白名单字段：任务编号、交付物、验收、职能、周、截止、主核 / 交叉核（名册显示名，不推原名）、
  打卡状态、进度、最近打卡时间与说明、成果文件名（只有文件名和短指纹，不传文件内容）、审核状态；
- 每行用“Helix ID”（项目编号:任务编号）定位，重复同步只更新、不重复建行；内容没变的行跳过；
- Token 只存本机 data/cloud.json：Windows 上用 DPAPI 加密（同模型 Key），界面只显示末 4 位；
- 打卡后后台自动推送；失败会记下原因并退避重试，不影响本机账本（账本才是事实来源，云端只是镜像）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from models import _Blob

API = os.environ.get("HELIX_CLOUD_BASE") or "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
_LOCK = threading.RLock()
STATE_OPTIONS = ["未打卡", "已开始", "进行中", "已完成", "受阻"]
REVIEW_OPTIONS = ["未播种", "待审核", "等上游", "有分歧", "已通过", "未通过"]
# 列名 → Notion 属性定义（缺的列会自动补上；标题列用数据库已有的那一列）
SCHEMA: dict[str, dict] = {
    "Helix ID": {"rich_text": {}},
    "项目": {"rich_text": {}},
    "编号": {"rich_text": {}},
    "周": {"number": {"format": "number"}},
    "职能": {"select": {}},
    "主核": {"rich_text": {}},
    "交叉核": {"rich_text": {}},
    "打卡状态": {"select": {"options": [{"name": n} for n in STATE_OPTIONS]}},
    "进度": {"number": {"format": "percent"}},
    "最近打卡": {"date": {}},
    "打卡人": {"rich_text": {}},
    "打卡说明": {"rich_text": {}},
    "成果文件": {"rich_text": {}},
    "审核状态": {"select": {"options": [{"name": n} for n in REVIEW_OPTIONS]}},
    "验收标准": {"rich_text": {}},
    "截止": {"date": {}},
    "同步时间": {"date": {}},
}
ROW_KEYS = ("helix_id", "project", "task_id", "title", "week", "duty", "owner", "cross_reviewer", "state", "progress", "checkin_at", "checkin_by",
            "note", "files", "review", "acceptance", "due")


class CloudError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_db_id(v: str) -> str:
    """接受数据库链接或 ID（带不带横线都行），返回 32 位十六进制。"""
    s = str(v or "").strip()
    m = re.findall(r"[0-9a-fA-F]{32}", s.replace("-", ""))
    if not m:
        raise CloudError("数据库 ID 不对：请粘贴 Notion 数据库的链接，或 32 位 ID")
    # 链接里 ?v= 后面是视图 ID，取路径里的第一个
    path = s.split("?")[0].replace("-", "")
    m2 = re.findall(r"[0-9a-fA-F]{32}", path)
    return (m2[-1] if m2 else m[0]).lower()


def _clip(v, n=1900) -> str:
    return str(v or "")[:n]


def _rt(v) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": _clip(v)}}] if v not in (None, "") else []}


def _date(v) -> dict:
    return {"date": {"start": str(v)} if v else None}


def _sel(v) -> dict:
    return {"select": {"name": _clip(v, 90).replace(",", "，")} if v else None}


def row_hash(row: dict) -> str:
    return hashlib.sha256(json.dumps({k: row.get(k) for k in ROW_KEYS}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def page_properties(row: dict, title_prop: str) -> dict:
    """白名单：只有这些字段会离开本机。"""
    title = f"{row.get('task_id') or ''} · {row.get('title') or ''}".strip(" ·")
    return {
        title_prop: {"title": [{"type": "text", "text": {"content": _clip(title, 200)}}]},
        "Helix ID": _rt(row.get("helix_id")), "项目": _rt(row.get("project")), "编号": _rt(row.get("task_id")),
        "周": {"number": row.get("week")}, "职能": _sel(row.get("duty")), "主核": _rt(row.get("owner")), "交叉核": _rt(row.get("cross_reviewer")),
        "打卡状态": _sel(row.get("state") or "未打卡"), "进度": {"number": round((row.get("progress") or 0) / 100, 4)},
        "最近打卡": _date(row.get("checkin_at")), "打卡人": _rt(row.get("checkin_by")), "打卡说明": _rt(row.get("note")),
        "成果文件": _rt(row.get("files")), "审核状态": _sel(row.get("review")), "验收标准": _rt(row.get("acceptance")),
        "截止": _date(row.get("due")), "同步时间": _date(_now()),
    }


class Cloud:
    """一份本机云同步配置（data/cloud.json）+ 后台推送线程。"""

    def __init__(self, data_dir: Path, rows_for: Callable[[str], list[dict]], http: Callable | None = None):
        self.path = Path(data_dir) / "cloud.json"
        try:
            config = json.loads((Path(data_dir).parent / "helix.config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}
        self.proxy = os.environ.get("HELIX_CLOUD_PROXY") or config.get("cloud_proxy") or ""
        self.rows_for = rows_for
        self.http = http or self._http
        self._dirty: dict[str, float] = {}
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self.delay = 0.05 if os.environ.get("HELIX_TEST") == "1" else 2.0
        self.gap = 0 if os.environ.get("HELIX_TEST") == "1" else 0.34  # Notion 限速约 3 次/秒

    # ---------------------------------------------------------------- store
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, c: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def _token(self, c: dict) -> str:
        t = c.get("token") or {}
        if not t.get("blob"):
            raise CloudError("还没有填 Notion 集成的 Token")
        try:
            return _Blob.unprotect(t.get("kind", "b64"), t["blob"]).decode("utf-8")
        except Exception as exc:
            raise CloudError("Token 无法解密（可能换了 Windows 账户），请重新填写") from exc

    def public(self) -> dict:
        c = self._load()
        st = c.get("status") or {}
        return {"configured": bool((c.get("token") or {}).get("blob") and c.get("database_id")), "enabled": bool(c.get("enabled")),
                "provider": "notion", "database_id": c.get("database_id") or "", "database_title": c.get("database_title") or "",
                "url": ("https://www.notion.so/" + c["database_id"]) if c.get("database_id") else "", "token_tail": (c.get("token") or {}).get("tail") or "",
                "last_sync": st.get("last_sync") or "", "last_ok": st.get("last_ok"), "last_error": st.get("last_error") or "", "pushed": st.get("pushed", 0),
                "skipped": st.get("skipped", 0), "rows": len(c.get("rows") or {}), "pending": sorted(self._dirty), "fields": list(SCHEMA)}

    # ---------------------------------------------------------------- http
    def _http(self, method: str, path: str, token: str, body: Any = None, timeout: int = 20) -> Any:
        """Notion 请求：限流/网络抖动自动重试；直连失败时自动尝试系统代理和常见本机代理端口。"""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        last: Exception | None = None
        for attempt in range(4):
            for proxy in self._proxies():
                req = urllib.request.Request(API.rstrip("/") + path, data=data, method=method,
                                             headers={"Authorization": "Bearer " + token, "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"})
                try:
                    handler = urllib.request.ProxyHandler({}) if proxy == "DIRECT" else urllib.request.ProxyHandler({"https": proxy, "http": proxy}) if proxy else None
                    opener = urllib.request.build_opener(handler) if handler else urllib.request.build_opener()
                    with opener.open(req, timeout=timeout) as r:
                        out = json.loads(r.read().decode("utf-8") or "{}")
                    if proxy != self.proxy:
                        self._auto_proxy = proxy  # 记住能用的通道
                    return out
                except urllib.error.HTTPError as e:
                    detail = e.read()[:300].decode("utf-8", "replace")
                    try:
                        detail = json.loads(detail).get("message") or detail
                    except Exception:
                        pass
                    if e.code == 401:
                        raise CloudError("Token 无效（HTTP 401）：请检查 Notion 集成的密钥") from e
                    if e.code == 404:
                        raise CloudError("找不到数据库（HTTP 404）：确认链接正确，并在数据库右上角“…→ 连接”里添加这个集成") from e
                    if e.code == 429 or e.code >= 500:
                        last = CloudError("Notion 限流（HTTP 429），稍后自动重试" if e.code == 429 else f"Notion 服务暂时不可用（HTTP {e.code}），稍后自动重试")
                        try:
                            wait = float(e.headers.get("Retry-After") or 0)
                        except (TypeError, ValueError):
                            wait = 0
                        time.sleep(min(10.0, max(wait, 1.5 * (attempt + 1))) if os.environ.get("HELIX_TEST") != "1" else 0)
                        break
                    raise CloudError(f"Notion 报错 HTTP {e.code}：{detail}") from e
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                    last = CloudError(f"连不上 Notion：{getattr(e, 'reason', e)}（离线时打卡照常记在本机，联网后自动补推）")
                    continue
            else:
                if attempt < 1 and os.environ.get("HELIX_TEST") != "1":
                    time.sleep(1.5)
                    continue
                break
        raise last or CloudError("连不上 Notion")

    def _proxies(self) -> list[str]:
        """直连 → 配置的代理 → 上次成功的代理 → 系统代理 → 常见本机代理（Clash/mihomo 7890/7897、v2rayN 10809）。"""
        if os.environ.get("HELIX_TEST") == "1" or os.environ.get("HELIX_CLOUD_BASE"):
            return [self.proxy] if self.proxy else [""]
        cands = [self.proxy] if self.proxy else []
        cands.append(getattr(self, "_auto_proxy", "") or "")
        cands += ["", "DIRECT", "http://127.0.0.1:7890", "http://127.0.0.1:7897", "http://127.0.0.1:10809"]
        out = []
        for c in cands:
            if c not in out:
                out.append(c)
        return out

    # ---------------------------------------------------------------- config
    def configure(self, database: str, token: str = "", enabled: bool = True) -> dict:
        with _LOCK:
            c = self._load()
            db = parse_db_id(database)
            if token:
                token = token.strip()
                if not re.fullmatch(r"(secret_|ntn_)[A-Za-z0-9]{20,}", token):
                    raise CloudError("Token 格式不对：Notion 内部集成密钥以 ntn_ 或 secret_ 开头")
                kind, blob = _Blob.protect(token.encode("utf-8"))
                if os.environ.get("HELIX_SECRET_KEY_FILE") and kind != "fernet":
                    raise CloudError("云端加密失败，Token 未保存")
                if os.name == "nt" and not os.environ.get("HELIX_SECRET_KEY_FILE") and kind != "dpapi":
                    raise CloudError("Windows 加密失败，Token 未保存；请检查当前 Windows 账户后重试")
                c["token"] = {"kind": kind, "blob": blob, "tail": token[-4:]}
            if os.environ.get("HELIX_SECRET_KEY_FILE") and (c.get("token") or {}).get("kind") != "fernet":
                raise CloudError("现有 Token 不是云端加密存储，请重新填写")
            if os.name == "nt" and not os.environ.get("HELIX_SECRET_KEY_FILE") and (c.get("token") or {}).get("kind") != "dpapi":
                raise CloudError("现有 Token 不是 Windows 加密存储，请重新填写")
            tok = self._token(c)
            if db != c.get("database_id"):
                c["rows"] = {}
            info = self.ensure_schema(tok, db)
            c.update(database_id=db, database_title=info["title"], title_prop=info["title_prop"], enabled=bool(enabled), updated_at=_now())
            c.setdefault("status", {})["last_error"] = ""
            self._save(c)
        return {**self.public(), "added": info["added"]}

    def disable(self, forget: bool = False) -> None:
        with _LOCK:
            c = self._load()
            if forget:
                c = {}
            else:
                c["enabled"] = False
            self._save(c)

    def ensure_schema(self, tok: str, db: str) -> dict:
        d = self.http("GET", f"/databases/{db}", tok)
        props = d.get("properties") or {}
        title_prop = next((k for k, v in props.items() if (v or {}).get("type") == "title"), "任务")
        add = {}
        for name, spec in SCHEMA.items():
            cur = props.get(name)
            if cur is None:
                add[name] = spec
            elif cur.get("type") != next(iter(spec)):
                raise CloudError(f"数据库里已有“{name}”列，但类型是 {cur.get('type')}，需要 {next(iter(spec))}：请改列名或删掉这一列")
        if add:
            self.http("PATCH", f"/databases/{db}", tok, {"properties": add})
        title = "".join(x.get("plain_text") or (x.get("text") or {}).get("content", "") for x in d.get("title") or []) or "Helix 组内打卡"
        return {"title": title, "title_prop": title_prop, "added": list(add)}

    # ---------------------------------------------------------------- sync
    def sync(self, pid: str, force: bool = False) -> dict:
        with _LOCK:
            c = self._load()
            if not c.get("enabled") and not force:
                return {"skipped": "云同步未开启"}
            tok, db = self._token(c), c.get("database_id")
            if not db:
                raise CloudError("还没有设置 Notion 数据库")
            title_prop = c.get("title_prop") or "任务"
            rows = self.rows_for(pid)
            cache = c.setdefault("rows", {})
            pushed = skipped = 0
            st = c.setdefault("status", {})
            try:
                for row in rows:
                    hid, h = row["helix_id"], row_hash(row)
                    old = cache.get(hid) or {}
                    if old.get("hash") == h and old.get("page_id") and not force:
                        skipped += 1
                        continue
                    props = page_properties(row, title_prop)
                    page = old.get("page_id") or self._find(tok, db, hid)
                    if page:
                        try:
                            self.http("PATCH", f"/pages/{page}", tok, {"properties": props})
                        except CloudError as exc:
                            if "404" not in str(exc):
                                raise
                            page = ""  # 云端那一行被删了：重建
                    if not page:
                        page = self.http("POST", "/pages", tok, {"parent": {"database_id": db}, "properties": props}).get("id") or ""
                    cache[hid] = {"page_id": page, "hash": h, "at": _now()}
                    pushed += 1
                    self._save(c)
                    if self.gap:
                        time.sleep(self.gap)
            except CloudError as exc:
                st.update(last_sync=_now(), last_ok=False, last_error=str(exc))
                self._save(c)
                raise
            st.update(last_sync=_now(), last_ok=True, last_error="", pushed=pushed, skipped=skipped)
            self._save(c)
            return {"pushed": pushed, "skipped": skipped, "rows": len(rows)}

    def _find(self, tok: str, db: str, hid: str) -> str:
        r = self.http("POST", f"/databases/{db}/query", tok, {"filter": {"property": "Helix ID", "rich_text": {"equals": hid}}, "page_size": 1})
        res = r.get("results") or []
        return res[0].get("id", "") if res else ""

    # ---------------------------------------------------------------- 后台推送
    def enqueue(self, pid: str) -> None:
        if not self._load().get("enabled"):
            return
        with self._cv:
            self._dirty[pid] = time.time() + self.delay
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="helix-cloud", daemon=True)
                self._thread.start()
            self._cv.notify()

    def _loop(self) -> None:
        backoff = 30.0
        while True:
            with self._cv:
                while not self._dirty:
                    self._cv.wait(600)
                pid, due = min(self._dirty.items(), key=lambda kv: kv[1])
                wait = due - time.time()
                if wait > 0:
                    self._cv.wait(wait)
                    continue
                self._dirty.pop(pid, None)
            try:
                self.sync(pid)
                backoff = 30.0
            except Exception:
                with self._cv:  # 失败：退避后重试（最长 10 分钟），本机账本不受影响
                    self._dirty.setdefault(pid, time.time() + (0.2 if self.delay < 1 else backoff))
                backoff = min(600.0, backoff * 2)


__all__ = ["Cloud", "CloudError", "SCHEMA", "page_properties", "parse_db_id", "row_hash"]
