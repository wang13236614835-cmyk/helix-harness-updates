// 上传队列与「登记本机大文件」。
// 浏览器先算 SHA-256，随上传一起发给服务器；服务器边收边算，不一致就丢弃并报错。
// 超过 64 MB 的文件不走上传（服务器会拒收），改为登记本机路径（不复制）。
// 纯逻辑（makeItems / runQueue / summarize …）不碰 DOM，可以在 node 里直接测试。

export const MAX_UPLOAD = 64 * 1024 * 1024;

// Missing capability flags mean legacy backend: never offer an unsupported action.
export function uploadCapabilities(s = {}) {
  const live = !["offline", "demo", "file"].includes(s.mode) && !!s.mode;
  return {
    register: live && s.capabilities?.files_register_in_place === true && !!(s.auth?.me?.role === "lead" || s.auth?.bootstrap),
    sha256: s.capabilities?.files_sha256 === true,
  };
}

export function fmtBytes(n) {
  n = Number(n) || 0;
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + " GB";
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  return Math.max(1, Math.round(n / 1024)) + " KB";
}

export async function sha256Hex(buf) {
  // 非安全上下文（例如用局域网 IP 访问云端版）没有 crypto.subtle：返回空串，上传照常进行但不做浏览器侧校验。
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (!subtle) return "";
  const d = await subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, "0")).join("");
}

let seq = 0;
export function makeItems(files, max = MAX_UPLOAD) {
  return Array.from(files || [], (file) => {
    const big = file.size > max, empty = file.size === 0;
    return {
      id: ++seq, file, name: file.name, bytes: file.size,
      state: big ? "too_big" : empty ? "failed" : "queued",
      permanent: big || empty,
      pct: 0, sha: "", verified: false, duplicate: false, registered: false, row: null,
      error: big ? "超过 64 MB 上传上限，请改用「登记本机大文件」" : empty ? "文件是空的" : "",
    };
  });
}

export function summarize(items) {
  const c = { total: items.length, queued: 0, running: 0, done: 0, failed: 0, too_big: 0, cancelled: 0 };
  for (const it of items) {
    if (it.state === "hashing" || it.state === "uploading") c.running++;
    else if (it.state in c) c[it.state]++;
  }
  return c;
}

export function requeueable(items) {
  return items.some((i) => !i.permanent && (i.state === "failed" || i.state === "cancelled"));
}

export function requeue(items) {
  let n = 0;
  for (const it of items) {
    if (!it.permanent && (it.state === "failed" || it.state === "cancelled")) { it.state = "queued"; it.error = ""; it.pct = 0; n++; }
  }
  return n;
}

export function abortError() { const e = new Error("已取消"); e.name = "AbortError"; return e; }

// 严格按顺序逐个发送；一个失败不影响后面的；取消后剩余的标为 cancelled。
export async function runQueue(items, { send, onChange = () => {}, cancelled = () => false }) {
  for (const it of items) {
    if (it.state !== "queued") continue;
    if (cancelled()) { it.state = "cancelled"; onChange(it); continue; }
    try {
      await send(it);
      it.state = "done";
    } catch (e) {
      it.state = e && e.name === "AbortError" ? "cancelled" : "failed";
      it.error = (e && e.message) || String(e);
    }
    onChange(it);
  }
}

export function describe(it) {
  const sz = fmtBytes(it.bytes);
  switch (it.state) {
    case "queued": return `${sz} · 等待`;
    case "hashing": return `${sz} · 正在计算校验码…`;
    case "uploading": return `${sz} · 上传中 ${it.pct}%`;
    case "done":
      if (it.registered) return `${sz} · 已原地登记（未复制）`;
      return `${sz} · ${it.duplicate ? "已存在，内容一致" : "已添加"}${it.verified ? " · 已校验一致 " + it.sha.slice(0, 12) + "…" : it.sha ? " · 已计算本机校验码，服务器未确认校验" : " · 未校验（此页面不能计算校验码）"}`;
    case "failed": return `${sz} · 失败：${it.error}`;
    case "too_big": return `${sz} · ${it.error}`;
    case "cancelled": return `${sz} · 已取消`;
    default: return sz;
  }
}

