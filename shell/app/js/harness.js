// 0.4：真 harness 的写操作。所有写入走 POST + X-Helix 头；真人写账本必须署名并勾选确认。

import { store, esc } from "./core/store.js?v=3301";

import { bus } from "./core/bus.js?v=3301";

import { members, roster, DEFAULT_DUTIES, suggestPair, CK_STATE, checkinOf, ckMain, fmtTs } from "./views/common.js?v=3301";

import { draft, kb } from "./views/forms.js?v=3301";

import { token } from "./views/auth.js?v=3301";

import { openLogin } from "./agents.js?v=3301";

import { openFile, initFileView } from "./fileview.js?v=3301";
import { gate } from "./ports/index.js?v=3301";
import { uploadFiles, openRegister, initUploads } from "./uploads.js?v=3301";



let ctx = { toast: () => {}, go: () => {}, select: () => {} };

export function initHarness(c) { ctx = c; initFileView(c); initUploads({ api, refresh, token, snapshot: () => store.snapshot, canWrite: () => gate.live, who: () => store.prefs?.me || "", toast: (m) => ctx.toast(m), openLogin }); }

const $ = (q, r = document) => r.querySelector(q);



export async function api(path, body, raw = false) {

  const h = { "X-Helix": "1", "X-Helix-Token": token() }; if (!raw) h["Content-Type"] = "application/json";

  const r = await fetch(path, { method: "POST", headers: h, body: raw ? body : JSON.stringify(body || {}) });

  const j = await r.json().catch(() => ({ ok: false, error: "服务没有返回 JSON（" + r.status + "）" }));

  if (!j.ok) { const e = new Error(j.error || r.status); e.auth = !!j.auth; if (j.auth && /登录/.test(j.error || "")) setTimeout(() => openLogin(), 50); throw e; }

  return j;

}

export async function refresh() { const s = await (await fetch("/api/snapshot", { cache: "no-store", headers: { "X-Helix-Token": token() } })).json(); store.set({ snapshot: s }); return s; }

const fail = (e) => ctx.toast("没有写入：" + (e.message || e));



// —— 表单草稿：每个控件 data-d="键"，刷新不丢

document.addEventListener("input", onDraft); document.addEventListener("change", onDraft);

function onDraft(e) {

  const el = e.target; const k = el.dataset?.d; if (!k) return;

  if (k.endsWith(".ref")) { const base = k.slice(0, -3); draft[base + "refs"] = [...document.querySelectorAll(`[data-d="${CSS.escape(k)}"]:checked`)].map((x) => x.value); }

  else draft[k] = el.type === "checkbox" ? el.checked : el.value;

  if (e.type === "change" && (el.type === "radio" || el.type === "checkbox" || el.tagName === "SELECT")) store.ui_({});

}



// —— 写账本

async function pair(id) {

  const b = (store.snapshot.items || []).find((x) => x.base_id === id); const k = kb(b);

  const refs = [...(draft[k + "refs"] || b.agent_refs || []), ...String(draft[k + "refx"] || "").split(/[;；\n]/).map((x) => x.trim()).filter(Boolean)];

  try {

    await api("/api/pair", { base_id: id, verdict: draft[k + "verdict"], decision: draft[k + "decision"] || (b.kind === "task" ? "NEEDS_EVIDENCE" : ""), note: draft[k + "note"] || "", revised_text: draft[k + "revised"] || "", source_refs: refs, quote: draft[k + "quote"] || "", id_checked: !!draft[k + "idck"], confirmed: !!draft[k + "ok"] });

    Object.keys(draft).filter((x) => x.startsWith(k)).forEach((x) => delete draft[x]);

    bus.emit("CUSTOM", { name: "base.paired", base: id }); ctx.toast("已写入账本：主核配对"); await refresh();

  } catch (e) { fail(e); }

}

async function cross(id) {

  const b = (store.snapshot.items || []).find((x) => x.base_id === id); const k = kb(b);

  try {

    await api("/api/cross", { base_id: id, agree: draft[k + "agree"] === "yes", note: draft[k + "cnote"] || "", confirmed: !!draft[k + "cok"] });

    Object.keys(draft).filter((x) => x.startsWith(k)).forEach((x) => delete draft[x]);

    bus.emit("CUSTOM", { name: "base.crossed", base: id }); ctx.toast("已写入账本：交叉核"); await refresh();

  } catch (e) { fail(e); }

}

async function seed() {

  try { const j = await api("/api/seed", {}); ctx.toast(`已播种 ${j.count} 条任务，等真人主核`); await refresh(); ctx.go("inbox"); } catch (e) { fail(e); }

}

async function takeover() {

  if (!confirm("在 Helix 自己的 data 目录里为这个项目建立账本？\nV4 原件与 Workbench 2.00 的文件仍然只读，不会被修改。")) return;

  try { await api("/api/takeover", {}); ctx.toast("账本已建立，可以播种本周任务了"); await refresh(); } catch (e) { fail(e); }

}

export async function upload(fileList, after) { return uploadFiles(fileList, after); }

function pickFiles(after, accept = "") {

  const inp = Object.assign(document.createElement("input"), { type: "file", multiple: !accept, accept });

  inp.onchange = () => after(inp.files); inp.click();

}

// 拖拽上传：数据页或向导第 5 步

document.addEventListener("dragover", (e) => { if (store.ui.view === "data" || wz?.step === 5) { e.preventDefault(); document.body.classList.add("dragging"); } });

document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });

document.addEventListener("drop", (e) => { document.body.classList.remove("dragging"); if (store.ui.view === "data" || wz?.step === 5) { e.preventDefault(); upload(e.dataTransfer.files, wz ? (r) => { wz.files.push(...r); drawWz(); } : null); } });



// —— 项目菜单

let menu = null;

