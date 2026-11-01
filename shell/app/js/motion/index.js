// 动效端口（Motion Port）：任何动效包只要实现 mount(el, helixPayload, {onSelect, paused}) → {update, focus, destroy}

// 即可替换。helixPayload 与 2.00 ui/helix_view.helix_payload 完全同构，所以 1.00/2.00 的动效可以原样接入。

import native from "./native.js?v=3301";

const v2 = {

  id: "v2", label: "经典双螺旋（服务端渲染）",

  mount(el, _p, opt = {}) {

    el.innerHTML = `<iframe title="helix-v2" src="/motion/helix-v2" style="position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent"></iframe>`;

    const f = el.querySelector("iframe");

    return { update() { f.src = "/motion/helix-v2?t=" + Date.now(); }, focus() {}, destroy() { el.innerHTML = ""; } };

  },

};

const calm = {

  id: "calm", label: "静态（低动效 / 投屏 / 打印）",

  mount(el, p, opt = {}) {

    const draw = (p) => {

      const bs = p.bases || [], n = Math.max(bs.length, 10), w = 1000, h = 300, x0 = 60, x1 = 880, mid = 160, amp = 70, turns = Math.max(1.6, n / 7);

      const path = (off) => Array.from({ length: 121 }, (_, i) => { const u = i / 120, a = u * turns * Math.PI * 2 + off; return `${i ? "L" : "M"}${(x0 + u * (x1 - x0)).toFixed(1)},${(mid + Math.sin(a) * amp).toFixed(1)}`; }).join("");

      const col = { complement: "#f1f5f9", revise: "#a78bfa", mismatch: "#f87171", deferred: "#64748b", auto: "#64748b", disputed: "#fb923c" };

      const rungs = bs.map((b, i) => { const u = (i + 0.5) / n, a = u * turns * Math.PI * 2, x = x0 + u * (x1 - x0), y1 = mid + Math.sin(a) * amp, y2 = mid - Math.sin(a) * amp; return `<g data-id="${b.id}" style="cursor:pointer"><line x1="${x}" y1="${y1}" x2="${x}" y2="${(y1 + y2) / 2}" stroke="${b.k === "task" ? "#38bdf8" : "#34d399"}" stroke-width="3"/><line x1="${x}" y1="${(y1 + y2) / 2}" x2="${x}" y2="${y2}" stroke="${col[b.s] || "#F59E0B"}" stroke-width="3" ${b.s === "open" ? 'stroke-dasharray="3 4"' : ""}/>${b.x ? `<circle cx="${x}" cy="${y2}" r="5" fill="none" stroke="#fde68a" stroke-width="2"/>` : ""}</g>`; }).join("");

      el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" style="position:absolute;inset:0;width:100%;height:100%"><path d="${path(Math.PI)}" stroke="#F59E0B" stroke-width="4" fill="none" opacity=".9"/><path d="${path(0)}" stroke="#10B981" stroke-width="4" fill="none"/>${rungs}<text x="20" y="30" fill="#7fd2c0" font-size="12" letter-spacing="2">HELIX · 静态视图</text><text x="${w - 20}" y="30" fill="${p.stalled ? "#fcd34d" : "#6ee7b7"}" font-size="13" text-anchor="end">${p.stalled ? "复制叉阻滞 · 等待真人" : "前进"}</text></svg>`;

      el.querySelectorAll("g[data-id]").forEach((g) => (g.onclick = () => opt.onSelect?.(g.dataset.id)));

    };

    draw(p);

    return { update: draw, focus() {}, destroy() { el.innerHTML = ""; } };

  },

};

export const motionPacks = { native, v2, calm };

export function mountMotion(id, el, payload, opt) { const pack = motionPacks[id] || native; return { id: pack.id, ...pack.mount(el, payload, opt) }; }

