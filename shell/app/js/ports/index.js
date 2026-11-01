// 数据端口（Data Port）：壳与数据之间唯一的接缝。

// 契约：{ id, label, connect(onSnapshot, onEvent) -> Promise<{ close() }> }，snapshot 必须是 helix.snapshot.v1。

// 换数据 = 换端口；视图与动效不需要改。

import { bus } from "../core/bus.js?v=3301";



export const SHELL_VERSION = "helix-shell 3.3.1";

// P0：最后一次真实快照按项目存在本机；断线时只读显示它，绝不换成演示数据

const SNAP_KEY = "helix.lastSnapshot.v1";

function remember(s) { if (!s || s.mode === "demo" || s.mode === "file" || s.mode === "offline") return; try { localStorage.setItem(SNAP_KEY, JSON.stringify({ at: new Date().toISOString(), project: s.project?.id || "", snap: s })); } catch { /* 太大或被禁用：断线时只显示空状态 */ } }

function recall() { try { return JSON.parse(localStorage.getItem(SNAP_KEY) || "null"); } catch { return null; } }

export function offlineSnapshot(err) {

  const r = recall(), base = r?.snap || { schema: "helix.snapshot.v1", items: [], projects: [], sprint: { weeks: [], pairs: [] }, metrics: {} };

  return { ...base, mode: "offline", offline: { since: new Date().toISOString(), data_at: r?.at || null, has_data: !!r?.snap, error: String(err?.message || err || "") },

    harness: { ...(base.harness || {}), writable: false, seedable: false, can_takeover: false, why: "连接断开，暂停写入" },

    ledger: { ...(base.ledger || {}), ok: null }, port: { kind: "连接断开" } };

}

// P0-1 补齐：统一写入闸门。只有“本机服务已连上且已拿到最新快照”时才放行写请求；

// 断线、重连等待、演示、文件快照期间，任何 /api 写请求都不会发到网络。

export const gate = { live: false };

const WRITE_BLOCKED = "连接断开或处于演示 / 快照模式，写入已暂停；恢复连接后再试";

if (typeof window !== "undefined" && window.fetch && !window.fetch.__helixGuard) {

  const raw = window.fetch.bind(window);

  const guarded = (input, init = {}) => {

    const method = String(init.method || (input && input.method) || "GET").toUpperCase();

    let path = ""; try { path = new URL(typeof input === "string" ? input : input.url, location.href).pathname; } catch { /* 交给原 fetch */ }

    if (method !== "GET" && method !== "HEAD" && path.startsWith("/api/") && !gate.live) {

      return Promise.resolve(new Response(JSON.stringify({ ok: false, error: WRITE_BLOCKED, offline: true }), { status: 503, headers: { "Content-Type": "application/json" } }));

    }

    return raw(input, init);

  };

  guarded.__helixGuard = true; window.fetch = guarded;

}

export const WRITE_CMD = /^(?:(?:hx-(?:pair|cross|seed|takeover|upload|register|switch|checkpoint|wizard|wizard-edit)|wz-(?:next|seed|files|import)|wl-new|person-(?:new|save|del|toggle|claim)|auth-(?:login|do-login|setpin|do-setpin))(?::|$)|ag-|md-|lt-|project-import)/;

export function toLogin() { if (/^\/(login|leader-login|register)/.test(location.pathname)) return; location.replace("/login?next=" + encodeURIComponent(location.pathname + location.hash)); }
async function getJSON(url) { const r = await fetch(url, { cache: "no-store", credentials: "same-origin", headers: { "X-Helix-Token": sessionStorage.getItem("helix.token") || "" } }); if (r.status === 401 && url.startsWith("/api/")) { toLogin(); throw new Error("登录已过期，请重新登录"); } if (!r.ok) throw new Error(`${url} → ${r.status}`); return r.json(); }



