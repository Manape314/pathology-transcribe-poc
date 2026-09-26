// Minimal service worker: caches the static app shell so the page is
// installable and opens offline. It does NOT cache /transcribe responses —
// transcription always needs the live backend.

const CACHE = "path-dictate-v6";
const SHELL = [
  "./",
  "./index.html",
  "./styles.css",
  "./auth.js",
  "./app.js",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

// Pre-cache the shell on install.
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

// Cache-first for the shell; never intercept POSTs (let /transcribe hit network).
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request))
  );
});
