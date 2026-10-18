"""3.1：前置登录（未登录读不到数据）、会话跨重启保持、Cookie 登录、SSE 心跳与失效通知。"""
import json, socket, time, unittest, urllib.error, urllib.request
import test_source_project as fixture


def raw_get(port, path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        r = op.open(req, timeout=10); return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class SessionGate(unittest.TestCase):
    def setUp(self):
        self.f = fixture.SourceProjectAPI(); self.f.setUp(); self.s = self.f.s; self.token = self.f.token

    def tearDown(self):
        self.f.tearDown()

    def test_gate_blocks_anonymous_reads(self):
        p = self.s.port
        self.assertEqual(raw_get(p, "/api/snapshot")[0], 401)
        self.assertEqual(raw_get(p, "/api/update/status")[0], 401)
        code, h, _ = raw_get(p, "/")
        self.assertIn(code, (302, 303)); self.assertTrue(h.get("Location", "").endswith("login"))
        for public in ("/api/health", "/api/version", "/login", "/leader-login"):
            self.assertEqual(raw_get(p, public)[0], 200, public)
        self.assertEqual(raw_get(p, "/api/snapshot", {"X-Helix-Token": self.token})[0], 200)

    def test_login_sets_cookie_and_cookie_alone_works(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.s.port}/api/auth/login", method="POST",
                                     data=json.dumps({"username": self.f.lead_name if hasattr(self.f, "lead_name") else "owner", "pin": "owner-password-123", "portal": "lead"}).encode(),
                                     headers={"X-Helix": "1", "Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=10)
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("helix_sid=", cookie); self.assertIn("HttpOnly", cookie); self.assertIn("Max-Age=", cookie)
        sid = cookie.split(";")[0]
        self.assertEqual(raw_get(self.s.port, "/api/snapshot", {"Cookie": sid})[0], 200)

    def test_session_survives_server_restart(self):
        self.s.stop(); self.s.start()
        self.assertEqual(raw_get(self.s.port, "/api/snapshot", {"X-Helix-Token": self.token})[0], 200)
        me = json.loads(raw_get(self.s.port, "/api/auth/me", {"X-Helix-Token": self.token})[2])
        self.assertTrue(me["me"])

    def test_logout_revokes(self):
        self.s.post("/api/auth/logout", {}, self.token)
        self.assertEqual(raw_get(self.s.port, "/api/snapshot", {"X-Helix-Token": self.token})[0], 401)

    def test_sse_hello_and_auth_expired(self):
        sock = socket.create_connection(("127.0.0.1", self.s.port), timeout=25)
        sock.sendall(f"GET /api/events?interval=0.5 HTTP/1.1\r\nHost: x\r\nX-Helix-Token: {self.token}\r\n\r\n".encode())
        buf = b""
        while b"RUN_STARTED" not in buf:
            buf += sock.recv(4096)
        self.assertIn(b"retry: 3000", buf)
        self.s.post("/api/auth/logout", {}, self.token)
        end = time.time() + 20
        while b"AUTH_EXPIRED" not in buf and time.time() < end:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        sock.close()
        self.assertIn(b"AUTH_EXPIRED", buf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
