import { store, esc, short } from "./core/store.js?v=3301";

import { bus } from "./core/bus.js?v=3301";

import { ports, connectPort, gate, WRITE_CMD } from "./ports/index.js?v=3301";

import { motionPacks, mountMotion } from "./motion/index.js?v=3301";

import { views, ORDER, ICONS, weekBand } from "./views/index.js?v=3301";

import { inspector } from "./views/inspector.js?v=3301";

import { side } from "./views/side.js?v=3301";

import { members, roster, workload, MEMBER_COLORS, av, portLabel, UI_EV, ceilLabel, projectStatusLabel } from "./views/common.js?v=3301";

import { initHarness, harnessCmd, openWizard, isWizardOpen, wizardKey, pairKey, api, refresh } from "./harness.js?v=3301";

import { initAgents, agentCmd, titleAuth, closeModal } from "./agents.js?v=3301";

import { initScience } from "./science.js?v=3301";

import { teamView, initLocalTeam, localTeamCmd } from "./local_team.js?v=3301";
import {initProjectImport,projectImportCmd} from "./project_import.js?v=3301";
import { initV31, v31Cmd } from "./v31.js?v=3301";
import { initHelixRemote, helixRemoteCmd } from "./helix_remote.js?v=3301";
import { uploadCapabilities } from "./uploads.js?v=3301";
import { initRelay, relayCmd } from "./relay.js?v=3301";
views.team={title:"我的分工与学习",icon:"people",render:teamView};
initScience();



const $ = (q, r = document) => r.querySelector(q);

const shell = $(".shell");

let motion = null, conn = null, lastView = null, lastHelixKey = "";



function applyPrefs() {

  const p = store.prefs;

  shell.classList.toggle("no-side", !p.side); shell.classList.toggle("no-insp", !p.insp || !store.ui.sel); shell.classList.toggle("no-dock", !p.dock);

  document.documentElement.dataset.theme = p.theme; document.documentElement.dataset.motion = p.motionOn ? "on" : "off";

}

// 左侧导航顺序 = 数字快捷键顺序：首页 → 任务 → 审核 → 文件 → 资料 → 工具 → 成员
const navIds = (s) => [...(s?.local_team ? ["team"] : []), "cockpit", "runs", "inbox", "data", "research", "tools", "people"];
const NAV_LB = { team: "分工", cockpit: "首页", runs: "任务", data: "文件", research: "资料", tools: "工具", people: "成员", inbox: "审核", settings: "设置" };
function activity() {

  const s = store.snapshot;

  $(".act").innerHTML = navIds(s).map((id) => { const v = views[id]; const n = s && v.badge ? v.badge(s) : 0; return `<button data-view="${id}" data-tip="${v.title}" aria-label="${v.title}" class="${store.ui.view === id ? "on" : ""}">${ICONS[v.icon]}<span class="lb">${NAV_LB[id] || v.title}</span>${n ? `<span class="n">${n}</span>` : ""}</button>`; }).join("") +

    `<div class="sp"></div><button data-view="settings" data-tip="设置" aria-label="设置" class="${store.ui.view === "settings" ? "on" : ""}">${ICONS.settings}<span class="lb">设置</span></button>`;

}

