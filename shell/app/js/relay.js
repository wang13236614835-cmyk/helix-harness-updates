// Helix 3.3 双螺旋接力：把“任务 → 实操 → 主核 → 交叉核 → 通过”做成一条一眼看懂、一步能动手的线。
// 这里放接力的状态判断、下一步推荐、首页/任务页/详情的渲染，以及周报；数据全部来自现有快照，不新增接口。
import { store, esc } from "./core/store.js?v=3301";
import { av, canon, isHold, meName, checkinOf, ckPill, ckBar, canCheckin, decLabel, fmtTs } from "./views/common.js?v=3301";
import { fileName } from "./presentation.js?v=3301";

let ctx = { toast: () => {}, go: () => {}, select: () => {}, run: () => {} };
export function initRelay(c) { ctx = { ...ctx, ...c }; }

// 阶段：主线 待播种 → 待主核 → 待交叉核 → 已通过；旁路 分歧 / 暂缓 / 等上游 / 未通过
export const STAGE = {
  unseeded: { label: "待播种", short: "未开始", tone: "" },
  primary: { label: "待主核", short: "待主核", tone: "warn" },
  cross: { label: "待交叉核", short: "待复核", tone: "warn" },
  passed: { label: "已通过", short: "已通过", tone: "ok" },
  disputed: { label: "分歧", short: "有分歧", tone: "bad" },
  hold: { label: "暂缓", short: "暂缓", tone: "" },
  blocked: { label: "等上游", short: "等上游", tone: "" },
  failed: { label: "未通过", short: "未通过", tone: "bad" },
};
const RANK = { disputed: 2, primary: 3, cross: 4, unseeded: 5, hold: 6, blocked: 7, failed: 8, passed: 9 };
const md = (d) => String(d || "").slice(5);

function basesByTask(s) {
  const m = new Map();
  for (const b of s.items || []) if (b.task_id) { if (!m.has(b.task_id)) m.set(b.task_id, []); m.get(b.task_id).push(b); }
  return m;
}