function projectMenu() {

  if (menu) return closeMenu();

  const s = store.snapshot; const anchor = $(".title .proj"); const r = anchor.getBoundingClientRect();

  menu = document.createElement("div"); menu.className = "pmenu"; menu.style.left = r.left + "px"; menu.style.top = r.bottom + 6 + "px";

  menu.innerHTML = `<div class="pm-h">项目</div>${(s?.projects || []).map((p) => `<button data-cmd="hx-switch:${esc(p.id)}" class="${p.active ? "on" : ""}"><b>${esc(p.name)}</b><small>${p.kind === "builtin" ? (s.generic_mode?"空白入口":"已识别来源") : (s.auth?.account_scope === "cloud" ? "云端项目" : "项目")} · ${esc(p.id)}</small></button>`).join("")}

    <hr><button data-cmd="project-import"><b>导入项目</b><small>读取本机文件夹或完整项目ZIP</small></button>${s?.harness?.kind==="local"?'<button data-cmd="project-export"><b>导出项目模板与资料</b><small>完整资料与计划，不带账号和签署</small></button>':""}${s?.harness?.can_adopt ? `<button data-cmd="hx-adopt"><b>接入已有项目</b><small>沿用已读取的资料与计划，加入项目列表</small></button>` : ""}<button data-cmd="hx-wizard"><b>＋ 新建项目</b><small>从头开始引导：成员、计划、数据、红线</small></button>${s?.harness?.kind === "local" ? `<button data-cmd="hx-wizard-edit"><b>编辑当前项目</b><small>改计划、成员、红线</small></button>` : ""}<button data-cmd="go:data"><b>数据与文件</b><small>添加数据，查看指纹</small></button>`;

  document.body.appendChild(menu);

  setTimeout(() => document.addEventListener("click", outside, true));

}

function outside(e) { if (menu && !menu.contains(e.target)) { closeMenu(); if (e.target.closest(".title .proj")) e.stopPropagation(); } }

function closeMenu() { menu?.remove(); menu = null; document.removeEventListener("click", outside, true); }

async function adoptPreview() {
  closeMenu();
  try {
    const p = await api("/api/projects/source-preview", {});
    ckModal(`<h3>接入已有项目<small>${esc(p.name)}</small></h3><p>已读取 ${p.task_count} 条任务、${p.source_file_count} 份来源文件。接入后可编辑计划、添加文件并运行任务；原始资料继续从本机读取。</p><div class="ck-foot"><button class="btn" data-cmd="ck-close">取消</button><button class="btn pri" data-cmd="hx-adopt-save" data-hash="${esc(p.snapshot_hash)}">加入项目列表</button></div>`);
  } catch(e) { fail(e); }
}
async function adoptSave() {
  const hash = ck.querySelector('[data-hash]')?.dataset.hash;
  try { const j=await api("/api/projects/adopt",{snapshot_hash:hash});ckClose();await refresh();ctx.go("cockpit");ctx.toast(j.existing ? "已打开接入的项目" : "已加入项目列表，可以编辑计划与运行任务"); } catch(e) { fail(e); }
}

async function switchProject(id) { closeMenu(); try { await api("/api/projects/active", { id }); await refresh(); ctx.toast("已切换项目"); ctx.go("cockpit"); } catch (e) { fail(e); } }



// —— 引导向导：从头开始一轮人机协同

const STEPS = ["项目", "成员", "计划", "红线", "数据", "开始"];

const CEIL = [["EXPLORATORY_ONLY", "探索性（默认）", "只能说“提示/可能”，不能说已验证"], ["HYPOTHESIS_SUPPORTED", "假设有支持", "有独立证据支持，但未经实验验证"], ["VALIDATED_FINDING", "已验证发现", "需要预注册与实验数据，慎选"]];

let wz = null;

const monday = () => { const d = new Date(); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d.toISOString().slice(0, 10); };

export function openWizard(mode = "new") {

  const s = store.snapshot, plan = s?.plan;

  const ro = roster(s).filter((p) => p.active !== false).map((p) => ({ name: p.name, role: p.role || "both" }));

  if (mode === "edit" && plan) {

    const w = plan.weeks || [];

    wz = { mode, step: 1, files: [], f: { name: plan.name, target: plan.target || "", question: plan.question || "", claim_ceiling: plan.claim_ceiling, members: (plan.members || []).map((m) => ({ ...m, duties: [...(m.duties || [])] })), duties: (plan.duties || DEFAULT_DUTIES).map((d) => ({ ...d })), start: w[0]?.start || monday(), n_weeks: w.length || 4, weeks: w.map((x) => ({ theme: x.theme })), tasks: (plan.tasks || []).map((t) => ({ ...t })), red_lines: (plan.red_lines || []).join("\n") } };

  } else {

    wz = { mode: "new", step: 1, files: [], f: { name: "", target: "", question: "", claim_ceiling: "EXPLORATORY_ONLY", members: (ro.length >= 2 ? ro : [...ro, { name: "", role: "both" }, { name: "", role: "both" }].slice(0, Math.max(2, ro.length))).map((m, i) => ({ ...m, duties: i ? [] : ["lead"] })), duties: DEFAULT_DUTIES.map((d) => ({ ...d })), start: monday(), n_weeks: 4, weeks: ["准备与数据整理", "分析", "复核", "收尾"].map((theme) => ({ theme })), tasks: [], red_lines: "" } };

  }

  drawWz();

}

function closeWz() { $(".wizard")?.remove(); wz = null; }

const val = (q) => $(q)?.value ?? "";

export const deps = (v) => (Array.isArray(v) ? v : String(v || "").split(/[,，;；、\s]+/)).map((x) => String(x).trim()).filter(Boolean);

function readWz() {

  const f = wz.f, st = wz.step;

  if (st === 1) Object.assign(f, { name: val("#wz-name").trim(), target: val("#wz-target").trim(), question: val("#wz-q").trim(), claim_ceiling: $("input[name=wz-ceil]:checked")?.value || f.claim_ceiling });

  if (st === 2) {

    const dt = [...document.querySelectorAll(".wz-dt")]; if (dt.length || $(".wz-dts")) f.duties = dt.map((r) => ({ key: r.dataset.key || "", label: r.querySelector(".dt-l").value.trim(), desc: r.querySelector(".dt-d").value.trim() })).filter((d) => d.label);

    f.members = [...document.querySelectorAll(".wz-mem")].map((r) => ({ name: r.querySelector("input").value.trim(), role: r.querySelector("select").value, duties: [...r.querySelectorAll(".wz-du input:checked")].map((i) => i.value) }));

  }

  if (st === 3) {

    f.start = val("#wz-start") || f.start; f.n_weeks = Math.max(1, Math.min(26, +val("#wz-n") || f.n_weeks));

    f.weeks = [...document.querySelectorAll(".wz-wk input")].map((i) => ({ theme: i.value.trim() }));

    // P0：在原任务上合并修改，保留依赖、日期、状态等界面上没有的字段

    f.tasks = [...document.querySelectorAll(".wz-task")].map((r) => { const old = f.tasks[+r.dataset.i] || {}; return { ...old, task_id: r.dataset.id || old.task_id || "", week: +r.querySelector(".t-w").value, deliverable: r.querySelector(".t-d").value.trim(), acceptance: r.querySelector(".t-a").value.trim(), owner: r.querySelector(".t-o").value, cross_reviewer: r.querySelector(".t-c").value, duty: r.querySelector(".t-u")?.value || "", depends_on: deps(r.querySelector(".t-p")?.value) }; });

  }

  if (st === 4) f.red_lines = val("#wz-red");

}

