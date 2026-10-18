// 0.5：登录（本机口令）、模型工坊（URL + Key 即用、按能力推荐）、运行页（DAG / Gate / 智能体执行与恢复）。

import { store, esc, short } from "./core/store.js?v=3110";

import { fileName, taskOutput, taskStatus } from "./presentation.js?v=3110";

import { views } from "./views/index.js?v=3110";

import { draft } from "./views/forms.js?v=3110";

import { av, fmtTs, roster, evTag, stepState, checkinOf, ckMain, CK_STATE } from "./views/common.js?v=3110";



let ctx = { toast: () => {}, go: () => {}, api: null, refresh: null };

export function initAgents(c) { ctx = { ...ctx, ...c }; }

import { token, me as me_ } from "./views/auth.js?v=3110";

export { token };

export const me = (s = store.snapshot) => me_(s);

const v = (k, d = "") => draft[k] ?? d;

const fail = (e) => { ctx.toast("没有完成：" + (e.message || e)); if (e.auth) openLogin(); };



// —— 弹窗

let dlg = null;

function modal(html) { closeModal(); dlg = document.createElement("div"); dlg.className = "hxm"; dlg.innerHTML = `<div class="hxm-box">${html}</div>`; dlg.addEventListener("mousedown", (e) => { if (e.target === dlg) closeModal(); }); document.body.appendChild(dlg); dlg.querySelector("input,select")?.focus(); }

export function closeModal() { dlg?.remove(); dlg = null; }

const val = (id) => dlg?.querySelector("#" + id)?.value || "";



// —— 登录

export function openLogin(pid) { location.assign('/login'); }
function leaderSettings(){
 const s=store.snapshot;if(s.auth?.me?.role!=='lead')return openLogin();
 modal(`<h3>组长设置</h3><p>管理当前项目和团队</p><div class="leader-settings"><button class="btn pri" data-cmd="hx-wizard-edit">编辑项目与分工</button><button class="btn" data-cmd="go:people">管理组员与邀请</button><button class="btn" data-cmd="ck-cloud">Notion 同步设置</button><button class="btn" data-cmd="go:models">人工智能设置</button></div><p class="sub">${esc(s.project?.name||'当前项目')}</p><div class="row-end"><button class="btn" data-cmd="modal-close">关闭</button></div>`);
}

