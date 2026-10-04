// Minimal service worker: caches the static app shell so the page is
// installable and opens offline. It does NOT cache /transcribe responses —
// transcription always needs the live backend.
//
// NETWORK-FIRST, not cache-first: an earlier cache-first version bit
// twice during development (this project changes constantly) — editing
// app.js/index.html silently kept serving the OLD cached copy until this
// file's own CACHE constant was manually bumped, which is easy to forget
// and, when forgotten, makes newly-shipped features (like the "Done"
// button) invisibly vanish for anyone with the app already installed.
// Network-first fixes that at the root: every load tries the live server
// first and only falls back to the cache when genuinely offline, so
// "stale until I remember to bump a version number" simply can't happen
// again. The cache is kept up to date opportunistically (whatever
// succeeds over the network gets cached for next time offline), not by
// a fixed pre-cache list.
const CACHE = "path-dictate-v9";
const SHELL = [
  "./",
  "./index.html",
  "./styles.css",
  "./config.js",
  "./auth.js",
  "./app.js",
  "./lookup.html",
  "./lookup.js",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

// Pre-cache the shell on install, so offline-first-launch still works even
// before the network-first fetch handler below has cached anything itself.
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

// Clean up old caches on activate.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first for the shell; never intercept POSTs (let /transcribe hit
// network only — a failed POST should surface as an error, not silently
// serve nothing from the cache).
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