// 任务状态以服务端 gates.tasks 为准：只有“主核 PASS + 交叉核一致”才算通过（与周进度同一套规则）。
export function taskStater(s, by = basesByTask(s)) {
  const G = s.gates?.tasks || {};
  return (t) => {
    const tb = by.get(t.task_id) || [], seeds = tb.filter((b) => b.kind === "task"), g = G[t.task_id]?.state && String(G[t.task_id].state).toLowerCase();
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
}

function actBaseOf(stage, bs) {
  const open = bs.find((b) => canon(b.status) === "open");
  const needX = bs.find((b) => b.needs_cross && !b.cross && canon(b.status) !== "open" && canon(b.status) !== "disputed");
  const disp = bs.find((b) => canon(b.status) === "disputed");
  return (stage === "primary" ? open : stage === "cross" ? needX : stage === "disputed" ? disp || needX : null) || bs.find((b) => b.kind === "task") || bs[0] || null;
}

// 轮到“我”动手：我是主核且待主核、我是交叉核且待交叉核、或我所在任务有分歧
export const isMineTurn = (f) => (f.stage === "primary" && f.role === "owner") || (f.stage === "cross" && f.role === "cross") || (f.stage === "disputed" && f.mine);

const memo = new WeakMap();
// scope：week 本周 / mine 与我有关(全部周) / all 全部计划。快照对象每次刷新都是新的，所以按快照缓存是安全的。
export function flows(s, prefs, scope = "week") {
  if (!s) return [];
  const me = meName(s, prefs), key = scope + "|" + me;
  let m = memo.get(s); if (!m) memo.set(s, (m = new Map()));
  if (m.has(key)) return m.get(key);
  const sp = s.sprint || {}, pairs = sp.pairs || [], plan = s.plan?.tasks || [];
  const planBy = new Map(plan.map((t) => [t.task_id, t])), pairBy = new Map(pairs.map((t) => [t.task_id, t]));
  const roleOf = (t) => (me ? (t.owner === me ? "owner" : t.cross_reviewer === me ? "cross" : "") : "") || t.my_role || "";
  const all = plan.length ? plan : pairs;
  const src = scope === "all" ? all : scope === "mine" ? all.filter(roleOf) : (pairs.length ? pairs : plan.filter((t) => +t.week === +sp.current_week));
  const by = basesByTask(s), stater = taskStater(s, by);
  const out = src.filter((t) => t.task_id).map((raw) => {
    const t = { ...raw, ...(planBy.get(raw.task_id) || {}), ...(pairBy.get(raw.task_id) || {}) };
    let stage = stater(t); if (stage === "open") stage = "primary";
    const bs = by.get(t.task_id) || [], role = roleOf(t);
    return { t, id: t.task_id, stage, role, mine: !!role, holder: stage === "primary" ? t.owner : stage === "cross" ? t.cross_reviewer : "", bs, act: actBaseOf(stage, bs) };
  });
  const rank = (f) => (isMineTurn(f) ? (f.stage === "disputed" ? 0 : 1) : RANK[f.stage] ?? 9);
  out.sort((a, b) => rank(a) - rank(b) || String(a.t.end || "").localeCompare(String(b.t.end || "")) || String(a.id).localeCompare(String(b.id)));
  m.set(key, out);
  return out;
}
export const flowOf = (s, prefs, tid) => flows(s, prefs, "all").find((f) => f.id === tid) || flows(s, prefs, "week").find((f) => f.id === tid) || null;
export const countBy = (F) => { const c = {}; for (const f of F) c[f.stage] = (c[f.stage] || 0) + 1; return c; };

export function holderText(f) {
  const t = f.t;
  if (f.stage === "primary") return f.role === "owner" ? "轮到你主核" : `等 ${t.owner || "负责人"} 主核`;
  if (f.stage === "cross") return f.role === "cross" ? "轮到你交叉核" : `等 ${t.cross_reviewer || "复核人"} 交叉核`;
  return { unseeded: "还没播种，任务尚未进入接力", passed: "双人核对完成", disputed: "两人结论不一致，需要当面对一下", hold: "已暂缓，等待重新启动", blocked: "等上游任务通过", failed: "关卡未通过或超出范围" }[f.stage] || "";
}

// 某项任务“现在能做什么”。pri 标记的是主动作：首页“下一步”、任务卡主按钮、N 键都用它。
export function actionsFor(s, f, prefs, { detail = true } = {}) {
  const tid = f.id, out = [], sel = f.act ? "base:" + f.act.base_id : "task:" + tid;
  const runs = (s.human_runs || []).filter((r) => r.task_id === tid);
  const running = runs.find((r) => r.status === "running"), done = runs.some((r) => r.status === "completed");
  if (f.stage === "disputed" && f.mine) out.push({ label: "去对齐分歧", sel, pri: 1 });
  else if (f.stage === "primary" && f.role === "owner") {
    if (running) out.push({ label: "继续实操", cmd: "hr-record:" + running.run_id, pri: 1 }, { label: "写核对结论", sel });
    else if (!done) out.push({ label: "开始实操", cmd: "hr-start:" + tid, pri: 1 }, { label: "直接写核对结论", sel });
    else out.push({ label: "写核对结论", sel, pri: 1 });
  } else if (f.stage === "cross" && f.role === "cross") out.push({ label: "做交叉核", sel, pri: 1 });
  if (s.team && s.harness?.writable && f.stage !== "passed" && canCheckin(s, f.t, prefs)) out.push({ label: "打卡", cmd: "ck-open:" + tid });
  if (detail) out.push({ label: "详情", sel: "task:" + tid });
  return out;
}
const btn = (a, sm) => `<button class="btn${sm ? " sm" : ""}${a.pri ? " pri" : ""}" ${a.sel ? `data-sel="${esc(a.sel)}"` : `data-cmd="${esc(a.cmd)}"`}>${esc(a.label)}</button>`;

// 一项任务已有的结果：主核结论、交叉核意见、实操记录、成果文件（来自打卡与实操工件，按指纹去重）
export function results(s, tid) {
  const bs = (s.items || []).filter((b) => b.task_id === tid), seed = bs.find((b) => b.decision) || bs[0] || null;
  const files = new Map(), add = (f) => { if (f?.sha256 && !files.has(f.sha256)) files.set(f.sha256, f.name || f.sha256.slice(0, 12)); };
  for (const h of checkinOf(s, tid)?.history || []) (h.files || []).forEach(add);
  const runs = (s.human_runs || []).filter((r) => r.task_id === tid);
  for (const r of runs) (r.outputs || []).forEach(add);
  const x = seed?.cross || null;
  return { decision: seed?.decision ? decLabel(seed.decision) : "", note: seed?.note || "", cross: x ? { who: x.reviewer, agree: !!x.agree, note: x.note || "" } : null, runs, files: [...files].map(([sha, name]) => ({ sha, name })) };
}
function resultsHtml(s, tid) {
  const R = results(s, tid);
  if (!R.decision && !R.cross && !R.files.length && !R.runs.length) return "";
  const line = [];
  if (R.decision) line.push(`<span><b>主核结论</b> ${esc(R.decision)}${R.note ? `<em>${esc(String(R.note).slice(0, 80))}</em>` : ""}</span>`);
  if (R.cross) line.push(`<span><b>交叉核</b> ${esc(R.cross.who || "")} ${R.cross.agree ? "一致" : "不一致"}${R.cross.note ? `<em>${esc(String(R.cross.note).slice(0, 80))}</em>` : ""}</span>`);
  if (R.runs.length) line.push(`<span><b>实操</b> ${R.runs.length} 次${R.runs.some((r) => r.status === "running") ? "，有一次进行中" : ""}</span>`);
  return `<div class="rl-res">${line.join("")}${R.files.length ? `<div class="rl-files">${R.files.slice(0, 6).map((f) => `<button class="chip" data-cmd="file-view:${esc(f.sha)}" title="${esc(f.sha)}">📎 ${esc(fileName(f.name))}</button>`).join("")}${R.files.length > 6 ? `<span class="sub">…另有 ${R.files.length - 6} 个</span>` : ""}</div>` : ""}</div>`;
}

// 一项任务走到哪了：主核 → 交叉核 → 通过（分歧/未通过在“交叉核”一格标红）
const STEP_AT = { unseeded: -1, blocked: -1, primary: 0, hold: 0, cross: 1, disputed: 1, failed: 1, passed: 3 };
function miniTrack(f) {
  const at = STEP_AT[f.stage] ?? -1, bad = f.stage === "disputed" || f.stage === "failed";
  return `<ol class="rl-steps${bad ? " bad" : ""}" aria-label="${esc(STAGE[f.stage].label)}">${["主核", "交叉核", "通过"].map((n, i) => `<li class="${i < at ? "done" : i === at ? "cur" : ""}">${n}</li>`).join("")}</ol>`;
}
const ckMini = (s, tid) => (s.team ? `<div class="rl-ck">${ckPill(s, tid)}${ckBar(s, tid)}</div>` : "");

// 两条链交叉的连接线（琥珀=真人链，绿=AI 链）
const LINK = `<svg class="rl-link" viewBox="0 0 44 20" aria-hidden="true"><path class="h" d="M0 4C22 4 22 16 44 16"/><path class="a" d="M0 16C22 16 22 4 44 4"/></svg>`;

function track(F, active, cmd, seedable) {
  const c = countBy(F), total = F.length, pass = c.passed || 0;
  const main = [["unseeded", "待播种"], ["primary", "待主核"], ["cross", "待交叉核"], ["passed", "已通过"]].filter(([k]) => k !== "unseeded" || c.unseeded || seedable);
  const side = [["disputed", "分歧"], ["hold", "暂缓"], ["blocked", "等上游"], ["failed", "未通过"]].filter(([k]) => c[k]);
  const node = ([k, t]) => `<button class="rl-node st-${k}${active === k ? " on" : ""}" data-cmd="${cmd}:${k}" aria-pressed="${active === k}"><b>${c[k] || 0}</b><span>${t}</span></button>`;
  const pct = total ? Math.round((100 * pass) / total) : 0;
  return `<section class="rl-track" aria-label="接力进度"><div class="rl-line">${main.map(node).join(LINK)}</div>${side.length ? `<div class="rl-side">${side.map(node).join("")}</div>` : ""}<div class="rl-prog" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" title="已通过 ${pass}/${total}"><i style="width:${pct}%"></i></div></section>`;
}

const weekLabel = (s) => { const sp = s.sprint || {}, w = (sp.weeks || []).find((x) => x.week === sp.current_week) || {}; return String(w.track_label || "").replace(/^第\s*\d+\s*周[：:\s·]*/, ""); };
const daysText = (sp) => (sp.days_left == null ? "" : sp.days_left === 0 ? "今天截止" : sp.days_left < 0 ? "本周期已结束" : `还剩 ${sp.days_left} 天`);

// ---------- 首页：接力台 ----------
function heroModel(s, prefs, F) {
  const me = meName(s, prefs), H = s.harness || {}, sp = s.sprint || {};
  if (H.seedable) return { kind: "seed", eyebrow: "第一步", title: `把第 ${H.seed_week} 周任务放进接力`, sub: "播种后，每项任务进入“主核 → 交叉核”配对，由真人逐项核对。", acts: [{ label: `播种第 ${H.seed_week} 周任务`, cmd: "hx-seed", pri: 1 }] };
  if (H.can_takeover) return { kind: "seed", eyebrow: "第一步", title: "在本机建立账本", sub: "建立后才能记录主核与交叉核的结论。", acts: [{ label: "在本机建立账本", cmd: "hx-takeover", pri: 1 }] };
  if (!me) return { kind: "wait", eyebrow: "先登录", title: "登录后显示轮到你的事", sub: "配对与交叉核只认登录的人。", acts: [{ label: "登录", cmd: "auth-login", pri: 1 }] };
  const turn = F.filter(isMineTurn);
  if (turn.length) {
    const f = turn[0], t = f.t;
    return { kind: "act", eyebrow: `轮到你了 · 共 ${turn.length} 件`, title: `${t.task_id} · ${t.deliverable || ""}`, sub: `${f.role === "cross" ? "你是交叉核" : f.stage === "disputed" ? "你和搭档的结论不一致" : "你是主核"} · 搭档 ${f.role === "cross" ? t.owner : t.cross_reviewer} ${f.stage === "cross" ? "已核完，等你" : "在等你"}${t.end ? ` · 截止 ${md(t.end)}` : ""}`, acts: actionsFor(s, f, prefs, { detail: false }), more: turn.slice(1, 4), f };
  }
  const open = F.filter((f) => f.stage !== "passed");
  if (!F.length) return { kind: "wait", eyebrow: "本周没有任务", title: "这一周还没有安排", sub: "新建或导入任务后，接力就开始了。", acts: H.kind === "local" ? [{ label: "编辑计划", cmd: "hx-wizard-edit", pri: 1 }] : [] };
  if (!open.length) return { kind: "done", eyebrow: "本周接力完成", title: `${F.length} 项任务全部通过`, sub: `${daysText(sp)}。可以生成周报，或进入下一周。`, acts: [{ label: "生成周报", cmd: "rl-report", pri: 1 }, ...(H.writable ? [{ label: "盖章检查点", cmd: "hx-checkpoint" }] : [])] };
  const hold = new Map(); for (const f of open) { const k = f.holder || (f.stage === "unseeded" ? "未播种" : STAGE[f.stage].label); hold.set(k, (hold.get(k) || 0) + 1); }
  return { kind: "wait", eyebrow: "暂时不需要你动手", title: `球在别人手上 · 还有 ${open.length} 项没通过`, sub: [...hold].slice(0, 4).map(([k, n]) => `${k} ${n}`).join("　"), acts: [{ label: "看全部队列", cmd: "rl-filter:all", pri: 1 }, { label: "生成周报", cmd: "rl-report" }] };
}
function heroHtml(s, h) {
  const more = (h.more || []).map((f) => `<button class="link" data-sel="task:${esc(f.id)}">${esc(f.id)} ${esc(String(f.t.deliverable || "").slice(0, 18))}</button>`).join("　");
  return `<section class="rl-hero" data-kind="${h.kind}"><div class="rl-eyebrow">${esc(h.eyebrow)}</div><h2 class="rl-title">${esc(h.title)}</h2><p class="rl-sub">${esc(h.sub)}</p>${h.acts?.length ? `<div class="rl-acts">${h.acts.map((a) => btn(a)).join("")}</div>` : ""}${more ? `<p class="rl-more">接下来：${more}</p>` : ""}</section>`;
}

function row(s, f, ui, prefs) {
  const t = f.t, st = STAGE[f.stage], turn = isMineTurn(f), a = turn ? actionsFor(s, f, prefs, { detail: false }).find((x) => x.pri) : null;
  return `<div class="rl-row st-${f.stage}${f.mine ? " me" : ""}${ui.sel === "task:" + f.id ? " on" : ""}" data-sel="task:${esc(f.id)}" tabindex="0"><div class="rl-pair">${av(s, t.owner)}${av(s, t.cross_reviewer)}</div><div class="rl-main"><div class="rl-t"><b>${esc(f.id)}</b><span>${esc(t.deliverable || "")}</span></div><div class="rl-who">主核 ${esc(t.owner || "—")} ⇄ 交叉核 ${esc(t.cross_reviewer || "—")}${f.mine ? `<em>${turn ? "轮到我" : "与我有关"}</em>` : ""}</div>${ckMini(s, f.id)}</div><div class="rl-state"><span class="pill ${st.tone}">${st.label}</span><small>${esc(holderText(f))}</small></div><div class="rl-end">${t.end ? `截止 ${esc(md(t.end))}` : ""}${a ? btn(a, true) : ""}</div></div>`;
}

export function relayHome(s, ui, prefs, X) {
  const sp = s.sprint || {}, F = flows(s, prefs, "week"), c = countBy(F), H = s.harness || {};
  const me = meName(s, prefs), mineOpen = F.filter((f) => f.mine && f.stage !== "passed");
  const filter = ui.rlFilter || (mineOpen.length ? "mine" : "all"), stage = ui.rlStage || "";
  const others = F.filter((f) => !f.mine && f.stage !== "passed");
  let list = F.filter((f) => (stage ? f.stage === stage : f.stage !== "passed") && (stage ? true : filter === "mine" ? f.mine : filter === "others" ? !f.mine : true));
  const passed = F.filter((f) => f.stage === "passed");
  const seg = [["mine", `我的 ${mineOpen.length}`], ["all", `全部 ${F.length - passed.length}`], ["others", `别人手上 ${others.length}`]];
  const lede = `第 ${sp.current_week ?? "?"} 周${weekLabel(s) ? " · " + esc(weekLabel(s)) : ""}${daysText(sp) ? " · " + daysText(sp) : ""}。已通过 <b>${c.passed || 0}</b>/${F.length} 项。`;
  return `<div class="rl-wrap"><div class="vh"><div><h1>接力台</h1><p class="lede">${lede}</p></div><span class="sp"></span><button class="btn" data-cmd="rl-report">周报</button><button class="btn" data-cmd="dock">智能体控制台</button></div>
    <div class="rl-top">${heroHtml(s, heroModel(s, prefs, F))}<div class="stage" id="stage"><div class="stage-tools"><div class="seg" id="motionSeg"></div></div></div></div>
    ${X.researchProgressCard ? X.researchProgressCard(s) : ""}
    ${track(F, stage, "rl-stage", H.seedable)}
    <section class="block rl-queue"><header><h2>接力队列</h2><span>${stage ? `筛选：${STAGE[stage].label}` : me ? `轮到你的排在最前` : `登录后，与你有关的会排在最前`}</span><span class="sp"></span>${X.teamHead ? X.teamHead(s) : ""}${stage ? `<button class="btn sm" data-cmd="rl-stage:${stage}">清除筛选 ×</button>` : `<div class="seg">${seg.map(([k, t]) => `<button data-cmd="rl-filter:${k}" class="${filter === k ? "on" : ""}">${t}</button>`).join("")}</div>`}</header>
      <div class="rl-list">${list.map((f) => row(s, f, ui, prefs)).join("") || `<div class="empty">${F.length ? (stage ? "这一阶段没有任务。" : filter === "mine" ? "你这边没有待办。可以切到“全部”看看别人的进度。" : "没有符合的任务。") : `本周没有任务。${H.kind === "local" ? `<button class="link" data-cmd="hx-wizard-edit">编辑计划</button>` : ""}`}</div>`}</div>
      ${!stage && passed.length ? `<details class="rl-done"><summary>已通过 ${passed.length} 项</summary><div class="rl-list">${passed.map((f) => row(s, f, ui, prefs)).join("")}</div></details>` : ""}</section>
    ${s.plan && X.board ? `<details class="block rl-fold"><summary><h2>本周分工：谁在干什么</h2><span>按职能分派；点人名或任务看详情</span></summary>${X.board(s, ui, prefs, true)}</details>` : ""}
    <p class="more"><button class="link" data-cmd="welcome">查看四周安排、红线和使用提示</button></p></div>`;
}

// ---------- 任务页：任务与成果 ----------
export function taskCard(s, f, prefs) {
  const t = f.t, st = STAGE[f.stage], acts = actionsFor(s, f, prefs), req = [t.human_work && `<h4>亲手做什么</h4><p>${esc(t.human_work)}</p>`, t.acceptance && `<h4>验收要求</h4><p>${esc(t.acceptance)}</p>`, t.depends_on?.length && `<p class="sub">前置任务：${t.depends_on.map(esc).join("、")}</p>`].filter(Boolean).join("");
  return `<article class="rl-card st-${f.stage}${f.mine ? " me" : ""}"><header><span class="pill ${st.tone}">${st.label}</span><h3>${esc(t.deliverable || "待补充任务名称")}</h3>${t.end ? `<span class="rl-due">截止 ${esc(md(t.end))}</span>` : ""}</header>
    ${miniTrack(f)}<div class="rl-people">${av(s, t.owner)} 主核 <b>${esc(t.owner || "待分配")}</b> ⇄ ${av(s, t.cross_reviewer)} 交叉核 <b>${esc(t.cross_reviewer || "待分配")}</b>${f.mine ? `<em>${isMineTurn(f) ? "轮到我" : "与我有关"}</em>` : ""}<span class="sp"></span><small>${esc(f.id)}${t.week ? " · 第 " + esc(t.week) + " 周" : ""}</small></div>
    <p class="rl-hold">${esc(holderText(f))}</p>${ckMini(s, f.id)}${resultsHtml(s, f.id)}<div class="rl-acts">${acts.map((a) => btn(a, false)).join("")}</div>${req ? `<details class="rl-req"><summary>任务要求与验收</summary>${req}</details>` : ""}</article>`;
}

export function relayTasks(s, ui, prefs) {
  const person = s.auth?.me, scope = ui.taskScope || (person?.role === "lead" ? "week" : "mine"), stage = ui.tkStage || "";
  const F = flows(s, prefs, scope), list = stage ? F.filter((f) => f.stage === stage) : F;
  const options = [["week", "本周任务"], ["mine", "我的任务"], ["all", "全部计划"]];
  return `<div class="vh"><div><h1>任务与成果</h1><p class="lede">任务沿“主核 → 交叉核 → 通过”前进，结论和成果留在卡片上。</p></div><span class="sp"></span><button class="btn" data-cmd="rl-report">周报</button></div>
    <div class="task-tabs">${options.map(([id, label]) => `<button class="btn ${scope === id ? "pri" : ""}" data-cmd="task-scope:${id}">${label}</button>`).join("")}<span class="sub">共 ${F.length} 项${stage ? `，筛选“${STAGE[stage].label}” ${list.length} 项` : ""}</span></div>
    ${F.length ? track(F, stage, "rl-tkstage", false) : ""}<div class="rl-cards">${list.map((f) => taskCard(s, f, prefs)).join("") || `<div class="card empty">${F.length ? "这一阶段没有任务。" : "暂时没有分配给你的任务。可以查看全部计划，或联系组长安排。"}</div>`}</div>`;
}

// ---------- 右侧详情：任务顶部的接力条 ----------
export function relayBlock(s, tid) {
  const f = flowOf(s, store.prefs, tid); if (!f) return "";
  const acts = actionsFor(s, f, store.prefs, { detail: false });
  return `<div class="rl-insp st-${f.stage}">${miniTrack(f)}<p class="rl-hold">${esc(holderText(f))}</p>${acts.length ? `<div class="rl-acts">${acts.map((a) => btn(a, true)).join("")}</div>` : ""}${resultsHtml(s, tid)}</div>`;
}

// ---------- 周报（连接：把本周接力的结果连成一份可贴出去的摘要） ----------
export function weeklyReport(s, prefs) {
  const sp = s.sprint || {}, F = flows(s, prefs, "week"), c = countBy(F), name = s.project?.name || s.project?.target || "Helix 项目";
  const L = [`# ${name} · 第 ${sp.current_week ?? "?"} 周接力周报`, "", `${weekLabel(s) ? weekLabel(s) + "；" : ""}${daysText(sp) || ""}  `, `已通过 ${c.passed || 0}/${F.length} 项；待主核 ${c.primary || 0}，待交叉核 ${c.cross || 0}${c.disputed ? `，分歧 ${c.disputed}` : ""}${c.hold ? `，暂缓 ${c.hold}` : ""}${c.unseeded ? `，未播种 ${c.unseeded}` : ""}。`, ""];
  const item = (f) => {
    const R = results(s, f.id), t = f.t;
    return `- **${f.id}** ${t.deliverable || ""} — 主核 ${t.owner || "—"} / 交叉核 ${t.cross_reviewer || "—"}${R.decision ? ` · 结论：${R.decision}` : ""}${R.cross ? ` · 交叉核${R.cross.agree ? "一致" : "不一致"}` : ""}${R.files.length ? `\n  - 成果：${R.files.map((x) => `${fileName(x.name)}（sha256:${x.sha.slice(0, 12)}）`).join("；")}` : ""}`;
  };
  for (const [title, keys] of [["已通过", ["passed"]], ["进行中", ["primary", "cross"]], ["分歧与受阻", ["disputed", "hold", "blocked", "failed"]], ["未播种", ["unseeded"]]]) {
    const g = F.filter((f) => keys.includes(f.stage)); if (!g.length) continue;
    L.push(`## ${title}（${g.length}）`, "", ...g.map((f) => item(f) + (keys.length > 1 || title === "分歧与受阻" ? `  \n  状态：${STAGE[f.stage].label}` : "")), "");
  }
  L.push(`> 由 Helix 生成于 ${fmtTs(new Date().toISOString())}；成果文件以 SHA-256 前 12 位标注，可在“文件”页按指纹找到原件。`);
  return L.join("\n");
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* 桌面窗口里剪贴板接口可能被拒，退回选中复制 */ }
  const ta = Object.assign(document.createElement("textarea"), { value: text }); ta.style.cssText = "position:fixed;opacity:0"; document.body.appendChild(ta); ta.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; } ta.remove(); return ok;
}
let dlg = null;
function closeReport() { dlg?.remove(); dlg = null; }
function openReport() {
  const s = store.snapshot; if (!s) return; closeReport();
  const text = weeklyReport(s, store.prefs);
  dlg = document.createElement("div"); dlg.className = "hxm"; dlg.id = "rl-report-dlg";
  dlg.innerHTML = `<div class="hxm-box rl-rep"><h3>本周周报<small>Markdown，可直接粘贴到 Notion、飞书或邮件</small></h3><textarea id="rl-report-text" rows="16" spellcheck="false">${esc(text)}</textarea><div class="row-end"><button class="btn" data-cmd="rl-report-close">关闭</button><button class="btn" data-cmd="rl-report-dl">下载 .md</button><button class="btn pri" data-cmd="rl-report-copy">复制</button></div></div>`;
  dlg.addEventListener("mousedown", (e) => { if (e.target === dlg) closeReport(); });
  document.body.appendChild(dlg);
}