function openSetPin(pid) {

  const s = store.snapshot, ro = roster(s), A = s?.auth || {}, has = new Set(A.with_pin || []), leads = ro.filter((p) => (A.lead_with_pin || []).includes(p.id));

  pid = pid || val("lg-who") || ro[0]?.id;

  modal(`<h3>设置 / 修改口令 <small>只存加盐散列，不存明文</small></h3>

    <label class="fl">成员<select id="sp-who">${ro.map((p) => `<option value="${esc(p.id)}" ${p.id === pid ? "selected" : ""}>${esc(p.name)}${has.has(p.id) ? " · 已有口令" : ""}</option>`).join("")}</select></label>

    <label class="fl">新口令（至少 6 位）<input id="sp-new" type="password" autocomplete="new-password"></label>

    <label class="fl">原口令（修改自己的口令时填）<input id="sp-cur" type="password" autocomplete="current-password"></label>

    ${leads.length ? `<label class="fl">或由组长批准（新成员首次设口令 / 忘记口令重置）<select id="sp-admin"><option value="">—</option>${leads.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select></label><label class="fl">组长口令<input id="sp-apin" type="password"></label>` : `<p class="warn-line">还没有组长设置口令：现在设口令不需要批准（引导模式）。请尽快给组长设口令。</p>`}

    <div class="row-end"><button class="btn" data-cmd="modal-close">取消</button><button class="btn pri" data-cmd="auth-do-setpin">保存口令</button></div>`);

}

async function doSetPin() {

  try {

    await ctx.api("/api/auth/set-pin", { person_id: val("sp-who"), new_pin: val("sp-new"), current_pin: val("sp-cur"), admin_id: val("sp-admin"), admin_pin: val("sp-apin") });

    if (store.snapshot?.auth?.account_scope === "cloud" && val("sp-who") === store.snapshot?.auth?.me?.id) { sessionStorage.removeItem("helix.token"); location.assign("/login"); return; }

    const pid = val("sp-who"); ctx.toast("口令已保存，请登录"); await ctx.refresh(); openLogin(pid);

  } catch (e) { ctx.toast(e.message || e); }

}

async function logout() { try { await ctx.api("/api/auth/logout", {}); } catch {} sessionStorage.removeItem("helix.token"); location.assign("/login"); }



export function titleAuth(s) {

  const m = me(s), A = s?.auth || {};

  if (m) return `<span class="who-ok" title="已登录 · 本机保持登录 30 天">${av(s, m.name)} <b>${esc(m.name)}</b><small>${m.role === "lead" ? "组长" : "组员"}</small></span>${m.role === "lead" ? `<button class="btn sm" data-cmd="leader-settings">组长设置</button>` : ""}<span class="logout" role="button" data-cmd="auth-logout">退出</span>`;

  return `<button class="btn sm ${A.bootstrap ? "" : "pri"}" data-cmd="auth-login">登录</button>${A.bootstrap ? `<small class="warn-dot" title="还没有人设置口令：身份可被冒用">未设口令</small>` : ""}`;

}



// —— 运行页

const ST = { passed: ["已通过", "ok"], failed: ["关卡未通过", "bad"], disputed: ["交叉核不一致", "bad"], blocked: ["阻断", "warn"], open: ["进行中", "info"], unseeded: ["未播种", ""] };

const RS = { completed: ["完成", "ok"], running: ["执行中", "info"], unknown: ["结果未知 · 等真人确认", "bad"], failed: ["失败 · 可恢复", "warn"], interrupted: ["中断 · 可恢复", "warn"], blocked: ["被拦下", "warn"], cancelled: ["已取消", ""] };

const SS = { done: "#10b981", requested: "#38bdf8", failed: "#f59e0b", unknown: "#ef4444", acknowledged: "#a78bfa", pending: "#cbd5e1" };

function runsView(s,ui=store.ui){
 const person=s.auth?.me,scope=ui.taskScope||(person?.role==='lead'?'week':'mine'),week=s.sprint?.current_week;
 const all=s.plan?.tasks||s.sprint?.pairs||[],mine=t=>!!person&&(!!t.my_role||[t.owner,t.cross_reviewer].includes(person.name));
 const tasks=all.filter(t=>scope==='all'||(scope==='mine'?mine(t):+t.week===+week));
 const options=[['week','本周任务'],['mine','我的任务'],['all','全部计划']];
 const row=t=>{const can=mine(t),checkin=ckMain(checkinOf(s,t.task_id)),progressLabel=checkin?`${(CK_STATE[checkin.state]||[checkin.state])[0]} · ${checkin.progress||0}%`:taskStatus(t.status);
  return `<article class="card task-card"><div class="task-card-head"><h2>${esc(t.deliverable||'待补充任务名称')}</h2><span class="pill">${esc(progressLabel)}</span></div><div class="task-facts"><span>负责人：<b>${esc(t.owner||'待分配')}</b></span><span>复核人：<b>${esc(t.cross_reviewer||'待分配')}</b></span><span>截止：<b>${esc(t.end||'待设置')}</b></span><span>第 ${esc(t.week)} 周</span></div><section><h4>亲手做什么</h4><p>${esc(t.human_work||'按任务要求实际操作，保存过程、输入输出和本人观察。')}</p></section><section><h4>完成时交什么</h4><p>${esc(taskOutput(t))}</p></section><details><summary>查看验收要求和任务编号</summary><p>${esc(t.acceptance||'请与负责人确认验收要求')}</p><p class="sub">任务编号：${esc(t.task_id)}${t.depends_on?.length?' · 前置任务：'+t.depends_on.map(esc).join('、'):''}</p></details><div class="task-actions">${can?`<button class="btn pri" data-cmd="hr-start:${esc(t.task_id)}">开始本人实操</button><button class="btn" data-cmd="ck-open:${esc(t.task_id)}">更新进度</button>`:'<span class="sub">由任务负责人或复核人执行</span>'}<button class="btn" data-sel="task:${esc(t.task_id)}">查看任务详情</button></div></article>`;
 };
 return `<div class="vh"><div><h1>任务与实操</h1><p class="lede">看清任务，亲手完成，提交成果，再由另一人复核。</p></div></div><div class="task-tabs">${options.map(([id,label])=>`<button class="btn ${scope===id?'pri':''}" data-cmd="task-scope:${id}">${label}</button>`).join('')}<span class="sub">共 ${tasks.length} 项</span></div><div class="task-list">${tasks.map(row).join('')||'<div class="card empty">暂时没有分配给你的任务。可以查看全部计划，或联系组长安排。</div>'}</div>${humanCards(s)}<details class="card advanced-tasks"><summary>人工智能辅助、计划确认与审核详情</summary>${advancedRunsView(s)}</details>`;
}

function advancedRunsView(s) {

  const G = s.gates || {}, plan = G.plan || {}, T = G.tasks || {}, R = s.agent_runs || {}, H = s.harness || {}, m = me(s), u = R.usage || {}, B = s.models?.budget || {};

  const tasks = (s.plan?.tasks) || (s.sprint?.pairs || []);

  const pairBy = Object.fromEntries((s.sprint?.pairs || []).map((p) => [p.task_id, p]));

  const allTasks = Object.keys(T).map((id) => ({ task_id: id, ...(tasks.find((t) => t.task_id === id) || pairBy[id] || {}), g: T[id] }));

  const freeze = !H.writable ? `<div class="lock">${esc(H.why || "当前项目只读")}</div>` : plan.frozen ? (plan.changed ? `<div class="lock warn">计划在冻结后被改动（指纹 ${short(plan.hash, 10)}，冻结时 ${short(plan.frozen.plan_hash, 10)}）。智能体已暂停接单，请组长确认后重新冻结。<div style="margin-top:8px"><button class="btn pri" data-cmd="ag-freeze">确认改动并重新冻结</button></div></div>` : `<div class="lock ok">计划已冻结：${plan.tasks} 项任务，指纹 <span class="mono">${short(plan.hash, 12)}</span> · ${esc(plan.frozen.by)} · ${fmtTs(plan.frozen.ts)}</div>`) : `<div class="lock">计划还没有冻结。冻结 = 把任务表、红线、主张上限的指纹写进账本，之后改计划会被发现。<div style="margin-top:8px"><button class="btn pri" data-cmd="ag-freeze">冻结计划（组长）</button></div></div>`;

  const row = (t) => { const g = t.g || {}, [lab, c] = ST[g.state] || [g.state, ""]; const can = H.writable && !["passed", "failed"].includes(g.state);

    return `<tr><td class="mono"><b>${esc(t.task_id)}</b></td><td>${esc(t.deliverable || "")}${t.human_work ? `<div class="sub">本人实操：${esc(t.human_work)}</div>` : ""}<div class="sub">${(g.deps || []).length ? "依赖 " + g.deps.map(esc).join("、") + " · " : ""}${esc(g.why || "")}</div></td><td>${t.owner ? av(s, t.owner) + " " + esc(t.owner) : ""} ⇄ ${t.cross_reviewer ? av(s, t.cross_reviewer) + " " + esc(t.cross_reviewer) : ""}</td><td><span class="pill ${c}">${esc(lab)}</span>${g.ai_open ? ` <span class="pill warn">AI 待配对 ${g.ai_open}</span>` : ""}</td>

      <td>${can ? `<button class="btn sm pri" data-cmd="hr-start:${esc(t.task_id)}" ${m ? "" : "disabled title='先登录'"}>真人执行</button> ${t.execution_mode !== "human" ? `<button class="btn sm" data-cmd="ag-start:${esc(t.task_id)}" ${m ? "" : "disabled title='先登录'"}>AI辅助起草</button>` : ""}` : ""}</td></tr>`; };

  const runCard = (r) => { const [lab, c] = RS[r.status] || [r.status, ""];

    const chain = (r.steps || []).map((st, i) => `${i ? `<span class="rl" style="background:${SS[st.state] || SS.pending}"></span>` : ""}<span class="rs" title="${esc(st.label)} · ${esc(stepState(st.state))}${st.model ? " · " + esc(st.model) : ""}${st.error ? " · " + esc(st.error) : ""}"><i style="background:${SS[st.state] || SS.pending}"></i><b>${esc(st.label)}</b><small>${esc(st.model || (st.state === "pending" ? "待执行" : ""))}${st.attempt > 1 ? ` · 第 ${st.attempt} 次` : ""}</small></span>`).join("");

    const errs = (r.steps || []).filter((x) => x.error).map((x) => `<li><b>${esc(x.label)}</b>：${esc(x.error)}${x.ack_by ? ` · 已由 ${esc(x.ack_by)} 确认：${esc(x.ack_note)}` : ""}</li>`).join("");

    const notes = (r.steps || []).flatMap((x) => (x.notes || []).map((n) => `<li>${esc(x.label)}：${esc(n)}</li>`)).join("");

    return `<div class="card run"><h3>${r.kind === "summary" ? "周汇总" : esc(r.task_id)} <small>${esc(r.run_id)} · ${esc(r.started_by)} 启动 · ${fmtTs(r.ts)}</small><span class="sp"></span><span class="pill ${c}">${esc(lab)}</span></h3>

      <div class="rchain">${chain}</div>

      <p class="sub">词元 输入 ${r.usage?.input || 0} / 输出 ${r.usage?.output || 0} · 调用 ${r.calls || 0} 次${r.base_ids?.length ? ` · 写成 ${r.base_ids.length} 条待判断条目` : ""}${r.output ? ` · 草稿 <span class="mono">${esc(r.output)}</span>（草稿，未应用）` : ""}${r.blocked ? ` · 拦下原因：${esc(r.blocked)}` : ""}</p>

      ${errs ? `<ul class="errs">${errs}</ul>` : ""}${notes ? `<details><summary>核验记录</summary><ul class="errs">${notes}</ul></details>` : ""}

      <div class="row-end">${r.status === "unknown" ? `<button class="btn pri" data-cmd="ag-ack:${esc(r.run_id)}">我已核对，确认结果未知</button>` : ""}${["failed", "interrupted", "blocked"].includes(r.status) ? `<button class="btn pri" data-cmd="ag-resume:${esc(r.run_id)}">从断点恢复</button>` : ""}${!["completed", "cancelled", "running"].includes(r.status) ? `<button class="btn" data-cmd="ag-cancel:${esc(r.run_id)}">取消</button>` : ""}${r.base_ids?.length ? `<button class="btn" data-cmd="go:inbox">去收件箱配对</button>` : ""}</div></div>`; };

  return `<div class="vh"><div><h1>运行</h1><p class="lede">真人亲自操作、跑工具和记录观察，AI辅助规划与起草。实操过程、真实工件和审核结论分别保留。</p></div><span class="sp"></span>

    <button class="btn" data-cmd="ag-summary" ${m && H.writable ? "" : "disabled"}>生成本周汇总草稿</button></div>

    <div class="grid g2"><div class="card"><h3>计划冻结<small>关卡判断的前提</small></h3>${freeze}</div>

    <div class="card"><h3>今日用量<small>超限即停，不偷偷换便宜模型</small></h3><dl class="kv"><dt>调用</dt><dd>${u.calls || 0} / ${B.max_calls_per_day ?? "—"}</dd><dt>词元</dt><dd>${(u.tokens || 0).toLocaleString()} / ${(B.max_tokens_per_day || 0).toLocaleString()}</dd><dt>单次运行上限</dt><dd>${(B.max_tokens_per_run || 0).toLocaleString()} 词元</dd></dl><button class="btn sm" data-cmd="go:models">模型与预算设置</button></div></div>

    <div class="card" style="margin-top:14px;padding:0"><table><thead><tr><th style="width:90px">任务</th><th>交付物 · 关卡状态</th><th style="width:180px">主核 ⇄ 交叉核</th><th style="width:170px">状态</th><th style="width:130px"></th></tr></thead><tbody>${allTasks.map(row).join("") || `<tr><td colspan="5" class="empty">还没有任务。${H.seedable ? "先播种本周任务。" : ""}</td></tr>`}</tbody></table></div>

    <h2 class="sec">AI辅助运行记录</h2>${(R.runs || []).map(runCard).join("") || `<div class="empty card">还没有运行。可亲自执行任务，或使用AI辅助起草。</div>`}

    <div class="card" style="margin-top:14px"><h3>账本事件<small>最近 ${(s.ledger?.tail || []).length} 条</small></h3><table><tbody>${(s.ledger?.tail || []).slice().reverse().slice(0, 25).map((e) => `<tr data-sel="event:${e.seq}"><td class="mono">${e.seq}</td><td><b>${evTag(e.type)}</b></td><td>${e.actor_kind === "human" ? av(s, e.actor) : ""} ${esc(e.actor)}</td><td class="mono">${fmtTs(e.ts)}</td></tr>`).join("")}</tbody></table></div>`;

}




function humanCards(s) {
  const states={running:"实操中",completed:"实操记录完成",blocked:"实操受阻"};
  const evidence=r=>`<details><summary>查看本人步骤、参数与工件</summary>${r.steps?`<h4>实际步骤</h4><pre style="white-space:pre-wrap">${esc(r.steps)}</pre>`:''}<h4>工具与参数</h4><pre style="white-space:pre-wrap">${esc(typeof r.parameters==='string'?r.parameters:JSON.stringify(r.parameters||{},null,2))}</pre>${(r.tools||[]).map(t=>`<p>${esc(t.tool)} · ${t.ok?'调用成功':'调用失败'} · ${fmtTs(t.ts)}</p><pre style="white-space:pre-wrap">${esc(JSON.stringify(t.tool_versions||[],null,2))}</pre>`).join('')}<h4>输入与输出文件指纹</h4>${[...(r.inputs||[]),...(r.outputs||[])].map(f=>`<p>${esc(fileName(f.name))} <span class="mono">${esc(f.sha256)}</span></p>`).join('')||'<p>尚未登记工件。</p>'}<p class="sub">可在数据页按文件名或指纹打开原始工件。</p></details>`;

  return `<h2 class="sec">真人执行记录</h2>${(s.human_runs||[]).map(r=>`<div class="card"><h3>${esc(r.task_id)} · ${esc(r.started_by)} <span class="pill">${states[r.status]||r.status}</span></h3><p>${esc(r.method)}</p><small>${fmtTs(r.started_at)} · ${r.tools?.length||0} 次实际工具调用 · ${r.record_kind==='human_reported'?'本人记录，待独立核对':'工具调用已记录，观察由本人填写'}</small>${r.observation?`<p>${esc(r.observation)}</p>`:''}${evidence(r)}${(r.tools||[]).filter(t=>!t.ok).map(t=>`<p class="lock warn">${esc(t.tool)}：${esc(t.error)}</p>`).join('')}${r.status==='running'?`<p><button class="btn pri" data-cmd="hr-record:${esc(r.run_id)}">继续实操 / 记录结果</button></p>`:''}</div>`).join('')||'<div class="card empty">亲手运行工具、处理文件或阅读原文后，保存步骤、参数和工件。真人可以直接执行，不必等AI起草。</div>'}`;
}
function humanStart(tid) {
  if(!me(store.snapshot))return openLogin();
  const t=(store.snapshot.plan?.tasks||[]).find(t=>t.task_id===tid)||{};
  modal(`<h3>真人执行 · ${esc(tid)}</h3><p>${esc(t.human_work||'亲自操作并保留输入、参数、版本、真实输出或原始错误。')}</p><label class="fl">本次准备亲自做什么<textarea id="hr-method" rows="3">${esc(t.deliverable||'')}</textarea></label><label class="fl">输入文件（可多选）<select id="hr-inputs" multiple>${(store.snapshot.files||[]).map(f=>`<option value="${esc(f.sha256)}">${esc(fileName(f.name))}</option>`).join('')}</select></label><label class="fl">工具、版本与参数（可稍后补）<textarea id="hr-params" rows="2"></textarea></label><div class="row-end"><button class="btn" data-cmd="modal-close">取消</button><button class="btn pri" data-cmd="hr-do-start:${esc(tid)}">开始实操</button></div>`);
}
async function humanBegin(tid) {
  try{const inputs=[...dlg.querySelector('#hr-inputs').selectedOptions].map(x=>x.value);const j=await ctx.api('/api/human/start',{task_id:tid,method:val('hr-method'),inputs,parameters:val('hr-params')});await ctx.refresh();humanRecord(j.run_id);}catch(e){fail(e);}
}
function humanRecord(rid) {
  const s=store.snapshot,r=(s.human_runs||[]).find(r=>r.run_id===rid);if(!r)return;
  const checked=new Set((r.outputs||[]).map(f=>f.sha256));
  store.ui_({humanRunId:rid,humanTaskId:r.task_id,humanProjectId:s.project.id});
  modal(`<h3>真人实操 · ${esc(r.task_id)}</h3><p>${esc(r.method)}</p><p><button class="btn" data-cmd="hr-tools">去科学工具并登记真实调用</button> <button class="btn" data-cmd="hr-files">去添加原生工具的成果或日志</button></p><label class="fl">实际执行步骤<textarea id="hr-steps" rows="3" placeholder="写你实际运行、处理或阅读了什么，包含命令/工具与操作顺序">${esc(r.steps||'')}</textarea></label><label class="fl">本人观察或阻塞<textarea id="hr-observation" rows="3" placeholder="真实看到什么、哪里不一致或卡在哪里">${esc(r.observation||'')}</textarea></label><label class="fl">工具版本与参数补充<textarea id="hr-params" rows="2">${esc(typeof r.parameters==='string'?r.parameters:JSON.stringify(r.parameters||{}))}</textarea></label><label class="fl">状态<select id="hr-status"><option value="completed">实操记录完成</option><option value="blocked">实操受阻</option></select></label><div class="fl">成果或日志文件（先在数据页登记）</div><div style="max-height:160px;overflow:auto">${(s.files||[]).slice(-30).map(f=>`<label class="ck"><input type="checkbox" class="hr-output" value="${esc(f.sha256)}" ${checked.has(f.sha256)?'checked':''}> ${esc(fileName(f.name))}</label>`).join('')}</div><p class="sub">实操记录不自动把科研任务判为通过。</p><div class="row-end"><button class="btn" data-cmd="modal-close">稍后继续</button><button class="btn pri" data-cmd="hr-do-finish:${esc(rid)}">保存实操结果</button></div>`);
}
async function humanFinish(rid) {
  try{await ctx.api('/api/human/finish',{run_id:rid,status:val('hr-status'),steps:val('hr-steps'),observation:val('hr-observation'),parameters:val('hr-params'),outputs:[...dlg.querySelectorAll('.hr-output:checked')].map(x=>x.value)});closeModal();store.ui_({humanRunId:null,humanTaskId:null,humanProjectId:null});await ctx.refresh();ctx.toast('实操过程与工件已登记，科研审核结论保持不变');}catch(e){fail(e);}
}

// —— 模型工坊

function modelsView(s) {

  const M = s.models || {}, provs = M.providers || [], roles = M.roles || {}, B = M.budget || {}, Rr = M.rules || {}, m = me(s), A = s.auth || {};

  const canEdit = A.can_admin ?? (A.bootstrap || m?.role === "lead");

  const CAP = { reasoning: "推理", long: "长文", cheap: "便宜", fast: "快", chinese: "中文", vision: "看图", code: "代码" };

  const all = provs.flatMap((p) => (p.models || []).map((x) => ({ p: p.id, pn: p.name, m: x })));

  const pline = (p) => `<div class="prov"><h4>${esc(p.name)} <small>${esc(p.style === "mock" ? "模拟 · 不联网" : p.style === "anthropic" ? "Anthropic 接口" : "OpenAI 兼容接口")}</small></h4>

    <p class="mono sub">${esc(p.base_url)}${p.key_tail ? ` · 密钥 …${esc(p.key_tail)}（${p.key_store === "dpapi" ? "Windows 加密保存" : "本机保存"}）` : p.builtin ? "" : " · 未填密钥"}</p>

    <div class="chips">${(p.infos || []).slice(0, 24).map((i) => `<span class="chip" title="${esc(i.family)}">${esc(i.model)}${i.caps.length ? ` <small>${i.caps.map((c) => CAP[c] || c).join("·")}</small>` : ""}</span>`).join("") || `<span class="sub">还没有模型列表</span>`}</div>

    ${p.last_test ? `<p class="sub">${p.last_test.ok ? `✓ ${p.last_test.models ? `拉到 ${p.last_test.models} 个模型` : `${esc(p.last_test.model || "")} 回复正常`} · ${p.last_test.latency_ms ?? "?"} ms` : `✕ ${esc(p.last_test.error || "")}`}</p>` : ""}

    ${p.builtin ? "" : `<div class="row-end"><button class="btn sm" data-cmd="md-test:${esc(p.id)}" ${canEdit ? "" : "disabled"}>测试</button><button class="btn sm" data-cmd="md-refresh:${esc(p.id)}" ${canEdit ? "" : "disabled"}>刷新模型</button><button class="btn sm" data-cmd="md-del:${esc(p.id)}" ${canEdit ? "" : "disabled"}>删除</button></div>`}</div>`;

  const opt = (role) => { const cur = roles[role] || {}; const key = "md.role." + role; const sel = v(key, cur.pinned ? cur.provider + "|" + cur.model : "");

    return `<select data-d="${key}"><option value="">自动推荐${cur.recommended ? `：${esc(cur.recommended.model)}` : ""}</option>${all.map((x) => `<option value="${esc(x.p + "|" + x.m)}" ${sel === x.p + "|" + x.m ? "selected" : ""}>${esc(x.pn)} · ${esc(x.m)}</option>`).join("")}</select>`; };

  return `<div class="vh"><div><h1>模型工坊</h1><p class="lede">不固定厂商：填接口地址和密钥就能用。Helix 按每个角色需要的能力推荐模型；批评者必须和起草者来自不同模型家族。</p></div></div>

    ${canEdit ? "" : `<div class="lock">配置模型需要组长登录。</div>`}

    <div class="grid g2"><div class="card"><h3>添加厂商</h3><p class="sub" style="margin-top:-6px">OpenAI 兼容（DeepSeek、通义、智谱、Kimi、OpenRouter、本地 vLLM / Ollama…）或 Anthropic 接口都可以。</p>

      <label class="fl">接口地址<input data-d="md.url" value="${esc(v("md.url"))}" placeholder="例如 https://api.deepseek.com/v1"></label>

      <label class="fl">API 密钥<input data-d="md.key" type="password" autocomplete="off" value="${esc(v("md.key"))}" placeholder="sk-…（只存在本机，界面不再显示）"></label>

      <details><summary>更多（可选）</summary><label class="fl">名称<input data-d="md.name" value="${esc(v("md.name"))}" placeholder="默认取域名"></label>

      <label class="fl">接口类型<select data-d="md.style"><option value="">自动判断</option><option value="openai" ${v("md.style") === "openai" ? "selected" : ""}>OpenAI 兼容</option><option value="anthropic" ${v("md.style") === "anthropic" ? "selected" : ""}>Anthropic</option></select></label>

      <label class="fl">模型名（拉不到列表时手填，逗号分隔）<input data-d="md.models" value="${esc(v("md.models"))}" placeholder="deepseek-chat, deepseek-reasoner"></label></details>

      <div class="row-end"><button class="btn pri" data-cmd="md-add" ${canEdit ? "" : "disabled"}>连接并拉取模型</button></div></div>

      <div class="card"><h3>已连接<small>${provs.filter((p) => !p.builtin).length} 家真实厂商</small></h3>${provs.map(pline).join("")}</div></div>

      <div class="card" style="margin-top:14px"><h3>角色分配<small>默认按能力自动推荐，可以固定</small></h3><table class="roles"><thead><tr><th style="width:190px">角色</th><th>使用的模型</th><th style="width:240px">推荐理由</th></tr></thead><tbody>${Object.entries(roles).map(([k, r]) => `<tr><td><b>${esc(r.label)}</b></td><td>${opt(k)}<div class="sub">当前：${esc(r.model || "无")}${r.family ? ` · 家族 ${esc(r.family)}` : ""}${r.pinned ? " · 已固定" : " · 自动"}</div></td><td class="sub">${esc((r.recommended?.why || []).join("、"))}</td></tr>`).join("")}</tbody></table>

      <p class="${M.heterogeneous ? "ok-t" : "warn-line"}">${M.heterogeneous ? "✓ 起草者与批评者来自不同模型家族（异源互审）" : "起草者与批评者是同一家族：运行会被拦下（或在下面允许同源，生成的条目会标注可信度降低）"}</p>

      ${(M.notes || []).map((n) => `<p class="warn-line">${esc(n)}</p>`).join("")}

      <h4>预算与规则</h4><div class="grid g3"><label class="fl">每日调用上限<input type="number" min="1" data-d="md.b.calls" value="${esc(v("md.b.calls", B.max_calls_per_day))}"></label><label class="fl">每日词元上限<input type="number" min="1" data-d="md.b.tok" value="${esc(v("md.b.tok", B.max_tokens_per_day))}"></label><label class="fl">单次运行词元上限<input type="number" min="1" data-d="md.b.run" value="${esc(v("md.b.run", B.max_tokens_per_run))}"></label></div>

      <label class="ck"><input type="checkbox" data-d="md.r.send" ${v("md.r.send", Rr.send_file_text !== false) ? "checked" : ""}> 抽取时把已登记文件的文本发给模型（未发表数据请确认厂商的数据政策）</label>

      <label class="ck"><input type="checkbox" data-d="md.r.same" ${v("md.r.same", !!Rr.allow_same_family) ? "checked" : ""}> 只有一家厂商时允许同源互审（不推荐）</label>

      <label class="ck"><input type="checkbox" data-d="md.r.mock" ${v("md.r.mock", M.allow_mock !== false) ? "checked" : ""}> 显示“模拟”厂商（离线试跑流程，结果不能当科研依据）</label>

      <div class="row-end"><button class="btn pri" data-cmd="md-save" ${canEdit ? "" : "disabled"}>保存分配与预算</button></div></div>`;

}



// —— 成员页补充：口令状态

function pinCard(s) {
 const ro=roster(s).filter(p=>p.active!==false),A=s.auth||{},has=new Set(A.with_pin||[]);
 return `<div class="card" style="margin-top:14px"><h3>个人账号与密码<small>默认匿名，沿用原分工</small></h3><p class="sub">组长为每位组员生成独立设置链接；对方自行设置账号和密码。设置后仍是原来的成员，任务、实操记录和复核关系不变。登录账号仅本人使用，团队显示匿名代号。</p><table><thead><tr><th>匿名代号</th><th>角色</th><th>账号</th><th></th></tr></thead><tbody>${ro.map(p=>`<tr><td>${esc(p.name)}</td><td>${p.role==='lead'?'组长':'组员'}</td><td>${has.has(p.id)?'本人已设置':'待本人设置'}</td><td>${!has.has(p.id)&&p.role!=='lead'&&A.me?.role==='lead'?`<button class="btn sm" data-cmd="auth-invite:${esc(p.id)}">生成设置链接</button>`:A.me?.id===p.id&&has.has(p.id)?`<button class="btn sm" data-cmd="auth-setpin:${esc(p.id)}">修改自己的密码</button>`:''}</td></tr>`).join('')}</tbody></table></div>`;
}

async function memberInvite(pid) {
 try {
  const j=await ctx.api('/api/auth/invite',{person_id:pid}),url=new URL(j.path,location.origin).href;
  modal(`<h3>${esc(j.name)} · 自行设置账号</h3><p>将此链接单独交给对应组员，7 天内有效，首次设置后失效。对方自行填写账号和密码，保留原来的分工。</p><label>个人设置链接<textarea readonly rows="4" aria-label="个人设置链接">${esc(url)}</textarea></label><div class="row-end"><button class="btn pri" data-cmd="auth-copy-invite">复制链接</button><button class="btn" data-cmd="modal-close">关闭</button></div>`);
 } catch(e) { ctx.toast(e.message||'生成失败'); }
}
async function copyMemberInvite() {
 const field=dlg?.querySelector('textarea');if(!field)return;
 try {await navigator.clipboard.writeText(field.value);ctx.toast('已复制，请单独交给对应组员');}
 catch {field.select();ctx.toast('请按 Ctrl+C 复制');}
}

views.runs = { ...views.runs, title: "任务与实操", render: runsView, badge: (s) => (s.agent_runs?.runs || []).filter((r) => r.status === "unknown").length };

views.models = { ...views.models, render: modelsView };

const peopleRender = views.people.render;

views.people = { ...views.people, render: (s, ...a) => peopleRender(s, ...a) + pinCard(s) };



// —— 命令

async function startRun(tid) {

  try {

    const pf = await (await fetch("/api/preflight?task_id=" + encodeURIComponent(tid), { headers: { "X-Helix-Token": token() }, cache: "no-store" })).json();

    if ((pf.blockers || []).length) return modal(`<h3>现在不能运行 ${esc(tid)} <small>关卡与安全检查没有通过</small></h3><ul class="errs">${pf.blockers.map((b) => `<li>${esc(b)}</li>`).join("")}</ul><div class="row-end"><button class="btn pri" data-cmd="modal-close">知道了</button></div>`);

    const r = pf.roles || {};

    if (!confirm(`让智能体为 ${tid} 起草？\n\n解旋：${r.helicase?.model}\n抽取：${r.extractor?.model}\n起草：${r.drafter?.model}\n互审：${r.critic?.model}\n\n结果会写成待判断条目，等指定主核和交叉核判断。`)) return;

    await ctx.api("/api/runs/start", { task_id: tid }); ctx.toast("已启动，可在本页看每一步"); await ctx.refresh();

  } catch (e) { fail(e); }

}

async function post(path, body, msg) { try { await ctx.api(path, body); if (msg) ctx.toast(msg); await ctx.refresh(); } catch (e) { fail(e); } }

export function agentCmd(cmd) {

  const [op, ...rest] = cmd.split(":"); const arg = rest.join(":");

  const A = {

    "modal-close": closeModal, "auth-invite": () => memberInvite(arg), "auth-copy-invite": copyMemberInvite, "auth-register": () => location.assign("/register"), "auth-do-register": () => location.assign("/register"), "auth-login": () => openLogin(), "auth-do-login": openLogin, "auth-setpin": () => openSetPin(arg), "auth-do-setpin": doSetPin, "auth-logout": logout,

    "ag-start": () => startRun(arg),
    "leader-settings": () => leaderSettings(), "task-scope": () => store.ui_({taskScope:arg}),
    "files-kind": () => store.ui_({fileKind:arg,fileLimit:60}), "files-search": () => store.ui_({fileQuery:document.querySelector('#file-search')?.value||'',fileLimit:60}), "files-more": () => store.ui_({fileLimit:(store.ui.fileLimit||60)+120}),
    "hr-start": () => humanStart(arg), "hr-do-start": () => humanBegin(arg), "hr-record": () => humanRecord(arg), "hr-do-finish": () => humanFinish(arg),
    "hr-tools": () => {closeModal();ctx.go("tools");}, "hr-files": () => {closeModal();ctx.go("data");},

    "ag-freeze": () => { const note = prompt("冻结说明（例如：确认 T2 交付物修改）", "确认计划"); if (note !== null) post("/api/plan/freeze", { note }, "计划已冻结（指纹写入账本）"); },

    "ag-resume": () => post("/api/runs/resume", { run_id: arg }, "已从断点恢复"),

    "ag-cancel": () => { const note = prompt("取消原因（可选）", ""); if (note !== null) post("/api/runs/cancel", { run_id: arg, note }, "已取消"); },

    "ag-ack": () => { const note = prompt("你核对了什么？（例如：已在模型后台确认这次没有生成 / 已计费但结果丢失）", ""); if (note) post("/api/runs/ack", { run_id: arg, note }, "已确认，可以恢复了"); },

    "ag-summary": () => post("/api/runs/summary", { week: store.snapshot?.sprint?.current_week }, "已启动周汇总草稿"),

    "md-add": async () => { try { const j = await ctx.api("/api/models/provider", { base_url: v("md.url"), api_key: v("md.key"), name: v("md.name"), style: v("md.style"), models: v("md.models") }); ["md.url", "md.key", "md.name", "md.style", "md.models"].forEach((k) => delete draft[k]); ctx.toast(j.provider.models?.length ? `已连接 ${j.provider.name}：${j.provider.models.length} 个模型` : `已保存 ${j.provider.name}，但没有拉到模型：${j.provider.last_test?.error || "请手填模型名"}`); await ctx.refresh(); } catch (e) { fail(e); } },

    "md-test": () => post("/api/models/test", { id: arg }, "测试通过"),

    "md-refresh": () => post("/api/models/refresh", { id: arg }, "模型列表已更新"),

    "md-del": () => { if (confirm("删除这个厂商和它的 Key？")) post("/api/models/remove", { id: arg }, "已删除"); },

    "md-save": () => { const roles = {}; for (const k of Object.keys(store.snapshot?.models?.roles || {})) { const x = v("md.role." + k, null); if (x === null) continue; const [provider, ...mm] = String(x).split("|"); roles[k] = x ? { provider, model: mm.join("|") } : {}; }

      const B = store.snapshot?.models?.budget || {};

      post("/api/models/roles", { roles, budget: { max_calls_per_day: +v("md.b.calls", B.max_calls_per_day), max_tokens_per_day: +v("md.b.tok", B.max_tokens_per_day), max_tokens_per_run: +v("md.b.run", B.max_tokens_per_run) },

        allow_mock: !!v("md.r.mock", store.snapshot?.models?.allow_mock !== false), rules: { send_file_text: !!v("md.r.send", store.snapshot?.models?.rules?.send_file_text !== false), allow_same_family: !!v("md.r.same", !!store.snapshot?.models?.rules?.allow_same_family) } }, "已保存"); },

  };

  if (A[op]) { if(op==="go")closeModal(); A[op](); return true; }

  return false;

}



document.addEventListener("keydown", (e) => { if (e.key === "Escape" && dlg) { e.stopPropagation(); closeModal(); } }, true);

