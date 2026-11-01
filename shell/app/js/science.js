import { store, esc } from "./core/store.js?v=3301";
import { fileName } from "./presentation.js?v=3301";



const rows = (f) => (f || []).map(x => `<tr><td><a href="${esc(x.url)}" download>${esc(fileName(x.path.split('/').pop()))}</a><details><summary>原文件位置与校验信息</summary><p>${esc(x.path)}</p><p class="mono">${esc(x.sha256)}</p></details></td><td>${(x.bytes / 1024).toFixed(1)} KB</td></tr>`).join("");

export function researchView(s, ui) {

  const b = s.research_bundle || {}, found = ui.researchResults;

  const groups = { v4: "V4 权威与证据", sprint: "当前研究与四周计划", gnn: "GNN 筛选与人审", literature: "文献库", "workbench-projects": "MASH 原工作台项目", "workbench-data": "原始数据与计算材料", qualification: "工具资格核验材料", "workbench-authority": "科研权威指针记录" };

  const papers = ui.researchPapers;

  return `<div class="vh"><div><h1>${esc(s.project?.name || "项目")} · 研究资料</h1><p class="lede">读取当前项目登记的资料，保留来源指纹；原件只读。</p></div></div>

    <div class="grid g4">${Object.entries(b.groups || {}).map(([k,g])=>`<div class="card"><h3>${esc(groups[k] || k)}</h3><b>${g.files} 个文件</b>${g.bytes ? `<p>${(g.bytes / 1048576).toFixed(1)} MB</p>` : ""}</div>`).join("")}</div>

    <div class="card" style="margin-top:16px"><h3>科研文件<small>${b.file_count || 0} 个项目资料文件 · ${b.pdf_files || 0} 份 PDF（原文、报告、笔记） · ${b.papers || 0} 条文献库记录</small></h3>

      <label class="fl">按文件名或路径查找<input id="research-query" value="${esc(ui.researchQuery || '')}" placeholder="例如 ADORA2A、MCULE、任务表"></label>

      <p><button class="btn pri" data-science="files">查找文件</button> <button class="btn" data-science="pdfs">查看 PDF 文件</button> <button class="btn" data-science="papers">查文献记录</button></p><p>文献库记录关联的本地 PDF 为 ${b.pdfs || 0} 份；与上方科研目录中的 PDF 数量分开统计。</p>

      ${ui.researchBusy ? '<p role="status">正在查找…</p>' : ''}

      ${found ? `<p>共 ${found.total} 项，当前显示 ${found.offset + 1}–${Math.min(found.offset + found.files.length, found.total)}</p><div style="overflow:auto"><table><thead><tr><th>文件（点击下载原件）</th><th>大小</th></tr></thead><tbody>${rows(found.files)}</tbody></table></div>${found.offset + found.files.length < found.total ? '<p><button class="btn" data-science="more">下一页</button></p>' : ''}` : '<p>输入关键词或直接查找，即可打开当前项目的真实科研文件。</p>'}

      ${papers ? `<h3>文献记录 · ${papers.total} 条</h3>${papers.papers.map(p=>`<article style="padding:12px 0;border-top:1px solid var(--line)"><b>${esc(p.title_cn || p.title)}</b><p>${esc(p.year || '')} · DOI ${esc(p.doi || '未登记')} · PMID ${esc(p.pmid || '未登记')} · ${p.pdf_included ? 'PDF 已随包' : '包内无 PDF 原文'}</p><p>${esc(p.abstract || '没有摘要')}</p>${p.pdf_included ? `<a href="/api/research/file?path=${encodeURIComponent(p.bundled_pdf)}" download>下载原文 PDF</a>` : ''}</article>`).join('')}` : ''}

      ${ui.researchError ? `<div class="lock bad">${esc(ui.researchError)}</div>` : ''}</div>`;

}



function vinaPanel(ui) {

  const fields = (kind, label, values) => `<div><b>${label}</b><div class="grid g3">${['x','y','z'].map((axis,i)=>`<label class="fl">${axis.toUpperCase()}<input id="vina-${kind}-${axis}" type="number" step="any" value="${esc(values?.[i] ?? '')}"></label>`).join('')}</div></div>`;

  return `<div class="card vina-panel" style="margin-top:16px"><h3>AutoDock Vina CPU 对接</h3><p>使用准备好的受体、配体 PDBQT 和对接盒参数。历史示例仅用于验证工具运行，结果不自动通过关卡。</p>

    <p><button class="btn" data-science="vina-example" ${ui.scienceBusy ? 'disabled' : ''}>加载随包历史示例</button> ${ui.vinaExample ? `<span class="pill warn">${esc(ui.vinaExample)}</span>` : ''}</p>

    <div class="grid g2"><label class="fl">受体 PDBQT<input type="file" id="vina-receptor" accept=".pdbqt"><small>${esc(ui.vinaReceptorName || '未选择')}</small></label><label class="fl">配体 PDBQT<input type="file" id="vina-ligand" accept=".pdbqt"><small>${esc(ui.vinaLigandName || '未选择')}</small></label></div>

    <div class="grid g2">${fields('center','盒中心（Å）',ui.vinaCenter)}${fields('size','盒尺寸（Å）',ui.vinaSize)}</div>

    <div class="grid g2"><label class="fl">搜索强度（1–8）<input id="vina-exhaustiveness" type="number" min="1" max="8" value="${esc(ui.vinaExhaustiveness || 2)}"></label><label class="fl">随机种子<input id="vina-seed" type="number" min="0" value="${esc(ui.vinaSeed ?? 2026)}"></label></div>

    <p><button class="btn pri" data-science="vina" ${ui.scienceBusy ? 'disabled' : ''}>运行 Vina 对接</button></p><small>本机 CPU 计算；保留输入指纹、构象、真实评分与日志。评分单位为 kcal/mol。</small></div>`;

}



