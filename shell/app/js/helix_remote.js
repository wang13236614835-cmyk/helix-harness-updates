import {store,esc} from './core/store.js?v=3301';
let ctx;
export function initHelixRemote(c){ctx=c;}
export function helixRemoteCmd(cmd){if(cmd!=='helix-remote')return false;open();return true;}
async function open(){
 document.querySelector('.helix-remote-dialog')?.remove();
 const root=document.createElement('div');root.className='lt-dialog helix-remote-dialog';
 root.innerHTML=`<form style="max-height:85vh;overflow:auto"><h2>Helix 云端协同与数据同步中心</h2><p>本机工作台保持可离线使用。云端账号与本地身份安全隔离，全量存证链式归档。</p><div class="remote-state" role="status">正在检查连接…</div><label>云服务器<input name="origin" type="url" value="https://helix.186-244-215-67.sslip.io" required></label><fieldset class="remote-auth"><legend>云账号</legend><label>操作<select name="mode"><option value="login">登录已有账号</option><option value="register">通过邀请注册</option><option value="setup">服务器首次设置（组长）</option></select></label><label>登录入口<select name="portal"><option value="lead">组长</option><option value="member">组员</option></select></label><label>账号<input name="username" autocomplete="username"></label><label>密码<input name="password" type="password" autocomplete="current-password"></label><label data-extra="register setup" hidden>再次填写密码<input name="confirm" type="password" autocomplete="new-password"></label><label data-extra="register" hidden>邀请链接或邀请码<input name="invite" autocomplete="off"></label><label data-extra="setup" hidden>部署者提供的初始化码<input name="code" type="password" autocomplete="off"></label><button class="btn primary" type="submit">连接云账号</button></fieldset><div class="remote-connected" hidden><p><button type="button" class="btn" data-action="status">刷新连接</button> <button type="button" class="btn" data-action="logout">退出云账号</button> <button type="button" class="btn" data-action="invite">生成团队邀请</button> <button type="button" class="btn" data-action="team">查看云团队</button></p><div class="remote-team"></div><hr><h3>项目与数据</h3><p class="remote-scope">明确选择方向，先预览再确认。双向变化具备冲突守护，拒绝无序覆盖。</p><label>方向<select name="direction"><option value="upload">本机 → 云服务器</option><option value="download">云服务器 → 本机</option></select></label><label>来源项目<select name="project"></select></label><button type="button" class="btn primary" data-action="metadata-current-preview">同步最新进度与分工</button> <button type="button" class="btn" data-action="preview">完整资料同步预览</button> <button type="button" class="btn" data-action="upload-start" hidden>开始大项目上传</button><div class="remote-preview"></div><div class="remote-progress" role="status"></div><button type="button" class="btn primary" data-action="commit" hidden>确认同步</button></div><p class="remote-error" role="alert"></p><button type="button" class="btn" data-close>关闭</button></form>`;
 document.body.append(root);
 const form=root.querySelector('form'),e=form.elements,state=root.querySelector('.remote-state'),error=root.querySelector('.remote-error'),progress=root.querySelector('.remote-progress');
 let status={},transfer=null,pollTimer=null,transferKind='full';
 const call=(action,body={})=>ctx.api('/api/helix-remote/'+action,body);
 function stopPoll(){if(pollTimer){clearInterval(pollTimer);pollTimer=null;}}
 function reset(){transfer=null;transferKind='full';stopPoll();progress.textContent='';root.querySelector('.remote-preview').textContent='';root.querySelector('[data-action=commit]').hidden=true;root.querySelector('[data-action=upload-start]').hidden=true;}
 function projects(){reset();const rows=e.direction.value==='upload'?(store.snapshot?.projects||[]).filter(p=>p.kind!=='builtin'):(status.projects||[]);e.project.innerHTML=rows.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.id)}</option>`).join('');}
 function render(r){status=r;e.origin.value=r.origin||e.origin.value;root.querySelector('.remote-auth').hidden=!!r.connected;root.querySelector('.remote-connected').hidden=!r.connected;state.textContent=r.connected?'已连接云服务器 · '+(r.account?.me?.name||'云账号')+' · '+(r.account?.me?.role==='lead'?'组长':'组员'):(r.error||'未登录云账号；首次部署请选择“服务器首次设置”');projects();}
 function fmtMB(n){return (n/1024/1024).toFixed(1);}
 async function run(fn){error.textContent='';const buttons=[...root.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{await fn();}catch(err){error.textContent=err.message;}finally{buttons.forEach(b=>b.disabled=false);}}
 root.querySelector('[data-close]').onclick=()=>{stopPoll();root.remove();};
 e.mode.onchange=()=>{root.querySelectorAll('[data-extra]').forEach(x=>x.hidden=!x.dataset.extra.split(' ').includes(e.mode.value));e.password.autocomplete=e.mode.value==='login'?'current-password':'new-password';};
 e.direction.onchange=projects;e.project.onchange=reset;e.origin.oninput=reset;
 form.onsubmit=ev=>{ev.preventDefault();run(async()=>{const body=Object.fromEntries(new FormData(form));body.name=body.username;const r=await call(body.mode,body);e.password.value='';e.confirm.value='';e.code.value='';render(r);});};
 root.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>run(async()=>{
  const action=b.dataset.action;
  if(action==='status'||action==='logout'){render(await call(action));return;}
  if(action==='invite'){const r=await call('invite');const box=root.querySelector('.remote-team');box.textContent='团队邀请（交给组员自行注册）：';const input=document.createElement('input');input.readOnly=true;input.value=r.url;input.onclick=()=>input.select();box.append(input);return;}
  if(action==='team'){const r=await call('team');root.querySelector('.remote-team').textContent=(r.roster||[]).map(p=>`${p.name} · ${p.role} · ${p.active===false?'停用':'启用'}`).join('\n')||'暂无成员';return;}
  if(action==='metadata-current-preview'){
   reset();if(e.direction.value!=='upload')throw Error('进度增量同步目前仅支持本机 → 云服务器；不会替换云端资料。');
   progress.textContent='正在核对两端修订与任务权限…';
   const r=await call(action,{project:e.project.value});transfer=r.transfer;transferKind='metadata';const p=r.preview;
   progress.textContent='仅同步进度、任务说明及研究摘要，不重传完整资料。';
   root.querySelector('.remote-preview').innerHTML=`<h3>进度与分工增量预览</h3><p>${((p.bytes||0)/1024).toFixed(1)} KB · ${p.changed?.length||0} 项变更</p><ul>${(p.changed||[]).map(path=>`<li>${esc(path)}</li>`).join('')}</ul><p>任务状态、历史账本和账号保持不变。确认后写入云端，发生冲突将停止。</p>`;
   root.querySelector('[data-action=commit]').hidden=false;return;
  }
  if(action==='preview'){
   reset();const selected=(status.projects||[]).find(p=>p.id===e.project.value);const r=await call('preview',{direction:e.direction.value,project:e.project.value,revision:selected?.revision});
   if(r.requires_transfer){root.querySelector('[data-action=upload-start]').hidden=false;state.textContent=r.error||'项目较大，请使用后台分块上传。';return;}
   transfer=r.transfer;const p=r.preview;
   root.querySelector('.remote-preview').innerHTML=`<h3>${esc(p.name||'项目')}</h3><p>${esc(r.scope||'项目数据与文件')} · ${p.file_count||0} 个文件 · ${fmtMB(p.bytes||0)} MB${r.differs===false?' · 两端内容一致':''}</p><details><summary>文件内容摘要</summary><ul>${(p.entries||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`;root.querySelector('[data-action=commit]').hidden=false;return;
  }
  if(action==='upload-start'){
   reset();const r=await call('upload-start',{project:e.project.value});const job=r.job;
   progress.textContent='已开始后台传输：正在整理项目与资料…';
   pollTimer=setInterval(()=>run(async()=>{
    const s=await call('transfer-status',{job});
    if(s.status==='running'){const pct=s.total?Math.round(100*s.sent/s.total):null;progress.textContent=s.phase+(pct!=null?`（${pct}% · ${fmtMB(s.sent)}/${fmtMB(s.total)} MB）`:'');return;}
    stopPoll();
    if(s.status==='error'){progress.textContent='';error.textContent=s.error||'传输失败';return;}
    progress.textContent='服务器已校验全部文件，请确认同步。';
    transfer=s.transfer;const p=s.preview;
    root.querySelector('.remote-preview').innerHTML=`<h3>${esc(p.name||'项目')}</h3><p>${esc(s.scope||'完整项目状态')} · ${p.file_count||0} 个文件 · ${fmtMB(p.bytes||0)} MB</p><details><summary>文件内容摘要</summary><ul>${(p.entries||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`;
    root.querySelector('[data-action=commit]').hidden=false;
   }),2000);return;
  }
  if(action==='commit'){if(!transfer)throw Error('请先读取预览或完成大项目上传');const r=await call(transferKind==='metadata'?'metadata-apply':'commit',{transfer,confirm:true});reset();await ctx.refresh();render(await call('status'));state.textContent='同步完成 · 项目 '+r.id+(r.created?'（云端新建）':'');}
 }));
 await run(async()=>render(await call('status')));
}
