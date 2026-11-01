import { esc, av, members, workload } from "./common.js?v=3301";

import { store } from "../core/store.js?v=3301";

import { flows, STAGE, isMineTurn } from "../relay.js?v=3301";

export function side(s, ui) {

  if (!s) return "";

  const sp = s.sprint || {}, items = s.items || [];

  const wk = flows(s, store.prefs, "week").map((f) => { const t = f.t, st = STAGE[f.stage], turn = isMineTurn(f); return `<div class="node tk ${ui.sel === "task:" + f.id ? "on" : ""} ${turn ? "rl-turn" : ""}" data-sel="task:${esc(f.id)}">${av(s, t.owner)}<span class="side-task-name" title="${esc(t.deliverable||f.id)}">${esc(t.deliverable||f.id)}</span><span class="c rl-c ${turn ? "warn" : st.tone}">${turn ? "轮到你" : st.short}</span></div>`; }).join("");

  const cur = (sp.weeks || []).find((w) => w.week === sp.current_week) || {};

  const exc = items.filter((b) => b.track === "V4_EXCEPTION");

  const mem = members(s);

  return `<h4>本周任务<span data-cmd="welcome" style="cursor:pointer" title="第 ${sp.current_week ?? "?"} 周 ${esc(cur.track_label || "")}：查看全部安排">${sp.current_week ? `第 ${sp.current_week} 周 · ${sp.days_left===0?"今天截止":sp.days_left<0?"已结束":"剩 "+(sp.days_left ?? "—")+" 天"}` : ""}</span></h4><div class="tree">${wk || `<div class="node">本周没有任务</div>`}</div>

  ${s.harness?.kind === "local" || s.generic_mode ? "" : `<h4>V4 异常任务<span>${exc.length}</span></h4><div class="tree">${exc.map((b) => `<div class="node l1 ${ui.sel === "base:" + b.base_id ? "on" : ""}" data-sel="base:${esc(b.base_id)}">${av(s, b.owner)}${esc(b.task_id)}<span class="c">${b.status === "open" ? "待负责人处理" : "…"}</span></div>`).join("") || `<div class="node l1">待创建任务</div>`}</div>`}

  <h4>成员<span data-view="people" style="cursor:pointer" title="管理成员名册">${mem.length} 人</span></h4><div class="tree">${mem.map((m) => `<div class="node ${ui.sel === "member:" + m.name ? "on" : ""}" data-sel="member:${esc(m.name)}">${av(s, m.name)}${esc(m.name)}${m.registered ? "" : ` <small style="opacity:.6">未登记</small>`}<span class="c">${workload(s, m.name).todo || ""}</span></div>`).join("")}<div class="node l1" data-view="people" style="opacity:.75">查看团队</div></div>`;

}