function check() {

  const f = wz.f, st = wz.step;

  if (st === 1 && !f.name) return "请填写项目名称";

  if (st === 2) { const n = f.members.filter((m) => m.name); if (new Set(n.map((m) => m.name)).size < 2) return "至少需要 2 位不同的真人：一位主核，一位交叉核"; if (n.some((m) => /^(ai|agent|system|harness)([-:].*)?$/i.test(m.name))) return "成员必须是真人，AI 不算成员"; }

  if (st === 3) { const t = f.tasks.filter((x) => x.deliverable); if (!t.length) return "至少写一条任务（或从表格导入）"; const bad = t.find((x) => !x.owner || x.owner === x.cross_reviewer); if (bad) return `“${bad.deliverable.slice(0, 16)}”的主核与交叉核需要是两位不同的人`; }

  return "";

}

async function next(dir) {

  readWz();

  if (dir > 0) { const m = check(); if (m) return ctx.toast(m); }

  if (dir > 0 && wz.step === 4) return save();

  if (dir > 0 && wz.step === 6) return closeWz();

  wz.step = Math.max(1, Math.min(6, wz.step + dir)); drawWz();

}

// 编辑已有计划时：保存前逐项列出任务与依赖的变化，确认后才写入

function planDiff(oldTasks, newTasks) {

  const key = (t) => t.task_id || "", O = new Map(oldTasks.map((t) => [key(t), t])), N = new Map(newTasks.filter((t) => t.task_id).map((t) => [key(t), t]));

  const out = [];

  for (const [id, t] of O) if (!N.has(id)) out.push(`删除任务 ${id}（${String(t.deliverable || "").slice(0, 20)}）`);

  for (const t of newTasks) if (!t.task_id) out.push(`新增任务（${String(t.deliverable || "").slice(0, 20)}）`);

  for (const [id, t] of N) { const o = O.get(id); if (!o) { out.push(`新增任务 ${id}`); continue; } const a = deps(o.depends_on).join(","), b = deps(t.depends_on).join(","); if (a !== b) out.push(`${id} 前置任务：${a || "无"} → ${b || "无"}`); if (+o.week !== +t.week) out.push(`${id} 周次：${o.week} → ${t.week}`); if ((o.owner || "") !== (t.owner || "") || (o.cross_reviewer || "") !== (t.cross_reviewer || "")) out.push(`${id} 主核/交叉核：${o.owner}/${o.cross_reviewer} → ${t.owner}/${t.cross_reviewer}`); }

  return out;

}

// 职能的变化（编辑时一起列出）

function dl(oldP, f) {

  const out = [], lab = (k) => (f.duties || []).find((d) => d.key === k)?.label || (oldP.duties || []).find((d) => d.key === k)?.label || k;

  const O = new Map((oldP.duties || []).map((d) => [d.key, d.label])), N = new Map((f.duties || []).filter((d) => d.key).map((d) => [d.key, d.label]));

  for (const [k, l] of O) if (!N.has(k)) out.push(`删除职能「${l}」`); else if (N.get(k) !== l) out.push(`职能改名：「${l}」→「${N.get(k)}」`);

  for (const d of f.duties || []) if (!d.key || !O.has(d.key)) out.push(`新增职能「${d.label}」`);

  const om = new Map((oldP.members || []).map((m) => [m.name, (m.duties || []).join(",")]));

  for (const m of f.members.filter((x) => x.name)) { const a = om.get(m.name) ?? "", b = (m.duties || []).join(","); if (om.has(m.name) && a !== b) out.push(`${m.name} 的职能：${a.split(",").filter(Boolean).map(lab).join("、") || "无"} → ${(m.duties || []).map(lab).join("、") || "无"}`); }

  const ot = new Map((oldP.tasks || []).map((t) => [t.task_id, t.duty || ""]));

  for (const t of f.tasks) if (t.task_id && ot.has(t.task_id) && ot.get(t.task_id) !== (t.duty || "")) out.push(`${t.task_id} 职能：${lab(ot.get(t.task_id)) || "无"} → ${lab(t.duty) || "无"}`);

  return out;

}

async function save() {

  const f = wz.f;

  wz.err = "";

  const signature = ms => JSON.stringify(ms.filter(m=>m.name&&m.active!==false&&m.role!=="observer").map(m=>[m.name,m.role||"both",[...(m.duties||[])].sort()]).sort());
  if (wz.mode === "edit" && signature(store.snapshot?.plan?.members||[]) !== signature(f.members)) {
    try {
      const j=await api("/api/team/suggest",{mode:"balanced",members:f.members,tasks:f.tasks});
      for(const r of j.rows) Object.assign(f.tasks[r.i],{owner:r.owner,cross_reviewer:r.cross_reviewer});
    } catch(e) { fail(e); return; }
  }

  if (wz.mode === "edit") { const ch = [...planDiff(store.snapshot?.plan?.tasks || [], f.tasks.filter((t) => t.deliverable)), ...dl(store.snapshot?.plan || {}, f)]; if (ch.length && !confirm("这次保存会改变计划结构：\n\n" + ch.slice(0, 20).join("\n") + (ch.length > 20 ? `\n…另有 ${ch.length - 20} 项` : "") + "\n\n确认保存？已冻结的计划需要组长重新冻结后才能继续运行。")) return; }

  const body = { name: f.name, target: f.target, question: f.question, claim_ceiling: f.claim_ceiling, members: f.members.filter((m) => m.name), duties: f.duties, start: f.start, n_weeks: f.n_weeks, weeks: f.weeks.slice(0, f.n_weeks), tasks: f.tasks.filter((t) => t.deliverable), red_lines: f.red_lines };

  try {

    const j = await api(wz.mode === "edit" ? "/api/projects/update" : "/api/projects", body);

    (j.warnings || []).forEach((w) => ctx.toast("提示：" + w));

    await refresh();

    if (wz.mode === "edit") { closeWz(); return ctx.toast("计划已保存"); }

    if (!store.prefs.me || !f.members.some((m) => m.name === store.prefs.me)) store.pref({ me: f.members.find((m) => m.name)?.name || "" });

    wz.created = j.project.id; wz.step = 5; drawWz();

  } catch (e) { if (/前置任务|依赖/.test(e.message || "")) { wz.err = e.message; wz.step = 3; drawWz(); } else fail(e); }

}