function lights() {
  const s = store.snapshot, m = s?.mode, net = window.__helixNet || {};
  const [cls, txt] = net.updating ? ["upd", "正在更新…"] : !m ? ["bad", "正在连接…"] : m === "offline" ? ["bad", "连接断开 · 重连中"] : m === "demo" ? ["warn", "演示数据"] : m === "file" ? ["warn", "快照只读"] : net.retrying ? ["warn", "重连中…"] : ["ok", "已连接"];
  const c = s?.cloud || {}, pend = (c.pending || []).length;
  const sync = c.enabled ? `<span class="sync ${c.last_ok === false ? "bad" : pend ? "warn" : "ok"}" data-cmd="ck-cloud" title="${esc(c.last_error ? "上次同步失败：" + c.last_error : c.last_sync ? "上次同步 " + hhmm(c.last_sync) : "云端团队协同同步")}">${c.last_ok === false ? "同步失败 · 将自动重试" : pend ? `待同步 ${pend}` : "团队已同步"}</span>` : "";
  $(".lights").innerHTML = `<span class="conn ${cls}" data-view="settings" title="${cls === "ok" ? "本机服务已连接，数据实时更新" : "正在自动重连，恢复后自动刷新；期间只读"}"><i></i>${txt}</span>${sync}`;
}
function statusbar() {
  const s = store.snapshot, sp = s?.sprint || {}, d = sp.days_left;
  const left = d == null ? "" : d === 0 ? "今天截止" : d < 0 ? "本周期已结束" : `剩 ${d} 天`;
  const live = !!s && !["offline", "demo", "file"].includes(s.mode);
  $(".status").innerHTML = `<span><i style="--c:${live ? "var(--ok-dot)" : "var(--warn-dot)"}"></i>${esc(s ? (s.mode === "offline" ? "连接断开 · 自动重连中" : live ? "本机服务已连接" : portLabel(s.port?.kind)) : "正在连接…")}</span>${sp.current_week ? `<span>第 ${sp.current_week} 周${left ? " · " + left : ""}</span>` : ""}<span class="sp"></span>${s?.harness && !s.harness.writable && s.mode !== "live" ? "<span>只读</span>" : ""}<span>Helix ${esc(window.__helixVer || "3.3.1")}</span><span class="kbd" data-cmd="palette">Ctrl K 命令面板</span>`;
}
function dock() {

  const tab = store.ui.dockTab;

  $(".dock nav").innerHTML = [["events", "事件流"], ["trace", "回合记录"], ["guard", "护栏红线"]].map(([k, t]) => `<button data-dock="${k}" class="${tab === k ? "on" : ""}">${t}</button>`).join("") + `<span class="sp"></span><button data-cmd="dock">收起</button>`;

  const C = { RUN_STARTED: "#6ee7b7", STATE_SNAPSHOT: "#7dd3fc", STATE_DELTA: "#fcd34d", RUN_ERROR: "#fca5a5", CUSTOM: "#c4b5fd" };

  const log = $(".dock .log");

  if (tab === "events") log.innerHTML = bus.log.slice(-120).map((e) => `<div class="ev" style="--c:${C[e.type] || "#cfe8e1"}"><span class="t">${e.ts.toTimeString().slice(0, 8)}</span><span class="k" title="${esc(e.type)}">${esc(UI_EV[e.type] || e.type)}</span><span>${esc(JSON.stringify(e.data))}</span></div>`).join("") || `<div class="ev"><span></span><span class="k">—</span><span>等待事件…</span></div>`;

  else if (tab === "trace") log.innerHTML = `<div class="ev"><span class="t">—</span><span class="k">回合</span><span>${store.snapshot?.metrics?.turns ?? 0} 个回合 · 阻滞 ${store.snapshot?.metrics?.blocked_turns ?? 0} · 结果未知 ${store.snapshot?.metrics?.outcome_unknown ?? 0}（接入模型后，这里显示每一步的输入、输出、词元用量和费用）</span></div>`;

  else log.innerHTML = (store.snapshot?.sprint?.red_lines || []).map((r) => `<div class="ev" style="--c:#fca5a5"><span class="t">红线</span><span class="k">机器校对</span><span>${esc(r)}</span></div>`).join("");

  log.scrollTop = log.scrollHeight;

}

function mountStage(force) {

  const el = $("#stage"); const s = store.snapshot; if (!el || !s || !s.helix) return; // 断线且无缓存时没有螺旋数据，不挂动效

  const key = JSON.stringify(s.helix);

  if (!motion || force || !el.contains(document.activeElement) && !el.firstChild) {

    motion?.destroy(); motion = mountMotion(store.prefs.motion, el, s.helix, { onSelect: (id) => select("base:" + id), paused: () => !store.prefs.motionOn });

    const tools = document.createElement("div"); tools.className = "stage-tools"; tools.innerHTML = `<div class="seg">${Object.values(motionPacks).map((p) => `<button data-motion="${p.id}" class="${p.id === store.prefs.motion ? "on" : ""}" title="${esc(p.label)}">${{ native: "原生", v2: "经典", calm: "静态" }[p.id]}</button>`).join("")}</div>`; el.appendChild(tools);

    lastHelixKey = key;

  } else if (key !== lastHelixKey) { motion.update(s.helix); lastHelixKey = key; }

  if (store.ui.sel?.startsWith("base:")) motion.focus?.(store.ui.sel.slice(5));

}

function titlebar() {

  const s = store.snapshot, p = s?.project || {};

  $(".title .proj").innerHTML = s ? `${esc(p.name || p.target || "未命名项目")}<span class="chev"></span>` : "…";

  const mem = members(s), me = store.prefs.me || "";

  if (!$(".title .ov")) { const b = Object.assign(document.createElement("button"), { className: "ov", textContent: "本轮概览" }); b.dataset.cmd = "welcome"; $(".title .me").after(b); }

  $(".title .me").innerHTML = s ? titleAuth(s) : "";

}

