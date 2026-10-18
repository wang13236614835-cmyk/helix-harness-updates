"""Helix 3.1 登录 / 注册 / 首次设置页面（桌面与云端共用）。Portal 只决定入口，不授予角色。"""
from html import escape

from version import VERSION as VER

STYLE = '''<style>
*{box-sizing:border-box}
:root{--ink:#16302f;--mute:#5f7471;--line:#d7e2df;--brand:#136354;--brand2:#0e4d42;--bg:#eef3f1;--err:#b4382d}
body{margin:0;min-height:100vh;display:grid;grid-template-columns:minmax(0,1.05fr) minmax(380px,1fr);background:var(--bg);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.hero{position:relative;overflow:hidden;background:radial-gradient(120% 90% at 0% 0%,#1d7b69 0%,#0f4a40 55%,#0a332d 100%);color:#e7f4f0;padding:56px 64px;display:flex;flex-direction:column;justify-content:space-between}
.hero:after{content:"";position:absolute;right:-120px;bottom:-120px;width:420px;height:420px;border-radius:50%;border:56px solid #ffffff10}
.logo{display:flex;align-items:center;gap:12px;font-weight:700;font-size:18px;letter-spacing:.5px}
.logo i{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#7be0c3,#2aa486);box-shadow:0 6px 18px #0004}
.hero h2{font-size:34px;line-height:1.3;margin:0 0 14px;font-weight:700;max-width:460px}
.hero p{margin:0;color:#bfdcd4;max-width:440px}
.hero ul{list-style:none;padding:0;margin:28px 0 0;display:grid;gap:10px;color:#d4ebe5}
.hero li:before{content:"✓";display:inline-block;width:22px;color:#7be0c3;font-weight:700}
.hero small{color:#9fc4ba}
main{display:grid;place-items:center;padding:32px}
.card{width:min(100%,400px)}
.card h1{font-size:26px;margin:0 0 4px}
.card .sub{margin:0 0 26px;color:var(--mute)}
label{display:block;margin:0 0 16px;font-weight:600;font-size:14px}
.field{position:relative;margin-top:6px}
input{font:inherit;width:100%;border-radius:10px;padding:12px 14px;border:1px solid #bfd0cb;background:#fff;color:var(--ink);transition:border-color .15s,box-shadow .15s}
input:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 4px #13635422}
.eye{position:absolute;right:6px;top:50%;transform:translateY(-50%);border:0;background:none;color:var(--mute);font:inherit;font-size:13px;padding:6px 8px;cursor:pointer;border-radius:6px}
.eye:hover{background:#eef3f1}
.row{display:flex;align-items:center;justify-content:space-between;margin:2px 0 18px;font-size:14px;color:var(--mute)}
.check{display:flex;align-items:center;gap:8px;margin:0;font-weight:500;cursor:pointer}
.check input{width:16px;height:16px;margin:0;accent-color:var(--brand)}
.go{width:100%;font:inherit;font-weight:650;border:0;border-radius:10px;padding:12px;background:var(--brand);color:#fff;cursor:pointer;transition:background .15s}
.go:hover{background:var(--brand2)}.go:disabled{opacity:.6;cursor:wait}
.error{color:var(--err);min-height:22px;margin:0 0 10px;font-size:14px}
.error:empty{margin:0;min-height:0}
.links{margin-top:22px;display:flex;justify-content:space-between;gap:12px;font-size:14px}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
.tag{display:inline-block;font-size:12px;font-weight:600;color:var(--brand);background:#13635414;border-radius:999px;padding:2px 10px;margin-bottom:12px}
.foot{margin-top:28px;font-size:12px;color:var(--mute)}
@media(max-width:860px){body{grid-template-columns:1fr}.hero{display:none}}
</style>'''

HERO = '''<section class="hero"><div class="logo"><i></i>Helix 工作台</div><div><h2>任务、文件和团队协作，<br>都在一个地方。</h2><p>登录后自动连接本机服务和团队同步，程序更新也会自动送达。</p><ul><li>每周任务与交付物一目了然</li><li>上传文件即读取，内容准确可追溯</li><li>断线自动重连，登录状态保持</li></ul></div><small>Helix Harness ''' + VER + '''</small></section>'''

COMMON_JS = r'''
const $=s=>document.querySelector(s);
document.querySelectorAll('.eye').forEach(b=>b.onclick=()=>{const i=b.parentNode.querySelector('input');const show=i.type==='password';i.type=show?'text':'password';b.textContent=show?'隐藏':'显示';});
async function post(path,body){let r;try{r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Helix':'1'},body:JSON.stringify(body)});}catch(e){throw Error('连不上 Helix 后台：请确认已双击「启动 Helix」，稍后再试');}
 let j={};try{j=await r.json();}catch(e){}if(!r.ok||!j.ok)throw Error(j.error||('操作未完成（'+r.status+'）'));return j;}
function done(j){try{sessionStorage.setItem('helix.token',j.token||'');}catch(e){}const n=new URLSearchParams(location.search).get('next')||(location.hash.length>2?'/'+location.hash:'');location.replace(n&&n.startsWith('/')&&!n.startsWith('//')&&!/^\/(login|leader-login|register)/.test(n)?n:(j.next||'/#/runs'));}
function busy(form,on,text){const b=form.querySelector('.go');b.disabled=on;if(on){b.dataset.t=b.textContent;b.textContent=text||'请稍候…';}else if(b.dataset.t)b.textContent=b.dataset.t;}
'''

