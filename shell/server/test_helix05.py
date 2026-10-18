"""Helix 0.5 端到端自测（含故障注入）。在临时副本里跑，不碰你的 data/：
    python server/test_helix05.py
覆盖：口令登录 / 冒用拒绝 / 指定复核人强制 / 证据引用核验 / 摘录逐字校验 / 主张上限 /
计划冻结 / DAG Gate 阻断 / 模拟智能体完整执行 / 异源互审 / 明确失败自动重试 /
超时 = 结果未知不重试 / 断电重启后从断点恢复 / 预算上限 / 账本哈希链完整。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parent.parent
OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✕ ") + name + ("" if cond else f"  → {detail}"), flush=True)


class MockNotion:
    """测试用的假 Notion API：记录请求，不联网。"""
    def __init__(self):
        import http.server
        import threading
        me = self
        self.db, self.rows, self.log, self.props, self.down = "c" * 32, {}, [], {"任务": {"type": "title"}}, False

        class Hd(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def _do(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"null") if n else None
                me.log.append((method, self.path, body))
                if me.down:
                    return self._send(503, {"message": "down"})
                if self.headers.get("Authorization") != "Bearer ntn_" + "t" * 40 or self.headers.get("Notion-Version") != "2022-06-28":
                    return self._send(401, {"message": "unauthorized"})
                p = self.path
                if p == f"/databases/{me.db}" and method == "GET":
                    return self._send(200, {"title": [{"plain_text": "组内打卡"}], "properties": me.props})
                if p == f"/databases/{me.db}" and method == "PATCH":
                    me.props.update({k: {"type": next(iter(v))} for k, v in body["properties"].items()}); return self._send(200, {})
                if p == f"/databases/{me.db}/query":
                    hid = body["filter"]["rich_text"]["equals"]
                    return self._send(200, {"results": [{"id": k} for k, v in me.rows.items() if v.get("Helix ID") == hid]})
                flat = lambda pr: {k: (v.get("number") if "number" in v else (v.get("select") or {}).get("name") if "select" in v else
                                       "".join(x["text"]["content"] for x in v.get("rich_text", v.get("title", [])) or []) if ("rich_text" in v or "title" in v) else (v.get("date") or {}).get("start")) for k, v in pr.items()}
                if p == "/pages" and method == "POST":
                    pid = "page-%d" % len(me.rows); me.rows[pid] = flat(body["properties"]); return self._send(200, {"id": pid})
                if p.startswith("/pages/") and method == "PATCH":
                    pid = p.split("/")[2]
                    if pid not in me.rows:
                        return self._send(404, {"message": "gone"})
                    me.rows[pid].update(flat(body["properties"])); return self._send(200, {"id": pid})
                return self._send(404, {"message": "?"})

            def do_GET(self): self._do("GET")
            def do_POST(self): self._do("POST")
            def do_PATCH(self): self._do("PATCH")
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


class Srv:
    def __init__(self, root: Path):
        self.root = root
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.proc = None

    def start(self):
        # This regression fixture exercises the explicit named-display mode.
        # Anonymous defaults and joining existing slots have their own API suite.
        cfgpath=self.root/'helix.config.json'
        cfg=json.loads(cfgpath.read_text(encoding='utf-8')) if cfgpath.exists() else {}
        if cfg.get('anonymous_members') is not False:  # 旧回归按实名名册验证
            cfg['anonymous_members']=False;cfgpath.write_text(json.dumps(cfg,ensure_ascii=False),encoding='utf-8')
        env = {**os.environ, "HELIX_TEST": "1", "HELIX_PORT": str(self.port), "HELIX_STATE_DIR": str(self.root / "_state"), "MASH_AIDD_RESEARCH_ROOTS": str(self.root / "_none")}
        self.proc = subprocess.Popen([sys.executable, str(self.root / "server" / "serve.py")], env=env, stdout=subprocess.DEVNULL, stderr=open(self.root / "err.log", "a"))
        for _ in range(100):
            try:
                self.get("/api/health"); return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("服务没起来：" + (self.root / "err.log").read_text())

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate(); self.proc.wait(5)

    read_token = ""  # 3.1 前置登录：账号建立后，读取接口也要带会话

    def get(self, path, token=""):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers={"X-Helix-Token": token or self.read_token})
        return json.loads(urllib.request.urlopen(req, timeout=20).read())

    def post(self, path, body=None, token="", raw=None):
        data = raw if raw is not None else json.dumps(body or {}).encode()
        h = {"X-Helix": "1", "X-Helix-Token": token, "Content-Type": "application/json"}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h, method="POST")
        try:
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())


def wait_run(srv, rid, tok, states=("completed", "failed", "unknown", "blocked", "interrupted"), t=20):
    for _ in range(int(t * 10)):
        r = next((x for x in srv.get("/api/runs", tok)["runs"] if x["run_id"] == rid), None)
        if r and r["status"] in states:
            return r
        time.sleep(0.1)
    return r


def map_unit():
    """姓名映射：按“受保护字段”通用处理；显示时原名→显示名，写入时显示名→原名。"""
    print("0 姓名映射（单元）")
    import re
    src = (SRC / "server" / "serve.py").read_text(encoding="utf-8")
    a, b = src.index("# 姓名映射（通用版）"), src.index("# ------------------------------------------------------------------ 快照")
    ns: dict = {"re": re}
    exec(src[a:b], ns)
    exec(src[src.index("# 条目状态统一成内核词表"):src.index("_ITEM_KEYS = (")], ns)
    m = {"1": "成员A", "张三": "老张"}
    o = {"task_id": "T1", "week": 1, "n": "1", "owner": "1", "members": ["1", "张三"], "workload": {"张三": 3},
         "items": [{"tid": "T11", "base_id": "b-张三", "sha256": "张三", "status": "deferred", "cross_reviewer": "张三", "detail": "张三 通过",
                    "rationale": "张三负责核对原始数据", "bond_reasons": ["张三 已确认"]}],
         "files": [{"name": "张三.csv"}], "port": {"detail": "D:/数据/张三/x"}, "new_field_v2": "由张三复核"}
    r = ns["apply_map"](o, m)
    it = r["items"][0]
    check("原名为“1”时编号/数字不被改坏", r["task_id"] == "T1" and r["n"] == "1" and r["week"] == 1 and it["tid"] == "T11", r)
    check("人员字段按名册替换", r["owner"] == "成员A" and r["members"] == ["成员A", "老张"] and it["cross_reviewer"] == "老张", r)
    check("任意说明字段（含 rationale、bond_reasons、将来新增字段）都替换", it["detail"] == "老张 通过" and it["rationale"] == "老张负责核对原始数据"
          and it["bond_reasons"] == ["老张 已确认"] and r["new_field_v2"] == "由老张复核" and r["workload"] == {"老张": 3}, r)
    check("ID、哈希、状态、文件名、路径不动", it["base_id"] == "b-张三" and it["sha256"] == "张三" and it["status"] == "deferred"
          and r["files"][0]["name"] == "张三.csv" and r["port"]["detail"] == "D:/数据/张三/x", r)
    check("写入前还原原名（往返一致，计划正文不会被代号替换）", ns["unmap"](r, m) == o, ns["unmap"](r, m))
    check("状态别名归一（defer/HOLD/Deferred → deferred）", [ns["canon_status"](x) for x in ("defer", "HOLD", "Deferred", "open")] == ["deferred"] * 3 + ["open"])


def evidence_unit():
    """证据等级：格式合法、记录存在、可解析、摘录匹配分开记录。"""
    print("0 证据等级（单元）")
    sys.path.insert(0, str(SRC / "server"))
    import evidence as E
    files = [{"sha256": "ab" * 32, "name": "a.pdf", "stored": "a.pdf"}]
    r = E.resolve("DOI:10.1000/xyz", files)
    check("DOI 只校验格式 → format_only，不是强来源", r["level"] == "format_only" and not r["strong"] and r["checks"]["record"] is None, r)
    r = E.check_refs(["PMID:12345678"], decision="PASS", files=files, id_checked=True)
    check("真人勾选已核对 → 记为 human_checked", r[0]["level"] == "human_checked" and r[0]["checks"]["record"] == "human", r)
    r = E.check_refs(["sha256:" + "ab" * 32], decision="PASS", files=files, quote="一句原文摘录内容", file_text=lambda _r: None)
    check("文件无法解析 → 摘录标为尚未核验", r[0]["quote"] == "unverified" and r[0]["checks"]["readable"] is False and "尚未核验" in r[0]["note"], r)


def plan_unit():
    """职能分工与文件预览（单元，不起服务）。"""
    print("0 职能分工与文件预览（单元）")
    sys.path.insert(0, str(SRC / "server"))
    import projects as P
    p, w = P.clean_project({"name": "x", "start": "2025-01-06", "n_weeks": 1, "members": ["甲", "乙"]})
    check("新项目带默认职能目录（主导/数据/文献/对接/外联/写作）", [d["label"] for d in p["duties"]] == ["主导", "数据", "文献", "对接", "外联", "写作"], p["duties"])
    p, w = P.clean_project({"name": "x", "start": "2025-01-06", "n_weeks": 1,
                            "members": [{"name": "甲", "duties": ["lead", "数据", "不存在", "data"]}, {"name": "乙", "duties": ["文献"]}, {"name": "丙"}],
                            "tasks": [{"task_id": "T1", "deliverable": "清洗数据", "owner": "甲", "cross_reviewer": "乙", "duty": "数据"},
                                      {"task_id": "T2", "deliverable": "读文献", "owner": "甲", "cross_reviewer": "乙", "duty": "lit"},
                                      {"task_id": "T3", "deliverable": "写稿", "owner": "乙", "cross_reviewer": "甲", "duty": "写作"},
                                      {"task_id": "T4", "deliverable": "杂项", "owner": "乙", "cross_reviewer": "甲", "duty": "扫地"}]})
    m = {x["name"]: x for x in p["members"]}
    check("成员职能按名称或键识别、去重、丢弃未知", m["甲"]["duties"] == ["lead", "data"] and m["乙"]["duties"] == ["lit"] and m["丙"]["duties"] == [], m)
    t = {x["task_id"]: x for x in p["tasks"]}
    check("任务职能统一成键；未知职能留空并提醒", t["T1"]["duty"] == "data" and t["T2"]["duty"] == "lit" and t["T4"]["duty"] == "" and any("扫地" in x for x in w), (t, w))
    check("分工提醒：没人负责 / 主核不具备 / 未分配", any("「写作」有任务但没有成员负责" in x for x in w) and any("T2 属于「文献」，主核 甲" in x for x in w)
          and any("丙 还没有分配职能" in x for x in w) and any("T3 属于「写作」" in x for x in w), w)
    p2, _ = P.clean_project({"duties": [{"key": "lead", "label": "主导"}, {"label": "建模"}, {"label": "建模"}]}, p)
    check("删除职能后成员与任务同步清理；自定义职能自动生成键", [d["key"] for d in p2["duties"]] == ["lead", "c2"]
          and next(x for x in p2["members"] if x["name"] == "甲")["duties"] == ["lead"] and all(x["duty"] == "" for x in p2["tasks"]), p2)
    r = P.parse_tasks("t.csv", "编号,交付物,主核,职能\nT1,清洗,甲,数据\n".encode())
    check("导入任务表识别“职能”列", r["rows"][0].get("duty") == "数据" and r["columns"].get("duty") == "职能", r)
    d = Path(tempfile.mkdtemp(prefix="hxpv-"))
    f = d / ("ab" * 6 + "-数据.csv"); f.write_text("a,b\n" + "".join(f"{i},{i * 2}\n" for i in range(301)), encoding="utf-8")
    r = P.preview(f)
    check("CSV 预览：前 200 行、记总行数、去掉指纹前缀", r["kind"] == "table" and r["readable"] and len(r["sheets"][0]["rows"]) == 200 and r["sheets"][0]["total"] == 302
          and r["truncated"] and r["name"] == "数据.csv", {k: r[k] for k in ("kind", "name", "truncated")})
    f = d / "说明.txt"; f.write_bytes("化合物 CPD-001 有效".encode("gb18030"))
    r = P.preview(f)
    check("GBK 文本也能预览", r["kind"] == "text" and "CPD-001 有效" in r["text"], r)
    (d / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16); (d / "a.svg").write_text("<svg/>")
    r1, r2 = P.preview(d / "a.png"), P.preview(d / "a.svg")
    check("图片可内联显示；SVG 不内联（防脚本）", r1["kind"] == "image" and r1["inline"] and not r2["inline"] and r2["kind"] == "text", (r1, r2))
    (d / "bad.pdf").write_bytes(b"%PDF-1.4 broken")
    r = P.preview(d / "bad.pdf")
    check("损坏 / 无文字层 PDF → 不可逐字核验，并说明原因", r["kind"] == "pdf" and r["readable"] is False and r.get("error"), r)
    shutil.rmtree(d, ignore_errors=True)


def team_unit():
    print("0.6 组内打卡 + 统筹重分配 + 云同步（单元）")
    sys.path.insert(0, str(SRC / "server"))
    import team as TM
    import cloud as CL
    mem = [{"name": "组长", "role": "lead", "duties": ["lead", "write"]}, {"name": "数A", "role": "both", "duties": ["data"]},
           {"name": "数B", "role": "both", "duties": ["data"]}, {"name": "文C", "role": "both", "duties": ["lit"]}]
    tasks = [{"task_id": "T1", "week": 1, "duty": "data", "deliverable": "清洗数据"}, {"task_id": "T2", "week": 1, "duty": "data", "deliverable": "统计"},
             {"task_id": "T3", "week": 2, "duty": "data", "deliverable": "作图", "depends_on": ["T1"]}, {"task_id": "T4", "week": 1, "duty": "lit", "deliverable": "检索文献"},
             {"task_id": "T5", "week": 2, "duty": "lead", "deliverable": "定方向"}, {"task_id": "T6", "week": 2, "duty": "write", "deliverable": "写报告", "depends_on": "T4"}]
    r = TM.suggest(mem, tasks, duties_list=[{"key": "data", "label": "数据"}])
    by = {x["task_id"]: x for x in r["rows"]}
    check("统筹模式：组长默认做交叉核兜底", r["lead"] == "组长" and all(by[t]["cross_reviewer"] == "组长" for t in ("T1", "T2", "T3", "T4")), by)
    check("统筹模式：主核按职能分，同周同职能分散给不同人", by["T1"]["owner"] in ("数A", "数B") and by["T2"]["owner"] in ("数A", "数B") and by["T1"]["owner"] != by["T2"]["owner"] and by["T4"]["owner"] == "文C", by)
    check("统筹模式：同一依赖链交给同一人（少交接）", by["T3"]["owner"] == by["T1"]["owner"] and "交接" in by["T3"]["why"] and "「数据」" in by["T1"]["why"], by)
    check("统筹模式：组长当主核时，交叉核换成别人", by["T5"]["owner"] == "组长" and by["T5"]["cross_reviewer"] != "组长", by)
    check("统筹模式：只有组长有的职能仍可由组长主核；主核≠交叉核", by["T6"]["owner"] == "组长" and all(x["owner"] != x["cross_reviewer"] for x in r["rows"]), by)
    try:
        TM.suggest([{"name": "独"}], tasks); ok1 = False
    except ValueError:
        ok1 = True
    check("统筹模式：少于 2 人拒绝", ok1)
    t = {"task_id": "T1", "week": 1}
    files = [{"sha256": "ab" * 32, "name": "a.csv"}]
    c = TM.build_checkin({"state": "doing", "progress": "130", "note": " 跑完一半 ", "files": ["sha256:" + "ab" * 8]}, t, files, "owner")
    check("打卡：进度夹到 0–100，未完成不显示 100%，成果文件按指纹前缀识别", c["progress"] == 95 and c["note"] == "跑完一半" and c["files"][0]["name"] == "a.csv", c)
    check("打卡：已完成 = 100%", TM.build_checkin({"state": "done", "progress": 10}, t, [], "owner")["progress"] == 100)
    bad = []
    for d in ({"state": "x"}, {"state": "blocked", "note": "卡"}, {"state": "doing", "files": ["cd" * 32]}):
        try:
            TM.build_checkin(d, t, files, "owner"); bad.append(d)
        except ValueError:
            pass
    check("打卡：未知状态 / 受阻不写原因 / 未登记成果文件 → 拒绝", not bad, bad)
    ev = [{"seq": i, "ts": f"2026-01-0{i}", "type": "task.checkin", "actor": a, "payload": {"task_id": "T1", "state": s, "progress": p, "as": r_}}
          for i, (a, s, p, r_) in enumerate([("数A", "started", 10, "owner"), ("组长", "doing", 0, "cross"), ("数A", "doing", 60, "owner")], 1)]
    f = TM.fold_checkins(ev + [{"seq": 9, "type": "base.paired", "payload": {}}])["T1"]
    check("打卡折叠：最新、主核/交叉核各自最新、历史新→旧", f["latest"]["seq"] == 3 and f["owner"]["progress"] == 60 and f["cross"]["actor"] == "组长" and [h["seq"] for h in f["history"]] == [3, 2, 1] and f["count"] == 3, f)
    sm = TM.summary({"T1": f}, [{"task_id": "T1", "week": 1}, {"task_id": "T2", "week": 1}])
    check("打卡汇总：按主核进度平均，未打卡算 0", sm["progress"] == 30 and sm["doing"] == 1 and sm["none"] == 1, sm)
    check("数据库链接 / ID 都能识别", CL.parse_db_id("https://www.notion.so/ws/打卡-" + "a" * 32 + "?v=" + "b" * 32) == "a" * 32 and CL.parse_db_id("aaaaaaaa-aaaa-aaaa-aaaa-" + "a" * 12) == "a" * 32)
    row = {"helix_id": "P:T1", "task_id": "T1", "title": "清洗", "owner": "数A", "state": "进行中", "progress": 60, "files": "a.csv (abababab)"}
    props = CL.page_properties(row, "Name")
    check("云端字段白名单（标题 + 固定列，进度按百分比）", set(props) == {"Name"} | set(CL.SCHEMA) and props["进度"]["number"] == 0.6 and props["Name"]["title"][0]["text"]["content"] == "T1 · 清洗", sorted(props))
    calls = []
    pages = {}

    def fake(method, path, tok, body=None, timeout=20):
        calls.append((method, path))
        if method == "GET":
            return {"title": [{"plain_text": "打卡"}], "properties": {"Name": {"type": "title"}, "周": {"type": "number"}}}
        if path.endswith("/query"):
            hid = body["filter"]["rich_text"]["equals"]
            return {"results": [{"id": k} for k, v in pages.items() if v == hid]}
        if path == "/pages":
            pid = "p%d" % len(pages); pages[pid] = body["properties"]["Helix ID"]["rich_text"][0]["text"]["content"]; return {"id": pid}
        return {}
    d = Path(tempfile.mkdtemp(prefix="helix-cloud-"))
    rows = [dict(row), {**row, "helix_id": "P:T2", "task_id": "T2"}]
    cl = CL.Cloud(d, lambda pid: rows, http=fake)
    cl.gap = 0
    r = cl.configure("a" * 32, "ntn_" + "k" * 40)
    added = [p for m, p in calls if m == "PATCH" and p.startswith("/databases/")]
    check("连接云端：自动补齐缺的列（已有的不重复加）", "周" not in r["added"] and "打卡状态" in r["added"] and len(added) == 1 and r["token_tail"] == "kkkk", r)
    check("Token 不以明文落盘", "ntn_" + "k" * 40 not in (d / "cloud.json").read_text(encoding="utf-8"))
    r1 = cl.sync("P")
    r2 = cl.sync("P")
    rows[0]["progress"] = 80
    n0 = len(calls); r3 = cl.sync("P")
    check("同步：首次建行 → 未变跳过 → 有变只更新那一行", r1["pushed"] == 2 and len(pages) == 2 and r2 == {"pushed": 0, "skipped": 2, "rows": 2}
          and r3["pushed"] == 1 and calls[n0:] == [("PATCH", "/pages/p0")], (r1, r2, r3, calls[n0:]))
    shutil.rmtree(d, ignore_errors=True)


def main():
    map_unit()
    evidence_unit()
    plan_unit()
    team_unit()
    mock = MockNotion()
    os.environ["HELIX_CLOUD_BASE"] = mock.url
    tmp = Path(tempfile.mkdtemp(prefix="helix05-"))
    root = tmp / "shell"
    shutil.copytree(SRC, root, ignore=shutil.ignore_patterns("data", "_devstate", "__pycache__", "docs", "*.zip"))
    (root / "data").mkdir()
    srv = Srv(root)
    srv.start()
    try:
        print("1 项目与身份")
        today = date.today().isoformat()
        r = srv.post("/api/projects", {"name": "测试项目", "target": "X", "claim_ceiling": "EXPLORATORY_ONLY", "start": today, "n_weeks": 2,
                                       "members": [{"name": "甲", "role": "lead"}, {"name": "乙", "role": "both"}, {"name": "丙", "role": "both"}],
                                       "tasks": [{"task_id": "T1", "week": 1, "deliverable": "整理活性数据", "acceptance": "有原始表", "owner": "乙", "cross_reviewer": "丙"},
                                                 {"task_id": "T2", "week": 1, "deliverable": "建模", "acceptance": "可复现", "owner": "丙", "cross_reviewer": "乙", "depends_on": "T1"}]})
        check("引导模式可建项目", r.get("ok"), r)
        base = {"name": "依赖测试", "start": today, "n_weeks": 1, "members": [{"name": "乙", "role": "both"}, {"name": "丙", "role": "both"}]}
        r = srv.post("/api/projects", {**base, "tasks": [{"task_id": "T1", "week": 1, "deliverable": "a", "owner": "乙", "cross_reviewer": "丙", "depends_on": "MISSING"}]})
        check("P0：依赖指向不存在的任务 → 拒绝保存（不静默删除）", not r.get("ok") and "MISSING" in r.get("error", ""), r)
        r = srv.post("/api/projects", {**base, "tasks": [{"task_id": "T1", "week": 1, "deliverable": "a", "owner": "乙", "cross_reviewer": "丙", "depends_on": "T2"},
                                                     {"task_id": "T2", "week": 1, "deliverable": "b", "owner": "丙", "cross_reviewer": "乙", "depends_on": "T1"}]})
        check("P0：依赖成环 → 拒绝保存（不静默删除）", not r.get("ok") and "成环" in r.get("error", ""), r)
        people = {p["name"]: p["id"] for p in srv.get("/api/snapshot")["people"]["roster"]}
        check("成员登记进名册", {"甲", "乙", "丙"} <= set(people), people)
        r = srv.post("/api/auth/set-pin", {"person_id": people["甲"], "new_pin": "lead-123456"})
        check("组长首次设口令（引导）", r.get("ok") and r.get("how") == "bootstrap", r)
        r = srv.post("/api/auth/set-pin", {"person_id": people["乙"], "new_pin": "yi-123456"})
        check("有组长后，新成员设口令需组长批准", not r.get("ok"), r)
        r = srv.post("/api/auth/set-pin", {"person_id": people["乙"], "new_pin": "yi-123456", "admin_id": people["甲"], "admin_pin": "wrong"})
        check("组长口令错误不能批准", not r.get("ok"), r)
        for n, pin in (("乙", "yi-123456"), ("丙", "bing-123456")):
            r = srv.post("/api/auth/set-pin", {"person_id": people[n], "new_pin": pin, "admin_id": people["甲"], "admin_pin": "lead-123456"})
            check(f"组长批准 {n} 设口令", r.get("ok"), r)
        tok = {n: srv.post("/api/auth/login", {"person_id": people[n], "pin": p})["token"] for n, p in (("甲", "lead-123456"), ("乙", "yi-123456"), ("丙", "bing-123456"))}
        srv.read_token = tok["甲"]
        r = srv.post("/api/auth/login", {"person_id": people["乙"], "pin": "nope"})
        check("口令错误登录失败", not r.get("ok"), r)
        r = srv.post("/api/projects/update", {"name": "改名"}, token=tok["乙"])
        check("非组长不能改计划", not r.get("ok") and r.get("auth"), r)
        r = srv.post("/api/people", {"people": []})
        check("未登录不能改名册（防止改指纹冒用）", not r.get("ok"), r)

        print("2 播种、冻结与指定复核人")
        r = srv.post("/api/seed", {}, token=tok["乙"])
        check("首次播种（会冻结计划）需组长", not r.get("ok"), r)
        r = srv.post("/api/seed", {}, token=tok["甲"])
        check("组长播种成功并自动冻结", r.get("ok") and r.get("count") == 2, r)
        s = srv.get("/api/snapshot", tok["甲"])
        check("计划已冻结", (s["gates"]["plan"]["frozen"] or {}).get("plan_hash") == s["gates"]["plan"]["hash"], s["gates"]["plan"])
        check("T2 被上游 T1 阻断", s["gates"]["tasks"]["T2"]["state"] == "blocked", s["gates"]["tasks"])
        b1 = next(b for b in s["items"] if b.get("task_id") == "T1")
        b2 = next(b for b in s["items"] if b.get("task_id") == "T2")
        body = {"base_id": b1["base_id"], "verdict": "complement", "decision": "PASS", "confirmed": True, "source_refs": ["PMID:12345678"]}
        r = srv.post("/api/pair", {**body, "reviewer": "乙"})
        check("不登录、只填名字 → 拒绝（不能冒用）", not r.get("ok") and r.get("auth"), r)
        r = srv.post("/api/pair", {**body, "reviewer": "乙"}, token=tok["丙"])
        check("非指定主核登录后也不能代签", not r.get("ok") and "不能代签" in r.get("error", ""), r)
        r = srv.post("/api/pair", {**body, "source_refs": ["我看过了"]}, token=tok["乙"])
        check("随手写的引用被拒绝", not r.get("ok") and "对不上" in r.get("error", ""), r)
        r = srv.post("/api/pair", {**body, "source_refs": ["https://example.com/a"]}, token=tok["乙"])
        check("PASS 只有网页链接 → 拒绝", not r.get("ok") and "可核验" in r.get("error", ""), r)
        r = srv.post("/api/pair", body, token=tok["乙"])
        check("PASS 只有 PMID、未勾选“已核对原文” → 拒绝（格式合法≠已验证）", not r.get("ok") and "DOI / PMID" in r.get("error", ""), r)
        r = srv.post("/api/pair", {**body, "source_refs": ["sha256:" + "a" * 64]}, token=tok["乙"])
        check("对不上文件的 SHA → 拒绝", not r.get("ok"), r)
        csv = "compound,ic50_nM,assay\nCPD-001,35,cAMP HTRF\nCPD-002,UNKNOWN,cAMP HTRF\n".encode()
        f = srv.post("/api/files?name=activity.csv", raw=csv, token=tok["乙"])["file"]
        pv = srv.get("/api/file/preview?sha=" + f["sha256"], tok["乙"])
        check("看原文：表格文件按行列返回", pv.get("ok") and pv["kind"] == "table" and pv["sheets"][0]["rows"][1][:2] == ["CPD-001", "35"], pv)
        check("快照计划里带职能目录", len(((srv.get("/api/snapshot", tok["乙"]).get("plan") or {}).get("duties") or [])) == 6, "")
        r = srv.post("/api/pair", {**body, "source_refs": ["sha256:" + f["sha256"]], "quote": "CPD-009,1,"}, token=tok["乙"])
        check("摘录不在文件里 → 拒绝", not r.get("ok") and "找不到" in r.get("error", ""), r)
        r = srv.post("/api/pair", {**body, "verdict": "revise", "revised_text": "CPD-001 已实验证实有效，进入正式 Top 1", "source_refs": ["sha256:" + f["sha256"]]}, token=tok["乙"])
        check("修正内容越过主张上限 → 拒绝", not r.get("ok") and "主张上限" in r.get("error", ""), r)
        r = srv.post("/api/pair", {"base_id": b2["base_id"], "verdict": "complement", "decision": "PASS", "confirmed": True, "source_refs": ["sha256:" + f["sha256"]]}, token=tok["丙"])
        check("上游没通过，下游不能判 PASS（Gate 阻断）", not r.get("ok") and "Gate 阻断" in r.get("error", ""), r)
        r = srv.post("/api/pair", {**body, "source_refs": ["sha256:" + f["sha256"]], "quote": "CPD-001,35,cAMP HTRF"}, token=tok["乙"])
        check("指定主核 + 文件 SHA + 逐字摘录 → 写入", r.get("ok"), r)
        r = srv.post("/api/cross", {"base_id": b1["base_id"], "agree": True, "confirmed": True}, token=tok["乙"])
        check("主核本人不能交叉核", not r.get("ok"), r)
        r = srv.post("/api/cross", {"base_id": b1["base_id"], "agree": True, "confirmed": True}, token=tok["甲"])
        check("非指定交叉核不能代签", not r.get("ok") and "不能代签" in r.get("error", ""), r)
        r = srv.post("/api/cross", {"base_id": b1["base_id"], "agree": True, "confirmed": True}, token=tok["丙"])
        check("指定交叉核写入", r.get("ok"), r)
        s = srv.get("/api/snapshot", tok["甲"])
        check("T1 通过 → T2 解除阻断", s["gates"]["tasks"]["T1"]["state"] == "passed" and s["gates"]["tasks"]["T2"]["state"] == "open", s["gates"]["tasks"])
        led = (root / "data/projects").glob("**/ledger.jsonl")
        ev = [json.loads(x) for p in led for x in p.read_text(encoding="utf-8").splitlines()]
        att = [e for e in ev if e["type"] == "write.attested"]
        check("每次真人写入都有口令会话证明", len(att) >= 2 and all(a["payload"]["method"] == "pin-session" for a in att), len(att))

        print("2b 组内打卡与云同步")
        r = srv.post("/api/checkin", {"task_id": "T1", "state": "doing", "progress": 50})
        check("打卡：未登录 → 拒绝", not r.get("ok") and r.get("auth"), r)
        r = srv.post("/api/checkin", {"task_id": "T1", "state": "doing", "progress": 50}, token=tok["甲"])
        check("打卡：不是这条任务的主核/交叉核（组长也不行）→ 不能代打", not r.get("ok") and "不能代打" in r.get("error", ""), r)
        r = srv.post("/api/checkin", {"task_id": "T1", "state": "blocked", "note": "卡"}, token=tok["乙"])
        check("打卡：受阻不写原因 → 拒绝", not r.get("ok") and "受阻" in r.get("error", ""), r)
        r = srv.post("/api/checkin", {"task_id": "T1", "state": "doing", "progress": 60, "note": "数据整理过半", "files": ["sha256:" + "e" * 64]}, token=tok["乙"])
        check("打卡：成果文件没登记 → 拒绝", not r.get("ok") and "登记" in r.get("error", ""), r)
        r = srv.post("/api/checkin", {"task_id": "T1", "state": "doing", "progress": 60, "note": "数据整理过半", "files": [f["sha256"]]}, token=tok["乙"])
        check("打卡：主核本人打卡写入账本", r.get("ok") and r.get("cloud") is False, r)
        r = srv.post("/api/checkin", {"task_id": "T1", "state": "started", "note": "开始复核"}, token=tok["丙"])
        check("打卡：交叉核本人也能打卡（记为复核进度）", r.get("ok"), r)
        s = srv.get("/api/snapshot", tok["甲"])
        ck = (s.get("team") or {}).get("checkins", {}).get("T1") or {}
        check("快照：每个任务有最新打卡、主核进度、历史", ck.get("count") == 2 and ck["owner"]["progress"] == 60 and ck["owner"]["actor"] == "乙" and ck["cross"]["actor"] == "丙"
              and ck["owner"]["files"][0]["name"] == "activity.csv" and s["team"]["summary"]["progress"] == 30, ck)
        ev = [json.loads(x) for p in (root / "data/projects").glob("**/ledger.jsonl") for x in p.read_text(encoding="utf-8").splitlines()]
        cks = [e for e in ev if e["type"] == "task.checkin"]
        check("打卡是真人事件，并有口令会话证明", len(cks) == 2 and all(e["actor_kind"] == "human" for e in cks)
              and sum(1 for e in ev if e["type"] == "write.attested" and e["payload"]["target_type"] == "task.checkin") == 2, len(cks))
        r = srv.post("/api/cloud/config", {"database": mock.db, "token": "ntn_" + "t" * 40}, token=tok["乙"])
        check("云同步：非组长不能配置", not r.get("ok") and r.get("auth"), r)
        r = srv.post("/api/cloud/config", {"database": mock.db, "token": "我的密码"}, token=tok["甲"])
        check("云同步：Token 格式不对 → 拒绝", not r.get("ok") and "Token" in r.get("error", ""), r)
        r = srv.post("/api/cloud/config", {"database": "https://www.notion.so/x/" + mock.db + "?v=" + "0" * 32, "token": "ntn_" + "t" * 40}, token=tok["甲"])
        check("云同步：组长粘贴数据库链接 + Token → 连接成功并补齐列", r.get("ok") and r["cloud"]["configured"] and r["cloud"]["token_tail"] == "tttt" and "打卡状态" in r["cloud"]["added"], r)
        check("云同步：Token 不以明文落盘", "ntn_" + "t" * 40 not in (root / "data/cloud.json").read_text(encoding="utf-8"))
        r = srv.post("/api/cloud/sync", {}, token=tok["乙"])
        rows = {v["编号"]: v for v in mock.rows.values()}
        check("云同步：组员一键同步，每个任务一行", r.get("ok") and len(mock.rows) == 2 and set(rows) == {"T1", "T2"}, (r, list(rows)))
        t1 = rows.get("T1") or {}
        check("云端行内容：进度 / 状态 / 打卡人 / 成果文件名 / 审核状态", t1.get("进度") == 0.6 and t1.get("打卡状态") == "进行中" and t1.get("打卡人") == "丙"
              and "activity.csv" in t1.get("成果文件", "") and t1.get("审核状态") == "已通过" and t1.get("主核") == "乙", t1)
        check("云端不含文件内容与令牌", "cAMP HTRF" not in json.dumps(mock.log, ensure_ascii=False) and all("token" not in json.dumps(b) for _, _, b in mock.log), "")
        r = srv.post("/api/cloud/sync", {}, token=tok["乙"])
        check("云同步：没变化的行跳过（不重复建行）", r.get("ok") and r["pushed"] == 0 and r["skipped"] == 2 and len(mock.rows) == 2, r)
        srv.post("/api/checkin", {"task_id": "T2", "state": "started", "progress": 5, "note": "先读文献"}, token=tok["丙"])
        for _ in range(60):
            if (next((v for v in mock.rows.values() if v["编号"] == "T2"), {}).get("打卡状态")) == "已开始":
                break
            time.sleep(0.1)
        check("云同步：打卡后后台自动推送（不用手动上交）", next(v for v in mock.rows.values() if v["编号"] == "T2").get("打卡状态") == "已开始", mock.rows)
        mock.down = True
        r = srv.post("/api/cloud/sync", {"force": True}, token=tok["乙"])
        s = srv.get("/api/snapshot", tok["甲"])
        check("云端连不上：报原因，本机打卡照常保留", not r.get("ok") and s["cloud"]["last_ok"] is False and s["cloud"]["last_error"] and s["team"]["checkins"]["T2"]["count"] == 1, (r, s["cloud"]))
        mock.down = False
        r = srv.post("/api/team/suggest", {"members": [{"name": "甲", "role": "lead"}, {"name": "乙", "duties": ["data"]}, {"name": "丙"}],
                                           "tasks": [{"task_id": "A", "week": 1, "duty": "data", "deliverable": "x"}, {"task_id": "B", "week": 1, "deliverable": "y", "depends_on": "A"}]}, token=srv.read_token)
        check("接口：统筹模式重分配（只给建议，不写入）", r.get("ok") and r["rows"][0]["owner"] == "乙" and r["rows"][0]["cross_reviewer"] == "甲", r)

        print("3 智能体执行（模拟模型）")
        m = srv.get("/api/models")
        check("未配真实模型时用模拟厂商并给出提示", any(p["id"] == "mock" for p in m["providers"]) and m["notes"], m["notes"])
        check("推荐：批评者与起草者异源", m["roles"]["critic"]["family"] != m["roles"]["drafter"]["family"], m["roles"])
        r = srv.post("/api/models/roles", {"roles": {"critic": {"provider": "mock", "model": "mock-a-fast"}}}, token=tok["甲"])
        pf = srv.get("/api/preflight?task_id=T2", tok["丙"])
        check("同源批评 → 运行前拦截", any("同一模型家族" in x for x in pf["blockers"]), pf)
        srv.post("/api/models/roles", {"roles": {"critic": {}}}, token=tok["甲"])
        r = srv.post("/api/runs/start", {"task_id": "T1"}, token=tok["乙"])
        check("已通过的任务不再起草", not r.get("ok"), r)
        r = srv.post("/api/runs/start", {"task_id": "T2"})
        check("未登录不能启动运行", not r.get("ok"), r)
        r = srv.post("/api/runs/start", {"task_id": "T2"}, token=tok["丙"])
        check("启动运行", r.get("ok"), r)
        run = wait_run(srv, r.get("run_id"), tok["丙"])
        check("五步执行完成", run and run["status"] == "completed", run and [(x["step"], x["state"]) for x in run["steps"]])
        ext = next(x for x in run["steps"] if x["step"] == "extract")
        check("编造的摘录被丢弃", any("疑似编造" in n for n in ext.get("notes") or []), ext)
        s = srv.get("/api/snapshot", tok["丙"])
        ai = [b for b in s["items"] if b.get("source") == "agent"]
        check("智能体碱基进入收件箱等真人", len(ai) == 3 and all(b["status"] == "open" for b in ai), len(ai))
        check("越界表述被校对拦截", any(b.get("proofread_block") for b in ai), [b.get("proofread_notes") for b in ai])
        check("碱基带已核验的文件引用", any(b.get("agent_refs") for b in ai), [b.get("agent_refs") for b in ai])
        check("碱基挂到指定主核/交叉核", all(b.get("owner") == "丙" and b.get("cross_reviewer") == "乙" for b in ai), [(b.get("owner"), b.get("cross_reviewer")) for b in ai])
        r = srv.post("/api/runs/start", {"task_id": "T2"}, token=tok["丙"])
        check("AI 碱基未配对时不能再跑（复制叉阻滞）", not r.get("ok") and "复制叉" in r.get("error", ""), r)
        for b in ai:
            v = "mismatch" if b.get("proofread_block") else "defer"
            r = srv.post("/api/pair", {"base_id": b["base_id"], "verdict": v, "note": "越界，删除" if v == "mismatch" else "等数据", "confirmed": True}, token=tok["丙"])
            check(f"真人配对 AI 碱基（{v}）", r.get("ok"), r)

        print("4 故障注入与恢复")
        (root / "data/_fault.json").write_text(json.dumps({"step": "plan", "kind": "server", "times": 1}))
        r = srv.post("/api/runs/start", {"task_id": "T2"}, token=tok["丙"])
        run = wait_run(srv, r.get("run_id"), tok["丙"])
        plan = next(x for x in run["steps"] if x["step"] == "plan")
        check("5xx 明确失败 → 自动重试后完成", run["status"] == "completed" and plan.get("attempt") == 2, (run["status"], plan))
        s = srv.get("/api/snapshot", tok["丙"])
        for b in [b for b in s["items"] if b.get("source") == "agent" and b["status"] == "open"]:
            srv.post("/api/pair", {"base_id": b["base_id"], "verdict": "defer", "note": "等数据", "confirmed": True}, token=tok["丙"])
        (root / "data/_fault.json").write_text(json.dumps({"step": "draft", "kind": "timeout", "times": 1}))
        r = srv.post("/api/runs/start", {"task_id": "T2"}, token=tok["丙"])
        rid = r.get("run_id")
        run = wait_run(srv, rid, tok["丙"])
        dr = next(x for x in run["steps"] if x["step"] == "draft")
        check("超时 = 结果未知，不自动重试", run["status"] == "unknown" and dr.get("attempt") == 1, (run["status"], dr))
        r = srv.post("/api/runs/start", {"task_id": "T2"}, token=tok["丙"])
        check("结果未知时不能开新运行", not r.get("ok") and "结果未知" in r.get("error", ""), r)
        r = srv.post("/api/runs/resume", {"run_id": rid}, token=tok["丙"])
        check("未确认不能恢复", not r.get("ok"), r)
        r = srv.post("/api/runs/ack", {"run_id": rid, "note": "已在后台确认没有生成"}, token=tok["丙"])
        check("真人确认结果未知", r.get("ok"), r)
        r = srv.post("/api/runs/resume", {"run_id": rid}, token=tok["丙"])
        run = wait_run(srv, rid, tok["丙"])
        pl = next(x for x in run["steps"] if x["step"] == "plan")
        check("恢复：已完成步骤复用、从断点继续", run["status"] == "completed" and pl.get("attempt") == 1, run["status"])
        s = srv.get("/api/snapshot", tok["丙"])
        for b in [b for b in s["items"] if b.get("source") == "agent" and b["status"] == "open"]:
            srv.post("/api/pair", {"base_id": b["base_id"], "verdict": "defer", "note": "等数据", "confirmed": True}, token=tok["丙"])
        (root / "data/_fault.json").write_text(json.dumps({"step": "critique", "kind": "crash", "times": 1}))
        r = srv.post("/api/runs/start", {"task_id": "T2"}, token=tok["丙"])
        rid = r.get("run_id")
        for _ in range(100):
            if srv.proc.poll() is not None:
                break
            time.sleep(0.1)
        check("模拟断电：服务进程在调用中途退出", srv.proc.poll() is not None)
        srv.start()
        tok = {n: srv.post("/api/auth/login", {"person_id": people[n], "pin": p})["token"] for n, p in (("甲", "lead-123456"), ("乙", "yi-123456"), ("丙", "bing-123456"))}
        run = next(x for x in srv.get("/api/runs", tok["丙"])["runs"] if x["run_id"] == rid)
        check("重启后从账本识别出“结果未知”的步骤", run["status"] == "unknown" and next(x for x in run["steps"] if x["step"] == "critique")["state"] == "requested", run["status"])
        srv.post("/api/runs/ack", {"run_id": rid, "note": "断电，已确认未计费"}, token=tok["丙"])
        r = srv.post("/api/runs/resume", {"run_id": rid}, token=tok["丙"])
        run = wait_run(srv, rid, tok["丙"])
        check("断电后恢复完成", run["status"] == "completed", run["status"])
        s = srv.get("/api/snapshot", tok["丙"])
        for b in [b for b in s["items"] if b.get("source") == "agent" and b["status"] == "open"]:
            srv.post("/api/pair", {"base_id": b["base_id"], "verdict": "defer", "note": "等数据", "confirmed": True}, token=tok["丙"])
        (root / "data/projects").glob("*")
        rd = next((root / "data/projects").glob("*/runs/" + rid))
        (rd / "plan.json").write_text("{}", encoding="utf-8")
        srv.post("/api/models/roles", {"budget": {"max_calls_per_day": 1}}, token=tok["甲"])
        r = srv.post("/api/runs/start", {"task_id": "T2"}, token=tok["丙"])
        check("预算用完 → 拒绝运行（不偷偷降级）", not r.get("ok") and "预算" in r.get("error", ""), r)
        srv.post("/api/models/roles", {"budget": {"max_calls_per_day": 200}}, token=tok["甲"])

        print("5 计划改动与周汇总")
        r = srv.post("/api/projects/update", {"tasks": [{"task_id": "T1", "week": 1, "deliverable": "整理活性数据", "acceptance": "有原始表", "owner": "乙", "cross_reviewer": "丙"},
                                                        {"task_id": "T2", "week": 1, "deliverable": "建模（改）", "acceptance": "可复现", "owner": "丙", "cross_reviewer": "乙", "depends_on": "T1"}]}, token=tok["甲"])
        pf = srv.get("/api/preflight?task_id=T2", tok["丙"])
        check("计划冻结后被改动 → 拦截", any("冻结后被改动" in x for x in pf["blockers"]), pf)
        r = srv.post("/api/plan/freeze", {"note": "T2 改交付物"}, token=tok["乙"])
        check("非组长不能重新冻结", not r.get("ok"), r)
        r = srv.post("/api/plan/freeze", {"note": "T2 改交付物"}, token=tok["甲"])
        check("组长重新冻结", r.get("ok"), r)
        r = srv.post("/api/runs/summary", {"week": 1}, token=tok["甲"])
        run = wait_run(srv, r.get("run_id"), tok["甲"])
        check("连接酶生成周汇总草稿（NOT_APPLIED）", run and run["status"] == "completed" and str(run.get("output", "")).endswith(".md"), run and run.get("status"))
        s = srv.get("/api/snapshot", tok["甲"])
        check("账本哈希链完整", s["ledger"]["ok"], s["ledger"])
        check("界面快照包含 gates / agent_runs / models / auth", all(k in s for k in ("gates", "agent_runs", "models", "auth")))
    finally:
        srv.stop()
        if not FAIL:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {len(OK)} / {len(OK) + len(FAIL)}" + (f"；失败：{FAIL}（临时目录 {tmp}）" if FAIL else ""))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