export function toolsView(s, ui) {

  const tools = s.science_tools || [];

  return `<div class="vh"><div><h1>科学工具</h1><p class="lede">使用随包的真实开源工具。计算与检索结果未经真人核验，不改变科研关卡。</p></div></div>

    ${ui.humanRunId && ui.humanProjectId===s.project?.id ? `<div class="lock info">正在登记真人实操：${esc(ui.humanTaskId)}。本页的实际输入、参数与输出会保存到这条记录。<button class="btn sm" data-cmd="hr-record:${esc(ui.humanRunId)}">返回实操记录</button></div>` : ""}<div class="grid g3">${tools.map(t=>`<div class="card"><h3>${esc(t.name)} <span class="pill ${t.version ? 'ok' : 'warn'}">${t.version ? '已安装 ' + esc(t.version) : '未安装'}</span></h3><p>${esc(t.scope)}</p><p>${t.offline ? '可离线计算' : '公开数据库查询需要联网'}</p><a href="${esc(t.source)}" target="_blank" rel="noopener">开源项目与许可证</a></div>`).join('')}</div>

    ${vinaPanel(ui)}<div class="card" style="margin-top:16px"><h3>RDKit 分子检查</h3><label class="fl">SMILES<input id="science-smiles" value="${esc(ui.scienceSmiles ?? 'CCO')}" placeholder="输入结构 SMILES"></label><label class="fl">第二个 SMILES（可选，用于相似度）<input id="science-other" value="${esc(ui.scienceOther || '')}"></label><p><button class="btn pri" data-science="rdkit" ${ui.scienceBusy ? 'disabled' : ''}>运行分子检查</button></p></div>

    <div class="grid g2" style="margin-top:16px"><div class="card"><h3>ToolUniverse</h3><label class="fl">PubChem CID<input id="science-cid" type="number" value="${esc(ui.scienceCid || '2244')}"></label><p><button class="btn pri" data-science="tooluniverse" ${ui.scienceBusy ? 'disabled' : ''}>查询 PubChem</button> <button class="btn" data-science="catalog" ${ui.scienceBusy ? 'disabled' : ''}>查看真实工具目录</button></p></div>

    <div class="card"><h3>BioMCP 文献检索</h3><label class="fl">公开检索词<input id="science-query" value="${esc(ui.scienceQuery || 'MASH ADORA2A')}"></label><p><button class="btn pri" data-science="biomcp" ${ui.scienceBusy ? 'disabled' : ''}>检索文献（最多 5 条）</button></p></div></div>

    <div class="card" style="margin-top:16px"><h3>运行结果</h3>${ui.scienceBusy ? '<p role="status">真实工具执行中…</p>' : ''}${ui.scienceError ? `<div class="lock bad">${esc(ui.scienceError)}</div>` : ''}${ui.scienceResult ? `<p class="pill warn">工具输出 · 尚未人工核验</p><p>${Object.entries(ui.scienceResult.artifacts || {}).map(([name,url])=>`<a class="btn" href="${esc(url)}" download>${esc({'pose.pdbqt':'下载对接构象','stdout.txt':'下载运行日志','run.json':'下载计算记录'}[name] || name)}</a>`).join(' ')}</p><pre style="white-space:pre-wrap;word-break:break-word;max-height:600px;overflow:auto">${esc(JSON.stringify(ui.scienceResult, null, 2))}</pre>` : '<p>上方按钮会实际运行工具，不展示预设结果。</p>'}</div>`;

}



