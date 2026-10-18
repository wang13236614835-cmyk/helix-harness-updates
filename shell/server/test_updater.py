"""3.1 自动更新：签名发布 → 渠道发现 → 校验暂存 → 应用 → 确认 / 回滚；篡改与陌生签名一律拒绝。"""
import io, json, tempfile, unittest, zipfile
from pathlib import Path
import updater as U
import ed25519_lite as ED


def fake_pkg(root: Path, marker: str) -> None:
    (root / "shell/app/js").mkdir(parents=True); (root / "shell/server").mkdir(parents=True); (root / "shell/data").mkdir(parents=True)
    (root / "shell/app/index.html").write_text("<html>" + marker, encoding="utf-8")
    (root / "shell/app/js/main.js").write_text("// " + marker, encoding="utf-8")
    (root / "shell/server/serve.py").write_text("# " + marker, encoding="utf-8")
    (root / "shell/data/people.json").write_text('{"secret": "' + marker + '"}', encoding="utf-8")
    (root / "VERSION.json").write_text('{"version": "3.1", "build": "20261018"}', encoding="utf-8")


class Updater(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = Path(self.tmp.name)
        self.lead, self.member, self.share = t / "lead", t / "member", t / "share"
        fake_pkg(self.lead, "NEW"); fake_pkg(self.member, "OLD"); self.share.mkdir()
        self.saved = {k: getattr(U, k) for k in ("SHELL", "PKG", "DATA", "UPD", "TRUST")}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(U, k, v)
        self.tmp.cleanup()

    def use(self, root: Path):
        U.PKG, U.SHELL = root, root / "shell"; U.DATA = U.SHELL / "data"; U.UPD = U.DATA / "update"; U.TRUST = U.SHELL / "update-trust.json"

    def publish(self, notes="修复"):
        self.use(self.lead)
        rel = U.make_release(notes)
        U.publish_folder(str(self.share), rel["manifest"], rel["raw"])
        return rel

    def test_round_trip_folder_feed_apply_and_confirm(self):
        rel = self.publish()
        m = rel["manifest"]; self.assertTrue(ED.verify(bytes.fromhex(m["pubkey"]), U.canonical(m), bytes.fromhex(m["sig"])))
        self.assertNotIn("shell/data/people.json", U.check_package(m, rel["raw"]))  # 数据和账号永不进入更新包
        self.use(self.member); U.save_settings({"feeds": [str(self.share)]})
        r = U.check_now()
        self.assertEqual(r["staged"]["build"], m["build"]); self.assertTrue((U.UPD / "staged/payload/shell/app/index.html").is_file())
        self.assertEqual(U.trust()["pubkeys"], [m["pubkey"]])  # 首次收到即记住组长密钥
        out = U.apply_staged(self.member); self.assertTrue(out["ok"])
        self.assertIn("NEW", (self.member / "shell/app/index.html").read_text(encoding="utf-8"))
        self.assertIn("OLD", (self.member / "shell/data/people.json").read_text(encoding="utf-8"))
        self.assertEqual(U.confirm_or_rollback(self.member, True), "ok")
        self.assertIn(repr(m["build"]), (self.member / "shell/server/version.py").read_text(encoding="utf-8"))

    def test_unhealthy_start_rolls_back(self):
        self.publish(); self.use(self.member); U.save_settings({"feeds": [str(self.share)]}); U.check_now()
        U.apply_staged(self.member)
        self.assertEqual(U.confirm_or_rollback(self.member, False), "rolled_back")
        self.assertIn("OLD", (self.member / "shell/app/index.html").read_text(encoding="utf-8"))
        self.assertFalse((self.member / "shell/server/version.py").exists())  # 新增文件也被移除
        st = U.state(); self.assertTrue(st["rolled_back"]); self.assertIn(st["rolled_back"]["tag"], st["bad"])

    def test_tampered_package_rejected(self):
        rel = self.publish(); self.use(self.member)
        raw = bytearray(rel["raw"]); raw[len(raw) // 2] ^= 1
        with self.assertRaises(U.UpdateError):
            U.stage(rel["manifest"], bytes(raw))
        bad = dict(rel["manifest"], notes="改过的说明")
        with self.assertRaises(U.UpdateError):
            U.stage(bad, rel["raw"])

    def test_foreign_key_rejected_after_pinning(self):
        rel = self.publish(); self.use(self.member); U.stage(rel["manifest"], rel["raw"])
        other = U.sign_manifest(b"\x07" * 32, {k: v for k, v in rel["manifest"].items() if k not in ("sig", "pubkey")})
        with self.assertRaises(U.UpdateError) as cm:
            U.stage(other, rel["raw"])
        self.assertIn("组长", str(cm.exception))

    def test_unsafe_members_rejected(self):
        for bad in ("../x.py", "shell/data/people.json", "C:/Windows/x", "shell/server/sessions.json", "random.txt"):
            with self.assertRaises(U.UpdateError):
                U.safe_member(bad)

    def test_hand_bundle_import(self):
        rel = self.publish(); bundle = U.bundle_for_hand(rel["manifest"], rel["raw"])
        self.use(self.member); st = U.import_package(bundle); self.assertEqual(st["build"], rel["manifest"]["build"])
        with self.assertRaises(Exception):
            buf = io.BytesIO(); zipfile.ZipFile(buf, "w").writestr("x.txt", "1"); U.import_package(buf.getvalue())



class FakeCloud:
    """内存版 Notion：只实现更新渠道用到的接口。"""
    def __init__(self):
        self.pages, self.blocks, self.files, self.proxy, self._auto_proxy = {}, {}, {}, "", ""

    def _load(self):
        return {"database_id": "db1", "token": {"blob": "x"}, "title_prop": "任务"}

    def _token(self, c):
        return "tok"

    def public(self):
        return {"configured": True}

    def http(self, method, path, tok, body=None, timeout=20):
        if path == "/databases/db1/query":
            want = body["filter"]["title"]["equals"]
            return {"results": [{"id": k} for k, v in self.pages.items() if v == want]}
        if path == "/pages":
            pid = "page%d" % (len(self.pages) + 1); self.pages[pid] = body["properties"]["任务"]["title"][0]["text"]["content"]; self.blocks[pid] = []
            return {"id": pid}
        if path == "/file_uploads":
            return {"id": "up1"}
        if method == "GET" and path.startswith("/blocks/"):
            pid = path.split("/")[2]
            out = []
            for i, b in enumerate(self.blocks[pid]):
                b = json.loads(json.dumps(b)); b["id"] = f"{pid}-b{i}"
                if b["type"] == "file":
                    b["file"] = {"type": "file", "file": {"url": "https://files.example/" + b["file"]["file_upload"]["id"]}}
                if b["type"] == "code":
                    b["code"]["rich_text"] = [{"plain_text": x["text"]["content"]} for x in b["code"]["rich_text"]]
                out.append(b)
            return {"results": out, "has_more": False}
        if method == "DELETE":
            return {}
        if method == "PATCH" and path.endswith("/children"):
            self.blocks[path.split("/")[2]] = list(body["children"]); return {}
        raise AssertionError(path)


class NotionFeedTest(Updater):
    def test_notion_publish_and_fetch_round_trip(self):
        cloud = FakeCloud()
        orig_send, orig_open = U.NotionFeed._send, U._open
        U.NotionFeed._send = lambda self, tok, uid, name, raw: cloud.files.__setitem__(uid, raw)
        U._open = lambda url, timeout=30, proxy="": cloud.files[url.rsplit("/", 1)[1]]
        try:
            self.use(self.lead); rel = U.make_release("长说明" * 600)
            U.NotionFeed(cloud).publish(rel["manifest"], rel["raw"])
            U.NotionFeed(cloud).publish(rel["manifest"], rel["raw"])  # 重复发布不重复建页
            self.assertEqual(len(cloud.pages), 1)
            self.use(self.member)
            r = U.check_now(cloud, ["notion"])
            self.assertEqual(r["staged"]["build"], rel["manifest"]["build"]); self.assertEqual(r["available"]["feed"], "Notion")
        finally:
            U.NotionFeed._send, U._open = orig_send, orig_open


for _n in [n for n in vars(Updater) if n.startswith("test_")]:
    setattr(NotionFeedTest, _n, None)  # 只继承夹具，不重复跑上面的用例

if __name__ == "__main__":
    unittest.main(verbosity=2)
