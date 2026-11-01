"""Helix 3.2 现代化简约质感登录 / 注册 / 首次设置页面（桌面客户端与云端共用）。"""
from html import escape

from version import VERSION as VER

STYLE = '''<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --brand: #0f5647;
  --brand-hover: #0a3d32;
  --brand-light: #e6f4f1;
  --accent: #10b981;
  --ink-900: #0f172a;
  --ink-700: #334155;
  --ink-500: #64748b;
  --ink-400: #94a3b8;
  --canvas-bg: #f8fafc;
  --card-bg: #ffffff;
  --border-subtle: #e2e8f0;
  --border-focus: #0f5647;
  --err-bg: #fef2f2;
  --err-border: #fecaca;
  --err-text: #991b1b;
}

body {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(0, 1.12fr) minmax(420px, 1fr);
  background: var(--canvas-bg);
  color: var(--ink-900);
  font: 14.5px/1.6 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* ---------- 左侧沉浸式品牌与动效区 ---------- */
.hero {
  position: relative;
  overflow: hidden;
  background: radial-gradient(130% 90% at 0% 0%, #156654 0%, #0d3d34 50%, #06231d 100%);
  color: #f1f8f6;
  padding: 56px 64px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.hero-backdrop {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    radial-gradient(circle at 80% 20%, rgba(45, 212, 191, 0.12) 0%, transparent 45%),
    radial-gradient(circle at 10% 80%, rgba(16, 185, 129, 0.08) 0%, transparent 50%);
}

.helix-visual {
  position: absolute;
  right: -30px;
  top: 50%;
  transform: translateY(-50%);
  width: 440px;
  height: 520px;
  opacity: 0.18;
  pointer-events: none;
  animation: floatHelix 8s ease-in-out infinite alternate;
}

@keyframes floatHelix {
  0% { transform: translateY(-50%) rotate(0deg); }
  100% { transform: translateY(-46%) rotate(4deg); }
}

.hero-top { position: relative; z-index: 2; }
.brand-badge {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.14);
  backdrop-filter: blur(12px);
  padding: 6px 14px 6px 10px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 500;
  color: #cdeee6;
}

.brand-icon {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: linear-gradient(135deg, #34d399, #059669);
  display: grid;
  place-items: center;
  box-shadow: 0 4px 12px rgba(5, 150, 105, 0.4);
}

.brand-icon svg { width: 16px; height: 16px; fill: #ffffff; }

.hero-content {
  position: relative;
  z-index: 2;
  margin: 40px 0;
  max-width: 480px;
}

.hero-content h1 {
  font-size: 36px;
  line-height: 1.25;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: #ffffff;
  margin-bottom: 16px;
}

.hero-content p {
  font-size: 15px;
  color: #b7ded4;
  margin-bottom: 32px;
  line-height: 1.65;
}

.feature-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.feature-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.09);
  backdrop-filter: blur(8px);
  padding: 12px 16px;
  border-radius: 12px;
  transition: all 0.2s ease;
}

.feature-item:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.16);
  transform: translateX(3px);
}

.feature-icon {
  width: 22px;
  height: 22px;
  margin-top: 1px;
  color: #34d399;
  flex-shrink: 0;
}

.feature-title {
  font-size: 13.5px;
  font-weight: 600;
  color: #ffffff;
  margin-bottom: 2px;
}

.feature-desc {
  font-size: 12.5px;
  color: #9dcbc0;
}

.hero-footer {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12.5px;
  color: #7bb5a7;
}

.live-indicator {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.25);
  padding: 4px 10px;
  border-radius: 999px;
  color: #6ee7b7;
  font-weight: 500;
  font-size: 12px;
}

.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
  animation: pulseDot 2s infinite;
}

@keyframes pulseDot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.85); }
}

/* ---------- 右侧现代交互面板 ---------- */
main {
  display: grid;
  place-items: center;
  padding: 40px 24px;
}

.card-wrapper {
  width: min(100%, 420px);
}

.auth-card {
  background: var(--card-bg);
  border: 1px solid var(--border-subtle);
  border-radius: 20px;
  box-shadow: 0 20px 45px -15px rgba(15, 74, 64, 0.07), 0 1px 3px rgba(0, 0, 0, 0.03);
  padding: 34px 32px 30px;
}

/* 顶部标签切换器 */
.segmented-tab {
  display: grid;
  grid-template-columns: 1fr 1fr;
  background: #f1f5f9;
  padding: 4px;
  border-radius: 12px;
  margin-bottom: 24px;
}

.tab-btn {
  border: none;
  background: transparent;
  padding: 9px 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-500);
  border-radius: 9px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.tab-btn.active {
  color: var(--brand);
  background: #ffffff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

/* 角色药丸选择器 */
.role-switch {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}

.role-pill {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 10px;
  border: 1px solid var(--border-subtle);
  background: #fafafa;
  color: var(--ink-700);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.role-pill:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.role-pill.active {
  background: var(--brand-light);
  border-color: var(--brand);
  color: var(--brand);
  font-weight: 600;
}

.card-header {
  margin-bottom: 18px;
}

.card-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--ink-900);
  letter-spacing: -0.3px;
  margin-bottom: 4px;
}

.card-desc {
  font-size: 13.5px;
  color: var(--ink-500);
}

.form-group {
  margin-bottom: 15px;
}

.form-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-700);
  margin-bottom: 6px;
}

.input-field {
  position: relative;
}

input[type="text"], input[type="password"] {
  width: 100%;
  font: inherit;
  font-size: 14px;
  color: var(--ink-900);
  background: #ffffff;
  border: 1px solid var(--border-subtle);
  border-radius: 11px;
  padding: 11px 14px;
  transition: all 0.15s ease;
}

input[type="text"]:focus, input[type="password"]:focus {
  outline: none;
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px rgba(15, 86, 71, 0.12);
}

input::placeholder {
  color: #94a3b8;
  font-size: 13.5px;
}

.eye-btn {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--ink-400);
  padding: 4px;
  cursor: pointer;
  display: grid;
  place-items: center;
  border-radius: 6px;
  transition: color 0.15s;
}

.eye-btn:hover { color: var(--ink-700); }
.eye-btn svg { width: 17px; height: 17px; }

.meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 12px 0 18px;
  font-size: 13px;
  color: var(--ink-500);
}

.custom-check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}

.custom-check input {
  width: 16px;
  height: 16px;
  accent-color: var(--brand);
  cursor: pointer;
  border-radius: 4px;
}

.submit-btn {
  width: 100%;
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  color: #ffffff;
  background: linear-gradient(180deg, var(--brand) 0%, var(--brand-hover) 100%);
  border: none;
  border-radius: 11px;
  padding: 12px 18px;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(15, 86, 71, 0.28);
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.submit-btn:hover {
  background: var(--brand-hover);
  box-shadow: 0 6px 18px rgba(15, 86, 71, 0.35);
  transform: translateY(-1px);
}

.submit-btn:disabled {
  opacity: 0.65;
  cursor: not-allowed;
  transform: none;
}

.alert-box {
  display: none;
  align-items: center;
  gap: 8px;
  background: var(--err-bg);
  border: 1px solid var(--err-border);
  color: var(--err-text);
  padding: 10px 13px;
  border-radius: 10px;
  font-size: 13px;
  margin-bottom: 16px;
}

.alert-box.visible { display: flex; }
.alert-box svg { width: 16px; height: 16px; flex-shrink: 0; }

.card-footer {
  text-align: center;
  margin-top: 22px;
  font-size: 12px;
  color: var(--ink-400);
}

.invite-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--brand-light);
  color: var(--brand);
  font-size: 12px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 6px;
  margin-bottom: 12px;
}

@media (max-width: 880px) {
  body { grid-template-columns: 1fr; }
  .hero { display: none; }
  main { padding: 24px 16px; }
}
</style>'''