// ---------- 命令 ----------
function runPrimary(f) {
  const a = actionsFor(store.snapshot, f, store.prefs, { detail: false }).find((x) => x.pri);
  if (!a) return ctx.select("task:" + f.id);
  return a.sel ? ctx.select(a.sel) : ctx.run(a.cmd);
}
export function relayCmd(cmd) {
  if (!cmd || !cmd.startsWith("rl-")) return false;
  const [op, arg] = [cmd.slice(0, cmd.indexOf(":") < 0 ? undefined : cmd.indexOf(":")), cmd.indexOf(":") < 0 ? "" : cmd.slice(cmd.indexOf(":") + 1)];
  const toggle = (k) => store.ui_({ [k]: store.ui[k] === arg ? "" : arg });
  if (op === "rl-stage") { toggle("rlStage"); return true; }
  if (op === "rl-tkstage") { toggle("tkStage"); return true; }
  if (op === "rl-filter") { store.ui_({ rlFilter: arg, rlStage: "" }); return true; }
  if (op === "rl-next") {
    const s = store.snapshot, f = s && flows(s, store.prefs, "week").find(isMineTurn);
    if (!f) { ctx.toast("现在没有轮到你的事"); return true; }
    ctx.go("cockpit"); runPrimary(f); return true;
  }
  if (op === "rl-report") { openReport(); return true; }
  if (op === "rl-report-close") { closeReport(); return true; }
  if (op === "rl-report-copy") { const t = dlg?.querySelector("#rl-report-text")?.value || ""; copyText(t).then((ok) => ctx.toast(ok ? "周报已复制" : "复制失败，请在文本框里手动全选复制")); return true; }
  if (op === "rl-report-dl") {
    const t = dlg?.querySelector("#rl-report-text")?.value || "", a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([t], { type: "text/markdown;charset=utf-8" })), download: `helix-周报-第${store.snapshot?.sprint?.current_week ?? "x"}周.md` });
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); return true;
  }
  return false;
}