export function xhrPost(url, body, headers, onProgress, hold) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", url);
    for (const [k, v] of Object.entries(headers)) x.setRequestHeader(k, v);
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let j;
      try { j = JSON.parse(x.responseText); } catch { j = { ok: false, error: "服务没有返回 JSON（" + x.status + "）" }; }
      resolve(j);
    };
    x.onerror = () => reject(new Error("网络中断，服务没有收到完整文件"));
    x.ontimeout = () => reject(new Error("等待服务响应超时"));
    x.onabort = () => reject(abortError());
    hold(x);
    x.send(body);
  });
}

// ---------------------------------------------------------------- 界面与控制（仅浏览器）
let H = { api: async () => { throw new Error("上传模块未初始化"); }, refresh: async () => {}, token: () => "", who: () => "", toast: () => {}, openLogin: () => {}, snapshot: () => ({}), canWrite: () => false };
export function initUploads(h) { H = { ...H, ...h }; }

let items = [], busy = false, stop = false, curXhr = null, panel = null, paintAt = 0, timer = 0;
const ICON = { queued: "·", hashing: "…", uploading: "↑", done: "✓", failed: "✗", too_big: "!", cancelled: "—" };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function paint(force) {
  const now = Date.now();
  if (!force && now - paintAt < 120) { if (!timer) timer = setTimeout(() => { timer = 0; paint(true); }, 130); return; }
  paintAt = now; render();
}

function closePanel() { if (busy) return; panel?.remove(); panel = null; items = []; }

function render() {
  if (!panel) {
    panel = el("div"); panel.id = "hx-uploads";
    panel.setAttribute("role", "status"); panel.setAttribute("aria-live", "polite");
    document.body.appendChild(panel);
  }
  const c = summarize(items);
  const finished = c.done + c.failed + c.too_big + c.cancelled;
  const cur = items.find((i) => i.state === "uploading");
  panel.replaceChildren();
  const head = el("header");
  head.append(el("b", "", busy
    ? `正在上传 ${Math.min(finished + 1, c.total)}/${c.total}`
    : `上传结束：成功 ${c.done}，失败 ${c.failed + c.cancelled}，超过上限 ${c.too_big}`));
  if (busy) {
    const b = el("button", "btn sm", stop ? "正在取消…" : "取消"); b.disabled = stop; b.onclick = cancel; head.append(b);
  } else {
    if (requeueable(items)) { const b = el("button", "btn sm", "重试失败项"); b.onclick = retry; head.append(b); }
    const x = el("button", "btn sm", "关闭"); x.onclick = closePanel; head.append(x);
  }
  panel.append(head);
  const bar = el("div", "hxu-bar"), fill = el("i");
  fill.style.width = (c.total ? Math.round(100 * (finished + (cur ? cur.pct / 100 : 0)) / c.total) : 0) + "%";
  bar.append(fill); panel.append(bar);
  const ul = el("ul");
  for (const it of items) {
    const li = el("li", "st-" + it.state);
    li.append(el("span", "hxu-ic", ICON[it.state] || "·"));
    const mid = el("div", "hxu-mid");
    mid.append(el("div", "hxu-nm", it.name), el("div", "hxu-sub", describe(it)));
    li.append(mid);
    const act = el("div", "hxu-act");
    if (it.state === "too_big" && uploadCapabilities(H.snapshot()).register && H.canWrite()) {
      const b = el("button", "btn sm", "登记本机文件…");
      b.onclick = () => openRegister(it.name, (row) => { it.state = "done"; it.registered = true; it.row = row; it.error = ""; paint(true); });
      act.append(b);
    }
    li.append(act); ul.append(li);
  }
  panel.append(ul);
}

async function send(it) {
  if (!H.canWrite()) throw new Error("连接断开或当前数据源只读，上传已暂停");
  it.state = "hashing"; paint(true);
  const buf = await it.file.arrayBuffer();
  it.sha = await sha256Hex(buf);
  if (stop) throw abortError();
  if (!H.canWrite()) throw new Error("连接断开或当前数据源只读，上传已暂停");
  it.state = "uploading"; it.pct = 0; paint(true);
  const url = `/api/files?name=${encodeURIComponent(it.name)}&by=${encodeURIComponent(H.who() || "")}${it.sha ? "&sha256=" + it.sha : ""}`;
  const j = await xhrPost(url, buf, { "X-Helix": "1", "X-Helix-Token": H.token() }, (p) => { it.pct = Math.round(p * 100); paint(false); }, (x) => { curXhr = x; });
  curXhr = null;
  if (!j.ok) {
    if (j.auth && /登录/.test(j.error || "")) setTimeout(() => H.openLogin(), 50);
    throw new Error(j.error || "上传失败");
  }
  it.row = j.file; it.duplicate = !!(j.file && j.file.duplicate); it.verified = j.verified === true;
}

