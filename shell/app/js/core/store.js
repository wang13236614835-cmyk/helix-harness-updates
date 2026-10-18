// 极简可观察 store：snapshot（数据端口给的）+ ui（选择、视图、偏好）。视图只读 store，不直接碰端口。
const subs = new Set();
// 受限文档（Edge 跟踪防护、嵌入页等）里连“读取” sessionStorage / localStorage 都会抛异常。
// 在这里统一兜底：不可用时换成仅本页内存的替身，其他模块照常调用，不会因此判定为断线或整页崩溃。
function memStore() { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), clear: () => m.clear(), key: (i) => [...m.keys()][i] ?? null, get length() { return m.size; }, __helixMemory: true }; }
for (const name of ["sessionStorage", "localStorage"]) {
  try { const box = window[name]; box.setItem("__helix_probe__", "1"); box.removeItem("__helix_probe__"); }
  catch { try { Object.defineProperty(window, name, { value: memStore(), configurable: true }); } catch { /* 无法替换时由各处 try 兜底 */ } }
}
// Edge 在 file/受限文档上读 localStorage 会抛 SecurityError，不能因此让整页起不来。
const memory = { prefs: null };
function storage() {
  try {
    const box = localStorage;
    const probe = "__helix_probe__";
    box.setItem(probe, "1");
    box.removeItem(probe);
    return box;
  } catch {
    return null;
  }
}
function loadPrefs() {
  const box = storage();
  if (!box) return memory.prefs || {};
  try { return JSON.parse(box.getItem("helix.prefs") || "{}") || {}; }
  catch { return {}; }
}
function savePrefs(prefs) {
  const box = storage();
  if (!box) { memory.prefs = prefs; return; }
  try { box.setItem("helix.prefs", JSON.stringify(prefs)); }
  catch { memory.prefs = prefs; }
}
const prefs = loadPrefs();
// 0.3：默认收起控制台，检查器只在选中对象时出现
if (prefs.v !== 3) { prefs.dock = false; prefs.insp = true; prefs.v = 3; }
// 0.4：修复旧版本把 motion 误存为 "on"
if (!["native", "v2", "calm"].includes(prefs.motion)) delete prefs.motion;
export const store = {
  snapshot: null,
  ui: { view: "cockpit", sel: null, dockTab: "events", ...{} },
  prefs: { port: "auto", motion: "native", theme: "day", motionOn: true, side: true, insp: true, dock: true, ...prefs },
  set(patch) { if (patch.snapshot && patch.snapshot.project?.id !== this.snapshot?.project?.id) { for (const key of Object.keys(this.ui)) if (key.startsWith("research")) delete this.ui[key]; this.ui.sel = null; for(const key of ["humanRunId","humanTaskId","humanProjectId","scienceResult"]) delete this.ui[key]; } Object.assign(this, patch); emit(); },
  ui_(patch) { Object.assign(this.ui, patch); emit(); },
  pref(patch) { Object.assign(this.prefs, patch); savePrefs(this.prefs); emit(); },
  subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
};
let queued = false;
function emit() { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; subs.forEach((f) => f(store)); }); }
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const short = (h, n = 10) => (h ? String(h).slice(0, n) + "…" : "—");
