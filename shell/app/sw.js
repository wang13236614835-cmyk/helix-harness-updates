// Helix 3.2.0：不再使用离线缓存。旧版本注册过的 Service Worker 会在这里自我注销并清理缓存，
// 保证程序更新后页面立即使用新文件。
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) await caches.delete(k);
  await self.registration.unregister();
  for (const c of await self.clients.matchAll({ type: "window" })) c.navigate(c.url);
})()));
