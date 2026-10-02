// Offline support for the signage player: the page and slides.json come from the network when it
// is there (falling back to the last copy), slide images are cached once and served from the
// cache (their file names are content hashes, so they never change).
const PAGE = "tni-signage-page";
const SLIDES = "tni-signage-slides";

self.addEventListener("install", e => { self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(self.clients.claim()); });

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/slides/")) {
    e.respondWith(caches.open(SLIDES).then(async c => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok) c.put(e.request, res.clone());
      return res;
    }));
    return;
  }
  // page, sw and slides.json: network first, cached copy when offline
  const key = url.origin + url.pathname;
  e.respondWith(fetch(e.request).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(PAGE).then(c => c.put(key, copy)); }
    return res;
  }).catch(() => caches.open(PAGE).then(c => c.match(key)).then(hit => hit || Response.error())));
});
