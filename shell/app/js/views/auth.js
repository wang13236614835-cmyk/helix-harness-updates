// 0.5：登录状态与署名块（独立小模块，避免循环导入）

import { esc, av } from "./common.js?v=3110";

export const token = () => sessionStorage.getItem("helix.token") || "";

export const me = (s) => s?.auth?.me || null;

export function signer(s, designated, label = "主核") {

  const m = me(s);

  if (!m) return `<div class="signer"><span>署名</span><button type="button" class="btn sm pri" data-cmd="auth-login">先登录</button><em>写入账本只认登录的人</em></div>`;

  const okDes = !designated || designated === m.name;

  return `<div class="signer"><span>署名</span><b>${av(s, m.name)} ${esc(m.name)}</b><small class="ok-t">口令已验证</small>${okDes ? "" : `<em>指定${label}是 ${esc(designated)}，只有本人登录后才能写入</em>`}</div>`;

}

export const canSign = (s, designated) => { const m = me(s); return !!m && (!designated || designated === m.name); };