function syncForm() {

  const ed = store.ui.edit, n = $("#pf-name"); if (!ed || !n) return;

  Object.assign(ed, { name: n.value, role: $("#pf-role").value, color: $("#pf-color").value, note: $("#pf-note").value });

}

// P0：断线 / 演示 / 版本不一致时，页面顶部固定提示，不让人把旧数据或假数据当成真实状态

function hhmm(iso) { try { const d = new Date(iso); return `${d.getMonth() + 1}月${d.getDate()}日 ${d.toTimeString().slice(0, 5)}`; } catch { return "—"; } }

function banner(s) {

  if (!s) return "";

  const box = (tone, html) => `<div class="lock banner ${tone}" role="status">${html}</div>`;

  let out = "";
  if(s.generic_mode&&!s.plan)out+=box('info','<b>空白通用工作台</b><span>导入已有项目和资料，或新建自己的任务计划。</span><button class="btn" data-cmd="project-import">导入项目</button><button class="btn" data-cmd="hx-wizard">新建项目</button>');

  if (s.mode === "offline") {

    const o = s.offline || {};

    out += box("bad", o.has_data

      ? `<b>连接断开 · 只读</b><span>显示的是「${esc(s.project?.name || s.project?.id || "当前项目")}」截至 ${esc(hhmm(o.data_at))} 的数据，写入已暂停，账本状态未知。正在自动重连…</span><span class="sp" style="flex:1"></span><button class="btn sm" data-cmd="reconnect">立即重试</button>`

      : `<b>无法连接 Helix 后台</b><span>这台电脑上还没有可用的项目数据。请先启动 Helix 服务（launch 目录），页面会自动连上。</span><span class="sp" style="flex:1"></span><button class="btn sm" data-cmd="reconnect">立即重试</button><button class="btn sm" data-cmd="demo-enter">进入演示（假数据）</button>`);

  }

  if (s.mode === "demo") out += box("warn", `<b>演示数据</b><span>这里的任务、人员和账本都是假的，不属于任何真实项目。</span><span class="sp" style="flex:1"></span><button class="btn sm" data-cmd="demo-exit">回到本机项目</button>`);

  if (s.version_mismatch) out += box("warn", `<b>页面版本过旧</b><span>页面是 ${esc(s.version_mismatch.page)}，后台是 ${esc(s.version_mismatch.server)}。请按 Ctrl+F5 强制刷新。</span>`);

  return out;

}

function render() {

  syncForm(); applyPrefs(); activity(); lights(); statusbar(); dock(); titlebar();

  const s = store.snapshot, v = views[store.ui.view] || views.cockpit;
  if(s?.generic_mode&&s.auth?.bootstrap){location.replace('/leader-login');return;}
  if(s?.mode==='live' && s.auth && !s.auth.bootstrap && !s.auth.me){location.replace('/login');return;}
  if(s?.auth?.me && store.prefs.me!==s.auth.me.name)store.pref({me:s.auth.me.name});

  $(".side").innerHTML = side(s, store.ui);

  const fa = document.activeElement, fk = fa?.closest?.(".insp") && fa.dataset?.d, fpos = fk && fa.selectionStart;

  $(".insp").innerHTML = `<header><b>任务详情</b><span class="sp" style="flex:1"></span>${store.ui.sel ? `<button class="btn" data-cmd="clear" style="padding:2px 8px">×</button>` : ""}</header>` + inspector(s, store.ui.sel);

  if (fk) { const n = $(`.insp [data-d="${CSS.escape(fk)}"]`); if (n && n.type !== "checkbox" && n.type !== "radio") { n.focus(); try { n.setSelectionRange(fpos, fpos); } catch {} } }

  const main = $(".main");
  const fileSearchFocused=document.activeElement?.id==='file-search',fileSearchPosition=fileSearchFocused?document.activeElement.selectionStart:null;

  if (!s) { main.innerHTML = `<div class="view"><div class="empty">正在连接数据端口…</div></div>`; return; }

  const viewChanged = lastView !== store.ui.view;

  if (viewChanged) { motion?.destroy(); motion = null; }

  const html = `<div class="view" ${viewChanged ? "" : 'style="animation:none"'}>${banner(s)}${s.local_team&&store.ui.view!=="team"?'<p class="lt-entry"><button class="btn" data-cmd="go:team">进入科研分工与研习区 →</button> 查看专属科研区、本周攻关任务与实操规范</p>':""}${v.render(s, store.ui, store.prefs, { ports, motion: motionPacks })}</div>`;

  // 只替换非舞台区域，避免重建 canvas 造成闪烁

  const stage = $("#stage");

  if (!viewChanged && stage && store.ui.view === "cockpit") {

    const tmp = document.createElement("div"); tmp.innerHTML = html; tmp.querySelector("#stage").replaceWith(stage); main.replaceChildren(...tmp.childNodes);

  } else main.innerHTML = html;
  if(fileSearchFocused){const search=document.querySelector('#file-search');search?.focus();if(search&&fileSearchPosition!=null)search.setSelectionRange(fileSearchPosition,fileSearchPosition);}

  lastView = store.ui.view;

  lockWrites();

  if (store.ui.view === "cockpit") mountStage(false);

  maybeWelcome();

}

