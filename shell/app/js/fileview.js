import { fileName } from "./presentation.js?v=3301";
// 0.5.2 看原文：在页面里打开已登记文件（表格 / PDF / 文本 / 图片），选中一句即可作为“原文摘录”。只读。

import { store, esc } from "./core/store.js?v=3301";

import { draft, kb } from "./views/forms.js?v=3301";

import { canon } from "./views/common.js?v=3301";



let ctx = { toast: () => {} }, cur = null;

export function initFileView(c) { ctx = c; }

const $ = (q, r = document) => r.querySelector(q);

const sizeTxt = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : n > 1024 ? Math.round(n / 1024) + " KB" : (n || 0) + " B");



// 当前选中、可写、待主核的条目（摘录要写给它）

function target() {

  const sel = store.ui?.sel || "", s = store.snapshot; if (!sel.startsWith("base:") || !s?.harness?.writable) return null;

  const b = (s.items || []).find((x) => x.base_id === sel.slice(5)); return b && canon(b.status) === "open" ? b : null;

}



export async function openFile(sha) {

  close();

  const f = (store.snapshot?.files || []).find((x) => x.sha256 === sha) || { sha256: sha, name: "文件" };

  cur = { sha, f, sheet: 0, page:0, q: "", mode: "text", data: null, err: "" };

  draw();

  try {

    const r = await fetch("/api/file/preview?sha=" + encodeURIComponent(sha), { cache: "no-store" });

    const j = await r.json(); if (!j.ok) throw new Error(j.error || r.status);

    if (!cur || cur.sha !== sha) return;

    cur.data = j; cur.mode = j.kind === "pdf" && !j.readable ? "page" : "text";

  } catch (e) { if (cur) cur.err = "打不开：" + (e.message || e); }

  draw();

}

function close() { $(".fvm")?.remove(); cur = null; }



