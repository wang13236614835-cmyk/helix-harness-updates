import { esc, short } from "../core/store.js?v=3301";

export { esc, short };

export const MEMBER_COLORS = ["#fcd34d", "#6ee7b7", "#7dd3fc", "#f9a8d4", "#c4b5fd", "#fdba74"];

export const ROLE_LABEL = { lead: "组长", primary: "主核", cross: "交叉核", both: "主核 + 交叉核", observer: "观察者" };

// 成员 = 名册里登记的真人（显示名由使用者自己填）+ 数据里出现但未登记的人（只显示代号“成员A…”）

export function roster(s) { return (s?.people?.roster || []); }

export function members(s) {

  const out = [], seen = new Set();

  for (const p of roster(s)) if (p.active !== false && !seen.has(p.name)) { seen.add(p.name); out.push({ name: p.name, person: p, registered: true }); }

  for (const d of (s?.people?.discovered || [])) if (!seen.has(d.code)) { seen.add(d.code); out.push({ name: d.code, key: d.key, registered: false }); }

  for (const m of (s?.sprint?.members || [])) if (!seen.has(m)) { seen.add(m); out.push({ name: m, registered: false }); }

  return out;

}

export function personOf(s, name) { return roster(s).find((p) => p.name === name); }

export function memberColor(s, name) {

  const p = personOf(s, name); if (p?.color) return p.color;

  const all = members(s).map((m) => m.name); const i = all.indexOf(name);

  return MEMBER_COLORS[(i < 0 ? String(name).length : i) % MEMBER_COLORS.length];

}

const initial = (n) => { const t = String(n); if (t === "组长") return "长"; const m = t.match(/^(?:成员|组员)(.)/); return m ? m[1] : t[0]; };

export const av = (s, name) => name ? `<span class="av ${personOf(s, name) ? "" : "anon"}" title="${esc(name)}" style="background:${memberColor(s, name)}">${esc(initial(name))}</span>` : "";

export const workload = (s, name) => { const it = s?.items || []; return { primary: it.filter((b) => b.owner === name).length, cross: it.filter((b) => b.cross_reviewer === name).length, todo: it.filter((b) => (b.owner === name && canon(b.status) === "open") || (b.cross_reviewer === name && b.needs_cross && canon(b.status) !== "open" && !b.cross)).length, tasks: (s?.sprint?.pairs || []).filter((t) => t.owner === name || t.cross_reviewer === name) }; };

// 条目状态统一成内核的词表；别名（过去式、旧写法、大小写）都归一，界面各处都先过 canon()。

const ALIAS = { pending: "open", todo: "open", new: "open", defer: "deferred", hold: "deferred", held: "deferred", paused: "deferred", postponed: "deferred",

  complemented: "complement", accepted: "complement", accept: "complement", agreed: "complement", revised: "revise", mismatched: "mismatch", rejected: "mismatch",

  dispute: "disputed", conflict: "disputed", audited: "audit", sampled: "audit" };

export const canon = (st) => { const k = String(st ?? "").trim().toLowerCase(); return ALIAS[k] || k; };

export const isHold = (b) => canon(b?.status) === "deferred" || canon(b?.verdict) === "deferred" || String(b?.decision || "").toUpperCase() === "HOLD";

export const STATUS = { open: ["待配对", "warn"], audit: ["待抽检", "warn"], auto: ["已先行", ""], deferred: ["暂缓", ""], complement: ["互补", "ok"], revise: ["已修正", "info"], mismatch: ["错配", "bad"], disputed: ["交叉核不一致", "bad"] };

export const statusPill = (b) => { const st = canon(b.status), crossWait = b.needs_cross && st !== "open" && !b.cross && st !== "disputed"; const [t, c] = crossWait ? ["待交叉核", "warn"] : (STATUS[canon(b.status)] || [b.status, ""]); return `<span class="pill ${c}">${esc(t)}</span>`; };