function select(sel) { store.ui_({ sel }); if (!store.prefs.insp) store.pref({ insp: true }); bus.emit("CUSTOM", { name: "select", sel }); }

function go(view) { if (views[view]) { store.ui_({ view }); location.hash = "/" + view; } }



// P0-1：未连上本机服务时，所有写入入口显示为禁用；点击也在捕获阶段拦下（弹窗等不经 render 的按钮同样适用）

const WRITE_TIP = "连接断开或演示模式：写入已暂停";

function lockWrites() {

  if (gate.live) return;

  document.querySelectorAll("[data-cmd]").forEach((b) => { if (WRITE_CMD.test(b.dataset.cmd)) { b.disabled = true; b.setAttribute("aria-disabled", "true"); b.title = WRITE_TIP; } });

  document.querySelectorAll(".pf-link").forEach((x) => { x.disabled = true; });

}

document.addEventListener("click", (e) => {

  const t = e.target.closest?.("[data-cmd]");

  if (t && !gate.live && WRITE_CMD.test(t.dataset.cmd)) { e.preventDefault(); e.stopImmediatePropagation(); toast(WRITE_TIP); }

}, true);

document.addEventListener("change", (e) => { if (!gate.live && e.target.classList?.contains("pf-link")) { e.stopImmediatePropagation(); e.target.value = ""; toast(WRITE_TIP); } }, true);

// 委托事件

document.addEventListener("click", (e) => {

  const t = e.target.closest("[data-view],[data-sel],[data-cmd],[data-dock],[data-motion],[data-f]"); if (!t || t === document.documentElement) return; // <html data-motion> 是全局开关，不是按钮

  if (t.dataset.view) return go(t.dataset.view);

  if (t.dataset.motion) { store.pref({ motion: t.dataset.motion }); return mountStage(true); }

  if (t.dataset.dock) return store.ui_({ dockTab: t.dataset.dock });

  if (t.dataset.f) return store.ui_({ filter: t.dataset.f });

  if (t.dataset.sel && wlEl?.contains(t)) closeWelcome();

  if (t.dataset.sel) { const [k, v] = t.dataset.sel.split(":"); return k === "view" ? go(v) : select(t.dataset.sel); }

  run(t.dataset.cmd);

});

document.addEventListener("change", (e) => {

  if (e.target.name === "port") { store.pref({ port: e.target.value }); connect(); }

  if (e.target.name === "motion") store.pref({ motion: e.target.value });

  if (e.target.id === "motionOn") store.pref({ motionOn: e.target.checked });

  if (e.target.id === "meSel") store.pref({ me: e.target.value });

  if (e.target.id === "meSel2") { store.pref({ me: e.target.value }); setTimeout(openWelcome, 30); }

  if (e.target.id === "welcomeOff") store.pref({ welcomeOff: e.target.checked });

  if (e.target.classList.contains("pf-link") && e.target.value) linkKey(e.target.dataset.key, e.target.value);

  if (e.target.id === "night") store.pref({ theme: e.target.checked ? "night" : "day" });

});

function toast(msg) { const d = Object.assign(document.createElement("div"), { className: "toast", textContent: msg }); document.body.appendChild(d); setTimeout(() => d.remove(), 2600); }