export function initScience() {

  document.addEventListener('change', async e => {

    if (!['vina-receptor','vina-ligand'].includes(e.target.id)) return;

    const file = e.target.files?.[0]; if (!file) return;

    const receptor = e.target.id === 'vina-receptor';

    if (file.size > (receptor ? 3000000 : 500000)) { store.ui_({scienceError:'PDBQT 文件超过当前入口的大小上限'}); return; }

    const content = await file.text();

    store.ui_({[receptor ? 'vinaReceptor' : 'vinaLigand']:content,[receptor ? 'vinaReceptorName' : 'vinaLigandName']:file.name,vinaExample:''});

  });

  document.addEventListener('click', async e => {

    const button = e.target.closest('[data-science]'); if (!button) return;

    const action = button.dataset.science;

    const read = id => document.getElementById(id)?.value || '';

    if (action === 'vina-example') {

      if (store.ui.scienceBusy) return;

      store.ui_({scienceBusy:true,scienceError:''});

      try { const r=await fetch('/api/tools/vina/example',{cache:'no-store'}); const x=await r.json(); if(!r.ok || !x.ok) throw Error(x.error || '示例不可用'); store.ui_({vinaReceptor:x.receptor_text,vinaLigand:x.ligand_text,vinaReceptorName:'历史 3BEJ 受体',vinaLigandName:'历史 MUF 参考配体',vinaCenter:x.center,vinaSize:x.size,vinaExample:x.label}); }

      catch(err) { store.ui_({scienceError:String(err.message || err)}); }

      finally { store.ui_({scienceBusy:false}); }

      return;

    }

    if (['files','more','papers','pdfs'].includes(action)) {

      const projectId = store.snapshot?.project?.id;
      const currentProject = () => store.snapshot?.project?.id === projectId;
      const q = action === 'pdfs' ? '.pdf' : read('research-query');
      const searchQuery=action==='papers'?q:({'任务表':'CURRENT_SPRINT_TASKS','本轮任务':'CURRENT_SPRINT_TASKS','后续任务':'CURRENT_FOLLOWUP_TASKS','研究进展':'DISCOVERY_STATE','实验验证':'VALIDATION_STATE','个人打卡':'CHECKIN','工作计划':'CURRENT_SPRINT_'}[q.trim()]||q);

      const offset = action === 'more' ? (store.ui.researchResults?.offset || 0) + 40 : 0;

      store.ui_({researchQuery:q,researchBusy:true,researchError:''});

      try { const r = await fetch('/api/research' + (action === 'papers' ? '/papers' : '') + '?q=' + encodeURIComponent(searchQuery) + '&offset=' + offset, {cache:'no-store'}); const data=await r.json(); if(!r.ok) throw Error(data.error); if(!currentProject()) return; store.ui_({[action === 'papers' ? 'researchPapers' : 'researchResults']:data}); }

      catch(err) { if(currentProject()) store.ui_({researchError:String(err.message || err)}); }

      finally { if(currentProject()) store.ui_({researchBusy:false}); }

      return;

    }

    if (store.ui.scienceBusy) return;

    const values={scienceSmiles:read('science-smiles'),scienceOther:read('science-other'),scienceCid:read('science-cid'),scienceQuery:read('science-query')};

    if(action==='vina') {

      const vector = kind => ['x','y','z'].map(axis => { const value=read('vina-'+kind+'-'+axis); return value.trim() ? Number(value) : null; });

      values.vinaCenter=vector('center'); values.vinaSize=vector('size'); values.vinaExhaustiveness=Number(read('vina-exhaustiveness')); values.vinaSeed=Number(read('vina-seed'));

      if([...values.vinaCenter,...values.vinaSize].some(x=>x===null || !Number.isFinite(x))) { store.ui_({scienceError:'请填写完整的对接盒中心与尺寸'}); return; }

    }

    store.ui_({...values,scienceBusy:true,scienceError:'',scienceResult:null});

    try {

      const toolProjectId=store.snapshot?.project?.id;
      const body={tool:action==='catalog'?'tooluniverse':action,smiles:values.scienceSmiles,other_smiles:values.scienceOther,cid:values.scienceCid,query:values.scienceQuery,action:action==='catalog'?'catalog':'run'};

      if(store.ui.humanRunId && store.ui.humanProjectId===toolProjectId)body.human_run_id=store.ui.humanRunId;
      if(action==='catalog') body.query='';

      if(action==='vina') Object.assign(body,{receptor_text:store.ui.vinaReceptor,ligand_text:store.ui.vinaLigand,center:values.vinaCenter,size:values.vinaSize,exhaustiveness:values.vinaExhaustiveness,seed:values.vinaSeed});

      const r=await fetch('/api/tools/run',{method:'POST',headers:{'Content-Type':'application/json','X-Helix':'1','X-Helix-Token':sessionStorage.getItem('helix.token')||''},body:JSON.stringify(body)});

      const data=await r.json(); if(!r.ok || !data.ok) throw Error(data.error || '工具执行失败'); if(store.snapshot?.project?.id!==toolProjectId)return; store.ui_({scienceResult:data}); if(body.human_run_id){const snap=await (await fetch('/api/snapshot',{cache:'no-store',headers:{'X-Helix-Token':sessionStorage.getItem('helix.token')||''}})).json();store.set({snapshot:snap});}

    } catch(err) { store.ui_({scienceError:String(err.message || err)}); }

    finally { store.ui_({scienceBusy:false}); }

  });

}