function body() {

  const d = cur.data, q = cur.q.trim().toLowerCase();

  if (cur.err) return `<div class="empty">${esc(cur.err)}</div>`;

  if (!d) return `<div class="empty">正在读取…</div>`;

  const url = "/api/file?inline=1&sha=" + encodeURIComponent(cur.sha);

  const mark = (t) => { const s = esc(t); if (!q) return s; const qq = esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); return s.replace(new RegExp(qq, "gi"), (m) => `<mark>${m}</mark>`); };

  if (d.kind === "table") {

    const sh = d.sheets[cur.sheet] || d.sheets[0] || { rows: [], total: 0 };

    const rows = sh.rows.map((r, i) => [r, i]).filter(([r, i]) => !q || i === 0 || r.join(" ").toLowerCase().includes(q));

    return `${d.sheets.length > 1 ? `<div class="fv-sheets">${d.sheets.map((x, i) => `<button class="chip ${i === cur.sheet ? "on" : ""}" data-fv="sheet:${i}">${esc(x.name)}</button>`).join("")}</div>` : ""}

      <div class="lt-row"><button class="btn sm" data-fv="page:${Math.max(0,(cur.page||0)-1)}" ${!cur.page?'disabled':''}>上一页</button><span>第${(cur.page||0)+1}/${Math.max(1,Math.ceil(rows.length/200))}页 · 已读取${sh.rows.length}/${sh.total}行</span><button class="btn sm" data-fv="page:${(cur.page||0)+1}" ${((cur.page||0)+1)*200>=rows.length?'disabled':''}>下一页</button></div>
      <table class="fv-tbl"><tbody>${rows.slice((cur.page||0)*200,((cur.page||0)+1)*200).map(([r, i]) => `<tr><th><button class="link" data-fv="row:${i}" title="把这一整行作为摘录">${i + 1}</button></th>${r.map((c) => `<td>${mark(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>

      ${sh.total > sh.rows.length ? `<p class="sub">当前为快速预览；点击“读取全文”可载入全部行再分页查看。</p>` : ""}`;

  }

  if (d.kind === "pdf") {

    if (cur.mode === "page") return `<iframe class="fv-pdf" src="${url}" title="原版页面"></iframe>`;

    return (d.pages || []).map((p, i) => `<div class="fv-page"><small>第 ${i + 1} 页</small><pre>${mark(p)}</pre></div>`).join("") + (d.truncated ? `<p class="sub">内容过长，只显示前一部分。</p>` : "");

  }

  if (d.kind === "image") return `<img class="fv-img" src="${url}" alt="${esc(d.name)}">`;

  if (d.kind === "text") return `<div class="fv-page"><pre>${mark(d.text || "")}</pre></div>${d.truncated ? `<p class="sub">文件较大，只显示前一部分。</p>` : ""}`;

  return `<div class="empty">${esc(d.error || "这种文件不能在页面里预览，请下载后用原软件打开。")}</div>`;

}



function draw() {

  if (!cur) return;

  let el = $(".fvm");

  if (!el) { el = document.createElement("div"); el.className = "hxm fvm"; document.body.appendChild(el); }

  const d = cur.data, b = target(), name = fileName(d?.name || cur.f.name);

  const badge = !d ? "" : d.readable ? `<span class="pill ok" title="摘录会被逐字核对">可逐字核验</span>` : `<span class="pill warn" title="${esc(d.error || "")}">不能逐字核验</span>`;

  const keepQ = $(".fv-q", el)?.value ?? cur.q;

  el.innerHTML = `<div class="hxm-box fv" role="dialog" aria-label="看原文">

    <header class="fv-head"><div><b>${esc(name)}</b><span class="mono">${esc(cur.sha.slice(0, 12))} · ${sizeTxt(d?.bytes ?? cur.f.bytes)}</span></div>${badge}<span class="sp"></span>

      ${d&&d.kind!=='image'?`<button class="btn sm" data-fv="full" ${cur.loading||d.full_read?'disabled':''}>${cur.loading?'正在读取…':d.full_read?'已完整读取':'读取全文'}</button>`:''}<a class="btn sm" href="/api/file?inline=1&sha=${esc(cur.sha)}" target="_blank" rel="noopener">新标签打开</a><a class="btn sm" href="/api/file?sha=${esc(cur.sha)}">下载</a><button class="x" data-fv="close" aria-label="关闭">×</button></header>

    <div class="fv-tools">${d && d.kind !== "image" ? `<input class="fv-q" placeholder="在文件里查找…" value="${esc(keepQ)}">` : ""}${d?.kind === "pdf" ? `<div class="seg"><button class="${cur.mode === "text" ? "on" : ""}" data-fv="mode:text">文字层（可摘录）</button><button class="${cur.mode === "page" ? "on" : ""}" data-fv="mode:page">原版页面</button></div>` : ""}

      <span class="sub">${b ? `选中文字（或点行号选整行），再点右下角按钮，写入 <b>${esc(b.task_id || b.base_id)}</b> 的原文摘录` : "选中文字后可复制；在收件箱里选中待主核条目时，可直接填入原文摘录"}</span></div>

    <div class="fv-body">${body()}</div>

    <footer class="fv-foot"><span class="sub" id="fv-selinfo">还没有选中文字</span><span class="sp"></span><button class="btn" data-fv="close">关闭</button><button class="btn pri" data-fv="use">${b ? "用作原文摘录" : "复制选中文字"}</button></footer></div>`;

  const qi = $(".fv-q", el); if (qi && document.activeElement !== qi && cur.q) { qi.focus(); qi.setSelectionRange(qi.value.length, qi.value.length); }

}



const picked = () => String(window.getSelection?.() || "").replace(/\s+/g, " ").trim();

let rowPick = "";

function use(text) {

  text = (text || "").slice(0, 400); if (!text) return ctx.toast("先在文件里选中一句原文，或点行号选整行");

  const b = target();

  if (!b) { navigator.clipboard?.writeText(text).then(() => ctx.toast("已复制选中文字"), () => ctx.toast("浏览器不允许复制，请按 Ctrl+C")); return; }

  const k = kb(b), ref = "sha256:" + cur.sha + " " + (cur.data?.name || cur.f.name);

  draft[k + "quote"] = text;

  const refs = new Set(draft[k + "refs"] ?? b.agent_refs ?? []); refs.add(ref); draft[k + "refs"] = [...refs];

  close(); store.ui_({}); ctx.toast("已填入原文摘录，并勾选了这份文件作为来源");

}



document.addEventListener("click", (e) => {

  if (!cur) return; const t = e.target.closest?.("[data-fv]");

  if (!t) { if (e.target.classList?.contains("fvm")) close(); return; }

  const [op, arg] = t.dataset.fv.split(":");

  if (op === "close") return close();

  if (op === "sheet") { cur.sheet = +arg; cur.page=0; return draw(); }
  if (op === 'page') {cur.page=Math.max(0,+arg);return draw();}
  if (op === 'full') {const sha=cur.sha;cur.loading=true;draw();fetch('/api/file/preview?full=1&sha='+encodeURIComponent(sha),{cache:'no-store'}).then(r=>r.json()).then(j=>{if(!cur||cur.sha!==sha)return;if(!j.ok)throw Error(j.error||'读取失败');cur.data=j;cur.page=0;}).catch(e=>{if(cur?.sha===sha)cur.err=e.message;}).finally(()=>{if(cur?.sha===sha){cur.loading=false;draw();}});return;}

  if (op === "mode") { cur.mode = arg; return draw(); }

  if (op === "row") { const sh = cur.data.sheets[cur.sheet] || cur.data.sheets[0]; rowPick = (sh.rows[+arg] || []).join(","); $("#fv-selinfo").textContent = "已选整行：" + rowPick.slice(0, 60); return; }

  if (op === "use") return use(picked() || rowPick);

});

document.addEventListener("selectionchange", () => { if (!cur) return; const t = picked(), el = $("#fv-selinfo"); if (el && t) { rowPick = ""; el.textContent = "已选中：" + t.slice(0, 60) + (t.length > 60 ? "…" : ""); } });

document.addEventListener("input", (e) => { if (cur && e.target.classList?.contains("fv-q")) { cur.q = e.target.value;cur.page=0; clearTimeout(cur.tm); cur.tm = setTimeout(draw, 200); } });

document.addEventListener("keydown", (e) => { if (cur && e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); } }, true);

