// Helix 3.2.0：设置页（程序更新 + 外观 + 高级），以及更新相关命令。
import { store, esc } from "./core/store.js?v=3110";
import { views } from "./views/index.js?v=3110";

let ctx = null, loading = false;
export function initV31(c) { ctx = c; }

const when = (iso) => { if (!iso) return "—"; try { const d = new Date(iso); return `${d.getMonth() + 1}月${d.getDate()}日 ${d.toTimeString().slice(0, 5)}`; } catch { return iso; } };
const feedName = (f) => f.startsWith("github:") ? "GitHub · " + f.slice(7) : f === "notion" ? "Notion（旧渠道）" : /^https?:/i.test(f) ? "网址 " + f : "共享文件夹 " + f;

async function load(force) {
  if (loading || (!force && store.ui.upd)) return;
  loading = true;
  try { const r = await fetch("/api/update/status", { cache: "no-store" }); const j = await r.json(); if (j.ok) store.ui_({ upd: j, updErr: "" }); else store.ui_({ updErr: j.error || "读取失败" }); }
  catch (e) { store.ui_({ updErr: "读取更新状态失败：" + e.message }); }
  finally { loading = false; }
}

function updateCard(s, ui) {
  const u = ui.upd;
  if (!u) { load(); return `<div class="card upd"><h3>程序更新</h3><p class="muted">${esc(ui.updErr || "正在读取…")}</p></div>`; }
  const cur = u.current || {}, st = u.staged, av = u.available, set = u.settings || {}, busy = ui.updBusy;
  const state = st ? `<div class="upd-state new"><b>新版本 ${esc(st.version)} 已下载并校验</b><span>${esc(st.notes || "")}</span><button class="btn pri" data-cmd="up-apply" ${busy ? "disabled" : ""}>立即安装并重启</button><small>${u.settings?.auto ? "无人操作 90 秒后也会自动安装；" : ""}约 5 秒完成，登录状态会保留</small></div>`
    : av ? `<div class="upd-state new"><b>发现新版本 ${esc(av.version)}</b><span>正在下载…</span></div>`
    : `<div class="upd-state ${u.last_error ? "" : "ok"}"><b>${u.last_error ? "更新渠道暂不可用" : u.last_check ? "已是最新版本" : "尚未检查更新"}</b><span>${u.last_error ? esc(u.last_error) : "上次检查：" + esc(when(u.last_check))}</span></div>`;
  const rolled = u.rolled_back ? `<p class="warn-line">上次更新 ${esc(u.rolled_back.version || "")} 启动失败，已自动恢复到原版本。</p>` : "";
  const feeds = (set.feeds || []);
  const lead = u.can_publish ? `<details class="upd-pub" ${ui.updPubOpen ? "open" : ""}><summary>组长：发布更新给组员</summary>
      <p class="muted">把本机当前程序打包并签名，组员电脑会自动收到并安装（只更新程序，不动项目数据和账号）。</p>
      <label class="lbl">更新说明<textarea id="up-notes" rows="2" placeholder="例如：修复上传后读取不全的问题">${esc(ui.updNotes || "")}</textarea></label>
      <label class="chk"><input type="checkbox" id="up-pub-github" ${set.publish_github ? "checked" : ""}> 发布到 GitHub Releases（需要仓库写入权限）</label>
      <label class="lbl">同时放到共享文件夹（可选）<input id="up-pub-dir" value="${esc(set.publish_dir || "")}" placeholder="例如 \\\\NAS\\helix-updates 或 网盘同步目录"></label>
      <div class="row"><button class="btn pri" data-cmd="up-publish" ${busy ? "disabled" : ""}>${busy === "publish" ? "正在打包签名…" : "发布更新"}</button><a class="btn" href="/api/update/bundle" ${u.published ? "" : "hidden"}>下载离线更新包</a></div>
      ${u.published ? `<p class="muted">最近发布：${esc(u.published.version)}（${esc(u.published.build)}）${esc(u.published.notes ? " · " + u.published.notes : "")}</p>` : ""}
      ${u.signing?.has_key ? `<p class="muted mono">发布签名指纹 ${esc(u.signing.fingerprint)}</p>` : `<p class="muted">首次发布会在本机生成签名密钥；组员第一次收到更新时记住这个密钥，之后只接受同一个组长签名的更新。</p>`}
      ${ui.updMsg ? `<p class="ok-line">${esc(ui.updMsg)}</p>` : ""}</details>` : (ui.updMsg ? `<p class="ok-line">${esc(ui.updMsg)}</p>` : "");
  return `<div class="card upd"><h3>程序更新<small>当前 ${esc(cur.version || "")}${cur.build ? "（" + esc(cur.build) + "）" : ""}</small></h3>${state}${rolled}
    <label class="chk"><input type="checkbox" id="up-auto" ${set.auto ? "checked" : ""}> 自动检查并安装组长发布的更新</label>
    <p class="muted">更新来源：${feeds.map((f) => esc(feedName(f))).join("、") || "未设置"}</p>
    <div class="row"><button class="btn" data-cmd="up-check" ${busy ? "disabled" : ""}>${busy === "check" ? "正在检查…" : "检查更新"}</button><button class="btn" data-cmd="up-import" ${busy ? "disabled" : ""}>导入更新包…</button></div>
    <details class="upd-feeds" ${u.github?.connected ? "" : "open"}><summary>GitHub 连接 · ${u.github?.connected ? "已连接" : "私有仓库需先连接"}</summary>
      <p class="muted">程序更新通过 GitHub 分发，任务打卡继续同步到 Notion。私有仓库需要自己的访问权限；登录凭据加密保存在这台电脑上。</p>
      <label class="lbl">更新仓库<input id="up-gh-repo" value="${esc(u.github?.repository || "")}" placeholder="用户名/仓库名"></label>
      <label class="lbl">GitHub 访问令牌（已有连接可留空）<input id="up-gh-token" type="password" autocomplete="off" placeholder="组员需要 Contents 读取权限；发布需要写入权限"></label>
      <details><summary>网络代理（可选）</summary><input id="up-gh-proxy" value="${esc(u.github?.proxy || "")}" placeholder="例如 http://127.0.0.1:7890"></details>
      <div class="row"><button class="btn sm" data-cmd="up-gh-connect">保存并验证连接</button><button class="btn sm" data-cmd="up-gh-saved">使用本机已有 GitHub 登录</button></div>
    </details>
    <details class="upd-feeds"><summary>其他更新来源</summary><p class="muted">每行一个：<code>github:用户名/仓库名</code>、共享文件夹路径，或 http(s) 网址。</p><textarea id="up-feeds" rows="3">${esc(feeds.join("\n"))}</textarea><div class="row"><button class="btn sm" data-cmd="up-save-feeds">保存</button></div></details>
    ${lead}${ui.updErr ? `<p class="err-line">${esc(ui.updErr)}</p>` : ""}</div>`;
}

