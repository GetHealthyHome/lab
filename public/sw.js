// App shell cache: network first, fall back to the last good copy when offline.
const CACHE = "tools-v1";
const SHELL = ["/", "/scan?mode=checkout", "/scan?mode=checkin", "/scan?mode=lookup", "/scan?mode=audit", "/scan?mode=damage", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return res; })
      .catch(async () => (await caches.match(e.request)) || (e.request.mode === "navigate" ? caches.match("/") : Response.error())),
  );
});
