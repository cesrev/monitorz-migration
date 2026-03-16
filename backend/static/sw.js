/* ==========================================================================
   Monitorz — Service Worker
   Cache les assets statiques (CSS, JS) uniquement.
   Les pages HTML (routes Jinja auth-required) ne sont JAMAIS cachees.
   ========================================================================== */

const CACHE_VERSION = 'v1';
const CACHE_NAME = 'monitorz-' + CACHE_VERSION;

// Assets critiques mis en cache a l'installation
const CRITICAL_ASSETS = [
  '/static/css/style.css',
  '/static/js/app.js',
];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(function(cache) { return cache.addAll(CRITICAL_ASSETS); })
      .then(function() { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys()
      .then(function(keys) {
        return Promise.all(
          keys.filter(function(k) { return k !== CACHE_NAME; })
              .map(function(k) { return caches.delete(k); })
        );
      })
      .then(function() { return clients.claim(); })
  );
});

self.addEventListener('fetch', function(e) {
  // Ne jamais cacher les navigations (pages HTML Jinja avec donnees auth)
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request));
    return;
  }

  // API: toujours reseau (laisser fetch() rejeter si hors-ligne)
  if (e.request.url.indexOf('/api/') !== -1) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Assets statiques: cache d'abord, reseau en fallback
  e.respondWith(
    caches.match(e.request).then(function(cached) {
      return cached || fetch(e.request);
    })
  );
});