function addRows(rows) {

  readWz(); const f = wz.f; const names = new Set(f.members.map((m) => m.name));

  f.duties = f.duties || DEFAULT_DUTIES.map((d) => ({ ...d }));

  const newKey = (lbl) => { let i = f.duties.length + 1, k; do { k = "c" + i++; } while (f.duties.some((d) => d.key === k)); f.duties.push({ key: k, label: String(lbl).slice(0, 12), desc: "" }); return k; };

  for (const r of rows) {

    for (const n of [r.owner, r.cross_reviewer]) if (n && !names.has(n)) { names.add(n); f.members.push({ name: n, role: "both", duties: [] }); }

    const u = String(r.duty || "").trim(); let dk = "";

    if (u) { dk = (f.duties.find((d) => d.key === u.toLowerCase() || d.label === u) || {}).key || newKey(u); const om = f.members.find((m) => m.name === r.owner); if (om) { om.duties = om.duties || []; if (!om.duties.includes(dk)) om.duties.push(dk); } }

    r.duty = dk;

    const wk = parseInt(String(r.week || "1").replace(/[^0-9]/g, "")) || 1; if (wk > f.n_weeks) { f.n_weeks = Math.min(26, wk); }

    f.tasks.push({ ...r, task_id: r.task_id || "", week: wk, deliverable: r.deliverable || "", acceptance: r.acceptance || "", owner: r.owner || "", cross_reviewer: r.cross_reviewer || "", depends_on: deps(r.depends_on) });

  }

  f.members = f.members.filter((m) => m.name);

  while (f.weeks.length < f.n_weeks) f.weeks.push({ theme: `第 ${f.weeks.length + 1} 周` });

  drawWz();

}

async function importTable() {

  pickFiles(async (fl) => {

    const file = fl[0]; if (!file) return;

    try { const j = await api(`/api/parse-tasks?name=${encodeURIComponent(file.name)}`, file, true); addRows(j.rows); ctx.toast(`导入 ${j.rows.length} 条任务（识别列：${Object.values(j.columns).join("、")}）`); }

    catch (e) { ctx.toast("导入失败：" + (e.message || e)); }

  }, ".csv,.xlsx,.xlsm,text/csv");

}

function templateCSV() {

  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob(["\ufeff编号,周,职能,交付物,验收,主核,交叉核,前置任务\nT1,1,数据,整理原始数据并写数据字典,字段与来源齐全,张三,李四,\nT2,1,文献,检索并筛选文献,每条有 DOI/PMID,李四,张三,\nT3,2,对接,完成分子对接并记录参数,参数与打分表齐全,王五,张三,T1\nT4,2,外联,联系实验平台确认验证方案,有书面回复,赵六,李四,\nT5,3,写作,写阶段报告初稿,引用都有来源,李四,王五,T2;T3\n"], { type: "text/csv" })), download: "helix-任务模板.csv" }); a.click();

}