const COMMANDS = [

  ...ORDER.map((id) => ({ id: "go:" + id, label: "前往 · " + views[id].title, hint: "G" })), { id: "go:settings", label: "前往 · 设置" },

  { id: "rl-next", label: "下一步：轮到我的事", hint: "N" }, { id: "rl-report", label: "生成本周周报（Markdown）" }, { id: "side", label: "切换侧栏", hint: "⌘B" }, { id: "insp", label: "切换检查器", hint: "⌘I" }, { id: "dock", label: "切换智能体控制台", hint: "⌘J" },

  { id: "motion:native", label: "动效 · 原生双螺旋" }, { id: "motion:v2", label: "动效 · 经典双螺旋" }, { id: "motion:calm", label: "动效 · 静态" },

  { id: "toggleMotion", label: "开 / 关动效" }, { id: "night", label: "切换夜间主题" }, { id: "reconnect", label: "重新连接数据端口" }, { id: "export", label: "导出当前快照 JSON" },

  { id: "import", label: "导入快照文件…" }, { id: "primer", label: "写引物，启动运行（helixd 阶段）" }, { id: "person-new", label: "添加真人到成员名册" }, { id: "welcome", label: "打开本轮概览（四周安排、红线、提示）" },

  { id: "hx-wizard", label: "新建项目（从头引导：成员、计划、数据、红线）" }, { id: "hx-upload", label: "添加数据文件…" }, { id: "hx-register", label: "登记本机大文件（不复制）…" }, { id: "hx-seed", label: "播种本周任务到收件箱" }, { id: "hx-projects", label: "切换项目" },
  { id: "helix-remote", label: "云账号与项目同步（登录服务器 / 上传下载项目）" },

];



// —— 开场页：四周安排、本周与我有关的事、红线、使用提示。每天第一次打开时出现，可关闭。

const today = () => new Date().toISOString().slice(0, 10);

