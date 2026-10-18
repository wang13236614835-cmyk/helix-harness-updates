// 事件总线：对齐 AG-UI 的事件命名（RUN_STARTED / STATE_SNAPSHOT / STEP_STARTED / CUSTOM …），
// 将来 helixd 推送 AG-UI 事件流时，控制台与螺旋可以零改动接上。
const log = [];
const subs = new Set();
export const bus = {
  log,
  emit(type, data = {}, level = "info") {
    const ev = { ts: new Date(), type, data, level };
    log.push(ev); if (log.length > 400) log.shift();
    subs.forEach((f) => f(ev));
    return ev;
  },
  on(fn) { subs.add(fn); return () => subs.delete(fn); },
};