export const bondTag = (b) => b.kind === "task" ? `<span class="tag tk">任务</span>` : b.bond === "GC" ? `<span class="tag gc">G–C</span>` : `<span class="tag at">A–T</span>`;

export const GATE_C = { NOT_EVALUATED: "na", PASS: "ok", PASSED: "ok", BLOCKED: "hi" };

export const fmtTs = (s) => s ? String(s).replace("T", " ").slice(0, 16) : "—";

export const crossState = (b) => b.cross ? (b.cross.agree ? "一致" : "不一致") : (b.needs_cross ? (canon(b.status) === "open" ? "待主核" : "待交叉核") : "—");



// —— 中文显示词表：界面只显示中文，原始代码放在 title 里便于对照账本 / 日志 ——

export const DEC_LABEL = { PASS: "通过", REJECTED: "否决", OUT_OF_DOMAIN: "超出适用域", NEEDS_EVIDENCE: "需要更多证据", HOLD: "暂缓", UNKNOWN: "未知" };

export const decLabel = (d) => d ? (DEC_LABEL[String(d).toUpperCase()] || String(d)) : "";

export const CEIL_LABEL = { EXPLORATORY_ONLY: "仅探索性", HYPOTHESIS_SUPPORTED: "假设有支持", VALIDATED_FINDING: "已验证发现" };

export const ceilLabel = (c) => c ? (CEIL_LABEL[String(c).toUpperCase()] || String(c)) : "—";

export const PORT_LABEL = { http: "本机服务", live: "本机服务", "helix-shell-server": "本机服务", demo: "演示数据", offline: "连接断开", file: "快照文件", local: "本机项目" };

export const portLabel = (k) => PORT_LABEL[String(k || "").toLowerCase()] || (k ? String(k) : "未连接");

export const PROJECT_STATUS = { DISCOVERY_BLOCKED: "发现阶段受阻", DISCOVERY_READY: "发现阶段就绪", DISCOVERY: "发现阶段", LOCAL: "本机项目", DRAFT: "草稿", ACTIVE: "进行中", UNKNOWN: "未知" };

export const projectStatusLabel = (s) => PROJECT_STATUS[String(s || "").toUpperCase()] || (s ? String(s) : "—");

export const ACTOR_KIND = { human: "真人", agent: "智能体", ai: "AI", system: "系统", model: "模型" };

export const actorKind = (k) => ACTOR_KIND[String(k || "").toLowerCase()] || (k ? String(k) : "");

export const STEP_STATE = { done: "完成", requested: "已请求", failed: "失败", unknown: "结果未知", acknowledged: "已确认", pending: "待执行", running: "执行中", skipped: "跳过", blocked: "被拦下" };

export const stepState = (k) => STEP_STATE[k] || k || "";

// 账本事件：先查整词，再按“对象.动作”拆开组合，未知词保留原样（不会显示空白）

const EV_FULL = { "write.attested": "写入证明", "plan.frozen": "计划冻结", "base.paired": "初审写入", "base.crossed": "复核写入", "base.proposed": "AI 提出条目",

  "ledger.genesis": "账本创建", genesis: "账本创建", checkpoint: "检查点盖章", "run.started": "运行开始", "run.completed": "运行完成", "run.failed": "运行失败" };

const EV_OBJ = { base: "条目", plan: "计划", run: "运行", step: "步骤", turn: "回合", sprint: "冲刺", project: "项目", file: "文件", files: "文件", people: "成员", person: "成员",

  ledger: "账本", write: "写入", model: "模型", models: "模型", auth: "登录", gate: "关卡", summary: "汇总", checkpoint: "检查点", core: "内核", review_session: "评审会话", port: "数据端口" };

const EV_ACT = { started: "开始", completed: "完成", failed: "失败", unknown: "结果未知", acknowledged: "已确认", resumed: "恢复", cancelled: "取消", interrupted: "中断",

  requested: "请求", done: "完成", proposed: "提出", paired: "初审", crossed: "复核", frozen: "冻结", seeded: "播种", created: "创建", updated: "更新", added: "添加", removed: "删除",

  attested: "证明", opened: "打开", closed: "关闭", blocked: "被拦下", connected: "已连接", reconnected: "已重连", disconnected: "已断开", error: "出错", genesis: "创建" };

