// Minimal service worker so FreeAI Hub installs cleanly as a PWA on Windows & mobile.
const CACHE_NAME = 'freeai-hub-v2';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Network-first pass-through; local FastAPI serves all dynamic API & SSE routes live.
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
