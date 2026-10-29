// アプリ本体をキャッシュしてオフラインで起動できるようにする。教材は IndexedDB にある。
const VERSION = "listening-v15";
const SHELL = ["./", "index.html", "app.css", "app.js", "engine.js", "store.js", "material.js", "boundary.js", "subtitle.js", "manifest.webmanifest",
  "icons/icon-180.png", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.search.includes("import=")) return;
  // 本体はネット優先（更新を反映）、つながらなければキャッシュ
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok && SHELL.some((p) => url.pathname.endsWith(p.replace("./", "/")))) {
      const copy = res.clone();
      caches.open(VERSION).then((c) => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request, { ignoreSearch: true })
    // 保存していないファイル（更新で増えたファイルなど）は、もう一度ネットから取る
    .then((hit) => hit || fetch(e.request))));
});