function welcomeHTML(s) {

  const sp = s.sprint || {}, p = s.project || {}, me = store.prefs.me || "";

  const cur = (sp.weeks || []).find((w) => w.week === sp.current_week) || {};

  const mineTasks = (sp.pairs || []).filter((t) => me && (t.owner === me || t.cross_reviewer === me));

  const allOpen = (sp.pairs || []).filter((t) => t.open || (t.bases && t.crossed < t.bases) || t.disputed);

  const mem = members(s);

  const taskLine = (t) => `<li data-sel="task:${esc(t.task_id)}">${av(s, t.owner)}<b>${esc(t.task_id)}</b><span>${esc(t.deliverable)}</span><em>${t.owner === me ? "我主核" : t.cross_reviewer === me ? "我交叉核" : t.open ? `等 ${esc(t.owner)}` : `等 ${esc(t.cross_reviewer)}`}</em></li>`;

  return `<div class="wl-box" role="dialog" aria-label="本轮概览">

    <header class="wl-hero"><span class="logo"></span><div><h1>${esc(p.name || p.target || "Helix Harness")}</h1>

      <p>第 ${sp.current_week ?? "?"} 周：${esc(cur.track_label || "")}，${esc(String(cur.start || "").slice(5))} 至 ${esc(String(cur.end || "").slice(5))}，${sp.days_left===0?"今天截止":`还剩 ${sp.days_left ?? "—"} 天`}。项目状态 <span title="${esc(p.status || "")}">${esc(projectStatusLabel(p.status))}</span>，结论最高只能写到 <span title="${esc(p.claim_ceiling || "")}">${esc(ceilLabel(p.claim_ceiling))}</span>。</p></div>

      <button class="wl-x" data-cmd="welcome-close" aria-label="关闭">×</button></header>

    <div class="wl-body">

      <section><h2>四周安排</h2>${weekBand(s)}</section>

      <div class="wl-cols">

        <section><h2>${me ? `本周与「${esc(me)}」有关的事` : "本周还在等人的事"}</h2>

          ${s.auth?.me ? "" : `<p class="wl-hint">先登录，这里会只列与你有关的任务；配对和交叉核也只认登录的人：<button class="btn sm pri" data-cmd="auth-login">登录</button></p>`}

          <ul class="wl-tasks">${(me ? mineTasks : allOpen).map(taskLine).join("") || `<li class="none">${me ? "本周没有与你有关的任务。" : "本周任务都已处理。"}</li>`}</ul></section>

        <section><h2>使用提示</h2><ul class="wl-tips">

          <li><kbd>Ctrl</kbd><kbd>K</kbd><span>搜索任何任务、成员或命令</span></li>

          <li><kbd>N</kbd><span>跳到轮到你的下一件事</span></li>

          <li><kbd>1</kbd>–<kbd>7</kbd><span>切换左侧页面：首页 1、任务 2、审核 3、文件 4</span></li>

          <li><kbd>J</kbd><kbd>K</kbd><span>在收件箱里上下移动，右侧会显示详情</span></li>

          <li><i class="dot" style="--c:var(--hu)"></i><span>螺旋上琥珀色是真人链，绿色是 AI 链；点横档可以看那一项</span></li>

          <li><i class="dot" style="--c:var(--task)"></i><span>数据里的人默认显示为代号，到“成员”页登记真人后显示你填的名字</span></li>

        </ul></section>

      </div>

      ${(sp.red_lines || []).length ? `<details class="redlines"><summary>本轮红线（${sp.red_lines.length} 条，机器校对会拦截）</summary><ul>${sp.red_lines.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details>` : ""}

    </div>

    <footer class="wl-foot"><label><input type="checkbox" id="welcomeOff" ${store.prefs.welcomeOff ? "checked" : ""}> 打开时不再自动显示（随时可点标题栏“本轮概览”）</label><span class="sp"></span><button class="btn" data-cmd="wl-new">＋ 从头开始一个新项目</button><button class="btn pri" data-cmd="welcome-close">开始今天的工作</button></footer>

  </div>`;

}

let wlEl = null;

function openWelcome() {

  const s = store.snapshot; if (!s) return;

  wlEl?.remove(); wlEl = document.createElement("div"); wlEl.className = "welcome"; wlEl.innerHTML = welcomeHTML(s); document.body.appendChild(wlEl);

  wlEl.addEventListener("click", (e) => { if (e.target === wlEl) closeWelcome(); });

  wlEl.querySelector(".wl-foot .btn")?.focus();

}

function closeWelcome() { if (!wlEl) return; wlEl.remove(); wlEl = null; store.pref({ welcomeSeen: today() }); }

let welcomeChecked = false;

function maybeWelcome() { if (welcomeChecked || !store.snapshot || store.snapshot.mode === "offline") return; welcomeChecked = true; const s = store.snapshot; if(s.generic_mode&&!s.plan)return; if(s.local_team){if(s.local_team.must_select)go("team");return;} if (s.mode === "empty" && !(s.projects || []).some((p) => p.kind === "local")) return openWizard("new"); if (!store.prefs.welcomeOff && !store.prefs.welcomeSeen) openWelcome(); }



// —— 成员名册：唯一的写操作，只写本机 data/people.json（不碰账本、不碰 research_v4）

const nextColor = () => MEMBER_COLORS[roster(store.snapshot).length % MEMBER_COLORS.length];

async function savePeople(list, msg) {

  try {

    const r = await fetch("/api/people", { method: "POST", headers: { "Content-Type": "application/json", "X-Helix": "1", "X-Helix-Token": sessionStorage.getItem("helix.token") || "" }, body: JSON.stringify({ people: list }) });

    const j = await r.json(); if (!j.ok) throw new Error(j.error || r.status);

    const s = await (await fetch("/api/snapshot", { cache: "no-store", headers: { "X-Helix-Token": sessionStorage.getItem("helix.token") || "" } })).json(); store.ui.edit = null; store.set({ snapshot: s });

    bus.emit("CUSTOM", { name: "people.saved", count: j.people.length }); toast(msg || "名册已保存");

  } catch (e) { toast("保存失败：" + (e.message || e)); }

}

function linkKey(key, id) { const list = roster(store.snapshot).map((p) => ({ ...p, keys: [...(p.keys || [])] })); const p = list.find((x) => x.id === id); if (!p) return; if (!p.keys.includes(key)) p.keys.push(key); savePeople(list, `已关联到 ${p.name}`); }

function personCmd(cmd) {

  const [op, a, b] = cmd.split(":"); const list = roster(store.snapshot).map((p) => ({ ...p, keys: [...(p.keys || [])] }));

  if (op === "person-new") { store.ui_({ edit: { name: "", role: "both", color: nextColor(), note: "", keys: [] } }); if (store.ui.view !== "people") go("people"); }

  else if (op === "person-claim") { const w = workload(store.snapshot, b); const role = w.primary && w.cross ? "both" : w.primary ? "primary" : w.cross ? "cross" : "both"; store.ui_({ edit: { name: "", role, color: nextColor(), note: "", keys: [a], code: b } }); if (store.ui.view !== "people") go("people"); setTimeout(() => $("#pf-name")?.focus(), 60); }

  else if (op === "person-edit") { const p = list.find((x) => x.id === a); if (p) { store.ui_({ edit: p }); if (store.ui.view !== "people") go("people"); } }

  else if (op === "person-cancel") store.ui_({ edit: null });

  else if (op === "pf-color") { const c = $("#pf-color"); if (c) c.value = a; syncForm(); store.ui_({}); }

  else if (op === "person-toggle") { const p = list.find((x) => x.id === a); if (p) { p.active = p.active === false; savePeople(list, p.active ? `已启用 ${p.name}` : `已停用 ${p.name}`); } }

  else if (op === "person-del") { const p = list.find((x) => x.id === a); if (p && confirm(`从名册删除“${p.name}”？（数据里的此人会恢复显示为代号）`)) savePeople(list.filter((x) => x.id !== a), "已删除"); }

  else if (op === "person-save") {

    syncForm(); const ed = store.ui.edit; const name = (ed.name || "").trim();

    if (!name) { toast("请填写显示名"); return $("#pf-name")?.focus(); }

    if (list.some((x) => x.name === name && x.id !== ed.id) || members(store.snapshot).some((m) => !m.registered && m.name === name)) return toast("这个显示名已被占用");

    const row = { id: ed.id || "p" + Date.now().toString(36), name, role: ed.role, color: ed.color, note: ed.note || "", keys: ed.keys || [], active: ed.active !== false };

    const i = list.findIndex((x) => x.id === row.id); i >= 0 ? (list[i] = row) : list.push(row);

    if (store.prefs.me && ed.code && store.prefs.me === ed.code) store.pref({ me: name });

    savePeople(list, `已保存 ${name}`);

  }

}

function run(cmd) {

  if (!cmd) return;

  if(cmd.startsWith('go:') || cmd==='hx-wizard-edit' || cmd==='ck-cloud')closeModal();

  if (v31Cmd(cmd)) return;
  if (relayCmd(cmd)) return;
  if (projectImportCmd(cmd)) return;
  if (localTeamCmd(cmd)) return;
  if (agentCmd(cmd)) return;
  if (helixRemoteCmd(cmd)) return;

  if (harnessCmd(cmd)) return;

  if (cmd === "welcome") return openWelcome();

  if (cmd === "welcome-close") return closeWelcome();

  if (cmd === "wl-new") { closeWelcome(); return openWizard("new"); }

  if (cmd.startsWith("person-") || cmd.startsWith("pf-")) return personCmd(cmd);

  if (cmd.startsWith("me:")) { store.pref({ me: cmd.slice(3) }); return toast("已设为“我”：" + cmd.slice(3)); }

  if (cmd.startsWith("go:")) return go(cmd.slice(3));

  if (cmd.startsWith("motion:")) { store.pref({ motion: cmd.slice(7) }); if (store.ui.view !== "cockpit") go("cockpit"); return setTimeout(() => mountStage(true)); }

  ({

    side: () => store.pref({ side: !store.prefs.side }), insp: () => store.pref({ insp: !store.prefs.insp }), dock: () => store.pref({ dock: !store.prefs.dock }),

    toggleMotion: () => store.pref({ motionOn: !store.prefs.motionOn }), night: () => store.pref({ theme: store.prefs.theme === "night" ? "day" : "night" }),

    palette: () => openPalette(), reconnect: () => (conn?.retryNow && conn.id === "offline" ? conn.retryNow() : connect()), clear: () => store.ui_({ sel: null }),

    "demo-enter": () => { store.pref({ port: "demo" }); connect(); }, "demo-exit": () => { store.pref({ port: "auto" }); connect(); },

    export: () => { const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([JSON.stringify(store.snapshot, null, 2)], { type: "application/json" })), download: `helix.snapshot.${(store.snapshot?.snapshot_hash || "x").slice(0, 8)}.json` }); a.click(); bus.emit("CUSTOM", { name: "export.snapshot" }); },

    import: () => { store.pref({ port: "file" }); connect(); },

    primer: () => toast("写引物 → 启动运行：helixd 阶段开放（需要真人署名）"),

  })[cmd]?.();

}

