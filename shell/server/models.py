"""0.5 模型接入：不固定厂商。填 URL + Key 即可工作；按任务需要的能力给每个角色推荐模型。

- 支持 OpenAI 兼容接口（/v1/chat/completions，绝大多数国内外厂商与 OpenRouter / 本地 vLLM / Ollama）
  和 Anthropic 原生接口（/v1/messages）；类型按 URL 自动判断，也可手选；
- Key 只存在本机 data/secrets.json：Windows 上用 DPAPI（当前 Windows 账户加密），其他系统为 base64 混淆；
  接口与界面只返回末 4 位；
- 能力从模型名推断（推理 / 长上下文 / 便宜 / 中文 / 视觉），并按角色打分推荐；
  批评者必须与起草者不同模型家族（异源互审），没有第二家族时运行会被拦下并说明原因；
- 内置“模拟”厂商：不联网、不计费，只用来试跑流程与自动测试，结果会明确标注为模拟。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
ROLES = {
    "helicase": "解旋酶 · 规划者",
    "extractor": "抽取者 · 文献/文件",
    "drafter": "聚合酶 · 起草者",
    "critic": "校对酶 · 批评者（异源）",
    "ligase": "连接酶 · 汇总者",
}
# 每个角色看重的能力（权重）
ROLE_NEEDS = {
    "helicase": {"reasoning": 3, "long": 1, "chinese": 1},
    "extractor": {"long": 3, "cheap": 2, "fast": 1},
    "drafter": {"reasoning": 2, "chinese": 2, "long": 1},
    "critic": {"reasoning": 3, "long": 1},
    "ligase": {"cheap": 2, "chinese": 2, "fast": 1},
}
CAP_LABEL = {"reasoning": "推理强", "long": "长上下文", "cheap": "便宜", "fast": "快", "chinese": "中文好", "vision": "看图", "code": "代码"}
_FAMILIES = [
    ("anthropic", r"claude"), ("openai", r"(^|/)(gpt|o[1-9]|chatgpt|text-davinci)"), ("google", r"gemini|gemma|palm"),
    ("deepseek", r"deepseek"), ("qwen", r"qwen|qwq|tongyi"), ("zhipu", r"glm|chatglm|zhipu"), ("moonshot", r"kimi|moonshot"),
    ("meta", r"llama"), ("mistral", r"mistral|mixtral|codestral|magistral"), ("minimax", r"minimax|abab"), ("doubao", r"doubao|skylark"),
    ("baidu", r"ernie|wenxin"), ("tencent", r"hunyuan"), ("stepfun", r"step-"), ("xai", r"grok"), ("yi", r"(^|/)yi-"),
    ("baichuan", r"baichuan"), ("internlm", r"internlm|intern-"), ("microsoft", r"phi-"), ("mock-a", r"^mock-a"), ("mock-b", r"^mock-b"),
]
_CN = {"deepseek", "qwen", "zhipu", "moonshot", "minimax", "doubao", "baidu", "tencent", "stepfun", "yi", "baichuan", "internlm", "mock-a", "mock-b"}
_SKIP = re.compile(r"embed|rerank|tts|whisper|dall-e|image|audio|moderation|speech|transcri|realtime|search-preview|davinci-00|babbage|ocr", re.I)


class ModelError(RuntimeError):
    """kind: config / auth / rate / server / network / timeout / parse / budget"""

    def __init__(self, msg: str, kind: str = "server", *, sent: bool = True):
        super().__init__(msg)
        self.kind, self.sent = kind, sent

    @property
    def outcome_unknown(self) -> bool:
        """请求可能已被模型处理（计费/生成）但我们没拿到结果：不能自动重试。"""
        return self.kind == "timeout" and self.sent

    @property
    def retriable(self) -> bool:
        return self.kind in {"rate", "server"} or (self.kind == "network" and not self.sent)


# ------------------------------------------------------------------ capability inference
def family(model: str) -> str:
    m = str(model or "").lower()
    for fam, rx in _FAMILIES:
        if re.search(rx, m):
            return fam
    return m.split("/")[0].split("-")[0] or "unknown"


def infer(model: str) -> dict:
    m = str(model or "").lower()
    fam = family(m)
    caps = set()
    if re.search(r"r1|reason|think|(^|[^a-z])o[134]|qwq|opus|gpt-5|gpt-4\.1|sonnet|gemini-(2\.5|3)-pro|-pro\b|k2|glm-4\.[5-9]|glm-[5-9]|max|v3\.[1-9]|large|mock-a-strong|mock-b", m):
        caps.add("reasoning")
    if re.search(r"mini|nano|flash|haiku|lite|turbo|small|air|tiny|[^0-9](1|3|7|8|9)b\b|chat$|mock-a-fast", m):
        caps.add("cheap")
    if "cheap" in caps or re.search(r"flash|turbo|fast|instant|haiku", m):
        caps.add("fast")
    if re.search(r"long|128k|200k|1m|gemini|claude|kimi|moonshot|qwen-(long|plus|max)|gpt-4\.1|gpt-5|glm-4|deepseek|mock", m):
        caps.add("long")
    if fam in _CN:
        caps.add("chinese")
    if re.search(r"vision|-vl|vl-|4o|gemini|claude-(3|sonnet|opus)|gpt-5|glm-4v", m):
        caps.add("vision")
    if re.search(r"coder|code", m):
        caps.add("code")
    if "reasoning" in caps and "cheap" in caps and not re.search(r"flash|mini", m):
        caps.discard("cheap")
    return {"model": model, "family": fam, "caps": sorted(caps)}


def score(model: str, role: str) -> tuple[float, list[str]]:
    info = infer(model)
    need = ROLE_NEEDS.get(role, {})
    s = sum(w for c, w in need.items() if c in info["caps"])
    if role in ("critic", "helicase", "drafter") and "cheap" in info["caps"] and "reasoning" not in info["caps"]:
        s -= 1
    return s, [CAP_LABEL[c] for c in need if c in info["caps"]]


# ------------------------------------------------------------------ secrets
class _Blob:  # Windows DPAPI via ctypes
    @staticmethod
    def protect(data: bytes) -> tuple[str, str]:
        if os.environ.get("HELIX_SECRET_KEY_FILE"):
            import secure_store
            return secure_store.protect(data)
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class DB(ctypes.Structure):
                    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

                inp = DB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
                out = DB()
                if ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), "helix", None, None, None, 0, ctypes.byref(out)):
                    blob = ctypes.string_at(out.pbData, out.cbData)
                    ctypes.windll.kernel32.LocalFree(out.pbData)
                    return "dpapi", base64.b64encode(blob).decode()
            except Exception:
                pass
        return "b64", base64.b64encode(data[::-1]).decode()

    @staticmethod
    def unprotect(kind: str, text: str) -> bytes:
        if kind == "fernet":
            import secure_store
            return secure_store.unprotect(text)
        raw = base64.b64decode(text)
        if kind == "dpapi":
            import ctypes
            from ctypes import wintypes

            class DB(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

            inp = DB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_char)))
            out = DB()
            if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
                raise ModelError("Key 无法解密（可能换了 Windows 账户），请重新填写。", "config", sent=False)
            data = ctypes.string_at(out.pbData, out.cbData)
            ctypes.windll.kernel32.LocalFree(out.pbData)
            return data
        return raw[::-1]


# ------------------------------------------------------------------ registry
DEFAULT_BUDGET = {"max_calls_per_day": 200, "max_tokens_per_day": 2_000_000, "max_tokens_per_run": 200_000, "timeout_s": 120}
MOCK = {"id": "mock", "name": "模拟（不联网、不计费）", "base_url": "mock://local", "style": "mock", "models": ["mock-a-strong", "mock-a-fast", "mock-b-critic"], "builtin": True}


class Registry:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "providers.json"
        self.secret_path = Path(data_dir) / "secrets.json"

    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        d.setdefault("providers", [])
        d.setdefault("roles", {})
        d["budget"] = {**DEFAULT_BUDGET, **(d.get("budget") or {})}
        d.setdefault("allow_mock", True)
        rules = {"allow_same_family": False, "send_file_text": True, **(d.get("rules") or {})}
        d["rules"] = rules
        return d

    def save(self, d: dict) -> None:
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def _secrets(self) -> dict:
        try:
            return json.loads(self.secret_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_secret(self, pid: str, key: str | None) -> None:
        with _LOCK:
            s = self._secrets()
            if key:
                kind, blob = _Blob.protect(key.encode("utf-8"))
                s[pid] = {"kind": kind, "blob": blob, "tail": key[-4:]}
            else:
                s.pop(pid, None)
            tmp = self.secret_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
            tmp.replace(self.secret_path)

    def key(self, pid: str) -> str:
        e = self._secrets().get(pid)
        return _Blob.unprotect(e["kind"], e["blob"]).decode("utf-8") if e else ""

    def providers(self, with_mock: bool = True) -> list[dict]:
        d = self.load()
        rows = list(d["providers"])
        if with_mock and d.get("allow_mock", True):
            rows.append(dict(MOCK))
        return rows

    def get(self, pid: str) -> dict | None:
        return next((p for p in self.providers() if p["id"] == pid), None)

    def public(self) -> dict:
        d, sec = self.load(), self._secrets()
        provs = []
        for p in self.providers():
            provs.append({**{k: p.get(k) for k in ("id", "name", "base_url", "style", "models", "builtin", "last_test", "added_at")},
                          "key_tail": (sec.get(p["id"]) or {}).get("tail", ""), "key_store": (sec.get(p["id"]) or {}).get("kind", ""),
                          "infos": [infer(m) for m in p.get("models") or []]})
        rec = recommend(self.providers())
        roles = {}
        for r in ROLES:
            cur = d["roles"].get(r) or {}
            use = cur if cur.get("model") and self.get(cur.get("provider", "")) else (rec["roles"].get(r) or {})
            roles[r] = {"label": ROLES[r], "provider": use.get("provider"), "model": use.get("model"), "pinned": bool(cur.get("model")),
                        "recommended": rec["roles"].get(r), "family": family(use.get("model") or "") if use.get("model") else ""}
        hetero = bool(roles["drafter"]["model"]) and roles["drafter"]["family"] != roles["critic"]["family"]
        return {"providers": provs, "roles": roles, "budget": d["budget"], "heterogeneous": hetero, "notes": rec["notes"], "allow_mock": d.get("allow_mock", True), "rules": d["rules"]}

    def upsert(self, data: dict) -> dict:
        url = str(data.get("base_url") or "").strip().rstrip("/")
        if not re.match(r"^https?://", url):
            raise ModelError("URL 需要以 http:// 或 https:// 开头，例如 https://api.deepseek.com/v1", "config", sent=False)
        url = re.sub(r"/(chat/completions|messages|completions)$", "", url)
        style = data.get("style") or ("anthropic" if "anthropic" in url else "openai")
        d = self.load()
        pid = re.sub(r"[^a-z0-9_-]", "", str(data.get("id") or "").lower()) or re.sub(r"[^a-z0-9]", "", re.sub(r"^https?://(api\.)?", "", url).split("/")[0].split(".")[0].lower())[:16] or "p"
        old = next((p for p in d["providers"] if p["id"] == pid), None)
        if pid == "mock":
            pid = "mock2"
        row = {**(old or {}), "id": pid, "name": str(data.get("name") or (old or {}).get("name") or pid)[:40], "base_url": url, "style": style if style in ("openai", "anthropic") else "openai",
               "added_at": (old or {}).get("added_at") or int(time.time())}
        manual = [m.strip() for m in re.split(r"[,，\s]+", str(data.get("models") or "")) if m.strip()]
        if manual:
            row["models"] = manual[:60]
        d["providers"] = [p for p in d["providers"] if p["id"] != pid] + [row]
        self.save(d)
        if data.get("api_key"):
            self._save_secret(pid, str(data["api_key"]).strip())
        if not manual:  # 没手填模型就去接口拉列表
            try:
                self.refresh_models(pid)
            except ModelError as exc:
                row["last_test"] = {"ok": False, "at": int(time.time()), "error": str(exc)}
                self._patch(pid, row)
        return self.get(pid)

    def _patch(self, pid: str, patch: dict) -> None:
        d = self.load()
        for p in d["providers"]:
            if p["id"] == pid:
                p.update(patch)
        self.save(d)

    def remove(self, pid: str) -> None:
        d = self.load()
        d["providers"] = [p for p in d["providers"] if p["id"] != pid]
        d["roles"] = {k: v for k, v in d["roles"].items() if v.get("provider") != pid}
        self.save(d)
        self._save_secret(pid, None)

    def set_roles(self, roles: dict, budget: dict | None = None, allow_mock: bool | None = None, rules: dict | None = None) -> None:
        d = self.load()
        for r, v in (roles or {}).items():
            if r not in ROLES:
                continue
            if not v or not v.get("model"):
                d["roles"].pop(r, None)
            elif self.get(str(v.get("provider"))):
                d["roles"][r] = {"provider": str(v["provider"]), "model": str(v["model"])[:120]}
        if budget:
            for k in DEFAULT_BUDGET:
                if k in budget:
                    d["budget"][k] = max(1, int(budget[k]))
        if allow_mock is not None:
            d["allow_mock"] = bool(allow_mock)
        for k in ("allow_same_family", "send_file_text"):
            if rules and k in rules:
                d["rules"][k] = bool(rules[k])
        self.save(d)

    def resolve_role(self, role: str) -> tuple[dict, str]:
        pub = self.public()["roles"].get(role) or {}
        p = self.get(pub.get("provider") or "")
        if not p or not pub.get("model"):
            raise ModelError(f"角色“{ROLES.get(role, role)}”还没有可用模型：请到 模型工坊 填 URL 和 Key。", "config", sent=False)
        return p, pub["model"]

    # -------------------------------------------------------------- network
    def refresh_models(self, pid: str) -> list[str]:
        p = self.get(pid)
        if not p or p.get("style") == "mock":
            return (p or {}).get("models") or []
        t0 = time.time()
        if p["style"] == "anthropic":
            data = _http("GET", _join(p["base_url"], "/v1/models" if not p["base_url"].endswith("/v1") else "/models"), None, _headers(p, self.key(pid)), 30)
        else:
            try:
                data = _http("GET", _join(p["base_url"], "/models"), None, _headers(p, self.key(pid)), 30)
            except ModelError as exc:
                if "404" in str(exc) and not p["base_url"].endswith("/v1"):
                    self._patch(pid, {"base_url": p["base_url"] + "/v1"})
                    p = self.get(pid)
                    data = _http("GET", _join(p["base_url"], "/models"), None, _headers(p, self.key(pid)), 30)
                else:
                    raise
        rows = data.get("data") if isinstance(data, dict) else data
        if isinstance(data, dict) and "models" in data and not rows:
            rows = data["models"]
        ids = []
        for r in rows or []:
            mid = r.get("id") or r.get("name") if isinstance(r, dict) else str(r)
            mid = str(mid or "").replace("models/", "")
            if mid and not _SKIP.search(mid) and mid not in ids:
                ids.append(mid)
        if not ids:
            raise ModelError("接口可以连通，但没有返回模型列表；请在“模型”里手动填写模型名。", "parse")
        self._patch(pid, {"models": ids[:80], "last_test": {"ok": True, "at": int(time.time()), "latency_ms": int((time.time() - t0) * 1000), "models": len(ids)}})
        return ids

    def test(self, pid: str, model: str = "") -> dict:
        p = self.get(pid)
        if not p:
            raise ModelError("找不到这个厂商。", "config", sent=False)
        model = model or (p.get("models") or [""])[0]
        if not model:
            raise ModelError("先填写或拉取模型列表。", "config", sent=False)
        t0 = time.time()
        r = self.chat(pid, model, [{"role": "user", "content": "只回复两个字：在线"}], max_tokens=16, timeout=40)
        res = {"ok": True, "at": int(time.time()), "model": model, "latency_ms": int((time.time() - t0) * 1000), "reply": r["text"][:40]}
        self._patch(pid, {"last_test": res})
        return res

    def chat(self, pid: str, model: str, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 4000, timeout: int = 120) -> dict:
        p = self.get(pid)
        if not p:
            raise ModelError("找不到这个厂商。", "config", sent=False)
        t0 = time.time()
        if p["style"] == "mock":
            text = mock_reply(model, messages)
            return {"text": text, "usage": {"input": sum(len(m["content"]) for m in messages) // 2, "output": len(text) // 2}, "latency_ms": int((time.time() - t0) * 1000), "model": model}
        key = self.key(pid)
        if p["style"] == "anthropic":
            system = "\n".join(m["content"] for m in messages if m["role"] == "system")
            body = {"model": model, "max_tokens": max_tokens, "messages": [m for m in messages if m["role"] != "system"]}
            if system:
                body["system"] = system
            url = _join(p["base_url"], "/v1/messages" if not p["base_url"].endswith("/v1") else "/messages")
            data = _http("POST", url, body, _headers(p, key), timeout)
            text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
            u = data.get("usage") or {}
            usage = {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}
        else:
            body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            url = _join(p["base_url"], "/chat/completions")
            try:
                data = _http("POST", url, body, _headers(p, key), timeout)
            except ModelError as exc:  # 有些接口不支持 response_format / temperature：明确被拒（未生成），可安全改参数再发
                if exc.kind == "server" and "400" in str(exc) and json_mode:
                    body.pop("response_format", None); body.pop("temperature", None)
                    data = _http("POST", url, body, _headers(p, key), timeout)
                else:
                    raise
            ch = (data.get("choices") or [{}])[0]
            text = (ch.get("message") or {}).get("content") or ch.get("text") or ""
            if isinstance(text, list):
                text = "".join(x.get("text", "") for x in text if isinstance(x, dict))
            u = data.get("usage") or {}
            usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)}
        if not str(text).strip():
            raise ModelError("模型返回了空内容。", "parse")
        return {"text": str(text), "usage": usage, "latency_ms": int((time.time() - t0) * 1000), "model": model}


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _headers(p: dict, key: str) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "HelixHarness/0.5"}
    if p["style"] == "anthropic":
        h.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif key:
        h["Authorization"] = "Bearer " + key
    return h


def _http(method: str, url: str, body: Any, headers: dict, timeout: int) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        if e.code in (401, 403):
            raise ModelError(f"Key 无效或无权限（HTTP {e.code}）：{detail}", "auth")
        if e.code == 429:
            raise ModelError(f"限流或额度不足（HTTP 429）：{detail}", "rate")
        if e.code == 404:
            raise ModelError(f"地址或模型不存在（HTTP 404）：{detail}", "server" if method == "GET" else "config")
        raise ModelError(f"接口报错 HTTP {e.code}：{detail}", "server")
    except (socket.timeout, TimeoutError) as e:
        raise ModelError(f"等待模型超时（{timeout} 秒）", "timeout", sent=method == "POST") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ModelError(f"等待模型超时（{timeout} 秒）", "timeout", sent=method == "POST") from e
        raise ModelError(f"连不上 {url.split('/')[2]}：{e.reason}", "network", sent=False) from e
    except ConnectionError as e:
        raise ModelError(f"连接中断：{e}", "timeout" if method == "POST" else "network", sent=method == "POST") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ModelError("接口返回的不是 JSON：" + raw[:120].decode("utf-8", "replace"), "parse") from e


# ------------------------------------------------------------------ recommend
def recommend(providers: list[dict]) -> dict:
    cands = [(p["id"], m) for p in providers for m in p.get("models") or [] if not _SKIP.search(m)]
    real = [c for c in cands if c[0] != "mock"]
    pool = real or cands
    out, notes = {}, []
    for role in ROLES:
        best = None
        for pid, m in pool:
            s, why = score(m, role)
            if role == "critic" and out.get("drafter") and family(m) == family(out["drafter"]["model"]):
                s -= 100  # 异源：不同模型家族
            key = (s, -len(m))
            if best is None or key > best[0]:
                best = (key, pid, m, why)
        if best:
            out[role] = {"provider": best[1], "model": best[2], "score": best[0][0], "why": best[3], "family": family(best[2])}
    if out.get("critic") and out.get("drafter") and out["critic"]["family"] == out["drafter"]["family"]:
        notes.append("只有一个模型家族：批评者无法异源。请再添加一家不同厂商（例如起草用 DeepSeek，批评用 Qwen / Claude / GPT），否则运行会被拦下。")
    if not real:
        notes.append("还没有真实模型：现在只能用“模拟”试跑流程（不联网、结果不可当科研依据）。")
    return {"roles": out, "notes": notes}


# ------------------------------------------------------------------ mock
def mock_reply(model: str, messages: list[dict]) -> str:
    """确定性的模拟输出：让流程、阻断与恢复可以离线测试。"""
    prompt = messages[-1]["content"]
    step = re.search(r"STEP=(\w+)", prompt)
    step = step.group(1) if step else ""
    if "FAIL_MOCK" in prompt and step in prompt.split("FAIL_MOCK:")[-1][:20]:
        raise ModelError("模拟故障（故障注入）", "server")
    if step == "plan":
        return json.dumps({"questions": [{"id": "Q1", "text": "交付物的验收标准需要哪些原始数据支撑？", "why": "来自任务验收标准"},
                                         {"id": "Q2", "text": "现有文件里是否有与交付物直接相关的记录？", "why": "来自已登记文件"}],
                           "risks": ["证据不足时只能给 NEEDS_EVIDENCE"]}, ensure_ascii=False)
    if step == "extract":
        quotes = re.findall(r"<<<FILE ([0-9a-f]{64}) [^\n]*\n(.*?)\n>>>", prompt, re.S)
        cards = []
        for i, (sha, text) in enumerate(quotes[:3], 1):
            line = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 8), "")
            if line:
                cards.append({"id": f"E{i}", "file_sha": sha, "quote": line[:120], "supports": "Q2", "note": "模拟抽取：取文件中第一条有效记录"})
        cards.append({"id": f"E{len(cards) + 1}", "file_sha": (quotes[0][0] if quotes else "0" * 64), "quote": "这句话不在任何文件里（故意的幻觉摘录）", "supports": "Q1", "note": "用于验证摘录核验"})
        return json.dumps({"cards": cards}, ensure_ascii=False)
    if step == "draft":
        ids = re.findall(r'"id": "(E\d+)"', prompt)
        bases = [{"kind": "action", "text": "根据已登记文件的记录，整理交付物所需的原始数据清单（探索性）", "rationale": "文件中有相关记录", "confidence": 0.62, "evidence": ids[:1]},
                 {"kind": "gap", "text": "验收标准中的关键指标在现有文件中未找到，记为 UNKNOWN", "rationale": "缺失必须记 UNKNOWN", "confidence": 0.5, "evidence": []},
                 {"kind": "action", "text": "该化合物活性已实验证实，可直接进入正式 Top 3", "rationale": "模拟越界表述，应被校对拦截", "confidence": 0.9, "evidence": ids[:1]}]
        return json.dumps({"summary": "模拟起草：2 条可配对要点 + 1 条故意越界", "bases": bases}, ensure_ascii=False)
    if step == "critique":
        return json.dumps({"reviews": [{"index": 1, "verdict": "support", "issue": "", "suggest": ""},
                                       {"index": 2, "verdict": "weak", "issue": "未说明缺哪些指标", "suggest": "列出指标名"},
                                       {"index": 3, "verdict": "overclaim", "issue": "把预测写成实验事实，违反主张上限", "suggest": "删除或改为待验证假说"}],
                           "missing": ["缺少对照组数据"]}, ensure_ascii=False)
    if step == "summary":
        return "# 周汇总（模拟）\n\n- 已配对要点按账本整理。\n- 本文件为草稿，未经真人确认不得外发。\n"
    return json.dumps({"ok": True})


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


__all__ = ["Registry", "ModelError", "ROLES", "recommend", "infer", "family", "digest"]
