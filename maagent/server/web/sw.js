const CACHE = 'maagent-shell-v10';
const ASSETS = ['/', '/index.html', '/styles.css', '/app.js', '/manifest.webmanifest', '/icon.png', '/icon-192.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS.map((url) => new Request(url, { cache: 'reload' })))).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith('maagent-shell-') && key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (!ASSETS.includes(url.pathname)) return;
  // Versioned shell loads immediately even if the Funnel connection stalls.
  // Installation replaces the whole shell; authenticated API data is never cached.
  event.respondWith(
    caches.open(CACHE).then(async (cache) => (await cache.match(url.pathname)) || fetch(event.request))
  );
});