HERO_HTML = '''<section class="hero">
  <div class="hero-backdrop"></div>
  <svg class="helix-visual" viewBox="0 0 200 300" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M40 20C90 70 150 110 160 150C170 190 110 230 40 280" stroke="#34d399" stroke-width="6" stroke-linecap="round"/>
    <path d="M160 20C110 70 50 110 40 150C30 190 90 230 160 280" stroke="#059669" stroke-width="6" stroke-linecap="round"/>
    <line x1="48" y1="45" x2="152" y2="45" stroke="#6ee7b7" stroke-width="2.5" stroke-dasharray="4 4"/>
    <line x1="75" y1="95" x2="125" y2="95" stroke="#6ee7b7" stroke-width="2.5" stroke-dasharray="4 4"/>
    <line x1="98" y1="150" x2="102" y2="150" stroke="#6ee7b7" stroke-width="3"/>
    <line x1="75" y1="205" x2="125" y2="205" stroke="#6ee7b7" stroke-width="2.5" stroke-dasharray="4 4"/>
    <line x1="48" y1="255" x2="152" y2="255" stroke="#6ee7b7" stroke-width="2.5" stroke-dasharray="4 4"/>
  </svg>
  <div class="hero-top">
    <div class="brand-badge">
      <div class="brand-icon">
        <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14.5v-9l6 4.5-6 4.5z"/></svg>
      </div>
      <span>Helix 协同科研工作台</span>
    </div>
  </div>
  <div class="hero-content">
    <h1>任务、数据与多方协作<br>凝聚于一处</h1>
    <p>实时连接本地环境与爱维云服务，全量账本防篡改，科研分工透明可溯。</p>
    <div class="feature-list">
      <div class="feature-item">
        <svg class="feature-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clip-rule="evenodd"/></svg>
        <div>
          <div class="feature-title">全量状态同步</div>
          <div class="feature-desc">本地离线操作无感保存，一键安全上传云端，双向冲突守护</div>
        </div>
      </div>
      <div class="feature-item">
        <svg class="feature-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-3.34 9.67-8 11.317C5.34 16.67 2 12.225 2 7c0-.682.057-1.35.166-2.001zm11.541 3.708a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>
        <div>
          <div class="feature-title">不可篡改存证账本</div>
          <div class="feature-desc">129+ 项原子事件链式追踪，保留历史签署与真实证据资格</div>
        </div>
      </div>
      <div class="feature-item">
        <svg class="feature-icon" viewBox="0 0 20 20" fill="currentColor"><path d="M13 6a3 3 0 11-6 0 3 3 0 016 0zM18 8a2 2 0 11-4 0 2 2 0 014 0zM14 15a4 4 0 00-8 0v3h8v-3zM6 8a2 2 0 11-4 0 2 2 0 014 0zM16 18v-3a5.972 5.972 0 00-.75-2.906A3.005 3.005 0 0119 15v3h-3zM4.75 12.094A5.973 5.973 0 004 15v3H1v-3a3 3 0 013.75-2.906z"/></svg>
        <div>
          <div class="feature-title">多成员协同分工</div>
          <div class="feature-desc">组长下发专属邀请，组员应用内一键加入，无需繁杂配置</div>
        </div>
      </div>
    </div>
  </div>
  <div class="hero-footer">
    <div class="live-indicator">
      <span class="live-dot"></span>
      <span>爱维云服务在线 · 长期增量守护</span>
    </div>
    <span>Helix Harness ''' + VER + '''</span>
  </div>
</section>'''