def _doc(title, inner, script):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0f4a40"><link rel="icon" href="/icon.svg"><title>'
            + title + ' · Helix ' + VER + '</title>' + STYLE + '</head><body>' + HERO + '<main><div class="card">' + inner
            + '<p class="foot">Helix Harness ' + VER + ' · 登录状态在本机保持 30 天，可随时在右上角退出</p></div></main><script>' + COMMON_JS + script + '</script></body></html>').encode()

def _pw(name, auto, ph='请输入密码', extra=''):
    return f'<div class="field"><input name="{name}" type="password" required autocomplete="{auto}" placeholder="{ph}" maxlength="128" {extra}><button type="button" class="eye" tabindex="-1">显示</button></div>'

def page(*, leader=False, cloud=False, leader_name='', message='', bootstrap=False):
    if leader and bootstrap and not cloud:
        return initial_setup()
    title = '组长登录' if leader else '组员登录'
    portal = 'lead' if leader else 'member'
    inner = (f'<span class="tag">{"组长入口" if leader else "成员入口"}</span><h1>{title}</h1>'
             f'<p class="sub">{"管理项目、成员、程序更新和团队设置" if leader else "登录后查看任务、上传文件、记录进度"}</p>'
             f'<form method="post" action="/login" id="login-form"><input type="hidden" name="portal" value="{portal}">'
             f'<label>账号<div class="field"><input id="username" name="username" required autocomplete="username" maxlength="24" value="{escape(leader_name if leader else "", quote=True)}" placeholder="请输入账号"></div></label>'
             f'<label>密码{_pw("password", "current-password")}</label>'
             '<div class="row"><label class="check"><input type="checkbox" name="remember" checked>保持登录（本机 30 天）</label></div>'
             f'<p class="error" role="status" id="message">{escape(message)}</p>'
             f'<button class="go" type="submit">{"登录组长工作台" if leader else "登录"}</button></form>'
             + ('<div class="links"><a href="/login">← 组员登录</a><span></span></div>' if leader else
                '<div class="links"><a href="/register">注册组员账号</a><a href="/leader-login">组长登录 →</a></div>'))
    script = '''const form=$('form'),user=$('#username'),message=$('#message');
(user.value?form.elements.password:user).focus();
form.addEventListener('submit',async e=>{e.preventDefault();busy(form,true,'正在登录…');message.textContent='';
 try{const f=form.elements;done(await post('/api/auth/login',{username:user.value.trim(),pin:f.password.value,portal:f.portal.value,remember:f.remember.checked}));}
 catch(err){message.textContent=err.message;busy(form,false);}});'''
    return _doc(title, inner, script)

def registration(join=''):
    inner = ('<span class="tag">成员入口</span><h1>注册组员账号</h1><p class="sub">设置自己的账号和密码，注册后自动登录</p><form>'
             '<label>账号<div class="field"><input name="name" required minlength="2" maxlength="24" autocomplete="username" placeholder="2–24 个字"></div></label>'
             f'<label>密码{_pw("pin", "new-password", "至少 8 位", "minlength=8")}</label>'
             f'<label>确认密码{_pw("confirm", "new-password", "再输入一次", "minlength=8")}</label>'
             '<p class="error" role="status"></p><button class="go">注册并登录</button></form>'
             '<div class="links"><a href="/login">← 已有账号，去登录</a></div>'
             '<p class="foot">这是本机账号。参加云端团队时，请使用组长提供的加入链接。</p>')
    script = '''const form=$('form'),message=$('[role=status]'),join=new URLSearchParams(location.search).get('join');
if(join){$('h1').textContent='设置组员账号';$('.sub').textContent='自行设置账号和密码，沿用原分工；团队只显示匿名代号';}
form.onsubmit=async e=>{e.preventDefault();busy(form,true);message.textContent='';try{const f=form.elements;if(f.pin.value!==f.confirm.value)throw Error('两次输入的密码不一致');
 await post(join?'/api/auth/claim':'/api/auth/register',{...(join?{join}:{}),name:f.name.value.trim(),pin:f.pin.value,pin_confirm:f.confirm.value});
 done(await post('/api/auth/login',{username:f.name.value.trim(),pin:f.pin.value,portal:'member',remember:true}));}catch(err){message.textContent=err.message;busy(form,false);}};'''
    return _doc('注册组员账号', inner, script)

def initial_setup():
    inner = ('<span class="tag">首次使用</span><h1>设置组长账号</h1><p class="sub">先设置团队管理账号，之后可导入项目、添加组员</p><form>'
             '<label>组长账号<div class="field"><input name="username" required maxlength="24" autocomplete="username" placeholder="例如：组长"></div></label>'
             f'<label>密码{_pw("pin", "new-password", "至少 8 位", "minlength=8")}</label>'
             f'<label>确认密码{_pw("confirm", "new-password", "再输入一次", "minlength=8")}</label>'
             '<p class="error" role="status"></p><button class="go">保存并登录组长</button></form>'
             '<div class="links"><a href="/login">← 返回组员登录</a></div>')
    script = '''const form=$('form'),message=$('[role=status]');form.elements.username.focus();
form.onsubmit=async e=>{e.preventDefault();const f=form.elements;busy(form,true);message.textContent='';
 try{if(f.pin.value!==f.confirm.value)throw Error('两次输入的密码不一致');done(await post('/api/auth/bootstrap',{username:f.username.value.trim(),pin:f.pin.value,pin_confirm:f.confirm.value}));}
 catch(err){message.textContent=err.message;busy(form,false);}};'''
    return _doc('设置组长账号', inner, script)
