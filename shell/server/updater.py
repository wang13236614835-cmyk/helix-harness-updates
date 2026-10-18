"""Helix 自动更新：组长签名发布，组员自动检查、下载、校验并在重启时应用。

- 只接受 Ed25519 签名正确、SHA256 一致、版本更高的程序包；不含 data/、账号、Token、配置。
- 发布渠道：GitHub 私有 Releases、共享文件夹、HTTP(S) 地址；保留旧 Notion 渠道的兼容读取；
  组长本机也会在 /api/update/manifest 与 /api/update/package 提供同一份包。
- 应用由 portable_launcher.py 的守护进程在服务停止时执行：先备份，再覆盖，新版本 45 秒内没启动则自动回滚。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import ed25519_lite as ED

SHELL = Path(__file__).resolve().parent.parent          # .../shell
PKG = SHELL.parent                                       # 解压根目录
DATA = SHELL / "data"
UPD = DATA / "update"
TRUST = SHELL / "update-trust.json"
SCHEMA = "helix.update.v1"
NOTION_TITLE = "⚙ Helix 程序更新"
RESTART_CODE = 75
MAX_PACKAGE = 40 * 1024 * 1024
ALLOWED_DIRS = ("shell/app/", "shell/server/", "shell/docs/", "shell/launch/", "tools/", "desktop/versions/", "desktop/src/")
ALLOWED_FILES = {"portable_launcher.py", "VERSION.json", "README.txt", "shell/update-trust.json", "shell/README.md",
                 "启动Helix.bat", "停止Helix.bat", "自测Helix.bat", "核验压缩包.bat", "verify_package.py",
                 "Helix.exe", "desktop/current.json", "desktop/THIRD-PARTY-NOTICES.txt"}
SKIP_PARTS = {"__pycache__", "data", ".git", "node_modules"}
_LOCK = threading.RLock()


class UpdateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def vkey(version: str, build: str = "") -> tuple:
    nums = tuple(int(x) for x in re.findall(r"\d+", str(version or "0"))[:4])
    return nums + (0,) * (4 - len(nums)) + (str(build or ""),)


def canonical(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "sig"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint(pub_hex: str) -> str:
    h = hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:16].upper()
    return " ".join(h[i:i + 4] for i in range(0, 16, 4))


def safe_member(name: str) -> str:
    name = str(name).replace("\\", "/")
    p = PurePosixPath(name)
    if not name or p.is_absolute() or any(x in ("", ".", "..") or ":" in x for x in p.parts):
        raise UpdateError("更新包含不安全路径：" + name[:120])
    rel = p.as_posix()
    if any(x in SKIP_PARTS for x in p.parts):
        raise UpdateError("更新包不能包含数据或缓存目录：" + rel)
    if not (rel in ALLOWED_FILES or rel.startswith(ALLOWED_DIRS) or re.fullmatch(r"CHANGELOG-[0-9.]+\.md", rel)):
        raise UpdateError("更新包含不允许覆盖的文件：" + rel)
    if p.name in ("helix.config.json", "credentials.json", "people.json", "cloud.json", "sessions.json"):
        raise UpdateError("更新包不能包含配置或账号文件：" + rel)
    return rel


# ------------------------------------------------------------------ 本机版本 / 信任 / 设置
def current_version() -> dict:
    try:
        import importlib
        import version as V
        importlib.reload(V)
        return {"version": V.VERSION, "build": V.BUILD}
    except Exception:
        info = _read_json(PKG / "VERSION.json", {})
        return {"version": str(info.get("version") or "0"), "build": str(info.get("build") or "")}


def trust() -> dict:
    t = _read_json(TRUST, {})
    pinned = _read_json(UPD / "trust-pinned.json", {})
    keys = list(dict.fromkeys([*(t.get("pubkeys") or []), *(pinned.get("pubkeys") or [])]))
    return {"pubkeys": [k for k in keys if re.fullmatch(r"[0-9a-f]{64}", str(k))], "feeds": list(t.get("feeds") or []),
            "tofu": bool(t.get("tofu", True))}


def settings() -> dict:
    s = _read_json(UPD / "settings.json", {})
    t = trust()
    import github_updates as GH
    configured_feeds = s.get('feeds') if 'feeds' in s else t['feeds']
    feeds = [f for f in dict.fromkeys(configured_feeds or []) if f != 'notion']
    return {"auto": bool(s.get("auto", True)), "interval_min": max(5, int(s.get("interval_min") or 30)),
            "feeds": feeds or ['github:' + GH.public_config()['repository']],
            "publish_dir": str(s.get("publish_dir") or ""), "publish_notion": False,
            "publish_github": bool(s.get("publish_github", True))}


def save_settings(data: dict) -> dict:
    cur = _read_json(UPD / "settings.json", {})
    if "auto" in data:
        cur["auto"] = bool(data["auto"])
    if "interval_min" in data:
        cur["interval_min"] = max(5, min(24 * 60, int(data["interval_min"] or 30)))
    if "feeds" in data:
        feeds = data["feeds"] if isinstance(data["feeds"], list) else str(data["feeds"]).splitlines()
        cur["feeds"] = [f.strip() for f in feeds if str(f).strip()][:8]
    if "publish_dir" in data:
        cur["publish_dir"] = str(data["publish_dir"] or "").strip()
    if "publish_notion" in data:
        cur["publish_notion"] = bool(data["publish_notion"])
    if "publish_github" in data:
        cur["publish_github"] = bool(data["publish_github"])
    _write_json(UPD / "settings.json", cur)
    return settings()


def state() -> dict:
    return _read_json(UPD / "state.json", {})


def _set_state(**kw) -> dict:
    with _LOCK:
        s = state()
        s.update(kw)
        _write_json(UPD / "state.json", s)
        return s


# ------------------------------------------------------------------ 签名密钥（只在组长电脑）
def _key_path() -> Path:
    return DATA / "update-signing.json"


def signing_key(create: bool = False) -> bytes | None:
    from models import _Blob
    raw = _read_json(_key_path(), {})
    if raw.get("blob"):
        try:
            return _Blob.unprotect(raw.get("kind", "b64"), raw["blob"])
        except Exception as exc:
            raise UpdateError("发布密钥无法解密（可能换了 Windows 账户）") from exc
    if not create:
        return None
    seed = secrets.token_bytes(32)
    kind, blob = _Blob.protect(seed)
    _write_json(_key_path(), {"schema": "helix.update-key.v1", "kind": kind, "blob": blob, "pub": ED.public_key(seed).hex(), "created": _now()})
    pub = ED.public_key(seed).hex()
    t = _read_json(TRUST, {})
    t["pubkeys"] = list(dict.fromkeys([*(t.get("pubkeys") or []), pub]))
    from github_updates import DEFAULT_FEED
    t.setdefault("feeds", [DEFAULT_FEED])
    _write_json(TRUST, t)
    return seed


def public_info() -> dict:
    raw = _read_json(_key_path(), {})
    pub = raw.get("pub") or ""
    return {"has_key": bool(pub), "pubkey": pub, "fingerprint": fingerprint(pub) if pub else ""}


# ------------------------------------------------------------------ 打包与签名
def build_package(version: str, build: str, notes: str = "") -> tuple[bytes, dict]:
    files: dict[str, bytes] = {}
    for top in ("shell/app", "shell/server", "shell/docs", "shell/launch", "tools"):
        base = PKG / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or any(x in SKIP_PARTS for x in path.relative_to(PKG).parts) or path.suffix in (".pyc", ".tmp", ".log"):
                continue
            files[path.relative_to(PKG).as_posix()] = path.read_bytes()
    for name in [*ALLOWED_FILES, *(p.name for p in PKG.glob("CHANGELOG-*.md"))]:
        path = PKG / name
        if path.is_file():
            files[name] = path.read_bytes()
    if "shell/server/serve.py" not in files or "shell/app/index.html" not in files:
        raise UpdateError("本机程序文件不完整，不能发布")
    # 发布时写入新的版本号，组员更新后显示同一版本
    files["shell/server/version.py"] = (
        '"""Helix application release (independent of historical Workbench versions)."""\n'
        f"VERSION = {version!r}\nBUILD = {build!r}\nPRODUCT = 'Helix Harness'\nSHELL_VERSION = 'helix-shell ' + VERSION\n").encode("utf-8")
    vj = _read_json(PKG / "VERSION.json", {})
    vj.update(version=version, build=build)
    files["VERSION.json"] = json.dumps(vj, ensure_ascii=False, indent=2).encode("utf-8")
    # Each release gets a fresh native directory: Windows never overwrites a running EXE/DLL.
    pointer = _read_json(PKG / "desktop/current.json", {})
    if pointer:
        entry = safe_member(pointer.get("entry", ""))
        old_dir = PurePosixPath(entry).parent
        if not entry.startswith("desktop/versions/") or PurePosixPath(entry).name != "Helix.Desktop.exe":
            raise UpdateError("桌面程序入口无效")
        new_dir = f"desktop/versions/{version}-{build}"
        for path in sorted((PKG / old_dir).iterdir()):
            if path.is_file() and path.suffix.lower() in (".exe", ".dll", ".ico", ".json", ".txt"):
                files[new_dir + "/" + path.name] = path.read_bytes()
        required = ("Helix.Desktop.exe", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll")
        if any(new_dir + "/" + name not in files for name in required) or "Helix.exe" not in files:
            raise UpdateError("桌面程序不完整，不能发布")
        files["desktop/current.json"] = json.dumps({"version": version, "build": build,
            "entry": new_dir + "/Helix.Desktop.exe"}, ensure_ascii=False, indent=2).encode()
    for name in files:
        safe_member(name)
    listing = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, raw in files.items():
            z.writestr("payload/" + name, raw)
        z.writestr("files.sha256.json", json.dumps(listing, ensure_ascii=False, indent=1))
    return buf.getvalue(), listing


def sign_manifest(seed: bytes, manifest: dict) -> dict:
    m = {**manifest, "pubkey": ED.public_key(seed).hex()}
    m["sig"] = ED.sign(seed, canonical(m)).hex()
    return m


def verify_manifest(m: dict, *, allow_tofu: bool = True) -> str:
    """返回签名公钥；失败抛 UpdateError。"""
    if not isinstance(m, dict) or m.get("schema") != SCHEMA:
        raise UpdateError("不是 Helix 更新清单")
    pub, sig = str(m.get("pubkey") or ""), str(m.get("sig") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", pub) or not re.fullmatch(r"[0-9a-f]{128}", sig):
        raise UpdateError("更新清单缺少签名")
    if not ED.verify(bytes.fromhex(pub), canonical(m), bytes.fromhex(sig)):
        raise UpdateError("更新签名无效，已拒绝")
    t = trust()
    if t["pubkeys"] and pub not in t["pubkeys"]:
        raise UpdateError("更新不是由本组组长密钥签名（指纹 %s），已拒绝" % fingerprint(pub))
    if not t["pubkeys"] and not (allow_tofu and t["tofu"]):
        raise UpdateError("本机还没有信任的组长发布密钥")
    return pub


def check_package(m: dict, raw: bytes) -> dict:
    if len(raw) != int(m.get("size") or -1) or hashlib.sha256(raw).hexdigest() != m.get("sha256"):
        raise UpdateError("更新包校验失败（大小或 SHA256 不一致）")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.testzip() is not None:
            raise UpdateError("更新包已损坏")
        listing = json.loads(z.read("files.sha256.json"))
        names = [n for n in z.namelist() if n.startswith("payload/") and not n.endswith("/")]
        if sorted(n[8:] for n in names) != sorted(listing):
            raise UpdateError("更新包文件清单不一致")
        if hashlib.sha256(json.dumps(listing, ensure_ascii=False, sort_keys=True).encode()).hexdigest() != m.get("files_sha256"):
            raise UpdateError("更新包文件清单与签名不符")
        for n in names:
            rel = safe_member(n[8:])
            if hashlib.sha256(z.read(n)).hexdigest() != listing[rel]:
                raise UpdateError("更新文件校验失败：" + rel)
    return listing


def make_release(notes: str = "", version: str = "") -> dict:
    seed = signing_key(create=True)
    cur = current_version()
    version = (version or cur["version"]).strip()
    if not re.fullmatch(r"\d+(\.\d+){0,3}", version):
        raise UpdateError("版本号格式应为 3.1 或 3.1.2")
    build = datetime.now().strftime("%Y%m%d%H%M%S")
    raw, listing = build_package(version, build, notes)
    if len(raw) > MAX_PACKAGE:
        raise UpdateError("更新包过大")
    name = f"Helix-Update-{version}-{build}.zip"
    m = sign_manifest(seed, {"schema": SCHEMA, "version": version, "build": build, "created": _now(), "file": name,
                             "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "notes": str(notes or "")[:1500],
                             "files": len(listing), "files_sha256": hashlib.sha256(json.dumps(listing, ensure_ascii=False, sort_keys=True).encode()).hexdigest()})
    out = UPD / "published"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("Helix-Update-*.zip"):
        old.unlink()
    (out / name).write_bytes(raw)
    _write_json(out / "helix-update.json", m)
    # 组长本机同步到同一版本号，避免把自己的发布当成“新版本”
    (SHELL / "server" / "version.py").write_bytes(dict(_zip_files(raw))["shell/server/version.py"])
    vj = _read_json(PKG / "VERSION.json", {})
    vj.update(version=version, build=build)
    _write_json(PKG / "VERSION.json", vj)
    return {"manifest": m, "path": str(out / name), "raw": raw}


def _zip_files(raw: bytes):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for n in z.namelist():
            if n.startswith("payload/") and not n.endswith("/"):
                yield n[8:], z.read(n)


def published() -> tuple[dict, Path] | None:
    m = _read_json(UPD / "published" / "helix-update.json", None)
    if not m:
        return None
    path = UPD / "published" / m.get("file", "")
    return (m, path) if path.is_file() else None


def publish_folder(folder: str, m: dict, raw: bytes) -> str:
    d = Path(folder).expanduser()
    if not d.is_dir():
        raise UpdateError("发布文件夹不存在：" + folder)
    for old in d.glob("Helix-Update-*.zip"):
        try:
            old.unlink()
        except OSError:
            pass
    (d / m["file"]).write_bytes(raw)
    _write_json(d / "helix-update.json", m)
    return str(d)


# ------------------------------------------------------------------ 渠道：读取清单与包
def _open(url: str, timeout: int = 30, proxy: str = "") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "HelixHarness-Updater"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy})) if proxy else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r:
        data = r.read(MAX_PACKAGE + 1)
    if len(data) > MAX_PACKAGE:
        raise UpdateError("下载内容过大")
    return data


class NotionFeed:
    """借用本机已连接的 Notion 打卡数据库；组长发布、组员读取同一页。"""

    def __init__(self, cloud):
        self.cloud = cloud

    def _ctx(self):
        c = self.cloud._load()
        db = c.get("database_id")
        if not db or not (c.get("token") or {}).get("blob"):
            raise UpdateError("本机还没有连接 Notion，跳过 Notion 更新渠道")
        return self.cloud._token(c), db, c.get("title_prop") or "任务"

    def _page(self, tok, db, title_prop, create=False) -> str:
        r = self.cloud.http("POST", f"/databases/{db}/query", tok,
                            {"filter": {"property": title_prop, "title": {"equals": NOTION_TITLE}}, "page_size": 5})
        res = [x for x in r.get("results") or [] if not x.get("archived")]
        if res:
            return res[0]["id"]
        if not create:
            return ""
        page = self.cloud.http("POST", "/pages", tok, {"parent": {"database_id": db}, "icon": {"type": "emoji", "emoji": "⚙️"},
                                                       "properties": {title_prop: {"title": [{"text": {"content": NOTION_TITLE}}]}}})
        return page["id"]

    def _children(self, tok, page) -> list:
        out, cursor = [], None
        for _ in range(10):
            q = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            r = self.cloud.http("GET", f"/blocks/{page}/children{q}", tok)
            out += r.get("results") or []
            if not r.get("has_more"):
                break
            cursor = r.get("next_cursor")
        return out

    def fetch(self):
        tok, db, tp = self._ctx()
        page = self._page(tok, db, tp)
        if not page:
            return None
        manifest, url = None, ""
        for b in self._children(tok, page):
            if b.get("type") == "code":
                text = "".join(x.get("plain_text", "") for x in b["code"].get("rich_text") or [])
                if SCHEMA in text:
                    try:
                        manifest = json.loads(text)
                    except ValueError:
                        pass
            if b.get("type") == "file":
                f = b["file"]
                url = (f.get("file") or {}).get("url") or (f.get("external") or {}).get("url") or url
        if not manifest:
            return None
        return manifest, (lambda: _open(url, 120, getattr(self.cloud, "_auto_proxy", "") or self.cloud.proxy) if url else (_ for _ in ()).throw(UpdateError("Notion 更新页缺少程序包附件")))

    def publish(self, m: dict, raw: bytes) -> str:
        tok, db, tp = self._ctx()
        page = self._page(tok, db, tp, create=True)
        up = self.cloud.http("POST", "/file_uploads", tok, {"mode": "single_part", "filename": m["file"], "content_type": "application/zip"})
        self._send(tok, up["id"], m["file"], raw)
        for b in self._children(tok, page):
            try:
                self.cloud.http("DELETE", f"/blocks/{b['id']}", tok)
            except Exception:
                pass
        text = json.dumps(m, ensure_ascii=False, indent=1)
        chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        blocks = [
            {"type": "callout", "callout": {"icon": {"type": "emoji", "emoji": "⚙️"}, "rich_text": [{"type": "text", "text": {"content": f"Helix {m['version']}（{m['build']}）· {when} 由组长发布。组员的 Helix 会自动下载、校验签名并更新，请勿手动修改本页。"}}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "更新说明：" + (m.get("notes") or "（无）")}}]}},
            {"type": "file", "file": {"type": "file_upload", "file_upload": {"id": up["id"]}, "name": m["file"]}},
            {"type": "code", "code": {"language": "json", "rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]}},
        ]
        self.cloud.http("PATCH", f"/blocks/{page}/children", tok, {"children": blocks})
        return page

    def _send(self, tok, upload_id, name, raw):
        from cloud import API, NOTION_VERSION
        boundary = "----helix" + secrets.token_hex(12)
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{urllib.parse.quote(name)}\"\r\n"
                f"Content-Type: application/zip\r\n\r\n").encode() + raw + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(API.rstrip("/") + f"/file_uploads/{upload_id}/send", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + tok, "Notion-Version": NOTION_VERSION,
                                              "Content-Type": "multipart/form-data; boundary=" + boundary})
        proxy = self.cloud.proxy
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy})) if proxy else urllib.request.build_opener()
        try:
            with opener.open(req, timeout=180) as r:
                r.read()
        except urllib.error.HTTPError as e:
            raise UpdateError("上传更新包到 Notion 失败：HTTP %s %s" % (e.code, e.read()[:200].decode("utf-8", "replace"))) from e


def feed_fetch(feed: str, cloud=None):
    """返回 (manifest, loader) 或 None。loader() 返回包字节。"""
    feed = str(feed or "").strip()
    if not feed:
        return None
    if feed.startswith('github:') or feed.startswith('https://github.com/'):
        from github_updates import GitHubFeed
        return GitHubFeed(feed).fetch()
    if feed == "notion":
        if cloud is None:
            return None
        return NotionFeed(cloud).fetch()
    if re.match(r"^https?://", feed, re.I):
        murl = feed if feed.lower().endswith(".json") else feed.rstrip("/") + "/helix-update.json"
        if "/api/update" in feed and not feed.lower().endswith(".json"):
            murl = feed.rstrip("/").rsplit("/api/update", 1)[0] + "/api/update/manifest"
        m = json.loads(_open(murl, 20).decode("utf-8"))
        purl = m.get("url") or (murl.rsplit("/", 1)[0] + ("/package" if murl.endswith("/api/update/manifest") else "/" + urllib.parse.quote(m.get("file", ""))))
        return m, (lambda: _open(purl, 180))
    d = Path(feed).expanduser()
    mf = d / "helix-update.json" if d.is_dir() else d
    if not mf.is_file():
        return None
    m = _read_json(mf, None)
    if not m:
        return None
    return m, (lambda: (mf.parent / m.get("file", "")).read_bytes())


# ------------------------------------------------------------------ 检查、下载、暂存
def is_newer(m: dict) -> bool:
    cur = current_version()
    return vkey(m.get("version"), m.get("build")) > vkey(cur["version"], cur["build"])


def stage(m: dict, raw: bytes) -> dict:
    pub = verify_manifest(m)
    listing = check_package(m, raw)
    tag = f"{m['version']}-{m['build']}"
    if tag in (state().get("bad") or []):
        raise UpdateError(f"{tag} 曾启动失败并已回滚，不再自动安装")
    root = UPD / "staged"
    with _LOCK:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        tmp = UPD / ("staging-" + secrets.token_hex(4))
        (tmp / "payload").mkdir(parents=True)
        for rel, data in _zip_files(raw):
            dst = tmp / "payload" / safe_member(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        _write_json(tmp / "manifest.json", m)
        tmp.replace(root)
        if not trust()["pubkeys"]:
            _write_json(UPD / "trust-pinned.json", {"pubkeys": [pub], "pinned_at": _now(), "how": "首次从团队渠道收到的组长签名"})
    _set_state(staged={"version": m["version"], "build": m["build"], "notes": m.get("notes", ""), "files": len(listing), "at": _now()}, last_error="")
    return {"version": m["version"], "build": m["build"], "files": len(listing)}


def import_package(raw: bytes) -> dict:
    """手动导入组长发来的更新包（ZIP 内含 helix-update.json + 包，或直接是签名包外层）。"""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        if "helix-update.json" in names:
            m = json.loads(z.read("helix-update.json"))
            inner = next((n for n in names if n.endswith(".zip")), "")
            if not inner:
                raise UpdateError("更新包里缺少程序包")
            pkg = z.read(inner)
        else:
            raise UpdateError("这不是 Helix 发布的更新包（缺少 helix-update.json）")
    if not is_newer(m):
        raise UpdateError("这个更新包不比当前版本新")
    return stage(m, pkg)


def bundle_for_hand(m: dict, raw: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("helix-update.json", json.dumps(m, ensure_ascii=False, indent=1))
        z.writestr(m["file"], raw)
        z.writestr("说明.txt", "把这个ZIP拖进 Helix「设置 → 程序更新 → 导入更新包」，签名校验通过后自动更新。不要解压。\n")
    return buf.getvalue()


def check_now(cloud=None, feeds: list | None = None) -> dict:
    s = settings()
    errors, found = [], None
    for feed in feeds or s["feeds"]:
        try:
            got = feed_fetch(feed, cloud)
            if not got:
                continue
            m, loader = got
            verify_manifest(m)
            if found is None or vkey(m["version"], m["build"]) > vkey(found[0]["version"], found[0]["build"]):
                found = (m, loader, feed)
        except Exception as exc:  # 单个渠道失败不影响其他渠道
            errors.append(f"{'Notion' if feed == 'notion' else feed}：{exc}")
    result = {"checked_at": _now(), "errors": errors[:4], "current": current_version()}
    if not found:
        _set_state(last_check=result["checked_at"], last_error="；".join(errors[:2]), available=None)
        return {**result, "available": None}
    m, loader, feed = found
    avail = {"version": m["version"], "build": m["build"], "notes": m.get("notes", ""), "feed": "Notion" if feed == "notion" else feed}
    if not is_newer(m):
        _set_state(last_check=result["checked_at"], last_error="", available=None)
        return {**result, "available": None, "latest": avail}
    st = state().get("staged") or {}
    if st.get("version") == m["version"] and st.get("build") == m["build"] and (UPD / "staged").exists():
        _set_state(last_check=result["checked_at"], available=avail)
        return {**result, "available": avail, "staged": st}
    raw = loader()
    staged = stage(m, raw)
    _set_state(last_check=result["checked_at"], available=avail)
    return {**result, "available": avail, "staged": staged}


def status() -> dict:
    st = state()
    from github_updates import public_config
    return {"current": current_version(), "settings": settings(), "trust": {"pubkeys": [fingerprint(k) for k in trust()["pubkeys"]]},
            "github": public_config(),
            "signing": public_info(), "staged": st.get("staged") if (UPD / "staged").exists() else None, "available": st.get("available"),
            "last_check": st.get("last_check", ""), "last_error": st.get("last_error", ""), "last_applied": st.get("last_applied"),
            "rolled_back": st.get("rolled_back"), "supervised": os.environ.get("HELIX_SUPERVISED") == "1",
            "published": (published() or ({},))[0] or None}


# ------------------------------------------------------------------ 应用（由守护进程在服务停止时调用）
def apply_staged(pkg_root: Path | None = None) -> dict | None:
    root = Path(pkg_root or PKG)
    upd = root / "shell" / "data" / "update"
    staged = upd / "staged"
    if not (staged / "manifest.json").is_file():
        return None
    m = _read_json(staged / "manifest.json", {})
    old = current_version()
    tag = f"{m.get('version')}-{m.get('build')}"
    backup = upd / "backup" / f"{old['version']}-{old['build'] or 'base'}"
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    created: list[str] = []
    payload = staged / "payload"
    files = [p for p in payload.rglob("*") if p.is_file()]
    # Activate only after all new desktop files have been installed successfully.
    files.sort(key=lambda p: p.relative_to(payload).as_posix() == "desktop/current.json")
    try:
        for src in files:
            rel = safe_member(src.relative_to(payload).as_posix())
            dst = root / rel
            if dst.exists():
                b = backup / "files" / rel
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, b)
            else:
                created.append(rel)
        _write_json(backup / "created.json", created)
        for src in files:
            rel = src.relative_to(payload).as_posix()
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".helix-new")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
    except Exception as exc:
        rollback(root, backup)
        _write_json(upd / "state.json", {**_read_json(upd / "state.json", {}), "last_error": f"应用更新失败，已还原：{exc}"})
        shutil.rmtree(staged, ignore_errors=True)
        return {"ok": False, "error": str(exc)}
    shutil.rmtree(staged, ignore_errors=True)
    st = _read_json(upd / "state.json", {})
    st.update(pending_verify={"tag": tag, "backup": str(backup), "from": old}, staged=None, available=None)
    _write_json(upd / "state.json", st)
    return {"ok": True, "tag": tag, "backup": str(backup)}


def rollback(root: Path, backup: Path) -> None:
    for rel in _read_json(backup / "created.json", []):
        try:
            (root / rel).unlink()
        except OSError:
            pass
    fb = backup / "files"
    if fb.is_dir():
        for src in fb.rglob("*"):
            if src.is_file():
                dst = root / src.relative_to(fb)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)


def confirm_or_rollback(pkg_root: Path, healthy: bool) -> str:
    upd = Path(pkg_root) / "shell" / "data" / "update"
    st = _read_json(upd / "state.json", {})
    pv = st.get("pending_verify")
    if not pv:
        return ""
    if healthy:
        st.update(pending_verify=None, last_applied={"tag": pv["tag"], "at": _now(), "from": pv.get("from")})
        _write_json(upd / "state.json", st)
        return "ok"
    rollback(Path(pkg_root), Path(pv["backup"]))
    st.update(pending_verify=None, bad=list(dict.fromkeys([*(st.get("bad") or []), pv["tag"]]))[-10:],
              rolled_back={"tag": pv["tag"], "at": _now()}, last_error=f"{pv['tag']} 启动失败，已自动回滚到原版本")
    _write_json(upd / "state.json", st)
    return "rolled_back"


# ------------------------------------------------------------------ 后台自动检查
class AutoUpdater:
    def __init__(self, cloud_getter, on_staged=None):
        self.cloud_getter = cloud_getter
        self.on_staged = on_staged
        self._t = None
        self._wake = threading.Event()

    def start(self):
        if os.environ.get("HELIX_TEST") == "1" or os.environ.get("HELIX_NO_AUTOUPDATE") == "1":
            return
        if self._t and self._t.is_alive():
            return
        self._t = threading.Thread(target=self._loop, name="helix-updater", daemon=True)
        self._t.start()

    def poke(self):
        self._wake.set()

    def _loop(self):
        time.sleep(20)
        while True:
            s = settings()
            if s["auto"]:
                try:
                    r = check_now(self.cloud_getter())
                    if r.get("staged") and self.on_staged:
                        self.on_staged(r["staged"])
                except Exception as exc:
                    _set_state(last_error=str(exc)[:300], last_check=_now())
            self._wake.wait(s["interval_min"] * 60)
            self._wake.clear()


# ------------------------------------------------------------------ 无守护进程时的重启助手
def spawn_restart_helper(pid: int, port: int, python: str, serve: Path) -> None:
    import subprocess
    flags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == "nt" else 0
    exe = Path(python)
    if os.name == "nt" and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").is_file():
        exe = exe.parent / "pythonw.exe"
    UPD.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(exe), str(Path(__file__).resolve()), "--restart-helper", "--pid", str(pid), "--port", str(port),
                      "--python", str(python), "--serve", str(serve)], cwd=str(Path(serve).parent.parent), env=dict(os.environ),
                     creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=open(UPD / "helper.err.log", "ab"), close_fds=True, start_new_session=os.name != "nt")


def _listening(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.5):
            return True
    except OSError:
        return False


def _helper_main(argv: list[str]) -> int:
    import argparse
    import subprocess
    ap = argparse.ArgumentParser()
    ap.add_argument("--restart-helper", action="store_true")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--serve", required=True)
    a = ap.parse_args(argv)
    log = UPD / "helper.log"

    def say(msg):
        with log.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")

    for _ in range(120):  # 等旧服务退出（最多 30 秒）
        if not _listening(a.port):
            break
        time.sleep(.25)
    time.sleep(.5)
    try:
        say("apply: %s" % apply_staged(PKG))
    except Exception as exc:  # noqa: BLE001
        say("apply failed: %s" % exc)
    flags = 0x08000000 if os.name == "nt" else 0
    data = Path(a.serve).resolve().parent.parent / "data"

    def spawn():
        return subprocess.Popen([a.python, a.serve], cwd=str(Path(a.serve).resolve().parent.parent), env=dict(os.environ),
                                creationflags=flags | ((0x00000008 | 0x00000200) if os.name == "nt" else 0),
                                stdin=subprocess.DEVNULL, stdout=open(data / "serve.log", "ab"), stderr=open(data / "serve.err.log", "ab"),
                                close_fds=True, start_new_session=os.name != "nt")

    proc = spawn()
    ok = False
    for _ in range(180):
        if proc.poll() is not None:
            break
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:%d/api/version" % a.port, timeout=1.5).read()
            ok = True
            break
        except Exception:  # noqa: BLE001
            time.sleep(.25)
    verdict = confirm_or_rollback(PKG, ok)
    say("verify: %s (healthy=%s)" % (verdict, ok))
    if verdict == "rolled_back":
        if proc.poll() is None:
            proc.terminate()
        time.sleep(1)
        spawn()
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(_helper_main(sys.argv[1:]))