EYE_SVG_SHOW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
EYE_SVG_HIDE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
ALERT_ICON = '<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>'

CLIENT_JS = r'''
const $ = function(s) { return document.querySelector(s); };
const $$ = function(s) { return document.querySelectorAll(s); };

$$('.eye-btn').forEach(function(btn) {
  btn.onclick = function() {
    var input = btn.parentElement.querySelector('input');
    var isPass = input.type === 'password';
    input.type = isPass ? 'text' : 'password';
    btn.innerHTML = isPass ? btn.dataset.hideIcon : btn.dataset.showIcon;
  };
});

async function postApi(path, body) {
  let r;
  try {
    r = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Helix': '1' },
      body: JSON.stringify(body)
    });
  } catch(e) {
    throw Error('无法连接 Helix 后台服务：请确认工作台服务正常运行中');
  }
  let j = {};
  try { j = await r.json(); } catch(e) {}
  if (!r.ok || j.ok === false) {
    throw Error(j.error || ('操作未完成（HTTP ' + r.status + '）'));
  }
  return j;
}

function done(j) {
  try {
    if (j.token) sessionStorage.setItem('helix.token', j.token);
  } catch(e) {}
  const params = new URLSearchParams(location.search);
  const next = params.get('next') || (location.hash.length > 2 ? '/' + location.hash : '');
  const target = (next && next.startsWith('/') && !next.startsWith('//') && !/^\/(login|leader-login|register)/.test(next))
    ? next
    : (j.next || '/#/runs');
  location.replace(target);
}

function setBusy(btn, on, text) {
  btn.disabled = on;
  if (on) {
    btn.dataset.originText = btn.textContent;
    btn.innerHTML = '<span class="live-dot" style="display:inline-block"></span> ' + (text || '请稍候…');
  } else if (btn.dataset.originText) {
    btn.textContent = btn.dataset.originText;
  }
}

function showAlert(box, msg) {
  if (!msg) {
    box.classList.remove('visible');
    box.textContent = '';
    return;
  }
  box.innerHTML = box.dataset.icon + '<span>' + msg + '</span>';
  box.classList.add('visible');
}

function switchTab(mode) {
  $$('.tab-btn').forEach(function(b) { b.classList.toggle('active', b.dataset.tab === mode); });
  $('#login-view').style.display = mode === 'login' ? 'block' : 'none';
  $('#register-view').style.display = mode === 'register' ? 'block' : 'none';
  showAlert($('#alert-msg'), '');
  var firstInput = $(mode === 'login' ? '#login-user' : '#reg-name');
  if (firstInput) setTimeout(function() { firstInput.focus(); }, 50);
}

function switchPortal(portal) {
  $$('.role-pill').forEach(function(p) { p.classList.toggle('active', p.dataset.portal === portal); });
  $('#login-portal').value = portal;
  var isLead = portal === 'lead';
  $('#login-title').textContent = isLead ? '组长登录' : '组员登录';
  $('#login-desc').textContent = isLead ? '管理项目、排期、团队成员与云端设置' : '登录后查看个人任务、提交成果并记录打卡';
  $('#login-btn').textContent = isLead ? '登录组长工作台' : '登录组员工作台';
}
'''