// 命令面板

const pal = $(".palette"), pin = $(".palette input"), plist = $(".palette .list"); let pidx = 0, pitems = [];

function openPalette() { pal.classList.add("on"); pin.value = ""; filterPalette(); pin.focus(); }

function filterPalette() {

  const q = pin.value.trim(); const s = store.snapshot;

  const dyn = s ? [...(s.sprint?.pairs || []).map((t) => ({ id: "sel:task:" + t.task_id, label: `任务 · ${t.task_id} ${t.deliverable.slice(0, 26)}`, hint: t.owner })), ...(s.items || []).slice(-40).map((b) => ({ id: "sel:base:" + b.base_id, label: `碱基 · ${b.text.slice(0, 34)}`, hint: b.status }))] : [];

  pitems = [...COMMANDS, ...dyn].filter((c) => (c.id !== "hx-register" || uploadCapabilities(s || {}).register) && (!q || c.label.toLowerCase().includes(q.toLowerCase()))).slice(0, 12); pidx = 0; drawPalette();

}

function drawPalette() { plist.innerHTML = pitems.map((c, i) => `<div class="it ${i === pidx ? "on" : ""}" data-i="${i}">${esc(c.label)}<small>${esc(c.hint || "")}</small></div>`).join("") || `<div class="it">没有匹配</div>`; }

