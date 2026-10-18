import { esc, short, av, statusPill, bondTag, GATE_C, fmtTs, crossState, members, workload, roster, ROLE_LABEL, MEMBER_COLORS, canon, isHold, evTag, actorKind, ceilLabel, portLabel, projectStatusLabel, dutyBoard, dutyChip, checkinOf, ckMain, ckPill, ckBar, canCheckin, CK_STATE } from "./common.js?v=3110";

import { dataView } from "./forms.js?v=3110";

import { researchView, toolsView } from "../science.js?v=3110";

const I = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;

export const ICONS = {

  cockpit: I('<path d="M4 4c4 4 12 4 16 0M4 20c4-4 12-4 16 0M7 7.5v9M12 8.6v6.8M17 7.5v9"/>'),

  inbox: I('<path d="M3 13h5l2 3h4l2-3h5M5 5h14l2 8v6H3v-6z"/>'),

  runs: I('<circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M7 6h10M6 8l5 8M18 8l-5 8"/>'),

  crew: I('<rect x="3" y="3" width="18" height="5" rx="2"/><rect x="3" y="10" width="18" height="5" rx="2"/><rect x="3" y="17" width="18" height="4" rx="2"/>'),

  evidence: I('<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="M9 12l2 2 4-4"/>'),

  models: I('<path d="M12 2l3 6 6 1-4.5 4 1 6-5.5-3-5.5 3 1-6L3 9l6-1z"/>'),

  people: I('<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.8-3.2 3-5 5.5-5s4.7 1.8 5.5 5"/><path d="M18 8v6M15 11h6"/>'),

  data: I('<path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>'),

  ledger: I('<rect x="3" y="9" width="6" height="6" rx="1.5"/><rect x="15" y="9" width="6" height="6" rx="1.5"/><path d="M9 12h6"/>'),

  settings: I('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),

};

const head = (s, title, extra = "") => `<div class="vh"><div><h1>${title}</h1><p class="lede">${esc(s.project?.name || s.project?.target || "")}，${s.mode === "live" ? (s.preview ? "实时数据（预览模式，账本未建立）" : "实时数据") : s.mode === "offline" ? "连接断开，只读显示最后一次数据" : s.mode === "demo" ? "演示快照（假数据）" : "文件快照"}。账本${s.ledger?.ok == null ? "状态未知" : s.ledger.ok ? "链完整" : "异常"}，${s.ledger?.count ?? 0} 条记录。</p></div><span class="sp"></span>${extra}</div>`;



// P1-4：进度按“任务”统计（不按条目），按显式周次归周（不靠编号前缀），百分比恒在 0–100%。

// 任务状态以服务端 gates.tasks 为准：只有“主核 PASS + 交叉核一致”才算通过；暂缓、分歧、拒绝分开显示。

export function weekStats(s) {

  const sp = s.sprint || {}, items = s.items || [], G = s.gates?.tasks || {};

  const tasks = (s.plan?.tasks?.length ? s.plan.tasks : sp.pairs || []).filter((t) => t.task_id);

  const weekOf = (t) => +t.week || +(String(t.task_id).match(/^W(\d+)-/) || [])[1] || 0;

  const byTask = new Map(); for (const b of items) if (b.task_id) (byTask.get(b.task_id) || byTask.set(b.task_id, []).get(b.task_id)).push(b);

  const stateOf = (t) => {

    const tb = byTask.get(t.task_id) || [], seeds = tb.filter((b) => b.kind === "task"), g = G[t.task_id]?.state && String(G[t.task_id].state).toLowerCase();

    if (g === "passed") return "passed";

    if (g === "failed") return "failed";

    if (g === "disputed" || tb.some((b) => canon(b.status) === "disputed")) return "disputed";

    if (g === "deferred" || g === "hold" || seeds.some(isHold)) return "hold";

    if (g === "blocked") return "blocked";

    if (!tb.length || g === "unseeded") return "unseeded";

    if (tb.some((b) => canon(b.status) === "open")) return "primary";

    if (tb.some((b) => b.needs_cross && !b.cross)) return "cross";

    if (!g && seeds.length && seeds.every((b) => b.cross?.agree === true && String(b.decision).toUpperCase() === "PASS")) return "passed"; // 无 gates 的旧数据源

    return "open";

  };

  return (sp.weeks || []).map((w) => {

    const list = tasks.filter((t) => weekOf(t) === w.week), c = { passed: 0, failed: 0, disputed: 0, hold: 0, blocked: 0, unseeded: 0, primary: 0, cross: 0, open: 0 };

    list.forEach((t) => { c[stateOf(t)]++; });

    const total = list.length || w.tasks || 0;

    return { ...w, total, ...c, pct: total ? Math.min(100, Math.round((100 * c.passed) / total)) : 0 };

  });

}

function weekBand(s) {

  const sp = s.sprint || {};

  const tag = (n, t, cls = "") => (n ? `<span class="pill ${cls}" style="margin:2px 4px 0 0">${t} ${n}</span>` : "");

  return `<div class="weeks">${weekStats(s).map((w) => `<div class="wk ${w.week === sp.current_week ? "cur" : ""}"><b>第 ${w.week} 周 ${esc(w.track_label)}</b><span>${esc(String(w.start).slice(5))} 至 ${esc(String(w.end).slice(5))}，${w.total} 项${w.week === sp.current_week ? "（本周）" : ""}</span><div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${w.pct}"><i style="width:${w.pct}%"></i></div><small>任务通过 ${w.passed}/${w.total}</small><div>${tag(w.primary, "待初审", "warn")}${tag(w.cross, "待复核", "warn")}${tag(w.hold, "暂缓")}${tag(w.disputed, "分歧", "bad")}${tag(w.failed, "拒绝/超范围", "bad")}${tag(w.blocked, "等上游")}${tag(w.unseeded, "未播种")}</div></div>`).join("")}</div>`;

}

function pairs(s) {

  const p = s.sprint?.pairs || [];

  if (!p.length) return `<div class="empty">本周没有任务（检查四周计划来源）</div>`;

  return p.map((t) => `<div class="pair" data-sel="task:${esc(t.task_id)}">${av(s, t.owner)}<div><b>${esc(t.task_id)} · ${esc(t.deliverable).slice(0, 44)}${t.deliverable.length > 44 ? "…" : ""}</b><small>${esc(t.owner)} ⇄ ${esc(t.cross_reviewer)} · ${t.bases ? `碱基 ${t.bases} · 待主核 ${t.open} · 交叉核 ${t.crossed}${t.disputed ? ` · 分歧 ${t.disputed}` : ""}` : "Helix 未播种"}</small></div>${av(s, t.cross_reviewer)}</div>`).join("");

}

function skeleton(s) {

  const k = s.skeleton || {};

  return `<div class="chips">${(k.gates || []).map((g) => `<span class="chip ${GATE_C[g.raw_status] || ""}" data-sel="gate:${g.id}" title="${esc(g.raw_status)} · ${esc(g.migration_status || "")}">${g.id}</span>`).join("")}</div>

  <div class="chips" style="margin-top:8px">${Object.entries(k.human_nodes || {}).map(([a, b]) => `<span class="chip">${a} ${esc(b)}</span>`).join("")}<span class="chip">冻结 ${esc(k.freeze?.status)} v${esc(k.freeze?.version)}</span><span class="chip">EP0 ${esc(k.validation?.ep0)}</span><span class="chip">实验数据 ${esc(k.validation?.actual_data_count)}</span><span class="chip">物料 ${esc(k.validation?.material_release)}</span></div>

  <table style="margin-top:10px"><thead><tr><th>候选轨</th><th>数</th></tr></thead><tbody>${Object.entries(k.tracks || {}).map(([a, b]) => `<tr><td>${({ EXTERNAL_LIBRARY: "MCULE 外部库", NATURAL_PRODUCTS: "天然产物 / 中药", DRUG_REPURPOSING: "老药再利用" })[a] || a}</td><td>${b}</td></tr>`).join("")}<tr data-sel="view:evidence"><td>V4 异常队列</td><td>${(k.exceptions || []).length}</td></tr></tbody></table>

  <div class="mono" style="margin-top:8px">${(s.witnesses || []).filter((w) => w.found).length}/${(s.witnesses || []).length} 来源见证 · 快照 ${short(s.snapshot_hash, 16)}</div>`;

}



// 0.5.2 分工看板：每人职能、手上的任务、下一步

function board(s, ui, prefs, compact) {

  const B = dutyBoard(s), me = prefs?.me || "", cur = s.sprint?.current_week;

  if (!B.rows.length) return `<div class="empty">${s.plan ? "计划里还没有成员。" : "内置项目没有本机计划；新建本机项目后可以按职能分工。"}</div>`;

  const rows = [...B.rows].sort((a, b) => (b.name === me) - (a.name === me));

  const card = (r) => { const tk = compact ? r.tasks.filter((t) => t.week === cur) : r.tasks;

    return `<div class="duty-card ${r.name === me ? "me" : ""}"><div class="dc-h" data-sel="member:${esc(r.name)}">${av(s, r.name)}<b>${esc(r.name)}</b><span class="sp"></span>${r.todo ? `<span class="pill warn">${r.todo} 待办</span>` : `<span class="pill ok">无待办</span>`}${r.disp ? `<span class="pill bad">${r.disp} 分歧</span>` : ""}</div>

      <div class="dc-du">${r.duties.length ? r.duties.map((k) => dutyChip(s, k, B.duties)).join("") : `<span class="duty none">未分配职能</span>`}<small>主核 ${r.prim} · 交叉核 ${r.cross}</small></div>

      <ul class="dc-tasks">${tk.slice(0, compact ? 4 : 12).map((t) => `<li data-sel="task:${esc(t.id)}"><i class="${t.as === "主核" ? "p" : "c"}">${t.as}</i>${dutyChip(s, t.duty, B.duties)}<b>${esc(t.id)}</b><span title="${esc(t.text)}">${esc(t.text)}</span>${ckMini(s, t.id)}</li>`).join("") || `<li class="none">${compact ? "本周没有任务" : "没有任务"}</li>`}${tk.length > (compact ? 4 : 12) ? `<li class="none">…另有 ${tk.length - (compact ? 4 : 12)} 条</li>` : ""}</ul>

      ${r.next ? `<div class="dc-next" data-sel="${esc(r.next.sel)}">下一步：${esc(r.next.t)}</div>` : ""}</div>`; };

  const participants=(s.plan?.members||[]).filter(m=>m.active!==false&&m.role!=="observer"), count=new Set(participants.map(m=>m.name)).size;
  return `<p class="sub">当前 ${count} 人参与（含组长） · ${s.local_team?"组长统筹与全程辅助 · 四岗位分工固定，账号自主落座":"组长也执行任务，复核由另一位成员分担 · 成员变更后重排未开始任务"}</p>${B.warn.length ? `<div class="lock" style="margin:0 0 12px"><b>分工提醒：</b>${B.warn.slice(0, compact ? 3 : 12).map(esc).join("；")}${compact && B.warn.length > 3 ? `；…共 ${B.warn.length} 条` : ""}</div>` : ""}<div class="duty-board">${rows.map(card).join("")}</div>`;

}

// 0.6 打卡：驾驶舱每条任务下的一行、看板里的小进度、本周汇总与云同步状态

function ckLine(s, t, prefs) {

  if (!s.team) return "";

  const c = checkinOf(s, t.task_id), l = c?.latest, can = canCheckin(s, t, prefs), live = s.harness?.writable;

  return `<div class="todo-ck">${ckPill(s, t.task_id)}${ckBar(s, t.task_id)}<span class="ck-note" title="${esc(l?.note || "")}">${l ? `${esc(l.actor || "")}：${esc(l.note || (CK_STATE[l.state] || [l.state])[0])}` : "还没有人打卡"}</span>${can && live ? `<button class="btn sm ck-btn" data-cmd="ck-open:${esc(t.task_id)}">打卡</button>` : ""}</div>`;

}

function ckMini(s, tid) { const l = ckMain(checkinOf(s, tid)); return l ? `<em class="ck-mini ck-${esc(l.state)}" title="${esc((CK_STATE[l.state] || [l.state])[0])}">${l.state === "blocked" ? "受阻" : (l.progress ?? 0) + "%"}</em>` : ""; }

export function teamHead(s) {

  if (!s.team) return "";

  const w = s.team.week || s.team.summary || {}, C = s.cloud || {};

  const cloud = !C.configured ? `<button class="link" data-cmd="ck-cloud">设置云同步</button>` : !C.enabled ? `<button class="link" data-cmd="ck-cloud">云同步已停用</button>`

    : `<button class="link ${C.last_ok === false ? "ck-err" : ""}" data-cmd="ck-sync" title="${esc(C.last_error || (C.last_sync ? "上次同步 " + fmtTs(C.last_sync) : "还没同步过"))}">${C.last_ok === false ? "云同步失败，点此重试" : C.pending?.length ? "正在同步…" : "云端已同步 " + fmtTs(C.last_sync).slice(5)}</button>`;

  return `<span class="ck-sum">本周平均进度 <b>${w.progress ?? 0}%</b>${w.blocked ? ` · <b class="bad-t">${w.blocked} 项受阻</b>` : ""}${w.none ? ` · ${w.none} 项未打卡` : ""}</span>${cloud}`;

}

function cloudCard(s) {

  const C = s.cloud || {}, admin = s.auth?.me?.role === "lead" || s.auth?.bootstrap;

  return `<div class="card" style="margin-bottom:14px"><h3>组内打卡 · 云同步<small>组员在 Helix 里打卡，进度自动推到 Notion 数据库，不用再手动上交</small><span class="sp" style="flex:1"></span>${C.configured ? `<button class="btn sm" data-cmd="ck-sync">立即同步</button>` : ""}${admin ? `<button class="btn sm${C.configured ? "" : " pri"}" data-cmd="ck-cloud">${C.configured ? "设置" : "连接 Notion"}</button>` : ""}</h3>

    ${C.configured ? `<dl class="kv"><dt>状态</dt><dd>${C.enabled ? (C.last_ok === false ? `<span class="pill bad">同步失败</span> ${esc(C.last_error)}` : `<span class="pill ok">已开启</span>`) : `<span class="pill">已停用</span>`}</dd><dt>数据库</dt><dd><a href="${esc(C.url)}" target="_blank" rel="noopener">${esc(C.database_title || C.database_id)}</a></dd><dt>上次同步</dt><dd>${fmtTs(C.last_sync)}${C.last_ok ? `（推送 ${C.pushed}，未变 ${C.skipped}）` : ""}</dd><dt>已同步</dt><dd>${C.rows} 条任务</dd><dt>Token</dt><dd class="mono">····${esc(C.token_tail)}</dd></dl>`

    : `<p class="sub" style="margin:0">${admin ? "点“连接 Notion”，按提示粘贴集成 Token 和数据库链接即可。" : "需要组长在这里连接 Notion。"}</p>`}

    <p class="mono" style="margin:8px 0 0">只同步：任务编号、交付物、验收、职能、周、截止、主核 / 交叉核（显示名）、打卡状态、进度、最近打卡说明、成果文件名、审核状态。文件内容、原名、口令都不会离开本机；账本仍以本机为准。</p></div>`;

}

function coverTable(s) {

  const B = dutyBoard(s);

  return `<table><thead><tr><th style="width:90px">职能</th><th>做什么</th><th>负责的人</th><th style="width:70px">任务</th><th style="width:70px">本周</th></tr></thead><tbody>${B.cover.map((c) => `<tr><td>${dutyChip(s, c.key, B.duties)}</td><td class="sub">${esc(c.desc || "")}</td><td>${c.people.length ? c.people.map((n) => `${av(s, n)} ${esc(n)}`).join("　") : `<span style="color:var(--warn-fg)">${c.tasks ? "没有人负责" : "—"}</span>`}</td><td>${c.tasks}</td><td>${c.week}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">没有职能目录</td></tr>`}</tbody></table>`;

}



export { weekBand };

export const views = {

  cockpit: { title: "工作台首页", icon: "cockpit", render: (s, ui, prefs) => {

    const m = s.metrics || {}, sp = s.sprint || {}, me = prefs?.me || "";

    const wk = (sp.weeks || []).find((w) => w.week === sp.current_week) || {};

    const gates = s.skeleton?.gates || [], gatesOpen = gates.filter((g) => GATE_C[g.raw_status] !== "ok").length;

    const state = (t) => t.disputed ? ["两人结论不一致，需要当面对一下", "bad", 0] : t.open ? [`等 ${t.owner} 主核`, "warn", 1] : (t.bases && t.crossed < t.bases) ? [`等 ${t.cross_reviewer} 交叉核`, "warn", 2] : t.crossed ? ["双人核对完成", "ok", 4] : ["还没开始", "", 3];

    const mine = (t) => me && (t.owner === me || t.cross_reviewer === me);

    const rows = (sp.pairs || []).map((t) => ({ t, st: state(t) })).sort((a, b) => (mine(b.t) - mine(a.t)) || (a.st[2] - b.st[2]));

    const todo = rows.filter((r) => r.st[2] < 3).length;

    const row = ({ t, st }) => `<div class="todo ${mine(t) ? "me" : ""} ${ui.sel === "task:" + t.task_id ? "on" : ""}" data-sel="task:${esc(t.task_id)}">

      ${av(s, t.owner)}<div class="todo-main"><div class="todo-title"><b>${esc(t.task_id)}</b><span>${esc(t.deliverable)}</span></div>

      <div class="todo-who">主核 ${esc(t.owner)}，交叉核 ${esc(t.cross_reviewer)}${mine(t) ? `<em>与我有关</em>` : ""}</div>${ckLine(s, t, prefs)}</div>

      <div class="todo-state ${st[1]}">${esc(st[0])}</div><div class="todo-due">截止 ${esc(String(t.end || wk.end || "").slice(5))}</div></div>`;

    return `<div class="vh"><div><h1>今日驾驶舱</h1><p class="lede">第 ${sp.current_week ?? "?"} 周：${esc(wk.track_label || "")}，${sp.days_left===0?"<b>今天截止</b>":`还剩 <b>${sp.days_left ?? "—"}</b> 天`}。${s.harness?.seedable ? "本周任务还没播种：点右上角按钮，任务会进入收件箱等真人主核。" : todo ? `本周有 <b>${todo}</b> 项等人处理。` : "本周任务都已处理。"}</p></div><span class="sp"></span>

      <button class="btn" data-cmd="dock">智能体控制台</button>${s.harness?.seedable ? `<button class="btn pri" data-cmd="hx-seed">播种第 ${s.harness.seed_week} 周任务</button>` : s.harness?.can_takeover ? `<button class="btn pri" data-cmd="hx-takeover">在本机建立账本</button>` : s.harness?.writable ? `<button class="btn pri" data-view="inbox">去收件箱配对</button>` : `<button class="btn pri" data-cmd="primer">写引物</button>`}</div>

    <div class="stage" id="stage"><div class="stage-tools"><div class="seg" id="motionSeg"></div></div></div>

    <div class="strip">${[["待主核", m.open_tasks ?? 0, "warn"], ["待交叉核", m.cross_pending ?? 0, "warn"], ["双人一致", m.duplex ?? 0, "ok"], ["结论分歧", m.disputed ?? 0, (m.disputed ? "bad" : "")]].map(([a, b, c]) => `<div class="stat ${b ? c : ""}"><b>${b}</b><span>${a}</span></div>`).join("")}

      <div class="stat wide" data-sel="view:evidence"><span>项目状态</span><strong title="${esc(s.project?.status || "")}">${esc(projectStatusLabel(s.project?.status))}</strong><small>结论最高只能写到 ${esc(ceilLabel(s.project?.claim_ceiling) || "—")}；${gates.length ? `${gatesOpen}/${gates.length} 道证据门未通过` : `${(sp.red_lines || []).length} 条红线由机器校对`}</small></div></div>

    <section class="block"><header><h2>本周要做的事</h2><span>${me ? `与「${esc(me)}」有关的排在最前` : `在标题栏选“我是…”，与你有关的会排在最前`}</span><span class="sp"></span>${teamHead(s)}</header>

      <div class="todos">${rows.length ? rows.map(row).join("") : `<div class="empty">本周没有任务。${s.harness?.kind === "local" ? `<button class="link" data-cmd="hx-wizard-edit">编辑计划</button>` : "检查四周计划文件是否能读到。"}</div>`}</div></section>

    ${s.plan ? `<section class="block"><header><h2>本周分工：谁在干什么</h2><span>按职能分派；点人名或任务看详情</span><span class="sp"></span><button class="link" data-view="people">完整分工</button></header>${board(s, ui, prefs, true)}</section>` : ""}

    <p class="more"><button class="link" data-cmd="welcome">查看四周安排、红线和使用提示</button></p>`; } },



  inbox: { title: "任务审核", icon: "inbox", badge: (s) => (s.metrics?.open_tasks || 0) + (s.metrics?.cross_pending || 0) + (s.metrics?.open_gc || 0), render: (s, ui, prefs) => {

    const items = (s.items || []).filter((b) => b.status === "open" || b.status === "audit" || (b.needs_cross && !b.cross) || b.status === "disputed");

    const f = ui.filter || "all";

    const shown = items.filter((b) => f === "all" || (f === "mine" ? (b.owner === prefs.me || b.cross_reviewer === prefs.me) : f === "cross" ? b.needs_cross && b.status !== "open" : b.status === "open"));

    return head(s, "配对收件箱", `<div class="seg" id="ifilter">${[["all", "全部"], ["open", "待主核"], ["cross", "待交叉核"], ["mine", prefs.me ? "我的 · " + prefs.me : "我的（先在标题栏选“我是…”）"]].map(([k, t]) => `<button data-f="${k}" class="${f === k ? "on" : ""}">${t}</button>`).join("")}</div>`) +

    (shown.length ? `<div class="card" style="padding:0"><table class="inbox"><thead><tr><th style="width:150px">任务</th><th>要核对的内容</th><th style="width:110px">主核</th><th style="width:110px">交叉核</th><th style="width:100px">状态</th></tr></thead><tbody>${shown.map((b) => `<tr data-sel="base:${esc(b.base_id)}" class="${ui.sel === "base:" + b.base_id ? "sel" : ""}"><td><div class="tid">${bondTag(b)}<b>${esc(b.task_id || b.base_id)}</b></div></td><td>${esc(String(b.text || "").replace(/^[^｜|]*[｜|]\s*[^：:]*主核[：:]\s*/, ""))}${b.kind === "task" ? "" : `<div class="sub">AI 置信 ${Math.round((b.confidence ?? .5) * 100)}%</div>`}</td><td>${av(s, b.owner)} ${esc(b.owner || b.reviewer || "—")}</td><td>${av(s, b.cross_reviewer)} ${esc(b.cross_reviewer || "—")}</td><td>${statusPill(b)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">收件箱清空了 · 复制叉可以前进</div>`) +

    `<p class="mono" style="margin-top:10px">键盘：J / K 上下选择；选中后 A 互补、E 修正、X 错配、D 暂缓，在右侧检查器勾选确认后写入账本。${s.harness?.writable ? "" : "当前账本只读：" + esc(s.harness?.why || "")}</p>`; } },



  runs: { title: "运行轨迹", icon: "runs", render: (s) => {

    const items = s.items || [], m = s.metrics || {};

    const steps = [["引物", "真人写目标、允许值、截止", "human", items.length ? "done" : "wait"], ["解旋 · 规划", "四周计划 → 本周碱基", "helicase", items.some((b) => b.source === "sprint_seed") ? "done" : "wait"], ["抽取 · 只读来源", `${(s.witnesses || []).filter((w) => w.found).length} 份原件 SHA 见证`, "extractor", (s.witnesses || []).length ? "done" : "wait"], ["起草", "起草者连续合成", "drafter", items.some((b) => b.source && b.source !== "sprint_seed") ? "done" : "idle"], ["互审 · 异源", "批评者不同厂商", "critic", "idle"], ["校对 · 红线", `拦截 ${m.proofread_blocked || 0}`, "proof", "done"], ["配对 · 真人", `待主核 ${m.open_tasks || 0} · 待交叉 ${m.cross_pending || 0}`, "human", (m.open_tasks || m.cross_pending) ? "block" : "done"], ["连接 · 周汇总", "周报 + Git 草稿", "ligase", "wait"], ["端粒 · 盖章", "真人确认链头", "human", "wait"]];

    const C = { done: "#10b981", block: "#f59e0b", idle: "#94a3b8", wait: "#cbd5e1" }, T = { done: "完成", block: "阻滞 · 等真人", idle: "未接模型", wait: "未开始" };

    return head(s, "运行轨迹") + `<div class="grid g2"><div class="card"><h3>第 ${s.sprint?.current_week ?? "?"} 周主线 · 状态图<small>参考 LangGraph 中断 / Temporal 事件历史</small></h3>

    <svg viewBox="0 0 440 ${steps.length * 52 + 10}" style="width:100%;max-height:560px">${steps.map((st, i) => { const y = 24 + i * 52, c = C[st[3]]; return `${i ? `<line x1="24" y1="${y - 40}" x2="24" y2="${y - 12}" stroke="${c}" stroke-width="2" ${st[3] === "wait" ? 'stroke-dasharray="3 4"' : 'stroke-dasharray="6 6" style="animation:flow 1.2s linear infinite"'}/>` : ""}<g data-sel="agent:${st[2]}" style="cursor:pointer"><circle cx="24" cy="${y}" r="10" fill="${c}" ${st[3] === "block" ? 'style="transform-origin:24px ' + y + 'px;animation:dual-knot-pulse 1.6s infinite"' : ""}/><text x="46" y="${y - 2}" font-size="14" font-weight="700" fill="currentColor">${st[0]}</text><text x="46" y="${y + 16}" font-size="12" fill="#607570">${esc(st[1])}</text><text x="434" y="${y + 4}" font-size="12" text-anchor="end" fill="${c}">${T[st[3]]}</text></g>`; }).join("")}</svg></div>

    <div class="card"><h3>账本事件<small>最近 ${(s.ledger?.tail || []).length} 条</small></h3><table><thead><tr><th>序号</th><th>事件</th><th>谁</th><th>时间</th></tr></thead><tbody>${(s.ledger?.tail || []).slice().reverse().map((e) => `<tr data-sel="event:${e.seq}"><td class="mono">${e.seq}</td><td><b>${evTag(e.type)}</b></td><td>${e.actor_kind === "human" ? av(s, e.actor) : ""} ${esc(e.actor)}</td><td class="mono">${fmtTs(e.ts)}</td></tr>`).join("")}</tbody></table></div></div>`; } },



  crew: { title: "智能体编队", icon: "crew", render: (s, ui) => {

    const ag = s.agents || [], mcp = s.mcp || [], mem = members(s);

    const unit = (sel, b, sm, cls = "") => `<div class="unit ${cls} ${ui.sel === sel ? "on" : ""}" data-sel="${sel}"><b>${b}</b><small>${sm}</small></div>`;

    const proto = (name, desc) => `<div class="proto"><svg viewBox="0 0 400 14" preserveAspectRatio="none"><line x1="0" y1="7" x2="400" y2="7" stroke="#10b981" stroke-width="2" stroke-dasharray="5 6" style="animation:flow 1.6s linear infinite"/></svg><span>${name}</span><small>${desc}</small><svg viewBox="0 0 400 14" preserveAspectRatio="none"><line x1="0" y1="7" x2="400" y2="7" stroke="#f59e0b" stroke-width="2" stroke-dasharray="5 6" style="animation:flow 1.6s linear infinite reverse"/></svg></div>`;

    return head(s, "智能体编队") + `<div class="card"><h3>六层结构<small>每层只和相邻层说话；协议即边界</small></h3><div class="layers">

    <div class="layer"><div class="lh"><b>L6 真人</b><small>主核 / 交叉核 / 组长</small></div><div class="row">${mem.map((m) => unit("member:" + m.name, `${av(s, m.name)} ${esc(m.name)}`, m.registered ? esc(ROLE_LABEL[m.person.role] || "") + " · 后随链" : "未登记 · 去成员页登记")).join("")}${unit("view:people", "＋ 添加真人", "名册在本机，可随时增删")}</div></div>

    ${proto("AG-UI 事件流", "智能体 ⇄ 界面：启动 · 快照 · 中断等人")}

    <div class="layer"><div class="lh"><b>L5 壳 / 界面</b><small>本应用 · 工作台第 29 页</small></div><div class="row">${unit("ui:shell", "Helix Shell", "驾驶舱 · 收件箱 · 轨迹")}${unit("ui:wb", "工作台 2.00 第 29 页", "Streamlit 内嵌客户端")}${unit("ui:cli", "helix 命令行", "运行 / 状态 / 考试 / 打包")}</div></div>

    <div class="layer"><div class="lh"><b>L4 编排</b><small>状态图 · 检查点 · 中断</small></div><div class="row">${ag.filter((a) => a.layer === 4).map((a) => unit("agent:" + a.id, esc(a.name), esc(a.role))).join("")}${unit("guard:red", "护栏 · 红线校对", "四周计划红线 · 主张上限")}${unit("guard:perm", "权限四级：读 / 建议 / 草稿 / 真人", "写 research_v4 一律拒绝")}</div></div>

    ${proto("A2A 交接", "编排者 ⇄ 角色 ⇄ AIDD-Harness-Studio")}

    <div class="layer"><div class="lh"><b>L3 角色智能体</b><small>模型自由配 · 异源互审</small></div><div class="row">${ag.filter((a) => a.layer === 3).map((a) => unit("agent:" + a.id, esc(a.name), esc(((s.models?.roles || {})[a.id] || {}).model || "未配模型"))).join("")}</div></div>

    ${proto("MCP", "角色 ⇄ 工具/数据：工具 · 资源 · 提示模板；模型采样需真人批准")}

    <div class="layer"><div class="lh"><b>L2 工具与数据</b><small>已接入工具与 MCP 规划（状态分别标明）</small></div><div class="row">${mcp.map((x) => unit("mcp:" + x.id, `${esc(x.name)} <span class="pill">${esc(x.perm)}</span>`, esc(x.kind), "st-" + x.status)).join("")}</div></div>

    <div class="layer"><div class="lh" style="background:#3b2604"><b>L1 权威（只读）</b><small>Helix 永远不是第二权威</small></div><div class="row">${unit("auth:v4", "V4 权威库", "D0–D6 关卡 · 候选 · 异常队列")}${unit("auth:sprint", "四周计划 / 任务表", "20 项 · 主核≠交叉核")}${unit("auth:review", "人审正式导入", "Git 草稿（未应用）→ AIDD 受控导入")}</div></div>

    </div></div>`; } },



  people: { title: "成员", icon: "people", badge: (s) => (s.people?.discovered || []).length, render: (s, ui, prefs) => {

    const P = s.people || {}, ro = roster(s), disc = P.discovered || [], ed = ui.edit;

    const canEdit = (s.mode === "live" || s.mode === "empty") && P.editable !== false;

    const roleOpts = (v) => Object.entries(P.roles || ROLE_LABEL).map(([k, t]) => `<option value="${k}" ${v === k ? "selected" : ""}>${esc(t)}</option>`).join("");

    const ctx = (name) => { const w = workload(s, name); return `${w.tasks.length ? w.tasks.map((t) => `<span class="chip" data-sel="task:${esc(t.task_id)}">${esc(t.task_id)} ${t.owner === name ? "主核" : "交叉核"}</span>`).join("") : `<span class="chip">本周无任务</span>`}<span class="chip">待处理 ${w.todo}</span>`; };

    const form = ed ? `<div class="card pform" style="margin-bottom:14px;border-color:var(--ai)"><h3>${ed.id ? "编辑成员" : ed.keys?.length ? `登记 ${esc(ed.code || "")} 为真人` : "添加真人"}<small>${ed.keys?.length ? "已关联数据指纹 " + ed.keys.map(esc).join(", ") + "（不保存原名）" : "暂未关联数据：以后可在下方“未登记”里关联"}</small></h3>

      <div class="grid g4" style="align-items:end"><label>显示名<input id="pf-name" maxlength="24" value="${esc(ed.name || "")}" placeholder="例如：小王 / 药化组A"></label><label>角色<select id="pf-role">${roleOpts(ed.role || "both")}</select></label>

      <label>颜色<div class="swatches">${MEMBER_COLORS.map((c) => `<button type="button" class="sw ${ed.color === c ? "on" : ""}" data-cmd="pf-color:${c}" style="background:${c}"></button>`).join("")}<input id="pf-color" type="color" value="${esc(ed.color || MEMBER_COLORS[0])}"></div></label><label>备注<input id="pf-note" maxlength="80" value="${esc(ed.note || "")}" placeholder="可选：负责方向、联系方式不要写"></label></div>

      <div style="margin-top:12px;display:flex;gap:8px"><button class="btn pri" data-cmd="person-save">保存</button><button class="btn" data-cmd="person-cancel">取消</button>${ed.id ? `<span class="sp" style="flex:1"></span><button class="btn" data-cmd="person-del:${esc(ed.id)}" style="color:#b91c1c">删除</button>` : ""}</div></div>` : "";

    return head(s, "成员", canEdit ? `<button class="btn pri" data-cmd="person-new">＋ 添加真人</button>` : "") +

    `<div class="lock" style="margin:0 0 14px">${canEdit ? "名册只保存在本机 <span class=\"mono\">data/people.json</span>：显示名、角色、颜色、备注由你自己填写；与数据里的人只用 12 位指纹关联，<b>名册里不保存原始姓名</b>。没登记的人在全界面只显示代号（成员A、成员B…）。" : "当前名册只读：组员可在下方“注册新账号”；修改名册及关联既有任务需要组长在管理端操作。"}</div>` + form +

    (s.plan ? `<div class="card" style="margin-bottom:14px"><h3>分工看板<small>每人的职能、手上的任务和下一步</small>${s.harness?.kind === "local" ? `<span class="sp" style="flex:1"></span><button class="btn sm" data-cmd="hx-wizard-edit">编辑分工</button>` : ""}</h3>${board(s, ui, prefs, false)}</div>

    <div class="card" style="margin-bottom:14px;padding-bottom:6px"><h3>职能覆盖<small>每项职能由谁负责、有多少任务</small></h3>${coverTable(s)}</div>` : "") + (s.team ? cloudCard(s) : "") +

    `<div class="card"><h3>已登记的真人<small>${ro.length} 人</small></h3>${ro.length ? `<div class="grid g3">${ro.map((p) => { const w = workload(s, p.name); return `<div class="person ${p.active === false ? "off" : ""}" data-sel="member:${esc(p.name)}">${av(s, p.name)}<div><b>${esc(p.name)}</b> <span class="pill">${esc(ROLE_LABEL[p.role] || p.role)}</span>${p.active === false ? ` <span class="pill">已停用</span>` : ""}<small>${esc(p.note || "—")}</small><small>主核 ${w.primary} · 交叉核 ${w.cross} · 待处理 ${w.todo} · 关联 ${(p.keys || []).length}</small></div>${canEdit ? `<div class="pact"><button class="btn" data-cmd="person-edit:${esc(p.id)}">编辑</button><button class="btn" data-cmd="person-toggle:${esc(p.id)}">${p.active === false ? "启用" : "停用"}</button></div>` : ""}</div>`; }).join("")}</div>` : `<div class="empty">还没有登记任何真人。组员可在下方“注册新账号”；组长可在管理端添加及关联成员。</div>`}</div>

    <div class="card" style="margin-top:14px"><h3>数据里出现、尚未登记的人<small>${disc.length} 位 · 只显示代号与任务上下文</small></h3>${disc.length ? `<table><thead><tr><th style="width:120px">代号</th><th>本周任务</th><th style="width:120px">指纹</th><th style="width:110px"></th></tr></thead><tbody>${disc.map((d) => `<tr><td>${av(s, d.code)} <b>${esc(d.code)}</b></td><td><div class="chips">${ctx(d.code)}</div></td><td class="mono">${esc(d.key)}</td><td>${canEdit ? `<button class="btn" data-cmd="person-claim:${esc(d.key)}:${esc(d.code)}">登记为真人</button>` : ""}${ro.length && canEdit ? `<select class="pf-link" data-key="${esc(d.key)}"><option value="">关联到已有…</option>${ro.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select>` : ""}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">数据里的人都已登记。</div>`}</div>

    <p class="mono" style="margin-top:10px">原理：指纹 = SHA-256("helix-person:" + 原名) 前 12 位。服务端在出快照前把原名替换为名册显示名或代号，前端、导出的快照文件、2.00 动效里都看不到原名。</p>`; } },



  data: { title: "项目文件", icon: "data", badge: () => 0, render: dataView },



  evidence: { title: "证据骨架", icon: "evidence", render: (s) => {

    const k = s.skeleton || {};

    if (s.harness?.kind === "local") return head(s, "证据骨架") + `<div class="card" style="margin-bottom:14px"><h3>研究问题</h3><p style="margin:0">${esc(s.project?.question || "（未填写）")}</p><p class="mono">研究对象 ${esc(s.project?.target || "—")}，结论上限 ${esc(ceilLabel(s.project?.claim_ceiling))}</p></div>

    <div class="grid g2"><div class="card"><h3>本项目红线<small>写进机器校对</small></h3>${(s.sprint?.red_lines || []).map((r) => `<div class="pair" style="grid-template-columns:auto 1fr;cursor:default"><span>⛔</span><div>${esc(r)}</div></div>`).join("") || `<div class="empty">没有设置红线</div>`}</div>

    <div class="card"><h3>来源见证<small>项目数据文件 SHA-256</small></h3><table><tbody>${(s.witnesses || []).map((w, i) => `<tr data-sel="witness:${i}"><td>✓</td><td>${esc(w.label)}</td><td class="mono">${short(w.sha256, 12)}</td></tr>`).join("") || `<tr><td>还没有数据文件 <button class="link" data-view="data">去添加</button></td></tr>`}</tbody></table></div></div>`;

    return head(s, "证据骨架") + `<div class="card" style="margin-bottom:14px"><h3>研究问题</h3><p style="margin:0">${esc(s.project?.question)}</p><p class="mono">方向 ${esc(s.project?.direction)} · 更新 ${fmtTs(s.project?.updated_at)}</p></div>

    <div class="grid" style="grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr)"><div class="card"><h3>发现阶段关卡 D0–D6</h3><table><thead><tr><th>关卡</th><th>状态</th><th>迁移</th><th>证据</th></tr></thead><tbody>${(k.gates || []).map((g) => `<tr data-sel="gate:${g.id}"><td><b>${g.id}</b></td><td>${esc(g.raw_status)}</td><td>${esc(g.migration_status)}</td><td>${esc(g.evidence_count)}</td></tr>`).join("")}</tbody></table>

    <h3 style="margin-top:14px">验证序列</h3><div class="chips">${(k.validation?.sequence || []).map((x) => `<span class="chip na">${esc(x)}</span>`).join("")}</div></div>

    <div class="card"><h3>V4 异常队列<small>可播种为 G–C 任务条目</small></h3><table><thead><tr><th style="width:150px">编号 / 优先级</th><th>对象</th></tr></thead><tbody>${(k.exceptions || []).map((e) => `<tr data-sel="exception:${esc(e.task_id)}"><td style="white-space:nowrap"><b>${esc(e.task_id)}</b><div class="sub">${esc(e.priority)}</div></td><td>${esc(e.object)}<div class="sub">${esc(e.category)}，${esc(e.target)}；旧状态 ${esc(e.status)}</div></td></tr>`).join("")}</tbody></table></div></div>

    <div class="grid g2" style="margin-top:14px"><div class="card"><h3>四周计划红线<small>写进机器校对</small></h3>${(s.sprint?.red_lines || []).map((r) => `<div class="pair" style="grid-template-columns:auto 1fr;cursor:default"><span>⛔</span><div>${esc(r)}</div></div>`).join("")}</div>

    <div class="card"><h3>来源见证<small>逐文件 SHA-256</small></h3><table><tbody>${(s.witnesses || []).map((w, i) => `<tr data-sel="witness:${i}"><td>${w.found ? "✓" : "✕"}</td><td>${esc(w.label)}</td><td class="mono">${short(w.sha256, 12)}</td></tr>`).join("")}</tbody></table></div></div>`; } },



  models: { title: "模型工坊", icon: "models", render: (s) => {

    const md = s.models || {}, roles = md.roles || {};

    const roleName = { drafter: "起草者", critic: "批评者（异源）", extractor: "抽取者", ligase: "连接酶 · 汇总" };

    const fam = (r) => String(r || "").split("/")[0];

    return head(s, "模型工坊", `<button class="btn" disabled title="helixd 阶段开放">＋ 添加厂商</button>`) + `<div class="grid g4">${(md.providers || []).map((p) => `<div class="card"><h3>${esc(p.id)}<small>${esc(p.kind)}</small></h3><div class="chips">${(p.models || []).map((m) => `<span class="chip">${esc(m)}</span>`).join("")}</div><p class="mono">密钥：${esc(p.key || p.base_url || "无")}</p><span class="pill">未探测</span></div>`).join("")}</div>

    <div class="grid g2" style="margin-top:14px"><div class="card"><h3>角色分配<small>按角色配，不是全局一个模型</small></h3><table><thead><tr><th>角色</th><th>模型</th><th>规则</th></tr></thead><tbody>${Object.entries(roles).map(([k, v]) => `<tr data-sel="agent:${k}"><td><b>${roleName[k] || k}</b></td><td class="mono">${esc(v)}</td><td>${k === "critic" ? (fam(v) !== fam(roles.drafter) ? `<span class="pill ok">异源 ✓</span>` : `<span class="pill bad">与起草者同源</span>`) : ""}</td></tr>`).join("")}</tbody></table>

    <p class="mono">预算：每周 ¥${esc(md.rules?.budget_week_cny)} · 超限 ${esc(md.rules?.on_exceed)}（不偷偷换便宜模型）· 非幂等失败 → 结果未知，等真人</p></div>

    <div class="card"><h3>换模型先考试<small>参考 Inspect AI：考题 / 解题器 / 评分器</small></h3><p>考卷 = 已经真人配对、交叉核一致的碱基（当前 <b>${s.metrics?.duplex ?? 0}</b> 道）。指标：互补率、Brier 校准分、红线触发率、每百条成本（元）。</p><div class="empty">${(s.metrics?.duplex ?? 0) < 20 ? `考卷不足 20 道，暂不出成绩（避免小样本误判）` : "可开考"}</div></div></div>`; } },



  ledger: { title: "账本与复现", icon: "ledger", render: (s) => {

    const t = s.ledger?.tail || [];

    return head(s, "账本与复现") + `<div class="card"><h3>哈希链<small>只追加 · 真人事件拒绝机器身份 · 链头 ${short(s.ledger?.head, 14)}</small></h3><div class="chain">${t.map((e, i) => `${i ? '<span class="lnk"></span>' : ""}<div class="blk ${e.actor_kind === "human" ? "h" : e.actor_kind === "system" ? "s" : ""}" data-sel="event:${e.seq}"><b>${evTag(e.type)}</b>${esc(e.actor)}<div class="mono">#${e.seq} · ${short(e.hash, 8)}</div></div>`).join("")}</div></div>

    <div class="grid g3" style="margin-top:14px"><div class="card"><h3>校验</h3><dl class="kv"><dt>状态</dt><dd>${s.ledger?.ok == null ? "未知（连接断开，无法校验）" : s.ledger.ok ? "✓ 链完整" : "✕ " + esc(s.ledger?.problem)}</dd><dt>事件数</dt><dd>${s.ledger?.count}</dd><dt>链头</dt><dd class="mono">${esc(s.ledger?.head)}</dd></dl></div>

    <div class="card"><h3>端粒检查点</h3><p class="mono">只证明过程完整，不等于关卡通过</p>${s.harness?.writable ? `<button class="btn pri" data-cmd="hx-checkpoint">盖章（真人署名）</button>` : `<button class="btn" disabled title="${esc(s.harness?.why || "")}">盖章（账本只读）</button>`}</div>

    <div class="card"><h3>复现包</h3><p>快照 JSON = 本页全部数据，可离线再打开（设置 → 导入快照文件）。</p><button class="btn pri" data-cmd="export">⬇ 导出当前快照</button></div></div>`; } },



  settings: { title: "设置", icon: "settings", render: (s, ui, prefs, reg) => head(s, "设置") + `<div class="grid g2">

    <div class="card"><h3>数据端口<small>换数据 = 换端口，视图不改</small></h3>${Object.values(reg.ports).map((p) => `<label class="pair" style="grid-template-columns:auto 1fr"><input type="radio" name="port" value="${p.id}" ${prefs.port === p.id ? "checked" : ""}><div><b>${esc(p.label)}</b><small>${p.id}</small></div></label>`).join("")}<label class="pair" style="grid-template-columns:auto 1fr"><input type="radio" name="port" value="auto" ${prefs.port === "auto" ? "checked" : ""}><div><b>自动</b><small>连接本机服务；断开时只读显示最后一次真实数据并自动重连，不会切到演示</small></div></label><p class="mono">数据格式：helix.snapshot.v1 · 当前：${esc(portLabel(s.port?.kind))} ${esc(s.port?.detail || "")}</p></div>

    <div class="card"><h3>动效包<small>数据格式与 2.00 版 helix_payload 一致</small></h3>${Object.values(reg.motion).map((p) => `<label class="pair" style="grid-template-columns:auto 1fr"><input type="radio" name="motion" value="${p.id}" ${prefs.motion === p.id ? "checked" : ""}><div><b>${esc(p.label)}</b><small>${p.id}</small></div></label>`).join("")}

    <label class="pair" style="grid-template-columns:auto 1fr"><input type="checkbox" id="motionOn" ${prefs.motionOn ? "checked" : ""}><div><b>动效开启</b><small>关闭后所有卡片与螺旋静止（系统“减少动态效果”始终优先）</small></div></label>

    <label class="pair" style="grid-template-columns:auto 1fr"><input type="checkbox" id="night" ${prefs.theme === "night" ? "checked" : ""}><div><b>夜间主题</b><small>只换配色，布局不变</small></div></label></div></div>` },

};

views.research = { title: "项目研究资料", icon: "evidence", render: researchView };

views.tools = { title: "科学工具", icon: "crew", render: toolsView };

export const ORDER = ["cockpit", "inbox", "data", "research", "tools", "runs", "crew", "people", "evidence", "models", "ledger"];