function stepHTML() {

  const f = wz.f, st = wz.step, mem = f.members.filter((m) => m.name).map((m) => m.name);

  const opt = (sel) => `<option value="">选择…</option>` + mem.map((n) => `<option ${n === sel ? "selected" : ""}>${esc(n)}</option>`).join("");

  if (st === 1) return `<h2>这个项目要回答什么问题？</h2><p class="wz-p">名称和问题会显示在驾驶舱顶部；结论上限决定 AI 和你们最多能把话说到哪一步，机器校对会按它拦截越界的表述。</p>

    <div class="wz-g2"><label class="fl">项目名称 *<input id="wz-name" maxlength="60" value="${esc(f.name)}" placeholder="例如：PPARα 激动剂老药筛选"></label><label class="fl">研究对象 / 靶点<input id="wz-target" maxlength="120" value="${esc(f.target)}" placeholder="可选"></label></div>

    <label class="fl">研究问题<textarea id="wz-q" rows="3" maxlength="600" placeholder="一句话写清楚要回答的问题，以及怎样算回答了">${esc(f.question)}</textarea></label>

    <div class="fl">结论上限</div><div class="wz-ceil">${CEIL.map(([id, t, d]) => `<label class="${f.claim_ceiling === id ? "on" : ""}"><input type="radio" name="wz-ceil" value="${id}" ${f.claim_ceiling === id ? "checked" : ""}><b>${t}</b><small>${d}</small><code>${id}</code></label>`).join("")}</div>`;

  if (st === 2) { const D = f.duties || [], miss = D.filter((d) => !f.members.some((m) => m.name && (m.duties || []).includes(d.key)));

    return `<h2>谁负责什么？</h2><p class="wz-p">当前 ${new Set(f.members.filter(m=>m.name&&m.active!==false&&m.role!=="observer").map(m=>m.name)).size} 人参与（含组长）。成员增减或职能调整后，保存时会按实际人数重排未开始的任务。组长也承担执行任务，复核由另一位成员分担。观察者不参与分配。</p>

    <div class="wz-mems">${f.members.map((m, i) => `<div class="wz-mem has-du"><span class="n">${i + 1}</span><input maxlength="24" value="${esc(m.name)}" placeholder="名字或代号"><select>${[["both", "主核 + 交叉核"], ["primary", "主核"], ["cross", "交叉核"], ["lead", "组长"], ["observer", "观察者"]].map(([k, t]) => `<option value="${k}" ${m.role === k ? "selected" : ""}>${t}</option>`).join("")}</select><button class="x" data-cmd="wz-mem-del:${i}" title="删除">×</button>

      <div class="wz-du">${D.map((d) => `<label class="${(m.duties || []).includes(d.key) ? "on" : ""}" title="${esc(d.desc || "")}"><input type="checkbox" value="${esc(d.key)}" ${(m.duties || []).includes(d.key) ? "checked" : ""}>${esc(d.label)}</label>`).join("") || `<span class="mut">先在下面的职能目录里加职能</span>`}</div></div>`).join("")}</div>

    ${miss.length ? `<p class="wz-p" style="color:var(--warn-fg)">还没有人负责：${miss.map((d) => esc(d.label)).join("、")}（没有这类任务可以不管）</p>` : ""}

    <button class="btn" data-cmd="wz-mem-add">＋ 再加一位</button>${roster(store.snapshot).filter((p) => !f.members.some((m) => m.name === p.name)).length ? `<p class="wz-p" style="margin-top:12px">名册里已有：${roster(store.snapshot).filter((p) => !f.members.some((m) => m.name === p.name)).map((p) => `<button class="chip" data-cmd="wz-mem-pick:${esc(p.name)}">＋ ${esc(p.name)}</button>`).join(" ")}</p>` : ""}

    <details class="wz-dts" ${wz.dtOpen ? "open" : ""}><summary>职能目录（${D.length} 项，可改名、增删；例如把“对接”改成“联络”）</summary>

      ${D.map((d, i) => `<div class="wz-dt" data-key="${esc(d.key || "")}"><input class="dt-l" maxlength="12" value="${esc(d.label)}" placeholder="职能名"><input class="dt-d" maxlength="80" value="${esc(d.desc || "")}" placeholder="做什么（可选）"><button class="x" data-cmd="wz-dt-del:${i}" title="删除">×</button></div>`).join("")}

      <button class="btn sm" data-cmd="wz-dt-add">＋ 加一项职能</button></details>`; }

  if (st === 3) return `<h2>分几周、每周做什么？</h2><p class="wz-p">每条任务会在当周被“播种”进收件箱：主核先判，交叉核再独立复核。也可以从 CSV / Excel 导入（第一行表头：编号、周、职能、交付物、验收、主核、交叉核、前置任务）。</p>

    <div class="wz-g3"><label class="fl">开始日期<input id="wz-start" type="date" value="${esc(f.start)}"></label><label class="fl">周数<input id="wz-n" type="number" min="1" max="26" value="${f.n_weeks}"></label><div class="fl"><span>&nbsp;</span><div style="display:flex;gap:8px"><button class="btn" data-cmd="wz-import">从表格导入</button><button class="btn" data-cmd="wz-template">下载模板</button></div></div></div>

    <div class="wz-wks">${Array.from({ length: f.n_weeks }, (_, i) => `<label class="wz-wk"><span>第 ${i + 1} 周</span><input maxlength="40" value="${esc(f.weeks[i]?.theme || "")}" placeholder="本周主题"></label>`).join("")}</div>

    <table class="wz-tasks"><thead><tr><th style="width:84px">周</th><th style="width:92px">职能</th><th>交付物（要做出什么）</th><th style="width:26%">验收（怎样算完成）</th><th style="width:118px">主核</th><th style="width:118px">交叉核</th><th style="width:120px">前置任务</th><th style="width:34px"></th></tr></thead><tbody>

    ${f.tasks.map((t, i) => `<tr class="wz-task" data-i="${i}" data-id="${esc(t.task_id || "")}"><td><select class="t-w">${Array.from({ length: f.n_weeks }, (_, w) => `<option value="${w + 1}" ${+t.week === w + 1 ? "selected" : ""}>第${w + 1}周</option>`).join("")}</select></td><td><select class="t-u"><option value="">—</option>${(f.duties || []).map((d) => `<option value="${esc(d.key)}" ${t.duty === d.key ? "selected" : ""}>${esc(d.label)}</option>`).join("")}</select></td><td><input class="t-d" value="${esc(t.deliverable)}" placeholder="例如：整理原始数据并写数据字典"></td><td><input class="t-a" value="${esc(t.acceptance)}" placeholder="例如：字段与来源齐全"></td><td><select class="t-o">${opt(t.owner)}</select></td><td><select class="t-c">${opt(t.cross_reviewer)}</select></td><td><input class="t-p" value="${esc(deps(t.depends_on).join(", "))}" placeholder="${t.task_id ? "如 T1, T2" : "填编号，逗号分隔"}" title="本任务开始前必须通过的任务编号${t.task_id ? "；本任务编号 " + esc(t.task_id) : "；新任务保存后自动编号"}"></td><td><button class="x" data-cmd="wz-task-del:${i}">×</button></td></tr>`).join("")}

    </tbody></table><div style="display:flex;gap:8px"><button class="btn" data-cmd="wz-task-add">＋ 加一条任务</button><button class="btn" data-cmd="wz-coord" title="按项目实际人数、职能与每周负载分配；包含组长，复核轮换">按当前人数重分工</button></div>`;

  if (st === 4) return `<h2>这一轮有哪些不能碰的红线？</h2><p class="wz-p">一行一条。红线会显示在概览页和控制台，AI 起草的内容会先过这些规则；默认给了一组通用规则，可以改。</p>

    <textarea id="wz-red" rows="10" class="wz-red">${esc(f.red_lines || DEFAULT_RED.join("\n"))}</textarea>`;

  if (st === 5) return `<h2>把这一轮要用到的数据放进来</h2><p class="wz-p">项目已创建。拖入或选择文件：原件不改动，Helix 在本机保存副本并记 SHA-256 指纹，配对时可以直接勾选作为来源引用。也可以以后在“数据与文件”页添加。</p>

    <div class="drop" data-cmd="wz-files"><b>把文件拖到这里，或点击选择</b><span>CSV、Excel、PDF、SDF、图片都可以；单个不超过 64 MB</span></div>

    <ul class="wz-files">${wz.files.map((x) => `<li><b>${esc(x.name)}</b><span class="mono">${esc(x.sha256.slice(0, 16))}…</span>${x.duplicate ? "<em>已存在</em>" : ""}</li>`).join("") || `<li class="none">还没有添加文件</li>`}</ul>`;

  const H = store.snapshot?.harness || {}, w = (store.snapshot?.sprint?.weeks || []).find((x) => x.week === H.seed_week);

  return `<h2>可以开始了：这是一轮人机协同怎么走</h2>

    <ol class="flow"><li><b>播种</b><span>Harness 把第 ${H.seed_week ?? 1} 周${w ? `（${esc(w.track_label)}）` : ""}的任务变成待核的“碱基”，放进收件箱。</span></li>

    <li><b>主核（真人）</b><span>读来源，判 互补 / 修正 / 错配 / 暂缓，给出结论；判“通过”“否决”必须附来源引用。</span></li>

    <li><b>交叉核（另一位真人）</b><span>独立复核主核结论；不一致要写理由，驾驶舱会标出“有分歧”。</span></li>

    <li><b>AI 起草（接入模型后）</b><span>AI 只能提出碱基，永远不能替人配对；机器校对按红线拦截越界表述。</span></li>

    <li><b>账本</b><span>每一步都追加到本机哈希链，谁、何时、依据什么，全部可追溯。</span></li></ol>

    <p class="wz-p">${H.seedable ? "现在播种第 " + (H.seed_week ?? 1) + " 周任务，然后去收件箱开始第一条主核。" : "本周任务已经播种过，直接去收件箱即可。"}</p>`;

}

