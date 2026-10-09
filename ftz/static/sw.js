/* Service worker: makes the app installable and keeps the look-and-feel files available.
 * It deliberately NEVER stores pages, API answers, documents or exports: those are private and must always
 * come live from the server, so a shared device cannot show someone else's ledger. */
const VERSION = "ftz-shell-v2";
const SHELL = ["/offline.html", "/style.css", "/auth.css", "/logo.png", "/compass.png", "/icon-192.png"];
const STATIC = new Set([...SHELL, "/favicon.png", "/icon-512.png", "/icon-maskable-512.png", "/apple-touch-icon.png", "/auth.js", "/pwa.js", "/manifest.webmanifest"]);

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url), p = url.pathname;
  if (req.method !== "GET" || url.origin !== location.origin) return;                       // leave everything else to the browser
  if (/^\/(api|auth|print|export)\//.test(p) || p === "/backup" || p === "/healthz" || p === "/sw.js") return;   // private or dynamic: never intercepted
  if (req.mode === "navigate") {                                                            // pages: always live; friendly page only if offline
    e.respondWith(fetch(req).catch(() => caches.match("/offline.html")));
    return;
  }
  if (STATIC.has(p) || p === "/app.js" || p === "/ft.js") {                                                   // assets: newest first, cached copy if offline
    e.respondWith(fetch(req).then((res) => {
      if (res.ok && STATIC.has(p)) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req)));
  }
});
