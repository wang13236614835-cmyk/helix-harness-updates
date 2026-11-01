import {store,esc} from './core/store.js?v=3301';
let ctx;export function initProjectImport(c){ctx=c;}
export function projectImportCmd(cmd){
 if(cmd==='project-export'){(async()=>{try{const r=await fetch('/api/projects/export',{headers:{'X-Helix-Token':sessionStorage.getItem('helix.token')||''}});if(!r.ok){const j=await r.json();throw Error(j.error||'导出失败');}const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='Helix-Project-3.2.0.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}catch(e){ctx.toast(e.message);}})();return true;}
 if(cmd!=='project-import')return false;
 if(document.querySelector('.pmenu'))ctx.closeMenu?.();document.querySelector('.project-import-dialog')?.remove();
 const root=document.createElement('div');root.className='lt-dialog project-import-dialog';
 const cloud=store.snapshot?.auth?.account_scope==='cloud';
 root.innerHTML=`<form><h2>读取并导入项目</h2><p>读取完整资料和任务计划，先预览再加入项目列表。来源文件保留原样。</p><label>项目ZIP<input name="archive" type="file" accept=".zip"></label>${cloud?'':`<p>或直接读取这台电脑上的项目文件夹：</p><label>本机项目文件夹<input name="folder" placeholder="例如 D:\\我的项目"></label>`}<p>可识别 project.helix.json、project.json、任务表.csv 或 tasks.xlsx；没有任务表的文件夹可先作为资料项目导入。</p><div class="import-preview"></div><p class="lt-error" role="status"></p><button class="btn primary" type="submit">读取并预览</button><button class="btn" type="button" data-close>取消</button></form>`;
 document.body.append(root);const form=root.querySelector('form'),button=form.querySelector('[type=submit]'),err=root.querySelector('[role=status]');let preview=null;
 root.querySelector('[data-close]').onclick=()=>{if(preview)ctx.api('/api/project-import/cancel',{token:preview.token}).catch(()=>{});root.remove();};
 const reset=()=>{preview=null;button.textContent='读取并预览';root.querySelector('.import-preview').innerHTML='';};
 form.elements.archive.onchange=reset;if(form.elements.folder)form.elements.folder.oninput=reset;
 form.onsubmit=async e=>{e.preventDefault();button.disabled=true;err.textContent='';try{
  if(preview){await ctx.api('/api/project-import/commit',{token:preview.token,snapshot_hash:preview.snapshot_hash});root.remove();await ctx.refresh();ctx.go('data');ctx.toast('项目和资料已导入，可在任务页运行');return;}
  const file=form.elements.archive.files[0],path=form.elements.folder?.value.trim();
  if(file)preview=await ctx.api('/api/project-import/zip?name='+encodeURIComponent(file.name.replace(/\.zip$/i,'')),file,true);
  else if(path)preview=await ctx.api('/api/project-import/folder',{path});
  else throw Error('请选择项目ZIP，或填写本机项目文件夹路径');
  root.querySelector('.import-preview').innerHTML=`<h3>${esc(preview.name)}</h3><p>完整读取 ${preview.file_count} 个文件 · ${(preview.bytes/1024/1024).toFixed(2)} MB · ${preview.task_count} 项任务</p><p>账号、连接Token与旧签署不导入，任务从待处理开始。</p>${preview.warnings.length?`<ul>${preview.warnings.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}<details><summary>文件清单与SHA（${preview.files.length}）</summary><ul>${preview.files.map(x=>`<li>${esc(x.path)} · ${x.bytes}字节 · ${x.sha256.slice(0,12)}</li>`).join('')}</ul></details>${preview.skipped.length?`<details><summary>跳过的配置与缓存（${preview.skipped.length}）</summary><ul>${preview.skipped.map(x=>`<li>${esc(x.path)}：${esc(x.reason)}</li>`).join('')}</ul></details>`:''}`;
  button.textContent='确认加入项目';
 }catch(e){err.textContent=e.message;}finally{button.disabled=false;}};
 return true;
}
