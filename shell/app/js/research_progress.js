import { esc } from "./core/store.js?v=3301";

const text = (v) => typeof v === "string" || typeof v === "number" ? String(v) : "";
const list = (v) => Array.isArray(v) ? v : v == null ? [] : [v];
const label = (v) => text(v) || text(v?.label) || text(v?.title) || text(v?.summary) || text(v?.text);

// Snapshot-owned, read-only summary: never infer scientific completion from task counts.
export function researchProgressCard(s = {}) {
  const raw = s.research_progress;
  const p = typeof raw === "string" ? { summary: raw } : raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const summary = text(p.summary) || text(p.text);
  const date = text(p.date) || text(p.updated_at) || text(p.as_of);
  const evidence = list(p.evidence || p.evidence_labels).map(label).filter(Boolean);
  const pending = list(p.pending_human_work || p.pending_human || p.pending).map(label).filter(Boolean);
  return `<section class="card" aria-label="研究进展摘要" style="margin-bottom:14px;overflow-wrap:anywhere">
    <h3>研究进展摘要<small>来自当前项目快照，不替代真人实验与交叉复核</small></h3>
    <p style="white-space:pre-wrap">${esc(summary || "当前服务尚未提供研究进展摘要；请以原始成果和审核账本为准。")}</p>
    <p class="sub">摘要日期：${esc(date || "未提供")}</p>
    <h4>证据标签</h4><div class="chips">${evidence.map((v) => `<span class="chip">${esc(v)}</span>`).join("") || '<span class="sub">尚未提供证据标签</span>'}</div>
    <h4>待真人完成</h4>${pending.length ? `<ul>${pending.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : '<p class="sub">尚未提供待办清单，不表示真人工作已经完成。</p>'}
  </section>`;
}