const DEFAULT_RED = ["缺失 = UNKNOWN，不能当 0", "主核与交叉核必须是两位不同的真人", "AI 写的内容必须经真人配对才算数", "结论不能超过本项目设定的结论上限", "PASS / REJECTED / OUT_OF_DOMAIN 必须附原始文件、DOI、PMID 或 SHA", "不事后改标准挑赢家；权重和阈值要先登记"];

function drawWz() {

  let el = $(".wizard");

  if (!el) { el = document.createElement("div"); el.className = "welcome wizard"; document.body.appendChild(el); }

  const st = wz.step, edit = wz.mode === "edit", lastStep = edit ? 4 : 6;

  const H = store.snapshot?.harness || {};

  el.innerHTML = `<div class="wl-box wz-box" role="dialog" aria-label="项目引导"><header class="wz-head"><div><b>${edit ? "编辑项目" : "新建项目"}</b><span>${edit ? "改完点保存，已写进账本的记录不会变" : "6 步，从零开始一轮人机协同"}</span></div>

    <ol class="wz-steps">${STEPS.slice(0, lastStep).map((t, i) => `<li class="${i + 1 === st ? "on" : i + 1 < st ? "done" : ""}"><i>${i + 1 < st ? "✓" : i + 1}</i>${t}</li>`).join("")}</ol><button class="wl-x" data-cmd="wz-close" aria-label="关闭">×</button></header>

    <div class="wl-body wz-body">${wz.err && st === 3 ? `<div class="lock" role="alert" style="margin:0 0 12px;border-color:#b91c1c;color:#b91c1c"><b>没有保存：</b>${esc(wz.err)}</div>` : ""}${stepHTML()}</div>

    <footer class="wl-foot">${st > 1 && st !== 5 && st !== 6 ? `<button class="btn" data-cmd="wz-back">上一步</button>` : ""}<span class="sp"></span>

    ${st === 4 ? `<button class="btn pri" data-cmd="wz-next">${edit ? "保存修改" : "创建项目"}</button>` : st === 5 ? `<button class="btn" data-cmd="wz-next">${wz.files.length ? "下一步" : "跳过，以后再加"}</button>` : st === 6 ? `<button class="btn" data-cmd="wz-close">稍后再说</button>${H.seedable ? `<button class="btn pri" data-cmd="wz-seed">播种第 ${H.seed_week ?? 1} 周任务，去收件箱</button>` : `<button class="btn pri" data-cmd="wz-inbox">去收件箱</button>`}` : `<button class="btn pri" data-cmd="wz-next">下一步</button>`}</footer></div>`;

  el.querySelector(".wz-body input,.wz-body textarea")?.focus();

}

// 周数改变时重画周主题与任务的周选项

document.addEventListener("change", (e) => { if (wz && e.target.id === "wz-n") { readWz(); while (wz.f.weeks.length < wz.f.n_weeks) wz.f.weeks.push({ theme: `第 ${wz.f.weeks.length + 1} 周` }); drawWz(); } if (wz && e.target.name === "wz-ceil") { readWz(); drawWz(); }

  if (wz && e.target.closest?.(".wz-du")) e.target.closest("label")?.classList.toggle("on", e.target.checked);

  if (wz && e.target.classList?.contains("t-u")) { readWz(); const r = e.target.closest(".wz-task"), i = +r.dataset.i, t = wz.f.tasks[i], p = suggestPair(wz.f.members, wz.f.tasks, t.duty, i);

    if (p && p.matched) { Object.assign(t, { owner: p.owner, cross_reviewer: p.cross_reviewer }); drawWz(); ctx.toast(`按职能建议：主核 ${p.owner}，交叉核 ${p.cross_reviewer}（可再改）`); } else if (t.duty) ctx.toast("还没有成员负责这项职能，可在第 2 步勾选"); } });

document.addEventListener("toggle", (e) => { if (wz && e.target.classList?.contains("wz-dts")) wz.dtOpen = e.target.open; }, true);



export function isWizardOpen() { return !!wz; }

export function wizardKey(e) { if (e.key === "Escape") closeWz(); }



// —— 0.6 组内打卡 + 云同步

let ck = null;

function ckModal(html, cls = "") { ckClose(); ck = document.createElement("div"); ck.className = "hxm"; ck.innerHTML = `<div class="hxm-box ${cls}" role="dialog">${html}</div>`; ck.addEventListener("mousedown", (e) => { if (e.target === ck) ckClose(); }); document.body.appendChild(ck); ck.querySelector("textarea,input:not([type=radio]):not([type=checkbox])")?.focus(); }

function ckClose() { ck?.remove(); ck = null; }

document.addEventListener("keydown", (e) => { if (ck && e.key === "Escape") ckClose(); });

document.addEventListener("change", (e) => { if (!ck?.contains(e.target)) return; if (e.target.name === "ck-st") { ck.querySelectorAll(".ck-states label").forEach((l) => l.classList.toggle("on", l.contains(e.target))); const r = ck.querySelector("#ck-p"); if (e.target.value === "done") { r.value = 100; } else if (+r.value >= 100) r.value = 95; ck.querySelector("#ck-pv").textContent = r.value + "%"; } });

document.addEventListener("input", (e) => { if (ck && e.target.id === "ck-p") ck.querySelector("#ck-pv").textContent = e.target.value + "%"; });

