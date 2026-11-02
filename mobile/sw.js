/* Hybrid AI service worker: makes the app installable, keeps the app shell
   available, and receives things shared from other Android apps. */
const SHELL = "hai-shell-v1";
const FILES = ["/", "/index.html", "/style.css", "/app.js", "/manifest.webmanifest",
               "/icon-192.png", "/icon-512.png", "/icon-maskable.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k.startsWith("hai-shell-") && k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;

  // Android "Share → Hybrid AI": keep what was shared, then open the app.
  if (url.pathname === "/share" && e.request.method === "POST") {
    e.respondWith((async () => {
      const form = await e.request.formData();
      const cache = await caches.open("hai-share");
      const meta = { title: form.get("title") || "", text: form.get("text") || "", url: form.get("url") || "" };
      if (meta.title || meta.text || meta.url) {
        await cache.put("/shared-text", new Response(JSON.stringify(meta), { headers: { "Content-Type": "application/json" } }));
      }
      let i = 0;
      for (const f of form.getAll("files")) {
        if (!f || !f.size) continue;
        await cache.put(`/shared-file-${Date.now()}-${i++}`, new Response(f, {
          headers: { "Content-Type": f.type || "application/octet-stream", "X-File-Name": encodeURIComponent(f.name || "shared") } }));
      }
      return Response.redirect("/?shared=1", 303);
    })());
    return;
  }

  // Never cache the API or downloads: always live from the laptop.
  if (url.pathname.startsWith("/api/") || url.pathname === "/pair") return;

  // App shell: network first (so updates arrive), cache when the laptop is unreachable.
  if (e.request.method === "GET") {
    e.respondWith(fetch(e.request).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("/index.html"))));
  }
});
