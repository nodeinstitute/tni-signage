// Offline support for the signage player.
// - page, sw.js and slides.json: from the network when it is there, else the last copy
// - slide images: cached once, then served from the cache (content-hash names never change)
// - videos: index.html downloads each one completely into the cache; here they are served from it,
//   including the byte ranges a <video> element asks for. Not cached yet: straight from the network.
const PAGE = "tni-signage-page";
const SLIDES = "tni-signage-slides";

self.addEventListener("install", e => { self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(self.clients.claim()); });

async function fromCacheWithRange(request, cached) {
  const blob = await cached.blob();
  const size = blob.size;
  const type = cached.headers.get("Content-Type") || "video/mp4";
  const range = request.headers.get("Range");
  if (!range) {
    return new Response(blob, { status: 200, headers: { "Content-Type": type, "Content-Length": String(size), "Accept-Ranges": "bytes" } });
  }
  const m = /bytes=(\d*)-(\d*)/.exec(range);
  let start, end;
  if (m && m[1] === "" && m[2] !== "") { start = Math.max(0, size - Number(m[2])); end = size - 1; }  // last N bytes
  else { start = m && m[1] ? Number(m[1]) : 0; end = m && m[2] ? Math.min(Number(m[2]), size - 1) : size - 1; }
  if (start >= size || start > end) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
  }
  return new Response(blob.slice(start, end + 1), {
    status: 206, statusText: "Partial Content",
    headers: { "Content-Type": type, "Content-Range": `bytes ${start}-${end}/${size}`,
               "Content-Length": String(end - start + 1), "Accept-Ranges": "bytes" }
  });
}

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/videos/")) {
    e.respondWith(caches.open(SLIDES).then(async c => {
      const hit = await c.match(url.href);
      return hit ? fromCacheWithRange(e.request, hit) : fetch(e.request);
    }));
    return;
  }
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
