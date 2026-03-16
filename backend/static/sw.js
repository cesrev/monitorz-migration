/* ==========================================================================
   Monitorz — Service Worker
   App-shell cache strategy for static assets only.
   HTML pages (auth-required Jinja routes) are NEVER cached.
   ========================================================================== */

const CACHE_VERSION = 'v1';
const CACHE_NAME = 'monitorz-' + CACHE_VERSION;

// Critical assets — must all succeed or SW install fails
const CRITICAL_ASSETS = [
  '/static/css/style.css',
  '/static/js/app.js',
];

// Optional assets — fail silently (one 404 won't block SW install)
const OPTIONAL_ASSETS = [
  '/static/img/logo.png',
  '/static/img/favicon.ico',
  '/static/manifest.json',
];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      // Critical assets must succeed
      return cache.addAll(CRITICAL_ASSETS).then(function() {
        // Optional assets fail silently
        return Promise.allSettled(
          OPTIONAL_ASSETS.map(function(url) {
            return cache.add(url).catch(function() {});
          })
        );
      });
    }).then(function() {
      // Only skip waiting AFTER cache is fully populated
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function(e) {
  // Delete stale cache versions, then claim all open clients
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys
          .filter(function(k) { return k !== CACHE_NAME; })
          .map(function(k) { return caches.delete(k); })
      );
    }).then(function() {
      return clients.claim();
    })
  );
});

self.addEventListener('fetch', function(e) {
  var url = new URL(e.request.url);

  // Never cache navigation requests (HTML pages require auth — Jinja renders user data)
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request));
    return;
  }

  // API calls: always go to network — let fetch() reject naturally when offline
  // Do NOT return a fake {"error":"offline"} 200 — dashboard JS expects real data shapes
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Static assets: cache-first, fall back to network
  e.respondWith(
    caches.match(e.request).then(function(cached) {
      return cached || fetch(e.request);
    })
  );
});