export const evLabel = (t) => { const k = String(t || ""); if (EV_FULL[k]) return EV_FULL[k]; const parts = k.split(/[.:]/); if (parts.length < 2) return EV_OBJ[k] || EV_ACT[k] || k;

  const o = parts.slice(0, -1).map((x) => EV_OBJ[x] || x).join("·"), a = EV_ACT[parts[parts.length - 1]] || parts[parts.length - 1]; return o + a; };

export const evTag = (t) => `<span title="${esc(t)}">${esc(evLabel(t))}</span>`;

// 界面事件流（AG-UI 事件名）

export const UI_EV = { RUN_STARTED: "界面启动", RUN_FINISHED: "运行结束", RUN_ERROR: "出错", STATE_SNAPSHOT: "收到快照", STATE_DELTA: "数据变化", CUSTOM: "操作", TEXT_MESSAGE: "消息", TOOL_CALL: "工具调用" };



// —— 0.5.2 职能分工：职能目录 + 分工看板 + 按职能自动分派

export const DEFAULT_DUTIES = [

  { key: "lead", label: "主导", desc: "定方向、拍板、对外汇报" }, { key: "data", label: "数据", desc: "整理数据、清洗、统计与作图" },

  { key: "lit", label: "文献", desc: "检索文献、读原文、核对出处" }, { key: "dock", label: "对接", desc: "分子对接 / 计算建模（可改名，例如“联络”）" },

  { key: "liaison", label: "外联", desc: "联系老师、合作方、实验平台" }, { key: "write", label: "写作", desc: "写报告、论文、申请材料" },

];

export const dutiesOf = (s) => (s?.plan?.duties?.length ? s.plan.duties : s?.plan ? [] : DEFAULT_DUTIES);

export const dutyLabel = (s, k) => (dutiesOf(s).find((d) => d.key === k)?.label || k || "");

const DUTY_CLS = new Set(["lead", "data", "lit", "dock", "liaison", "write"]);

export const dutyChip = (s, k, list) => { if (!k) return `<span class="duty none">未定职能</span>`; const d = (list || dutiesOf(s)).find((x) => x.key === k); return `<span class="duty ${DUTY_CLS.has(k) ? "d-" + k : ""}" title="${esc(d?.desc || "")}">${esc(d?.label || k)}</span>`; };

// 每人：职能、手上的任务（作主核 / 交叉核）、待办数、下一步；外加职能覆盖与分工提醒

export function dutyBoard(s) {

  const plan = s?.plan || {}, duties = dutiesOf(s), tasks = plan.tasks || [], items = s?.items || [];

  const cur = s?.sprint?.current_week;

  const rows = (plan.members || []).map((m) => {

    const mine = tasks.filter((t) => t.owner === m.name || t.cross_reviewer === m.name).map((t) => ({ id: t.task_id, text: t.deliverable, duty: t.duty || "", week: t.week, as: t.owner === m.name ? "主核" : "交叉核" }));

    const todo = items.filter((b) => (canon(b.status) === "open" && b.owner === m.name) || (canon(b.status) === "paired" && b.cross_reviewer === m.name));

    const nb = todo[0];

    return { name: m.name, duties: m.duties || [], tasks: mine.sort((a, b) => (a.week === cur ? -1 : 0) - (b.week === cur ? -1 : 0) || a.week - b.week), todo: todo.length,

      next: nb ? { t: (canon(nb.status) === "open" ? "主核判定：" : "交叉核复核：") + String(nb.text || "").slice(0, 40), sel: "base:" + nb.base_id, d: nb.task_id } : null,

      prim: mine.filter((t) => t.as === "主核").length, cross: mine.filter((t) => t.as === "交叉核").length, disp: items.filter((b) => canon(b.status) === "disputed" && (b.owner === m.name || b.cross_reviewer === m.name)).length };

  });

  const warn = [];

  const has = new Map(rows.map((r) => [r.name, new Set(r.duties)]));

  const cover = duties.map((d) => ({ ...d, people: rows.filter((r) => r.duties.includes(d.key)).map((r) => r.name), tasks: tasks.filter((t) => t.duty === d.key).length, week: tasks.filter((t) => t.duty === d.key && t.week === cur).length }));

  for (const c of cover) if (c.tasks && !c.people.length) warn.push(`「${c.label}」有 ${c.tasks} 条任务，但没有成员负责这项职能`);

  const leadDuties = new Set((s?.plan?.members || []).filter((m) => m.role === "lead").flatMap((m) => m.duties || []));  // 组长统筹职能可按周轮值
  for (const t of tasks) if (t.duty && !leadDuties.has(t.duty) && has.has(t.owner) && !has.get(t.owner).has(t.duty)) warn.push(`${t.task_id} 属于「${dutyLabel(s, t.duty)}」，主核 ${t.owner} 没有这项职能`);

  for (const r of rows) if (!r.duties.length) warn.push(`${r.name} 还没有分配职能`);

  return { rows, warn: warn.slice(0, 12), cover, duties };

}