function openCheckin(tid) {

  const s = store.snapshot; if (!s?.auth?.me) return openLogin();

  const t = (s.plan?.tasks || s.sprint?.pairs || []).find((x) => x.task_id === tid) || (s.sprint?.pairs || []).find((x) => x.task_id === tid) || { task_id: tid };

  const me = s.auth.me.name, as = t.owner === me ? "主核" : t.cross_reviewer === me ? "交叉核" : "";

  const c = checkinOf(s, tid), mine = (c?.history || []).find((h) => h.actor === me), last = mine || ckMain(c) || {};

  const st = mine?.state === "done" ? "done" : mine ? "doing" : "started", files = s.files || [];

  ckModal(`<h3>打卡 · ${esc(tid)}<small>${esc(t.deliverable || "")}${as ? `（你是${as}）` : ""}</small></h3>

    <div class="ck-states">${Object.entries(CK_STATE).map(([k, [lab]]) => `<label class="${k === st ? "on" : ""}"><input type="radio" name="ck-st" value="${k}" ${k === st ? "checked" : ""}><b>${lab}</b><small>${{ started: "刚动手", doing: "做了一部分", done: "可以交给审核", blocked: "需要协调" }[k]}</small></label>`).join("")}</div>

    <label class="fl">进度<span class="ck-prog"><input id="ck-p" type="range" min="0" max="100" step="5" value="${st === "done" ? 100 : Math.min(95, +(last.progress || (st === "started" ? 10 : 0)))}"><b id="ck-pv">${st === "done" ? 100 : Math.min(95, +(last.progress || (st === "started" ? 10 : 0)))}%</b></span></label>

    <label class="fl">说明（做到哪了 / 卡在哪里，最多 400 字）<textarea id="ck-n" rows="3" maxlength="400" placeholder="例如：数据清洗完成 2/3，缺第 3 批原始表"></textarea></label>

    ${files.length ? `<div class="fl">成果文件（可选，必须是已登记的数据文件）</div><div class="ck-files">${files.slice(-15).reverse().map((f) => `<label><input type="checkbox" class="ck-f" value="${esc(f.sha256)}"> ${esc(f.name)} <span class="mono">${esc(String(f.sha256).slice(0, 8))}</span></label>`).join("")}</div>` : `<p class="sub">要附成果文件，先在“数据与文件”页添加。</p>`}

    <p class="sub" style="margin:0 0 6px">打卡只记录进度，不改变审核结论：任务是否通过，仍由主核判定 + 交叉核复核。${s.cloud?.enabled ? "打卡后会自动同步到云端。" : ""}</p>

    <div class="ck-foot"><button class="btn" data-cmd="ck-close">取消</button><button class="btn pri" data-cmd="ck-submit:${esc(tid)}">打卡</button></div>`);

}

async function submitCheckin(tid) {

  const body = { task_id: tid, state: ck.querySelector("input[name=ck-st]:checked")?.value, progress: +ck.querySelector("#ck-p").value, note: ck.querySelector("#ck-n").value.trim(), files: [...ck.querySelectorAll(".ck-f:checked")].map((x) => x.value) };

  try { const j = await api("/api/checkin", body); ckClose(); ctx.toast(j.cloud ? "已打卡，正在同步到云端" : "已打卡（写入本机账本）"); await refresh(); setTimeout(refresh, 2500); } catch (e) { fail(e); }

}

function openCloud() {

  const C = store.snapshot?.cloud || {};

  ckModal(`<h3>云端协同数据库同步<small>各研究节点打卡与实验进度自动汇入云数据库，实时归档存证</small></h3>

    <ol class="ck-steps"><li>配置云端协同数据库集成密钥（支持 ntn_ / secret_ 等内部集成 Key）。</li>

    <li>在云端协同数据库中确认连接授权。</li><li>把数据库链接或 ID 粘贴到下面，缺失列将自动对齐补全。</li></ol>

    <label class="fl">云端数据库链接或 ID<input id="cl-db" value="${esc(C.database_id || "")}" placeholder="https://www.notion.so/…"></label>

    <label class="fl">集成密钥（Token）<input id="cl-tk" type="password" autocomplete="off" placeholder="${C.token_tail ? "已保存 ····" + esc(C.token_tail) + "，留空表示不改" : "ntn_…"}"></label>

    <label class="ck"><input id="cl-on" type="checkbox" ${C.enabled !== false ? "checked" : ""}> 开启自动同步（打卡、复核、更新计划后自动推送）</label>

    <p class="sub">密钥加密保存在本机中；云数据库仅接收任务进度与排期存证字段，不含未授权明文文件。需要组长权限。</p>

    <div class="ck-foot">${C.configured ? `<button class="btn" data-cmd="ck-cloud-off" style="margin-right:auto;color:var(--bad-fg)">停用</button>` : ""}<button class="btn" data-cmd="ck-close">取消</button><button class="btn pri" data-cmd="ck-cloud-save">测试并保存</button></div>`, "ck-wide");

}

async function saveCloud() {

  const body = { database: ck.querySelector("#cl-db").value.trim(), token: ck.querySelector("#cl-tk").value.trim(), enabled: ck.querySelector("#cl-on").checked };

  try { const j = await api("/api/cloud/config", body); ckClose(); ctx.toast(`已连接「${j.cloud.database_title || "云端协同数据库"}」${j.cloud.added?.length ? `，补上 ${j.cloud.added.length} 列` : ""}${body.enabled ? "，开始同步" : ""}`); await refresh(); setTimeout(refresh, 3000); } catch (e) { ctx.toast("没有保存：" + (e.message || e)); }

}

async function cloudSync(force = false) {

  if (!store.snapshot?.cloud?.configured) return openCloud();

  try { const j = await api("/api/cloud/sync", { force }); ctx.toast(j.skipped === "云同步未开启" ? "云同步已停用，可在设置里开启" : `已同步到云端：更新 ${j.pushed} 条，未变 ${j.skipped} 条`); await refresh(); } catch (e) { ctx.toast("同步失败：" + (e.message || e)); await refresh(); }

}

// 向导第 3 步：统筹模式重分配（组长统筹兜底复核，同链同人，同周分散）

async function coordAssign() {

  readWz(); const f = wz.f;

  try {

    const j = await api("/api/team/suggest", { mode: "balanced", members: f.members.filter((m) => m.name), tasks: f.tasks, duties: f.duties });

    const ch = j.rows.filter((r) => r.changed && f.tasks[r.i]);

    if (!ch.length) return ctx.toast(`当前已按 ${j.member_count} 人（含组长）分工，无需调整`);

    ckModal(`<h3>按 ${j.member_count} 人重分工<small>包含组长；按职能与每周负载安排执行，另一位成员独立复核。已开始的 ${j.protected_count} 条保留原分工。</small></h3>

      <ul class="coord-list">${ch.map((r) => { const t = f.tasks[r.i]; return `<li><b>${esc(t.task_id || "新任务")}</b> ${esc(String(t.deliverable || "").slice(0, 24))}：${esc(t.owner || "—")} / ${esc(t.cross_reviewer || "—")} → <b>${esc(r.owner)} / ${esc(r.cross_reviewer)}</b><small>${esc(r.why)}</small></li>`; }).join("")}</ul>

      <p class="sub">每人负载：${Object.entries(j.load).map(([n, l]) => `${esc(n)} 主核 ${l.primary} · 复核 ${l.cross}`).join("；")}${(j.notes || []).length ? "<br>" + j.notes.map(esc).join("；") : ""}</p>

      ${wz.mode === "edit" ? `<p class="sub" style="color:var(--warn-fg)">保存后需要组长重新冻结计划；已经播种进收件箱的条目仍按原来的主核 / 交叉核。</p>` : ""}

      <div class="ck-foot"><button class="btn" data-cmd="ck-close">不改</button><button class="btn pri" data-cmd="wz-coord-apply">应用这 ${ch.length} 处调整</button></div>`, "ck-wide");

    wz.coord = ch;

  } catch (e) { fail(e); }

}



