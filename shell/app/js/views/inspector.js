import { esc, short, av, statusPill, bondTag, fmtTs, crossState, personOf, workload, ROLE_LABEL, decLabel, evLabel, actorKind, portLabel, ceilLabel, dutyChip, dutyBoard, checkinOf, ckPill, ckBar, canCheckin, CK_STATE } from "./common.js?v=3110";

import { writeBlock } from "./forms.js?v=3110";

import { store } from "../core/store.js?v=3110";

const kv = (rows) => `<dl class="kv">${rows.filter((r) => r[1] !== undefined && r[1] !== null && r[1] !== "").map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;

export function inspector(s, sel) {

  if (!s) return `<div class="body"><div class="empty">等待数据端口…</div></div>`;

  if (!sel) return `<div class="body"><p style="margin-top:0"><b>检查器</b></p><p class="mono">点任意条目、任务、关卡、智能体、MCP 服务器或账本事件，在这里看详情。</p>

    ${kv([["项目", esc(s.project?.id)], ["V4 编号", esc(s.project?.v4_id)], ["靶点", esc(s.project?.target)], ["结论上限", s.project?.claim_ceiling ? esc(ceilLabel(s.project.claim_ceiling)) : ""], ["数据端口", esc(s.mode === "offline" ? "连接断开" : portLabel(s.port?.kind))], ["快照", `<span class="mono">${short(s.snapshot_hash, 16)}</span>`], ["生成", fmtTs(s.generated_at)]])}</div>`;

  const [type, ...rest] = sel.split(":"); const id = rest.join(":");

  if (type === "base") {

    const b = (s.items || []).find((x) => x.base_id === id); if (!b) return `<div class="body empty">这条条目不在当前快照里</div>`;

    return `<div class="body"><p style="margin-top:0">${bondTag(b)} ${statusPill(b)}</p><p style="font-size:14px;font-weight:600">${esc(b.text)}</p>

      ${kv([["任务", esc(b.task_id)], ["主核", b.owner ? `${av(s, b.owner)} ${esc(b.owner)}` : ""], ["交叉核", b.cross_reviewer ? `${av(s, b.cross_reviewer)} ${esc(b.cross_reviewer)}` : ""], ["双人状态", crossState(b)], ["主核结论", b.decision ? `<span title="${esc(b.decision)}">${esc(decLabel(b.decision))}</span>` : ""], ["配对人", esc(b.reviewer)], ["理由", esc(b.note)], ["AI 置信度", Math.round((b.confidence ?? .5) * 100) + "%"], ["来源", esc(b.source)], ["原关卡", esc(b.legacy_gate)], ["允许结论", esc((b.allowed_result || []).map(decLabel).join(" / "))]])}

      ${b.rationale ? `<p class="mono" style="margin-top:10px">${esc(b.rationale)}</p>` : ""}

      ${(b.bond_reasons || []).length ? `<p><b>为什么是 ${b.bond === "GC" ? "G–C" : "A–T"}</b></p><ul style="margin:0;padding-left:18px">${b.bond_reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}

      ${b.cross ? `<p><b>交叉核</b> ${av(s, b.cross.reviewer)} ${esc(b.cross.reviewer)} · ${b.cross.agree ? "一致" : "不一致"}<br><span class="mono">${esc(b.cross.note)}</span></p>` : ""}

      ${(b.source_refs || []).length ? `<p><b>原件</b></p>${b.source_refs.map((r) => `<div class="mono">${esc(typeof r === "string" ? r : r.ref || JSON.stringify(r))}</div>`).join("")}` : ""}

      ${writeBlock(s, b, store.prefs.me)}</div>`;

  }

  if (type === "task") {

    const t = (s.sprint?.pairs || []).find((x) => x.task_id === id) || {}; const bs = (s.items || []).filter((b) => b.task_id === id);

    return `<div class="body"><p style="margin-top:0"><span class="tag tk">任务</span><b>${esc(id)}</b> · ${esc(t.track_label)}</p><p style="font-size:14px;font-weight:600">${esc(t.deliverable)}</p>

      ${kv([["职能", s.plan ? dutyChip(s, ((s.plan.tasks || []).find((x) => x.task_id === id) || {}).duty) : ""], ["主核", `${av(s, t.owner)} ${esc(t.owner)}`], ["交叉核", `${av(s, t.cross_reviewer)} ${esc(t.cross_reviewer)}`], ["截止", esc(t.end)], ["验收", esc(t.acceptance)], ["任务表状态", esc(t.status)]])}

      ${s.team ? (() => { const c = checkinOf(s, id), can = canCheckin(s, t, store.prefs) && s.harness?.writable;

        return `<p style="display:flex;align-items:center;gap:8px"><b>打卡</b>${ckPill(s, id)}${ckBar(s, id)}<span class="sp" style="flex:1"></span>${can ? `<button class="btn sm pri" data-cmd="ck-open:${esc(id)}">打卡</button>` : ""}</p>

        ${(c?.history || []).map((h) => `<div class="ck-h"><span class="pill ${(CK_STATE[h.state] || [])[1] || ""}">${esc((CK_STATE[h.state] || [h.state])[0])}${h.state === "blocked" ? "" : " " + (h.progress ?? 0) + "%"}</span>${av(s, h.actor)}<b>${esc(h.actor || "")}</b><small>${h.as === "cross" ? "交叉核" : "主核"} · ${fmtTs(h.ts)}</small>${h.note ? `<div>${esc(h.note)}</div>` : ""}${(h.files || []).map((f) => `<button class="link" data-cmd="file-view:${esc(f.sha256)}">📎 ${esc(f.name)}</button>`).join(" ")}</div>`).join("") || `<div class="sub">还没有人打卡${can ? "，点右边“打卡”记一下进度" : ""}</div>`}`; })() : ""}

      <p><b>Helix 条目 · ${bs.length}</b></p>${bs.map((b) => `<div class="pair" data-sel="base:${esc(b.base_id)}" style="grid-template-columns:1fr auto"><div><small>${esc(b.text).slice(0, 60)}</small></div>${statusPill(b)}</div>`).join("") || `<div class="empty">尚未播种${s.harness?.seedable ? `<div style="margin-top:8px"><button class="btn pri" data-cmd="hx-seed">播种本周任务</button></div>` : ""}</div>`}</div>`;

  }

  if (type === "agent") {

    const a = (s.agents || []).find((x) => x.id === id) || { id, name: { human: "真人", proof: "校对（规则）" }[id] || id };

    return `<div class="body"><p style="margin-top:0;font-size:14px;font-weight:700">${esc(a.name)}</p>${kv([["层级", a.layer ? "第 " + a.layer + " 层" : ""], ["职责", esc(a.role)], ["模型", esc(((s.models?.roles || {})[id] || {}).model || "无（规则或真人）")], ["学自", esc(a.learned_from)]])}<p class="mono">模型在“模型工坊”按角色替换；批评者必须与起草者不同厂商。</p></div>`;

  }

  if (type === "mcp") {

    const m = (s.mcp || []).find((x) => x.id === id) || {};

    return `<div class="body"><p style="margin-top:0;font-size:14px;font-weight:700">${esc(m.name)}</p>${kv([["类型", esc(m.kind)], ["范围", esc(m.scope)], ["权限", esc(m.perm)], ["状态", esc({ planned: "规划中", candidate: "候选（待评估）", live: "已接入" }[m.status] || m.status)]])}

      <p class="mono">MCP 三类能力：工具（可调用）· 资源（只读上下文）· 提示模板。客户端发起的模型采样、向用户追问，都必须由真人批准。</p></div>`;

  }

  if (type === "event") {

    const e = (s.ledger?.tail || []).find((x) => String(x.seq) === id) || {};

    return `<div class="body"><p style="margin-top:0;font-size:14px;font-weight:700" title="${esc(e.type)}">${esc(evLabel(e.type))} <small class="mono">${esc(e.type)}</small></p>${kv([["序号", e.seq], ["谁", `${esc(e.actor)}（${esc(actorKind(e.actor_kind))}）`], ["时间", fmtTs(e.ts)], ["哈希", `<span class="mono">${esc(e.hash)}</span>`]])}</div>`;

  }

  if (type === "gate") {

    const g = (s.skeleton?.gates || []).find((x) => x.id === id) || {};

    return `<div class="body"><p style="margin-top:0;font-size:14px;font-weight:700">发现阶段关卡 ${esc(id)}</p>${kv([["状态", esc(g.raw_status)], ["迁移", esc(g.migration_status)], ["证据数", esc(g.evidence_count)]])}<div class="lock">关卡只由 V4 权威库与真人决定；Helix 不会修改关卡。</div></div>`;

  }

  if (type === "exception") {

    const e = (s.skeleton?.exceptions || []).find((x) => x.task_id === id) || {};

    return `<div class="body"><p style="margin-top:0;font-size:14px;font-weight:700">${esc(id)}</p>${kv([["对象", esc(e.object)], ["类别", esc(e.category)], ["目标", esc(e.target)], ["状态", esc(e.status)], ["优先", esc(e.priority)]])}</div>`;

  }

  if (type === "witness") {

    const w = (s.witnesses || [])[+id] || {};

    return `<div class="body"><p style="margin-top:0;font-size:14px;font-weight:700">${esc(w.label)}</p>${kv([["路径", `<span class="mono">${esc(w.path)}</span>`], ["SHA-256", `<span class="mono">${esc(w.sha256)}</span>`], ["大小", esc(w.bytes)], ["修改", fmtTs(w.mtime)]])}</div>`;

  }

  if (type === "member") {

    const p = personOf(s, id), w = workload(s, id), d = (s.people?.discovered || []).find((x) => x.code === id);

    const live = s.mode === "live" && s.people?.editable !== false;

    return `<div class="body"><p style="margin-top:0;font-size:15px;font-weight:700">${av(s, id)} ${esc(id)}</p>${(() => { const r = s.plan ? dutyBoard(s).rows.find((x) => x.name === id) : null; return r ? kv([["职能", r.duties.length ? r.duties.map((k) => dutyChip(s, k)).join(" ") : `<span class="duty none">未分配</span>`], ["下一步", r.next ? `<button class="link" data-sel="${esc(r.next.sel)}">${esc(r.next.t)}</button>` : "暂无待办"]]) : ""; })()}${kv([["身份", p ? "已登记真人" : "未登记（代号）"], ["审核权限", p ? esc(ROLE_LABEL[p.role] || p.role) : ""], ["备注", p ? esc(p.note) : ""], ["主核", w.primary], ["交叉核", w.cross], ["待处理", w.todo], ["本周任务", w.tasks.map((t) => esc(t.task_id)).join("、") || "—"]])}

      ${live ? (p ? `<button class="btn" data-cmd="person-edit:${esc(p.id)}">编辑成员</button>` : d ? `<button class="btn pri" data-cmd="person-claim:${esc(d.key)}:${esc(d.code)}">登记为真人</button>` : "") : ""}

      <button class="btn" data-cmd="me:${esc(id)}" style="margin-left:6px">这是我</button></div>`;

  }

  return `<div class="body"><p class="mono">${esc(sel)}</p></div>`;

}