def page(*, leader=False, cloud=False, leader_name='', message='', bootstrap=False, initial_tab='login', invite='', join='', setup=False):
    if leader and bootstrap and not cloud:
        return initial_setup()
    if setup:
        initial_tab = 'register'

    current_portal = 'lead' if leader else 'member'
    portal_val = escape(current_portal, quote=True)
    initial_user = escape(leader_name if leader else '', quote=True)
    initial_invite = escape(invite or '', quote=True)
    initial_join = escape(join or '', quote=True)

    tab_login_active = 'active' if initial_tab != 'register' else ''
    tab_reg_active = 'active' if initial_tab == 'register' else ''
    login_display = 'block' if initial_tab != 'register' else 'none'
    reg_display = 'block' if initial_tab == 'register' else 'none'
    login_title = '组长登录' if leader else '组员登录'
    login_desc = '管理项目、排期、团队成员与云端设置' if leader else '登录后查看个人任务、提交成果并记录打卡'
    login_btn_text = '登录组长工作台' if leader else '登录组员工作台'
    pill_member_active = 'active' if not leader else ''
    pill_lead_active = 'active' if leader else ''
    alert_visible = 'visible' if message else ''
    badge_html = '<div class="invite-badge">✓ 已关联专属邀请码</div>' if initial_invite else ''
    invite_group_style = 'display:none' if initial_join or setup or not cloud else ''
    registration_title = '设置组长账号' if setup else ('设置组员账号' if join else '注册组员账号')
    registration_desc = ('首次部署：设置组长账号和密码' if setup else
                         '设置后沿用原分工，团队只显示匿名代号。' if join else
                         '加入团队工作台，拥有本人的独立身份与打卡记录')
    if cloud and not setup:
        registration_title = '加入团队'
    tab_register_text = '设置组长账号' if setup else '注册新账号'

    parts = [
        '<!doctype html><html lang="zh-CN"><head>',
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="theme-color" content="#0d3d34"><link rel="icon" href="/icon.svg">',
        f'<title>登录与注册 · Helix {VER}</title>',
        STYLE,
        '</head><body>',
        HERO_HTML,
        '<main><div class="card-wrapper"><div class="auth-card">',
        '<nav class="segmented-tab" role="tablist">',
        f'<button type="button" class="tab-btn {tab_login_active}" data-tab="login">账号登录</button>',
        ('<a class="tab-btn" style="text-align:center;text-decoration:none" href="/register">注册新账号</a>' if cloud else
         f'<button type="button" class="tab-btn {tab_reg_active}" data-tab="register">{tab_register_text}</button>'),
        '</nav>',
        f'<div id="alert-msg" class="alert-box {alert_visible}" data-icon="{ALERT_ICON}">{ALERT_ICON}<span>{escape(message)}</span></div>',
        f'<div id="login-view" style="display:{login_display};">',
        f'<div class="card-header"><h2 id="login-title" class="card-title">{login_title}</h2><p id="login-desc" class="card-desc">{login_desc}</p></div>',
        '<div class="role-switch">',
        f'<a class="role-pill {pill_member_active}" style="text-decoration:none" href="/login"><span>组员入口</span></a>',
        f'<a class="role-pill {pill_lead_active}" style="text-decoration:none" href="/leader-login"><span>组长入口</span></a>',
        '</div>',
        '<form id="login-form" method="post" action="/login">',
        f'<input type="hidden" id="login-portal" name="portal" value="{portal_val}">',
        '<div class="form-group"><label class="form-label" for="login-user">账号名</label><div class="input-field">',
        f'<input id="login-user" name="username" type="text" required autocomplete="username" maxlength="32" placeholder="请输入你的账号或显示名" value="{initial_user}">',
        '</div></div>',
        '<div class="form-group"><label class="form-label" for="login-pw">密码</label><div class="input-field">',
        '<input id="login-pw" name="password" type="password" required autocomplete="current-password" maxlength="128" placeholder="请输入登录密码">',
        f'<button type="button" class="eye-btn" tabindex="-1" data-show-icon="{escape(EYE_SVG_SHOW, quote=True)}" data-hide-icon="{escape(EYE_SVG_HIDE, quote=True)}">{EYE_SVG_SHOW}</button>',
        '</div></div>',
        '<div class="meta-row"><label class="custom-check"><input type="checkbox" name="remember" checked><span>保持登录状态（30 天）</span></label></div>',
        f'<button type="submit" id="login-btn" class="submit-btn">{login_btn_text}</button>',
        '</form></div>',
        f'<div id="register-view" style="display:{reg_display};">',
        f'<div class="card-header"><h2 class="card-title">{registration_title}</h2><p class="card-desc">{registration_desc}</p></div>',
        badge_html,
        '<form id="register-form">',
        f'<input type="hidden" name="join" value="{initial_join}">',
        '<div class="form-group"><label class="form-label" for="reg-name">姓名或显示名</label><div class="input-field">',
        '<input id="reg-name" name="name" type="text" required minlength="2" maxlength="24" autocomplete="username" placeholder="2–24 个字（如：李华）">',
        '</div></div>',
        '<div class="form-group"><label class="form-label" for="reg-pw">设置密码</label><div class="input-field">',
        '<input id="reg-pw" name="pin" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="至少 8 位数字与字母组合">',
        f'<button type="button" class="eye-btn" tabindex="-1" data-show-icon="{escape(EYE_SVG_SHOW, quote=True)}" data-hide-icon="{escape(EYE_SVG_HIDE, quote=True)}">{EYE_SVG_SHOW}</button>',
        '</div></div>',
        '<div class="form-group"><label class="form-label" for="reg-confirm">确认密码</label><div class="input-field">',
        '<input id="reg-confirm" name="pin_confirm" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="再次输入密码">',
        f'<button type="button" class="eye-btn" tabindex="-1" data-show-icon="{escape(EYE_SVG_SHOW, quote=True)}" data-hide-icon="{escape(EYE_SVG_HIDE, quote=True)}">{EYE_SVG_SHOW}</button>',
        '</div></div>',
        f'<div class="form-group" id="invite-group" style="{invite_group_style}">',
        '<label class="form-label" for="reg-invite">团队邀请码 / 加入链接</label><div class="input-field">',
        f'<input id="reg-invite" name="invite" type="text" autocomplete="off" placeholder="输入邀请码或直接粘贴组长下发的链接" value="{initial_invite}">',
        '</div></div>',
        '<div style="height: 12px"></div>',
        f'<button type="submit" id="reg-btn" class="submit-btn">{"设置并进入组长工作台" if setup else "注册并进入工作台"}</button>',
        '</form></div>',
        '<p class="card-footer">Helix 科研协同系统 · 本地与云端数据安全隔离</p>',
        '</div></div></main>',
        '<script>',
        CLIENT_JS,
        'const cloudMode = ' + ('true' if cloud else 'false') + ';',
        'const setupMode = ' + ('true' if setup else 'false') + ';',
        '''
        $$('.tab-btn').forEach(function(btn) {
          if (btn.dataset.tab) btn.onclick = function() { switchTab(btn.dataset.tab); };
        });
        $$('.role-pill').forEach(function(pill) {
          if (pill.dataset.portal) pill.onclick = function() { switchPortal(pill.dataset.portal); };
        });
        $('#login-form').onsubmit = async function(e) {
          if (cloudMode) return;
          e.preventDefault();
          var form = e.target;
          var btn = $('#login-btn');
          var alertBox = $('#alert-msg');
          showAlert(alertBox, '');
          setBusy(btn, true, '正在登录…');
          try {
            var u = form.username.value.trim();
            var p = form.password.value;
            var portal = form.portal.value;
            var remember = form.remember.checked;
            var res = await postApi('/api/auth/login', { username: u, pin: p, portal: portal, remember: remember });
            done(res);
          } catch(err) {
            showAlert(alertBox, err.message);
            setBusy(btn, false);
          }
        };
        $('#register-form').onsubmit = async function(e) {
          e.preventDefault();
          var form = e.target;
          var btn = $('#reg-btn');
          var alertBox = $('#alert-msg');
          showAlert(alertBox, '');
          if (form.pin.value !== form.pin_confirm.value) {
            showAlert(alertBox, '两次输入的密码不一致，请仔细核对');
            return form.pin_confirm.focus();
          }
          setBusy(btn, true, '正在注册…');
          try {
            var rawInvite = (form.invite ? form.invite.value : '').trim();
            var joinCode = (form.join ? form.join.value : '').trim();
            if (rawInvite.indexOf('://') !== -1) {
              try {
                var urlObj = new URL(rawInvite);
                var q = urlObj.searchParams;
                if (q.get('invite')) rawInvite = q.get('invite');
                if (!joinCode && q.get('join')) joinCode = q.get('join');
              } catch(e) {}
            }
            var payload = {
              name: form.name.value.trim(),
              pin: form.pin.value,
              pin_confirm: form.pin_confirm.value
            };
            var route = '/api/auth/register';
            if (setupMode) {
              payload.username = payload.name;
              delete payload.name;
              route = '/api/auth/bootstrap';
            } else if (joinCode) {
              payload.join = joinCode;
              route = '/api/auth/claim';
            }
            var res = await postApi(route, payload);
            done(res);
          } catch(err) {
            showAlert(alertBox, err.message);
            setBusy(btn, false);
          }
        };
        ''',
        '</script></body></html>'
    ]
    return '\n'.join(parts).encode('utf-8')

def registration(join=''):
    return page(initial_tab='register', join=join)

def initial_setup():
    return page(leader=True, setup=True, message='首次部署：请先设置组长账号和密码')