function pick(i) { const c = pitems[i]; pal.classList.remove("on"); if (!c) return; c.id.startsWith("sel:") ? select(c.id.slice(4)) : run(c.id); }

pin.addEventListener("input", filterPalette);

plist.addEventListener("click", (e) => { const it = e.target.closest(".it[data-i]"); if (it) pick(+it.dataset.i); });

pal.addEventListener("click", (e) => { if (e.target === pal) pal.classList.remove("on"); });

$(".title .search").addEventListener("click", openPalette);

document.addEventListener("keydown", (e) => {

  if (isWizardOpen()) return wizardKey(e);

  if (wlEl) { if (e.key === "Escape") closeWelcome(); return; }

  const mod = e.metaKey || e.ctrlKey;

  if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); return pal.classList.contains("on") ? pal.classList.remove("on") : openPalette(); }

  if (pal.classList.contains("on")) { if (e.key === "Escape") pal.classList.remove("on"); if (e.key === "ArrowDown") { pidx = Math.min(pidx + 1, pitems.length - 1); drawPalette(); } if (e.key === "ArrowUp") { pidx = Math.max(pidx - 1, 0); drawPalette(); } if (e.key === "Enter") pick(pidx); return; }

  if (mod && e.key.toLowerCase() === "b") { e.preventDefault(); run("side"); }

  if (mod && e.key.toLowerCase() === "i") { e.preventDefault(); run("insp"); }

  if (mod && e.key.toLowerCase() === "j") { e.preventDefault(); run("dock"); }

  if (e.target.matches("input,textarea,select")) return;

  if (!mod && pairKey(e)) return;

  if (/^[1-9]$/.test(e.key) && navIds(store.snapshot)[+e.key - 1]) go(navIds(store.snapshot)[+e.key - 1]);

  if (!mod && !e.altKey && e.key.toLowerCase() === "n") run("rl-next");

  if (store.ui.view === "inbox" && /^[jk]$/i.test(e.key)) { const rows = [...document.querySelectorAll(".main tr[data-sel]")]; const i = rows.findIndex((r) => r.dataset.sel === store.ui.sel); const n = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key.toLowerCase() === "j" ? 1 : -1)))]; if (n) select(n.dataset.sel); }

  if (e.key === "Escape") store.ui_({ sel: null });

});

async function connect() {

  conn?.close?.();

  try { conn = await connectPort(store.prefs.port, (snap) => store.set({ snapshot: snap })); }

  catch (e) { toast("数据端口连接失败：" + (e.message || e)); }

}

initHarness({ toast, go, select });

initAgents({ toast, go, api, refresh });
initLocalTeam({toast,go,api,refresh});
initProjectImport({toast,go,api,refresh,closeMenu:()=>harnessCmd('hx-projects')});
initV31({toast,go,api,refresh});
initHelixRemote({api,refresh});
initRelay({toast,go,select,run,api,refresh});

store.subscribe(() => { try { render(); } catch (e) { window.__helixReport?.("render", e.stack || e.message); } });

bus.on(() => { if (store.prefs.dock && store.ui.dockTab === "events") dock(); });

const h = location.hash.replace("#/", ""); if (views[h]) store.ui.view = h;

addEventListener("hashchange", () => { const v = location.hash.replace("#/", ""); if (views[v] && v !== store.ui.view) store.ui_({ view: v }); });

bus.emit("RUN_STARTED", { shell: "helix-shell 3.3.1", schema: "helix.snapshot.v1" });

try { render(); } catch (e) { window.__helixReport?.("render", e.stack || e.message); }

window.__helixBooted = true; connect();

addEventListener("helix:net", () => { try { lights(); } catch {} });