const base = views.settings;
views.settings = { ...base, render: (s, ui, prefs, reg) => {
  const me = s.auth?.me;
  return `<div class="vh"><div><h1>设置</h1><p class="lede">${me ? `当前登录：<b>${esc(me.name)}</b>（${me.role === "lead" ? "组长" : "组员"}）` : ""}</p></div><div class="acts"><button class="btn" data-cmd="auth-logout-all">退出登录</button></div></div>
  <div class="grid g2">${updateCard(s, ui)}
    <div class="card"><h3>外观</h3>
      <label class="pair" style="grid-template-columns:auto 1fr"><input type="checkbox" id="night" ${prefs.theme === "night" ? "checked" : ""}><div><b>夜间主题</b><small>只换配色，布局不变</small></div></label>
      <label class="pair" style="grid-template-columns:auto 1fr"><input type="checkbox" id="motionOn" ${prefs.motionOn ? "checked" : ""}><div><b>动画效果</b><small>关闭后页面保持静止（系统“减少动态效果”始终优先）</small></div></label>
      <details class="adv"><summary>动画样式</summary>${Object.values(reg.motion).map((p) => `<label class="pair" style="grid-template-columns:auto 1fr"><input type="radio" name="motion" value="${p.id}" ${prefs.motion === p.id ? "checked" : ""}><div><b>${esc(p.label)}</b></div></label>`).join("")}</details>
    </div>
    <div class="card"><h3>连接</h3><p>${s.mode === "live" ? "✅ 本机服务已连接，数据实时更新。" : "⚠️ 正在重连本机服务，期间页面只读。"}</p><p class="muted">断线后会自动重连；电脑休眠、后台重启或安装更新后也会自动恢复，无需重新登录（本机保持登录 30 天）。</p>
      <details class="adv"><summary>高级：数据来源</summary>${Object.values(reg.ports).map((p) => `<label class="pair" style="grid-template-columns:auto 1fr"><input type="radio" name="port" value="${p.id}" ${prefs.port === p.id ? "checked" : ""}><div><b>${esc(p.label)}</b></div></label>`).join("")}<label class="pair" style="grid-template-columns:auto 1fr"><input type="radio" name="port" value="auto" ${prefs.port === "auto" ? "checked" : ""}><div><b>自动（推荐）</b><small>连接本机服务；断开时只读显示最后一次真实数据并自动重连</small></div></label></details>
    </div></div>`;
} };

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Helix": "1" }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({ ok: false, error: "后台没有返回结果（" + r.status + "）" }));
  if (!j.ok) throw new Error(j.error || "操作未完成");
  return j;
}
async function act(kind, fn) {
  store.ui_({ updBusy: kind, updErr: "", updMsg: "" });
  try { await fn(); } catch (e) { store.ui_({ updErr: e.message }); } finally { store.ui_({ updBusy: "" }); }
}