async function drain() {
  busy = true; stop = false; paint(true);
  await runQueue(items, { send, onChange: () => paint(true), cancelled: () => stop });
  busy = false; stop = false; curXhr = null; paint(true);
  await Promise.resolve(H.refresh()).catch(() => {});
  // 结果汇总已显示在面板标题里（aria-live 会播报），不再重复弹提示条，避免遮住面板。
}

function cancel() { stop = true; if (curXhr) curXhr.abort(); paint(true); }

async function retry() {
  if (busy || !requeue(items)) return;
  await drain();
}

export async function uploadFiles(fileList, after) {
  if (!H.canWrite()) { H.toast("连接断开或当前数据源只读，上传已暂停"); return []; }
  const fresh = makeItems(fileList);
  if (!fresh.length) return [];
  if (busy) { H.toast("上一批还在上传，请等它结束"); return []; }
  if (!uploadCapabilities(H.snapshot()).register) for (const it of fresh) {
    if (it.state === "too_big") it.error = "超过 64 MB 上传上限；当前服务或账号不支持本机登记，请拆分文件后上传";
  }
  items = fresh;
  await drain();
  const rows = items.filter((i) => i.state === "done" && i.row).map((i) => i.row);
  if (after) after(rows);
  return rows;
}

let regOnKey = null;
function closeRegister() {
  document.getElementById("hx-register")?.remove();
  if (regOnKey) { document.removeEventListener("keydown", regOnKey); regOnKey = null; }
}

export function openRegister(hint = "", onDone) {
  if (!uploadCapabilities(H.snapshot()).register || !H.canWrite()) { H.toast("当前服务或账号不支持登记本机文件"); return; }
  closeRegister();
  const back = el("div", "hxm"); back.id = "hx-register";
  const box = el("div", "hxm-box"); back.append(box);
  box.append(el("h3", "", "登记本机大文件"));
  box.append(el("p", "sub", "不复制文件，只登记它在这台电脑上的完整路径、大小、修改时间和校验码，适合超过 64 MB 的大文件（仅管理员可用）。之后文件若被移动或修改，Helix 会拒绝提供它，以免内容与校验码对不上。"));
  if (hint) box.append(el("p", "sub", `要登记的文件：${hint}。浏览器看不到本机路径，请把它的完整路径粘贴到下面。`));
  const path = el("input"); path.type = "text"; path.placeholder = "例如 D:\\资料\\round06_complete.zip"; path.setAttribute("aria-label", "文件完整路径"); path.style.width = "100%";
  const note = el("input"); note.type = "text"; note.placeholder = "备注（可选）"; note.maxLength = 200; note.setAttribute("aria-label", "备注"); note.style.width = "100%"; note.style.marginTop = "8px";
  const msg = el("p", "sub"); msg.setAttribute("role", "status");
  const row = el("div"); row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:12px";
  const ok = el("button", "btn pri", "登记"), no = el("button", "btn", "取消");
  no.onclick = closeRegister;
  ok.onclick = async () => {
    if (!uploadCapabilities(H.snapshot()).register || !H.canWrite()) { msg.textContent = "连接或权限已变化，请关闭后重试"; return; }
    if (!path.value.trim()) { msg.textContent = "请先填写完整路径"; path.focus(); return; }
    ok.disabled = true; msg.textContent = "正在读取文件并计算校验码，大文件可能需要几十秒，请勿关闭…";
    try {
      const j = await H.api("/api/files/register", { path: path.value, note: note.value });
      H.toast(j.file.duplicate ? "已存在相同内容的文件" : "已登记（未复制）");
      await Promise.resolve(H.refresh()).catch(() => {});
      if (onDone) onDone(j.file);
      closeRegister();
    } catch (e) { msg.textContent = "没有登记：" + (e.message || e); ok.disabled = false; }
  };
  row.append(no, ok);
  box.append(path, note, msg, row);
  back.addEventListener("mousedown", (e) => { if (e.target === back) closeRegister(); });
  regOnKey = (e) => { if (e.key === "Escape") closeRegister(); };
  document.addEventListener("keydown", regOnKey);
  document.body.appendChild(back);
  setTimeout(() => path.focus(), 0);
}