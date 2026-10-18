"""Helix Harness 视觉层：可旋转的 3D 双螺旋（canvas）+ 页面样式。

- 青绿链 = AI 合成链，琥珀链 = 真人配对链；
- 每一级“横档”= 一个碱基；横档中间的小点 = 氢键数（A–T 2 个，G–C 3 个）；
- 已配对：两半实线相接；待配对：真人半边虚线呼吸；错配：红色断裂；修正：紫色；
- 复制叉在右端：有未配对 G–C 时显示“阻滞”，否则继续旋转前进。
完全离线，无外部依赖；尊重 prefers-reduced-motion。
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

CSS_FILE = "helix_harness.css"


def page_css() -> str:
    try:
        from ui.theme import theme_css_path
        path = theme_css_path().with_name(CSS_FILE)
    except Exception:
        path = Path(__file__).resolve().parents[2] / "assets" / CSS_FILE
    try:
        return "<style>" + path.read_text(encoding="utf-8") + "</style>"
    except OSError:
        return ""


def helix_payload(bases: Sequence[Mapping[str, Any]], *, stalled: bool, autonomy_label: str, limit: int = 30) -> dict[str, Any]:
    rows = list(bases)[-limit:]
    return {
        "stalled": bool(stalled),
        "autonomy": autonomy_label,
        "bases": [
            {
                "id": str(b.get("base_id")),
                "t": str(b.get("text") or "")[:140],
                "k": str(b.get("kind") or "action"),
                "b": str(b.get("bond") or "GC"),
                "s": str(b.get("status") or "open"),
                "c": round(float(b.get("confidence") or 0.5), 2),
                "r": str(b.get("reviewer") or ""),
                "p": bool(b.get("proofread_block")),
                "x": (1 if (b.get("cross") or {}).get("agree") else (-1 if b.get("cross") else 0)),
                "tk": str(b.get("task_id") or ""),
            }
            for b in rows
        ],
    }


_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:transparent;overflow:hidden;font-family:Inter,"Microsoft YaHei",system-ui,sans-serif}
#hx{position:relative;height:100%;border-radius:20px;overflow:hidden;color:#eef9f6;
background:radial-gradient(circle at 12% 18%,#1f7a6640,transparent 34%),radial-gradient(circle at 88% 22%,#b7792b2e,transparent 30%),linear-gradient(155deg,#05181c,#0b2a2b 52%,#081d21)}
#hx:before{content:"";position:absolute;inset:0;opacity:.18;pointer-events:none;background-image:linear-gradient(#9adfce12 1px,transparent 1px),linear-gradient(90deg,#9adfce12 1px,transparent 1px);background-size:26px 26px}
canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
.lg{position:absolute;left:18px;bottom:14px;display:flex;flex-wrap:wrap;gap:12px;font-size:11.5px;color:#bfe3d9}
.lg i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.hd{position:absolute;left:20px;top:16px}
.hd span{display:block;font-size:10.5px;letter-spacing:.16em;color:#7fd2c0;font-weight:800}
.hd b{display:block;margin-top:5px;font-size:18px;color:#f4fbf9}
.hd em{display:inline-block;margin-top:7px;font-style:normal;font-size:11.5px;padding:3px 9px;border-radius:99px;background:#0e3b37;border:1px solid #4fae9766;color:#c9f2e4}
.fork{position:absolute;right:18px;top:16px;text-align:right;font-size:11.5px;color:#cfeee4}
.fork b{display:block;font-size:14px;margin-top:4px}
.fork.st b{color:#fcd34d}.fork.go b{color:#6ee7b7}
#tip{position:absolute;pointer-events:none;max-width:340px;padding:8px 11px;border-radius:10px;background:#0a2422f2;border:1px solid #7fd4c255;font-size:12px;line-height:1.5;opacity:0;transition:opacity .12s;box-shadow:0 8px 22px #0008}
#tip.on{opacity:1}#tip small{display:block;color:#9cc9bd;margin-top:3px}
.empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#9cc9bd;font-size:13px;pointer-events:none}
</style></head><body><div id="hx">
<canvas id="cv"></canvas>
<div class="hd"><span>HELIX HARNESS · 人机双螺旋</span><b>AI 合成 · 真人配对</b><em>__AUTONOMY__</em></div>
<div class="fork __FORKCLS__"><span>复制叉</span><b>__FORKTXT__</b></div>
<div class="lg"><span><i style="background:#10B981"></i>AI 链</span><span><i style="background:#F59E0B"></i>真人链</span><span><i style="background:#f1f5f9"></i>互补</span><span><i style="background:#a78bfa"></i>修正</span><span><i style="background:#f87171"></i>错配</span><span><i style="background:#64748b"></i>暂缓 / 先行</span><span><i style="background:#38bdf8"></i>真实任务</span><span><i style="background:transparent;border:2px solid #fde68a"></i>双人互补</span><span>● ● A–T　● ● ● G–C</span></div>
<div id="tip"></div>__EMPTY__
</div><script>
const D=__DATA__;
const cv=document.getElementById('cv'),cx=cv.getContext('2d'),tip=document.getElementById('tip');
const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
let W=0,H=0,dpr=Math.max(1,window.devicePixelRatio||1),t0=performance.now(),hover=-1,rungs=[],drag=null,spin=0;
function size(){const r=cv.getBoundingClientRect();W=r.width;H=r.height;cv.width=W*dpr;cv.height=H*dpr;cx.setTransform(dpr,0,0,dpr,0,0)}
addEventListener('resize',size);size();
const N=Math.max(D.bases.length,10);
const COLORS={disputed:'#fb923c',complement:'#f1f5f9',revise:'#a78bfa',mismatch:'#f87171',deferred:'#64748b',auto:'#64748b',open:'#F59E0B',audit:'#F59E0B'};
const LABEL={disputed:'交叉核不一致',open:'待配对 · G–C',audit:'待抽检 · A–T',auto:'已先行 · A–T',deferred:'暂缓',complement:'互补',revise:'已修正',mismatch:'错配'};
function frame(now){
  const t=reduce?0:(now-t0)/1000;
  cx.clearRect(0,0,W,H);
  const x0=70,x1=W-120,mid=H*0.52,amp=Math.min(78,H*0.24),turns=Math.max(1.6,N/7);
  const phase=t*0.55+spin;
  const pts=[];
  const S=220;
  for(let i=0;i<=S;i++){const u=i/S,a=u*turns*Math.PI*2+phase;pts.push({x:x0+u*(x1-x0),a})}
  function P(p,off){const a=p.a+off;return{x:p.x,y:mid+Math.sin(a)*amp,z:Math.cos(a)}}
  // back strands
  for(const [off,col] of [[0,'#10B981'],[Math.PI,'#F59E0B']]){
    for(let pass=0;pass<2;pass++){
      cx.beginPath();let on=false;
      for(const p of pts){const q=P(p,off);const front=q.z>0;if(front===(pass===1)){if(!on){cx.moveTo(q.x,q.y);on=true}else cx.lineTo(q.x,q.y)}else on=false}
      cx.strokeStyle=col;cx.globalAlpha=pass?1:.28;cx.lineWidth=pass?(off?4.2:3.4):2.4;cx.shadowColor=col;cx.shadowBlur=pass?12:0;cx.stroke();cx.shadowBlur=0;
    }
  }
  cx.globalAlpha=1;
  // rungs
  rungs=[];
  const list=D.bases;
  list.forEach((b,i)=>{
    const u=(i+0.5)/N,a=u*turns*Math.PI*2+phase,x=x0+u*(x1-x0);
    const ya=mid+Math.sin(a)*amp,yh=mid+Math.sin(a+Math.PI)*amp,z=Math.cos(a);
    const ym=(ya+yh)/2,alpha=.45+.55*(z+1)/2,hl=i===hover;
    cx.globalAlpha=hl?1:alpha;
    // AI half
    cx.strokeStyle=b.p?'#fca5a5':(b.k==='task'?'#38bdf8':'#34d399');cx.lineWidth=hl?4:2.6;cx.setLineDash([]);
    cx.beginPath();cx.moveTo(x,ya);cx.lineTo(x,ym);cx.stroke();
    // human half
    const col=COLORS[b.s]||'#F59E0B';
    if(b.s==='open'||b.s==='audit'){const pulse=reduce?.6:.35+.45*(Math.sin(t*3+i)+1)/2;cx.globalAlpha=(hl?1:alpha)*pulse;cx.setLineDash([3,4]);}
    if(b.s==='mismatch'){cx.strokeStyle=col;cx.lineWidth=hl?4:2.6;cx.beginPath();cx.moveTo(x,ym+(yh>ym?4:-4));cx.lineTo(x+6,ym+(yh-ym)*.45);cx.lineTo(x,yh);cx.stroke();}
    else{cx.strokeStyle=col;cx.lineWidth=hl?4:2.6;cx.beginPath();cx.moveTo(x,ym);cx.lineTo(x,yh);cx.stroke();}
    cx.setLineDash([]);cx.globalAlpha=hl?1:alpha;
    if(b.x){cx.beginPath();cx.arc(x,yh,hl?6:4.6,0,Math.PI*2);cx.strokeStyle=b.x>0?'#fde68a':'#f87171';cx.lineWidth=2;cx.stroke();}
    // hydrogen bonds
    const nb=b.b==='GC'?3:2;for(let k=0;k<nb;k++){const yy=ym+(k-(nb-1)/2)*6;cx.beginPath();cx.arc(x,yy,hl?2.4:1.8,0,7);cx.fillStyle=b.b==='GC'?'#fde68a':'#a7f3d0';cx.fill()}
    // beads
    cx.beginPath();cx.arc(x,ya,hl?6:4.4,0,7);cx.fillStyle='#10B981';cx.fill();
    cx.beginPath();cx.arc(x,yh,hl?6:4.4,0,7);cx.fillStyle=(b.s==='open'||b.s==='audit')?'#0b2a2b':col;cx.strokeStyle='#F59E0B';cx.lineWidth=1.6;cx.fill();cx.stroke();
    rungs.push({x,y1:Math.min(ya,yh)-8,y2:Math.max(ya,yh)+8,i});
  });
  cx.globalAlpha=1;
  // replication fork
  const fx=x1+18;
  cx.strokeStyle=D.stalled?'#fcd34d':'#6ee7b7';cx.lineWidth=2.4;cx.setLineDash(D.stalled?[4,4]:[]);
  cx.beginPath();cx.moveTo(x1,mid);cx.lineTo(fx+40,mid-amp*.9);cx.moveTo(x1,mid);cx.lineTo(fx+40,mid+amp*.9);cx.stroke();cx.setLineDash([]);
  cx.beginPath();cx.arc(x1,mid,D.stalled?7+(reduce?0:Math.sin(t*4)*2):6,0,7);cx.fillStyle=D.stalled?'#f59e0b':'#10b981';cx.shadowColor=cx.fillStyle;cx.shadowBlur=16;cx.fill();cx.shadowBlur=0;
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
cv.addEventListener('mousemove',e=>{
  const r=cv.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
  if(drag!==null){spin+=(x-drag)/90;drag=x}
  let best=-1,bd=14;for(const g of rungs){const d=Math.abs(g.x-x);if(d<bd&&y>g.y1&&y<g.y2){bd=d;best=g.i}}
  hover=best;
  if(best>=0){const b=D.bases[best];tip.innerHTML=`<b>${b.b==='GC'?'G–C':'A–T'} · ${LABEL[b.s]||b.s}</b><br>${b.t.replace(/</g,'&lt;')}<small>AI 置信 ${Math.round(b.c*100)}%${b.r?' · 配对人 '+b.r.replace(/</g,'&lt;'):''}${b.p?' · 校对拦截':''}</small>`;tip.style.left=Math.min(x+14,W-350)+'px';tip.style.top=Math.max(8,y-70)+'px';tip.classList.add('on')}
  else tip.classList.remove('on');
});
cv.addEventListener('mouseleave',()=>{hover=-1;tip.classList.remove('on');drag=null});
cv.addEventListener('mousedown',e=>{drag=e.clientX-cv.getBoundingClientRect().left});
addEventListener('mouseup',()=>{drag=null});
</script></body></html>"""


def helix_html(payload: Mapping[str, Any]) -> str:
    stalled = bool(payload.get("stalled"))
    empty = "" if payload.get("bases") else '<div class="empty">还没有碱基 · 开始一轮螺旋回合，或导入 1.00 的 AI 工作包</div>'
    return (
        _TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__AUTONOMY__", escape(str(payload.get("autonomy") or "")))
        .replace("__FORKCLS__", "st" if stalled else "go")
        .replace("__FORKTXT__", "阻滞 · 等待真人配对" if stalled else "前进 · 可开启下一轮")
        .replace("__EMPTY__", empty)
    )


__all__ = ["helix_html", "helix_payload", "page_css"]