export function v31Cmd(cmd) {
  if (cmd === "up-gh-connect" || cmd === "up-gh-saved") {
    const repository = document.getElementById("up-gh-repo").value, proxy = document.getElementById("up-gh-proxy").value;
    const input = document.getElementById("up-gh-token"), token = input.value; input.value = "";
    act("connect", async () => { const j = await post("/api/update/github", {repository, proxy, token, use_saved_login: cmd === "up-gh-saved"}); store.ui_({upd:{...store.ui.upd,...j.status},updMsg:"GitHub 仓库连接成功，可以检查更新"}); }); return true;
  }
  if (cmd === "up-check") { act("check", async () => { const j = await post("/api/update/check"); store.ui_({ upd: { ...store.ui.upd, ...j.status }, updMsg: j.staged ? "已下载新版本 " + j.staged.version : j.available ? "" : "已是最新版本" + (j.errors?.length ? "（" + j.errors.join("；") + "）" : "") }); }); return true; }
  if (cmd === "up-apply") { act("apply", async () => { const j = await post("/api/update/apply"); window.__helixNote?.(j.restarting ? "正在安装更新并重启，页面会自动恢复…" : "暂时无法自动重启，请关闭 Helix 后重新启动以完成安装", true); }); return true; }
  if (cmd === "up-import") {
    const inp = Object.assign(document.createElement("input"), { type: "file", accept: ".zip" });
    inp.onchange = () => act("import", async () => { const f = inp.files[0]; if (!f) return; const r = await fetch("/api/update/import", { method: "POST", headers: { "X-Helix": "1", "Content-Type": "application/zip" }, body: f }); const j = await r.json(); if (!j.ok) throw new Error(j.error || "导入失败"); store.ui_({ upd: { ...store.ui.upd, ...j.status }, updMsg: "更新包校验通过：" + (j.staged?.version || "") }); });
    inp.click(); return true;
  }
  if (cmd === "up-save-feeds") { act("save", async () => { const j = await post("/api/update/settings", { feeds: document.getElementById("up-feeds").value }); store.ui_({ upd: { ...store.ui.upd, ...j.status }, updMsg: "更新来源已保存" }); }); return true; }
  if (cmd === "up-publish") {
    const notes = document.getElementById("up-notes")?.value || "", dir = document.getElementById("up-pub-dir")?.value || "", github = !!document.getElementById("up-pub-github")?.checked;
    store.ui.updNotes = notes; store.ui.updPubOpen = true;
    act("publish", async () => {
      await post("/api/update/settings", { publish_dir: dir, publish_github: github });
      const j = await post("/api/update/publish", { notes });
      store.ui_({ upd: { ...store.ui.upd, ...j.status }, updNotes: "", updMsg: `已发布 ${j.manifest.version}（${j.manifest.build}）→ ${j.sent.join("、")}` + (j.errors.length ? "；未成功：" + j.errors.join("；") : "") });
    }); return true;
  }
  if (cmd === "auth-logout-all") { fetch("/api/auth/logout", { method: "POST", headers: { "X-Helix": "1", "Content-Type": "application/json" }, body: "{}" }).finally(() => { try { sessionStorage.removeItem("helix.token"); } catch {} location.replace("/login"); }); return true; }
  return false;
}

// 自动更新开关：直接保存
document.addEventListener("change", (e) => {
  if (e.target?.id === "up-auto") act("save", async () => { const j = await post("/api/update/settings", { auto: e.target.checked }); store.ui_({ upd: { ...store.ui.upd, ...j.status }, updMsg: e.target.checked ? "已开启自动更新" : "已关闭自动更新" }); });
});
// 进入设置页时刷新一次状态
addEventListener("hashchange", () => { if (location.hash === "#/settings") load(true); });