// —— 键盘：选中待配对碱基时 A/E/X/D 选择结论

export function pairKey(e) {

  const sel = store.ui.sel; if (!sel?.startsWith("base:") || !store.snapshot?.harness?.writable) return false;

  const b = (store.snapshot.items || []).find((x) => x.base_id === sel.slice(5)); if (!b || b.status !== "open") return false;

  const m = { a: "complement", e: "revise", x: "mismatch", d: "defer" }[e.key.toLowerCase()]; if (!m) return false;

  draft[kb(b) + "verdict"] = m; store.ui_({}); return true;

}



export function harnessCmd(cmd) {

  const [op, ...rest] = cmd.split(":"); const arg = rest.join(":");

  const f = wz?.f;

  const A = {

    "hx-pair": () => pair(arg), "hx-cross": () => cross(arg), "hx-seed": seed, "hx-takeover": takeover,

    "hx-adopt": adoptPreview, "hx-adopt-save": adoptSave,
    "hx-upload": () => pickFiles((fl) => upload(fl)), "hx-register": () => openRegister(), "hx-projects": projectMenu, "hx-switch": () => switchProject(arg),

    "hx-wizard": () => { closeMenu(); openWizard("new"); }, "hx-wizard-edit": () => { closeMenu(); openWizard("edit"); },

    "hx-checkpoint": async () => { if (!store.snapshot?.auth?.me) return openLogin(); const label = prompt("检查点名称（会以你的名字写入账本）", `第 ${store.snapshot?.sprint?.current_week ?? ""} 周检查点`); if (!label) return; try { await api("/api/checkpoint", { label }); ctx.toast("已盖章"); await refresh(); } catch (e) { fail(e); } },

    "wz-close": closeWz, "wz-next": () => next(1), "wz-back": () => next(-1), "wz-import": importTable, "wz-template": templateCSV,

    "wz-mem-add": () => { readWz(); f.members.push({ name: "", role: "both" }); drawWz(); },

    "wz-mem-del": () => { readWz(); f.members.splice(+arg, 1); drawWz(); },

    "wz-mem-pick": () => { readWz(); f.members = f.members.filter((m) => m.name); f.members.push({ name: arg, role: "both" }); drawWz(); },

    "wz-dt-add": () => { readWz(); wz.dtOpen = true; let j = f.duties.length + 1; while (f.duties.some((d) => d.key === "c" + j)) j++; f.duties.push({ key: "c" + j, label: "新职能", desc: "" }); drawWz(); setTimeout(() => [...document.querySelectorAll(".wz-dt .dt-l")].at(-1)?.select(), 20); },

    "wz-dt-del": () => { readWz(); wz.dtOpen = true; const d = f.duties.splice(+arg, 1)[0]; if (d?.key) { f.members.forEach((m) => { m.duties = (m.duties || []).filter((k) => k !== d.key); }); f.tasks.forEach((t) => { if (t.duty === d.key) t.duty = ""; }); } drawWz(); },

    "wz-auto": () => { readWz(); let n = 0; f.tasks.forEach((t, i) => { if (!t.deliverable) return; const p = suggestPair(f.members, f.tasks, t.duty, i); if (p && (p.owner !== t.owner || p.cross_reviewer !== t.cross_reviewer)) { Object.assign(t, { owner: p.owner, cross_reviewer: p.cross_reviewer }); n++; } }); drawWz(); ctx.toast(n ? `已按职能调整 ${n} 条任务的主核/交叉核，请检查` : "当前分派已经符合职能"); },

    "wz-task-add": () => { readWz(); const n = f.members.filter((m) => m.name).map((m) => m.name); const i = f.tasks.length, duty = f.tasks.at(-1)?.duty || ""; const p = suggestPair(f.members, f.tasks, duty); f.tasks.push({ week: f.tasks.at(-1)?.week || 1, duty, deliverable: "", acceptance: "", depends_on: [], owner: p?.owner || n[i % n.length] || "", cross_reviewer: p?.cross_reviewer || n[(i + 1) % n.length] || "" }); drawWz(); setTimeout(() => [...document.querySelectorAll(".wz-task .t-d")].at(-1)?.focus(), 20); },

    "wz-task-del": () => { readWz(); f.tasks.splice(+arg, 1); drawWz(); },

    "file-view": () => openFile(arg),

    "ck-open": () => openCheckin(arg), "ck-submit": () => submitCheckin(arg), "ck-close": ckClose, "ck-cloud": openCloud, "ck-cloud-save": saveCloud, "ck-sync": () => cloudSync(false),

    "ck-cloud-off": async () => { if (!confirm("停用云同步？云端已有的行不会删除，本机打卡照常记录。")) return; try { await api("/api/cloud/disable", {}); ckClose(); ctx.toast("云同步已停用"); await refresh(); } catch (e) { fail(e); } },

    "wz-coord": coordAssign,

    "wz-coord-apply": () => { (wz?.coord || []).forEach((r) => { const t = wz.f.tasks[r.i]; if (t) Object.assign(t, { owner: r.owner, cross_reviewer: r.cross_reviewer }); }); const n = wz?.coord?.length || 0; wz.coord = null; ckClose(); drawWz(); ctx.toast(`已调整 ${n} 条任务，保存计划后生效`); },

    "wz-files": () => pickFiles((fl) => upload(fl, (r) => { wz.files.push(...r); drawWz(); })),

    "wz-seed": async () => { closeWz(); await seed(); }, "wz-inbox": () => { closeWz(); ctx.go("inbox"); },

  };

  if (A[op]) { A[op](); return true; }

  return false;

}

