/* Helix 3.2.0 启动守护（普通脚本，先于 main.js 执行）：
 * 1) 清掉旧版 Service Worker 缓存，避免更新后新旧文件混用；
 * 2) 所有 /api 请求自动带上登录凭据；登录失效（401）统一回到登录页；
 * 3) 每 5 秒确认后台状态：断线显示“重连中”，恢复后立即重连；程序更新后自动刷新页面。 */
(function () {
  "use strict";
  var net = window.__helixNet = { retrying: false, updating: false, fails: 0 };
  function emit() { try { window.dispatchEvent(new Event("helix:net")); } catch (e) {} }
  try {
    if ("serviceWorker" in navigator) navigator.serviceWorker.getRegistrations().then(function (rs) { rs.forEach(function (r) { r.unregister(); }); }).catch(function () {});
    if (window.caches) caches.keys().then(function (ks) { ks.forEach(function (k) { if (/^helix/.test(k)) caches.delete(k); }); }).catch(function () {});
  } catch (e) {}

  var AUTH_PAGE = /^\/(login|leader-login|register)/;
  function toLogin() {
    if (AUTH_PAGE.test(location.pathname) || window.__helixToLogin) return;
    window.__helixToLogin = 1;
    try { sessionStorage.removeItem("helix.token"); } catch (e) {}
    location.replace("/login?next=" + encodeURIComponent(location.pathname + location.hash));
  }
  window.__helixToLogin_ = toLogin;

  var raw = window.fetch.bind(window);
  function tokHeader() { var t = ""; try { t = sessionStorage.getItem("helix.token") || ""; } catch (e) {} return t ? { "X-Helix-Token": t } : {}; }
  window.fetch = function (input, init) {
    init = init || {};
    var path = "";
    try { path = new URL(typeof input === "string" ? input : input.url, location.href).pathname; } catch (e) {}
    if (path.indexOf("/api/") === 0) {
      var h = new Headers(init.headers || (typeof input !== "string" && input.headers) || {});
      var tok = ""; try { tok = sessionStorage.getItem("helix.token") || ""; } catch (e) {}
      if (tok && !h.get("X-Helix-Token")) h.set("X-Helix-Token", tok);
      var o = {}; for (var k in init) o[k] = init[k];
      o.headers = h; o.credentials = init.credentials || "same-origin";
      init = o;
    }
    return raw(input, init).then(function (r) {
      if (r.status === 401 && path.indexOf("/api/") === 0 && path.indexOf("/api/auth/") !== 0) {
        // 401 也可能是“已登录但无权代签”之类：先确认会话是否真的失效，再决定是否回登录页
        raw("/api/auth/me", { cache: "no-store", credentials: "same-origin", headers: tokHeader() }).then(function (x) { return x.json(); })
          .then(function (j) { if (j && j.ok && !j.me && !j.bootstrap) toLogin(); }).catch(function () {});
      }
      return r;
    });
  };

  function note(text, sticky) {
    var el = document.getElementById("hxNote");
    if (!el) { el = document.createElement("div"); el.id = "hxNote"; (document.body || document.documentElement).appendChild(el); }
    el.textContent = text; el.className = "on";
    clearTimeout(note.t); if (!sticky) note.t = setTimeout(function () { el.className = ""; }, 4000);
  }
  window.__helixNote = note;

  var first = null, timer = null, pendingReload = false, lastKey = 0;
  document.addEventListener("keydown", function () { lastKey = Date.now(); }, true);
  function typing() {
    var a = document.activeElement;
    return a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.value && Date.now() - lastKey < 20000;
  }
  function reloadSoon(ver) {
    if (pendingReload) return; pendingReload = true;
    net.updating = true; emit();
    note("Helix 已更新到 " + ver + "，正在刷新页面…", true);
    (function tryReload(n) {
      if (typing() && n < 30) { note("Helix 已更新到 " + ver + "，停止输入后自动刷新（或按 F5）", true); return setTimeout(function () { tryReload(n + 1); }, 2000); }
      location.reload();
    })(0);
  }
  function check() {
    clearTimeout(timer);
    raw("/api/version", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (v) {
      window.__helixVer = v.version;
      if (!first) first = v;
      var wasDown = net.retrying || net.fails > 0;
      net.fails = 0; net.retrying = false; net.updating = false;
      if (v.version !== first.version || (v.build || "") !== (first.build || "")) return reloadSoon(v.version + (v.build ? "（" + v.build + "）" : ""));
      if (wasDown || v.started !== first.started) {
        first = v;
        if (wasDown) note("已重新连上 Helix 后台");
        try { window.dispatchEvent(new Event("helix:server-back")); } catch (e) {}
      }
      emit();
    }).catch(function () {
      net.fails++;
      if (net.fails >= 1 && !net.retrying) { net.retrying = true; emit(); }
    }).then(function () {
      timer = setTimeout(check, net.fails ? Math.min(2000 * net.fails, 6000) : 5000);
    });
  }
  window.addEventListener("helix:restarting", function () { net.updating = true; emit(); note("后台正在重启或安装更新，稍候自动恢复…", true); setTimeout(check, 1500); });
  window.addEventListener("online", check);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) check(); });
  if (!AUTH_PAGE.test(location.pathname)) check();
})();