export const ports = {

  http: {

    id: "http", label: "本机服务 · 实时（serve.py / helixd）",

    async connect(onSnap, onLost = () => {}) {

      const health = await getJSON("/api/health");

      const mismatch = health.version && health.version !== SHELL_VERSION ? health.version : "";

      if (mismatch) bus.emit("RUN_ERROR", { version: "页面 " + SHELL_VERSION + " ≠ 后台 " + mismatch }, "warn");

      const load = async (why) => { const s = await getJSON("/api/snapshot"); if (mismatch) s.version_mismatch = { page: SHELL_VERSION, server: mismatch }; remember(s); gate.live = true; bus.emit("STATE_SNAPSHOT", { why, mode: s.mode, items: (s.items || []).length }); onSnap(s); };

      await load("connect");

      let es = null;

      if (window.EventSource) {

        es = new EventSource("/api/events?interval=2");

        es.addEventListener("RUN_STARTED", () => bus.emit("RUN_STARTED", { stream: "/api/events" }));

        es.addEventListener("STATE_SNAPSHOT", (e) => { bus.emit("STATE_DELTA", JSON.parse(e.data)); load("ledger-changed").catch(onLost); });

        let broken = false, quiet = null;
        const beat = () => { clearTimeout(quiet); quiet = setTimeout(() => { if (es && es.readyState !== 2) { es.close(); onLost(new Error("实时推送 45 秒无响应")); } }, 45000); };
        beat(); ["HEARTBEAT", "RUN_STARTED", "STATE_SNAPSHOT"].forEach((t) => es.addEventListener(t, beat));
        es.addEventListener("AUTH_EXPIRED", () => { es.close(); toLogin(); });
        es.addEventListener("RESTARTING", (e) => { let d = {}; try { d = JSON.parse(e.data); } catch {} window.dispatchEvent(new CustomEvent("helix:restarting", { detail: d })); });
        es.onopen = () => { beat(); if (broken) { broken = false; load("reconnected").catch(onLost); } };
        es.onerror = () => { broken = true; bus.emit("RUN_ERROR", { stream: "实时推送中断，正在检查后台" }, "warn");
          if (es.readyState === 2) { clearTimeout(quiet); getJSON("/api/health").then(() => onLost(new Error("实时推送已关闭"))).catch(onLost); return; }
          getJSON("/api/health").catch((err) => { es.close(); clearTimeout(quiet); onLost(err); }); };
        const _close = es.close.bind(es); es.close = () => { clearTimeout(quiet); _close(); };

      }

      bus.emit("CUSTOM", { name: "port.connected", port: "http", mode: health.mode, workbench: health.workbench_app || health.error });

      return { close() { es && es.close(); }, health };

    },

  },

  demo: {

    id: "demo", label: "演示快照（假数据，只供体验）",

    async connect(onSnap) {

      const s = await getJSON("data/demo.snapshot.json"); s.mode = "demo"; onSnap(s);

      bus.emit("CUSTOM", { name: "port.connected", port: "demo" });

      return { close() {} };

    },

  },

  file: {

    id: "file", label: "导入快照文件（.json）",

    connect(onSnap) {

      return new Promise((resolve, reject) => {

        const inp = Object.assign(document.createElement("input"), { type: "file", accept: ".json,application/json" });

        inp.onchange = async () => {

          try { const s = JSON.parse(await inp.files[0].text()); if (!String(s.schema || "").startsWith("helix.snapshot")) throw new Error("不是 helix.snapshot 文件"); s.mode = "file"; onSnap(s); bus.emit("CUSTOM", { name: "port.connected", port: "file", file: inp.files[0].name }); resolve({ close() {} }); }

          catch (e) { reject(e); }

        };

        inp.click();

      });

    },

  },

};



// 自动 / 本机服务：只连本机服务。断开 → 只读显示最后一次真实快照，按 1s、2s、4s…（最长 30s）自动重连。

// 演示只能由用户在设置里显式选择。

export async function connectPort(pref, onSnap) {

  gate.live = false;

  if (pref !== "auto" && pref !== "http") {

    try { const h = await ports[pref].connect(onSnap); return { id: pref, ...h }; }

    catch (e) { bus.emit("RUN_ERROR", { port: pref, error: String(e.message || e) }, "warn"); throw e; }

  }

  let inner = null, timer = null, closed = false, offline = false, delay = 1000, busy = false;

  const handle = { id: "http", close() { closed = true; clearTimeout(timer); inner?.close?.(); }, retryNow() { if (offline) { delay = 1000; clearTimeout(timer); attempt(); } } };

  const lost = (err) => {

    if (closed) return;

    gate.live = false; inner?.close?.(); inner = null; handle.id = "offline";

    bus.emit("RUN_ERROR", { port: "http", error: String(err?.message || err), retry_in_ms: delay }, "warn");

    if (!offline) { offline = true; onSnap(offlineSnapshot(err)); }

    clearTimeout(timer); timer = setTimeout(attempt, delay); delay = Math.min(delay * 2, 8000);

  };

  async function attempt() {

    if (closed || busy) return; busy = true;

    try { inner = await ports.http.connect(onSnap, lost); handle.id = "http"; handle.health = inner.health; if (offline) bus.emit("CUSTOM", { name: "port.reconnected" }); offline = false; delay = 1000; }

    catch (e) { lost(e); }

    finally { busy = false; }

  }

  await attempt();
  // 3.2.0：切回窗口、网络恢复、后台重启（boot.js 检测到）时立即重连，不必等退避计时
  const kick = () => { if (offline) handle.retryNow(); };
  window.addEventListener("online", kick); window.addEventListener("helix:server-back", kick);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) kick(); });
  return handle;

}