// —— 0.6 组内打卡：每条任务的主核 / 交叉核本人打卡，进度直接可见（可同步到云端）

export const CK_STATE = { started: ["已开始", "info"], doing: ["进行中", "warn"], done: ["已完成", "ok"], blocked: ["受阻", "bad"] };

export const checkinOf = (s, tid) => s?.team?.checkins?.[tid] || null;

export const ckMain = (c) => c?.owner || c?.latest || null;

export const meName = (s, prefs) => s?.auth?.me?.name || prefs?.me || "";

export const canCheckin = (s, t, prefs) => { const me = meName(s, prefs); return !!me && !!t && (t.owner === me || t.cross_reviewer === me); };

export const ckPill = (s, tid) => { const l = ckMain(checkinOf(s, tid)); if (!l) return `<span class="pill ck-none">未打卡</span>`; const [t, k] = CK_STATE[l.state] || [l.state, ""];

  return `<span class="pill ${k}" title="${esc(fmtTs(l.ts))} · ${esc(l.actor || "")}">${esc(t)} ${l.state === "blocked" ? "" : (l.progress ?? 0) + "%"}</span>`; };

export const ckBar = (s, tid) => { const l = ckMain(checkinOf(s, tid)), p = Math.max(0, Math.min(100, +(l?.progress || 0))); return `<span class="ckbar ${l ? "ck-" + l.state : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p}" title="进度 ${p}%"><i style="width:${p}%"></i></span>`; };

// 按职能建议主核 / 交叉核：主核 = 有该职能且负载最少；交叉核优先同职能的另一人，其次组长，再次负载最少

export function suggestPair(members, tasks, duty, skip = -1) {

  const names = members.filter((m) => m.name).map((m) => m.name); if (names.length < 2) return null;

  const load = new Map(names.map((n) => [n, 0]));

  tasks.forEach((t, i) => { if (i === skip) return; if (load.has(t.owner)) load.set(t.owner, load.get(t.owner) + 1); if (load.has(t.cross_reviewer)) load.set(t.cross_reviewer, load.get(t.cross_reviewer) + 0.5); });

  const by = (arr) => [...arr].sort((a, b) => load.get(a) - load.get(b) || names.indexOf(a) - names.indexOf(b));

  const hasD = (n, d) => (members.find((m) => m.name === n)?.duties || []).includes(d);

  const cand = duty ? names.filter((n) => hasD(n, duty)) : [];

  const owner = by(cand.length ? cand : names)[0];

  const rest = names.filter((n) => n !== owner);

  const same = rest.filter((n) => duty && hasD(n, duty)), lead = rest.filter((n) => hasD(n, "lead"));

  const cross = by(same.length ? same : lead.length ? lead : rest)[0];

  return { owner, cross_reviewer: cross, matched: cand.length > 0 };

}

