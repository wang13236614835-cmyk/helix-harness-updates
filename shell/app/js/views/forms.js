import { fileName, fileKind, fileKindLabel } from "../presentation.js?v=3110";
import { store } from "../core/store.js?v=3110";
// 0.4：真人写入表单（主核配对、交叉核）与“数据与文件”页。表单值存在 draft 里，实时刷新不会丢输入。

import { esc, short, av, members, fmtTs, canon, decLabel } from "./common.js?v=3110";

import { signer, canSign } from "./auth.js?v=3110";

export const draft = {};

const VERDICTS = [["complement", "互补", "A", "AI/任务内容成立，我确认"], ["revise", "修正", "E", "方向对，但要改写"], ["mismatch", "错配", "X", "不成立，写明原因"], ["defer", "暂缓", "D", "等数据或别人"]];

const DEC = { UNKNOWN: "未知", OUT_OF_DOMAIN: "超出适用域", NEEDS_EVIDENCE: "需要更多证据", HOLD: "暂缓", REJECTED: "否决", PASS: "通过" };

const v = (k, d = "") => draft[k] ?? d;

const who = (s, key, def) => `<select data-d="${key}">${members(s).map((m) => `<option ${(v(key, def)) === m.name ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>`;

export const kb = (b) => "f." + b.base_id + ".";



export function writeBlock(s, b, me) {

  const H = s.harness || {};

  if (!H.writable) return `<div class="lock">${esc(H.why || "当前数据源只读")}${H.can_adopt ? `<div style="margin-top:8px"><button class="btn pri" data-cmd="hx-adopt">接入已有项目</button></div>` : H.can_takeover ? `<div style="margin-top:8px"><button class="btn pri" data-cmd="hx-takeover">在本机建立账本</button></div>` : ""}</div>`;

  const k = kb(b);

  if (canon(b.status) === "open") {

    const verdict = v(k + "verdict"), dec = v(k + "decision", "NEEDS_EVIDENCE"), isTask = b.kind === "task";

    const refs = new Set(v(k + "refs", b.agent_refs || [])), files = s.files || [];

    const needRef = (H.needs_refs || []).includes(dec) && isTask && verdict !== "defer";

    return `<form class="hx-form" data-form="pair" data-base="${esc(b.base_id)}"><h4>主核配对 <small>由真人写入账本，写入后不能改</small></h4>

      <div class="verdicts">${VERDICTS.map(([id, t, key, tip]) => `<label class="vd ${verdict === id ? "on" : ""} vd-${id}"><input type="radio" name="verdict" value="${id}" data-d="${k}verdict" ${verdict === id ? "checked" : ""}><b>${t}</b><kbd>${key}</kbd><small>${tip}</small></label>`).join("")}</div>

      ${isTask && verdict !== "defer" ? `<label class="fl">结论（四周计划允许的取值）<select data-d="${k}decision">${(H.allowed_decisions || Object.keys(DEC)).map((d) => `<option value="${d}" ${dec === d ? "selected" : ""}>${esc(DEC[d] || d)}</option>`).join("")}</select></label>` : ""}

      ${verdict === "revise" ? `<label class="fl">修正后的内容<textarea rows="2" data-d="${k}revised" placeholder="改写成什么">${esc(v(k + "revised"))}</textarea></label>` : ""}

      <label class="fl">${verdict === "mismatch" ? "错配原因（至少 4 个字，会成为 AI 的修复记忆）" : verdict === "defer" ? "暂缓原因 / 在等什么" : "理由（可选）"}<textarea rows="2" data-d="${k}note" placeholder="你看了什么、为什么这么判">${esc(v(k + "note"))}</textarea></label>

      <div class="fl">来源引用${needRef ? `<em> · 结论为“${esc(decLabel(dec))}”时至少要一条</em>` : "（可选）"}

        ${files.length ? `<div class="refs">${files.slice(-12).reverse().map((f) => `<label><input type="checkbox" data-d="${k}ref" value="${esc("sha256:" + f.sha256 + " " + f.name)}" ${refs.has("sha256:" + f.sha256 + " " + f.name) ? "checked" : ""}> ${esc(f.name)} <span class="mono">${short(f.sha256, 8)}</span> <button type="button" class="link" data-cmd="file-view:${esc(f.sha256)}">看原文</button></label>`).join("")}</div>` : `<div class="sub">项目还没有数据文件，可以在“数据与文件”页添加，或直接在下面写 DOI / PMID / 路径。</div>`}

        <input data-d="${k}refx" value="${esc(v(k + "refx"))}" placeholder="其他引用：DOI / PMID / 文件路径，多条用分号隔开">

        ${isTask && verdict !== "defer" ? `<label class="ck"><input type="checkbox" data-d="${k}idck" ${v(k + "idck") ? "checked" : ""}> 我已打开这条 DOI / PMID 的原文核对过 <small class="sub">Helix 离线只能检查格式；只靠 DOI / PMID 判“通过”“否决”“超出适用域”时必须勾选</small></label>` : ""}</div>

      ${verdict !== "defer" ? `<label class="fl">原文摘录（可选：点上面的“看原文”，选中一句即可填入；Helix 会逐字核对，无法解析的文件记为“尚未核验”）<input data-d="${k}quote" value="${esc(v(k + "quote"))}" placeholder="例如 CPD-001,35,cAMP HTRF"></label>` : ""}

      ${signer(s, b.owner, "主核")}

      <label class="ck"><input type="checkbox" data-d="${k}ok" ${v(k + "ok") ? "checked" : ""}> 我本人已阅读来源并作出判断</label>

      <button type="button" class="btn pri" data-cmd="hx-pair:${esc(b.base_id)}" ${verdict && v(k + "ok") && canSign(s, b.owner) ? "" : "disabled"}>写入账本</button></form>`;

  }

  if (b.needs_cross && !b.cross && canon(b.status) !== "disputed") {

    const agree = v(k + "agree");

    return `<form class="hx-form" data-form="cross" data-base="${esc(b.base_id)}"><h4>交叉核 <small>另一位真人独立复核主核结论</small></h4>

      <p class="sub" style="margin:0 0 8px">主核 ${av(s, b.reviewer)} ${esc(b.reviewer || "")} 判为 <b>${esc({ complement: "互补", revise: "修正", mismatch: "错配", deferred: "暂缓" }[canon(b.status)] || b.status)}</b>${b.decision ? `，结论 <b title="${esc(b.decision)}">${esc(decLabel(b.decision))}</b>` : ""}</p>

      <div class="verdicts two">${[["yes", "一致", "我独立核查后同意"], ["no", "不一致", "需要写明理由"]].map(([id, t, tip]) => `<label class="vd ${agree === id ? "on" : ""} vd-${id === "yes" ? "complement" : "mismatch"}"><input type="radio" name="agree" value="${id}" data-d="${k}agree" ${agree === id ? "checked" : ""}><b>${t}</b><small>${tip}</small></label>`).join("")}</div>

      <label class="fl">${agree === "no" ? "不一致的理由（至少 4 个字）" : "备注（可选）"}<textarea rows="2" data-d="${k}cnote">${esc(v(k + "cnote"))}</textarea></label>

      ${signer(s, b.cross_reviewer, "交叉核")}${s.auth?.me && b.reviewer === s.auth.me.name ? `<em class="sub">你是这条的主核，不能再做交叉核</em>` : ""}

      <label class="ck"><input type="checkbox" data-d="${k}cok" ${v(k + "cok") ? "checked" : ""}> 我本人独立核查了主核材料</label>

      <button type="button" class="btn pri" data-cmd="hx-cross:${esc(b.base_id)}" ${agree && v(k + "cok") && canSign(s, b.cross_reviewer) && b.reviewer !== s.auth?.me?.name ? "" : "disabled"}>写入账本</button></form>`;

  }

  return b.cross ? `<div class="lock ok">双人核对已完成，账本里可追溯。</div>` : "";

}



export function dataView(s,ui,prefs){
 const all=s.files||[],q=String(ui.fileQuery||'').trim().toLowerCase(),kind=ui.fileKind||'all',admin=s.auth?.me?.role==='lead';
 const files=all.filter(f=>(kind==='all'||fileKind(f)===kind)&&(!q||(fileName(f.name)+' '+f.name+' '+(f.note||'')).toLowerCase().includes(q)));
 return `<div class="vh"><div><h1>项目文件</h1><p class="lede">查看项目资料，上传自己的成果和操作日志。</p></div><span class="sp"></span><button class="btn pri" data-cmd="hx-upload">上传文件</button></div><div class="file-toolbar"><input id="file-search" value="${esc(ui.fileQuery||'')}" placeholder="搜索中文名称或原文件名" aria-label="搜索文件"><button class="btn" data-cmd="files-search">搜索</button></div><div class="task-tabs">${[['all','全部文件'],['plan','计划与任务'],['evidence','科研依据'],['work','操作成果与资料']].map(([id,label])=>`<button class="btn ${kind===id?'pri':''}" data-cmd="files-kind:${id}">${label}</button>`).join('')}<span class="sub">${files.length} 个文件</span></div><div class="drop" data-cmd="hx-upload"><b>拖入文件，或点击上传</b><span>支持表格、文档、PDF、图片和工具日志，单个不超过 64 MB。</span></div><div class="file-list">${files.slice().reverse().slice(0,ui.fileLimit||60).map(f=>`<article class="card file-card"><div><span class="pill">${esc(fileKindLabel(f))}</span><h3>${esc(fileName(f.name))}</h3>${f.note?`<p>${esc(f.note)}</p>`:''}<p class="sub">${f.bytes>1048576?(f.bytes/1048576).toFixed(1)+' MB':Math.max(1,Math.round(f.bytes/1024))+' KB'}${f.added_by?' · 上传人：'+esc(f.added_by):''}${f.added_at?' · '+fmtTs(f.added_at):''}</p><details><summary>原文件信息</summary><p>原文件名：${esc(f.name)}</p><p class="mono">文件校验码：${esc(f.sha256)}</p></details></div><div class="file-actions"><button class="btn pri" data-cmd="file-view:${esc(f.sha256)}">打开文件</button><a class="btn" href="/api/file?sha=${esc(f.sha256)}">下载</a></div></article>`).join('')||'<div class="card empty">没有找到文件。可调整筛选条件，或上传一份成果。</div>'}</div>${files.length>(ui.fileLimit||60)?`<div class="row-more"><button class="btn" data-cmd="files-more">显示更多（还有 ${files.length-(ui.fileLimit||60)} 个）</button></div>`:''}`;
}

document.addEventListener('input',e=>{if(e.target.id==='file-search')storeFileQuery(e.target.value);});
function storeFileQuery(value){store.ui.fileQuery=value;}
document.addEventListener('keydown',e=>{if(e.target.id==='file-search'&&e.key==='Enter'){e.preventDefault();document.querySelector('[data-cmd="files-search"]')?.click();}});
