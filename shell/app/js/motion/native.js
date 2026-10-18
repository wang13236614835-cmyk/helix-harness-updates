// 动效包 native：2.00 helix_view 双螺旋的逐行移植（同一 payload、同一配色、同一图例语义），
// 增加 update(payload) 平滑过渡、点击选中、外部高亮、暂停（尊重 reduced-motion / 偏好）。
const COLORS = { disputed: "#fb923c", complement: "#f1f5f9", revise: "#a78bfa", mismatch: "#f87171", deferred: "#64748b", auto: "#64748b", open: "#F59E0B", audit: "#F59E0B" };
const LABEL = { disputed: "交叉核不一致", open: "待配对 · G–C", audit: "待抽检 · A–T", auto: "已先行 · A–T", deferred: "暂缓", complement: "互补", revise: "已修正", mismatch: "错配" };
const e = (s) => String(s ?? "").replace(/</g, "&lt;");

export default {
  id: "native", label: "双螺旋 · 原生",
  mount(el, payload, opt = {}) {
    el.innerHTML = `<canvas style="position:absolute;inset:0;width:100%;height:100%;cursor:crosshair"></canvas>
      <div class="hx-hd" style="position:absolute;left:20px;top:16px;color:#eef9f6;pointer-events:none"><span style="display:block;font-size:10.5px;letter-spacing:.16em;color:#7fd2c0;font-weight:800">HELIX HARNESS · 人机双螺旋</span><b style="display:block;margin-top:5px;font-size:18px">AI 合成 · 真人配对</b><em class="hx-auto" style="display:inline-block;margin-top:7px;font-style:normal;font-size:11.5px;padding:3px 9px;border-radius:99px;background:#0e3b37;border:1px solid #4fae9766;color:#c9f2e4"></em></div>
      <div class="hx-fork" style="position:absolute;right:18px;top:16px;text-align:right;font-size:11.5px;color:#cfeee4;pointer-events:none"><span>复制叉</span><b style="display:block;font-size:14px;margin-top:4px"></b></div>
      <div style="position:absolute;left:18px;bottom:14px;display:flex;flex-wrap:wrap;gap:12px;font-size:11.5px;color:#bfe3d9;pointer-events:none;max-width:70%">${[["#10B981", "AI 链"], ["#F59E0B", "真人链"], ["#f1f5f9", "互补"], ["#a78bfa", "修正"], ["#f87171", "错配"], ["#64748b", "暂缓/先行"], ["#38bdf8", "真实任务"]].map(([c, t]) => `<span><i style="display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px;background:${c}"></i>${t}</span>`).join("")}<span><i style="display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px;border:2px solid #fde68a"></i>双人互补</span></div>
      <div class="hx-tip" style="position:absolute;pointer-events:none;max-width:340px;padding:8px 11px;border-radius:10px;background:#0a2422f2;border:1px solid #7fd4c255;font-size:12px;line-height:1.5;opacity:0;transition:opacity .12s;color:#eef9f6;box-shadow:0 8px 22px #0008;z-index:2"></div>
      <div class="hx-empty" style="position:absolute;inset:0;display:none;align-items:center;justify-content:center;color:#9cc9bd;font-size:13px;pointer-events:none">还没有碱基 · 播种本周任务或开始一轮螺旋回合</div>`;
    const cv = el.querySelector("canvas"), cx = cv.getContext("2d"), tip = el.querySelector(".hx-tip");
    let D = payload, W = 0, H = 0, dpr = Math.max(1, devicePixelRatio || 1), t0 = performance.now(), hover = -1, rungs = [], drag = null, spin = 0, raf = 0, alive = true, focus = null, grow = 0;
    const paused = () => opt.paused?.() || matchMedia("(prefers-reduced-motion: reduce)").matches;
    const size = () => { const r = el.getBoundingClientRect(); W = r.width; H = r.height; cv.width = W * dpr; cv.height = H * dpr; cx.setTransform(dpr, 0, 0, dpr, 0, 0); };
    const ro = new ResizeObserver(size); ro.observe(el); size();
    const chrome = () => {
      el.querySelector(".hx-auto").textContent = ({ standard: "标准螺旋", conservative: "保守模式", exploratory: "探索模式" })[D.autonomy] || D.autonomy || "标准螺旋";
      const f = el.querySelector(".hx-fork b"); f.textContent = D.stalled ? "阻滞 · 等待真人配对" : "前进 · 可开启下一轮"; f.style.color = D.stalled ? "#fcd34d" : "#6ee7b7";
      el.querySelector(".hx-empty").style.display = (D.bases || []).length ? "none" : "flex";
    };
    chrome();
    function frame(now) {
      if (!alive) return;
      const t = paused() ? 0 : (now - t0) / 1000; grow = Math.min(1, grow + 0.04);
      cx.clearRect(0, 0, W, H);
      const list = D.bases || [], N = Math.max(list.length, 10);
      const x0 = 70, x1 = W - 120, mid = H * 0.52, amp = Math.min(78, H * 0.24), turns = Math.max(1.6, N / 7), phase = t * 0.55 + spin;
      const pts = []; for (let i = 0; i <= 220; i++) { const u = i / 220, a = u * turns * Math.PI * 2 + phase; pts.push({ x: x0 + u * (x1 - x0), a }); }
      const P = (p, off) => { const a = p.a + off; return { x: p.x, y: mid + Math.sin(a) * amp, z: Math.cos(a) }; };
      for (const [off, col] of [[0, "#10B981"], [Math.PI, "#F59E0B"]]) for (let pass = 0; pass < 2; pass++) {
        cx.beginPath(); let on = false;
        for (const p of pts) { const q = P(p, off), front = q.z > 0; if (front === (pass === 1)) { if (!on) { cx.moveTo(q.x, q.y); on = true; } else cx.lineTo(q.x, q.y); } else on = false; }
        cx.strokeStyle = col; cx.globalAlpha = pass ? 1 : 0.28; cx.lineWidth = pass ? (off ? 4.2 : 3.4) : 2.4; cx.shadowColor = col; cx.shadowBlur = pass ? 12 : 0; cx.stroke(); cx.shadowBlur = 0;
      }
      cx.globalAlpha = 1; rungs = [];
      list.forEach((b, i) => {
        const u = (i + 0.5) / N, a = u * turns * Math.PI * 2 + phase, x = x0 + u * (x1 - x0);
        const g = Math.min(1, grow * N / (i + 1));
        const ya = mid + Math.sin(a) * amp * g, yh = mid + Math.sin(a + Math.PI) * amp * g, z = Math.cos(a), ym = (ya + yh) / 2, alpha = 0.45 + 0.55 * (z + 1) / 2;
        const hl = i === hover || (focus && b.id === focus);
        cx.globalAlpha = hl ? 1 : alpha;
        cx.strokeStyle = b.p ? "#fca5a5" : b.k === "task" ? "#38bdf8" : "#34d399"; cx.lineWidth = hl ? 4 : 2.6; cx.setLineDash([]);
        cx.beginPath(); cx.moveTo(x, ya); cx.lineTo(x, ym); cx.stroke();
        const col = COLORS[b.s] || "#F59E0B";
        if (b.s === "open" || b.s === "audit") { const pulse = paused() ? 0.6 : 0.35 + 0.45 * (Math.sin(t * 3 + i) + 1) / 2; cx.globalAlpha = (hl ? 1 : alpha) * pulse; cx.setLineDash([3, 4]); }
        cx.strokeStyle = col; cx.lineWidth = hl ? 4 : 2.6; cx.beginPath();
        if (b.s === "mismatch") { cx.moveTo(x, ym + (yh > ym ? 4 : -4)); cx.lineTo(x + 6, ym + (yh - ym) * 0.45); cx.lineTo(x, yh); } else { cx.moveTo(x, ym); cx.lineTo(x, yh); }
        cx.stroke(); cx.setLineDash([]); cx.globalAlpha = hl ? 1 : alpha;
        if (b.x) { cx.beginPath(); cx.arc(x, yh, hl ? 6 : 4.6, 0, Math.PI * 2); cx.strokeStyle = b.x > 0 ? "#fde68a" : "#f87171"; cx.lineWidth = 2; cx.stroke(); }
        const nb = b.b === "GC" ? 3 : 2; for (let k = 0; k < nb; k++) { const yy = ym + (k - (nb - 1) / 2) * 6; cx.beginPath(); cx.arc(x, yy, hl ? 2.4 : 1.8, 0, 7); cx.fillStyle = b.b === "GC" ? "#fde68a" : "#a7f3d0"; cx.fill(); }
        cx.beginPath(); cx.arc(x, ya, hl ? 6 : 4.4, 0, 7); cx.fillStyle = "#10B981"; cx.fill();
        cx.beginPath(); cx.arc(x, yh, hl ? 6 : 4.4, 0, 7); cx.fillStyle = b.s === "open" || b.s === "audit" ? "#0b2a2b" : col; cx.strokeStyle = "#F59E0B"; cx.lineWidth = 1.6; cx.fill(); cx.stroke();
        if (focus && b.id === focus) { cx.beginPath(); cx.arc(x, ym, 15 + Math.sin(t * 4) * 2, 0, 7); cx.strokeStyle = "#ffffffaa"; cx.lineWidth = 1.2; cx.stroke(); }
        rungs.push({ x, y1: Math.min(ya, yh) - 8, y2: Math.max(ya, yh) + 8, i });
      });
      cx.globalAlpha = 1;
      const fx = x1 + 18; cx.strokeStyle = D.stalled ? "#fcd34d" : "#6ee7b7"; cx.lineWidth = 2.4; cx.setLineDash(D.stalled ? [4, 4] : []);
      cx.beginPath(); cx.moveTo(x1, mid); cx.lineTo(fx + 40, mid - amp * 0.9); cx.moveTo(x1, mid); cx.lineTo(fx + 40, mid + amp * 0.9); cx.stroke(); cx.setLineDash([]);
      cx.beginPath(); cx.arc(x1, mid, D.stalled ? 7 + (paused() ? 0 : Math.sin(t * 4) * 2) : 6, 0, 7); cx.fillStyle = D.stalled ? "#f59e0b" : "#10b981"; cx.shadowColor = cx.fillStyle; cx.shadowBlur = 16; cx.fill(); cx.shadowBlur = 0;
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);
    cv.addEventListener("mousemove", (ev) => {
      const r = cv.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
      if (drag !== null) { spin += (x - drag) / 90; drag = x; }
      let best = -1, bd = 14; for (const g of rungs) { const d = Math.abs(g.x - x); if (d < bd && y > g.y1 && y < g.y2) { bd = d; best = g.i; } }
      hover = best;
      if (best >= 0) { const b = D.bases[best]; tip.innerHTML = `<b>${b.b === "GC" ? "G–C" : "A–T"} · ${LABEL[b.s] || b.s}</b><br>${e(b.t)}<small style="display:block;color:#9cc9bd;margin-top:3px">AI 置信 ${Math.round(b.c * 100)}%${b.r ? " · 配对人 " + e(b.r) : ""}${b.tk ? " · " + e(b.tk) : ""}${b.p ? " · 校对拦截" : ""}</small>`; tip.style.left = Math.min(x + 14, W - 350) + "px"; tip.style.top = Math.max(8, y - 70) + "px"; tip.style.opacity = 1; }
      else tip.style.opacity = 0;
    });
    cv.addEventListener("mouseleave", () => { hover = -1; tip.style.opacity = 0; drag = null; });
    cv.addEventListener("mousedown", (ev) => { drag = ev.clientX - cv.getBoundingClientRect().left; });
    cv.addEventListener("click", () => { if (hover >= 0) opt.onSelect?.(D.bases[hover].id); });
    const up = () => (drag = null); addEventListener("mouseup", up);
    return {
      update(p) { const grew = (p.bases || []).length !== (D.bases || []).length; D = p; if (grew) grow = 0.3; chrome(); },
      focus(id) { focus = id; },
      destroy() { alive = false; cancelAnimationFrame(raf); ro.disconnect(); removeEventListener("mouseup", up); el.innerHTML = ""; },
    };
  },
};
