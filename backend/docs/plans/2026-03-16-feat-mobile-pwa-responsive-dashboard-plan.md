---
title: Mobile PWA — Responsive Dashboard Adaptation
type: feat
date: 2026-03-16
deepened: 2026-03-16
---

# Mobile PWA — Responsive Dashboard Adaptation

## Enhancement Summary

**Deepened on:** 2026-03-16
**Research agents used:** best-practices-researcher, security-sentinel, performance-oracle, architecture-strategist, julik-frontend-races-reviewer, kieran-python-reviewer, code-simplicity-reviewer, pattern-recognition-specialist

### Key Improvements from Research
1. **CRITICAL SECURITY FIX**: Do NOT cache `/dashboard`, `/login`, or `/` in the service worker app-shell. These are Jinja2 auth-required routes — caching them exposes previous users' data on shared devices.
2. **Race condition fix**: `skipWaiting()` must be inside the `waitUntil()` chain, chained *after* `addAll()` completes — not called in parallel.
3. **Flask route fix**: Use `app.static_folder` (not `'static'` string), add `Cache-Control: no-cache` + `Service-Worker-Allowed: /` headers.
4. **CSS anti-pattern**: Replace `transform: none !important` with explicit `transform: translateX(0)` via proper cascade ordering.
5. **iOS bottom nav**: Add `-webkit-overflow-scrolling: touch` + `scrollbar-width: none` for smooth momentum scrolling.
6. **Simplification**: The manifest already exists and is linked. Only 2 actual tasks remain: SW file + Flask route + SW registration.
7. **addAll() resilience**: Split critical vs optional assets to prevent one 404 from killing the entire SW installation.

### New Considerations Discovered
- `clients.claim()` combined with `skipWaiting()` can hijack live tabs mid-session — safe default is to omit `skipWaiting()` unless offline-first is critical
- Flask's `send_from_directory('static', ...)` uses cwd-relative path — breaks on Railway if process starts from wrong directory
- The offline JSON `{"error":"offline"}` fake 200 response will cause silent JS crashes (dashboard expects real data shapes, not error objects)
- `RAILWAY_GIT_COMMIT_SHA` env var is available for auto-versioning the SW cache name without a build step

---

## Overview

Make the existing Monitorz Flask web app fully responsive and installable as a PWA on iOS/Android. The goal is NOT a new app — it is the same app at the same URL (`/dashboard`, `/login`, `/`) adapted for small screens. Same dark theme, same logo, same sidebar sections, same design language.

## Problem Statement

The existing app (`http://localhost:5050/dashboard`) is desktop-only:
- Sidebar occupies 260px fixed width — on mobile it overlaps content or hides behind a hamburger toggle that breaks at 480px (CSS cascade bug: `transform: translateX(-100%)` from the 768px breakpoint is not cleanly overridden at 480px).
- No service worker → cannot be installed as PWA, no offline support.
- `manifest.json` exists but lacks maskable icons, `scope`, and `lang` fields.
- Many tables, modals, and stat grids overflow on narrow screens.
- The 35+ `fetch()` calls in `dashboard.html` rely on cookie sessions — this works correctly in PWA standalone mode.

## Proposed Solution

### Phase 1 — CSS Mobile Fixes (partially done)

**Files:** `static/css/style.css`

- [x] Added `MOBILE PWA FIXES` block at end of `style.css`:
  - iOS safe-area insets: `env(safe-area-inset-bottom)` for sidebar and main padding
  - `min-height: -webkit-fill-available` for iOS Safari viewport

- [x] **Fix CSS anti-pattern**: Replace `transform: none !important` with clean cascade. The root cause is two separate 768px blocks in style.css. The correct fix restructures the sidebar responsive rules:

```css
/* At 480px — explicit reset, no !important needed */
@media (max-width: 480px) {
  .sidebar {
    transform: translateX(0); /* explicit reset wins by source order */
    /* No !important needed — source order determines cascade at equal specificity */
  }
}
```

- [x] **iOS bottom nav momentum scrolling** — add to the 480px sidebar block:

```css
@media (max-width: 480px) {
  .sidebar {
    -webkit-overflow-scrolling: touch; /* momentum scroll on iOS */
    scrollbar-width: none;             /* hide scrollbar Firefox */
  }
  .sidebar::-webkit-scrollbar { display: none; } /* hide scrollbar WebKit */
}
```

### Research Insights: CSS

**iOS Scroll Best Practice:**
`overflow-x: auto` alone produces jerky scroll on iOS Safari. The `-webkit-overflow-scrolling: touch` property enables native-feel momentum scrolling for the bottom nav. Combined with `scrollbar-width: none`, the scrollbar is hidden for a cleaner mobile UX.

**CSS Anti-Pattern:**
`!important` in media queries is a code smell that signals cascade confusion. The correct approach: since CSS specificity is equal for the same selector in different media query blocks, **source order determines the winner**. A later block at the same specificity level wins cleanly. Structure the overrides so the 480px block comes after the 768px block in source.

**Breakpoint validity:**
480px still works for current iPhones (375px iPhone SE/14, 390px iPhone 15, 393px iPhone 15 Pro). The 480px breakpoint correctly catches all of them.

---

### Phase 2 — PWA Service Worker

**Files to create:** `static/sw.js`
**Files to modify:** `templates/dashboard.html`, `templates/login.html`, `templates/landing.html`, `static/js/app.js`

#### CRITICAL: Do NOT cache HTML routes in APP_SHELL

`/dashboard`, `/login`, and `/` are Jinja2-rendered routes that contain authenticated user data (`{{ user.name }}`, `{{ accounts }}`, `{{ orders_count }}`). Caching these HTML responses would:
1. Serve stale data on the second visit (frozen snapshot from first install)
2. Expose previous user's data on shared devices after logout — `session.clear()` cannot clear the SW cache

**Only cache static assets (CSS, JS, images):**

```javascript
// static/sw.js
// Cache name includes version for invalidation
// RAILWAY_GIT_COMMIT_SHA injected at deploy time — or use a manual version string
const CACHE_VERSION = 'v1'; // increment on deploy if not using auto-versioning
const CACHE_NAME = `monitorz-${CACHE_VERSION}`;

// Split critical (must succeed) vs optional (can fail silently)
const CRITICAL_ASSETS = [
  '/static/css/style.css',
  '/static/js/app.js',
];

const OPTIONAL_ASSETS = [
  '/static/img/logo.png',
  '/static/img/favicon.ico',
  '/static/manifest.json',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(async (cache) => {
      // Critical assets must all succeed — fail fast if missing
      await cache.addAll(CRITICAL_ASSETS);
      // Optional assets fail silently — one 404 won't break SW install
      await Promise.allSettled(
        OPTIONAL_ASSETS.map(url => cache.add(url).catch(() => {}))
      );
    }).then(() => self.skipWaiting()) // only skip AFTER cache is fully warm
  );
});

self.addEventListener('activate', (e) => {
  // Clean up old cache versions
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // NEVER cache navigation requests (HTML pages) — always go to network
  // This prevents caching auth-required Jinja templates
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request));
    return;
  }

  // API calls: network-first, let fetch() reject naturally on offline
  // Do NOT return a fake 200 {"error":"offline"} — dashboard JS can't handle it
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Static assets: cache-first, fall back to network
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
```

**SW Registration** — add to `static/js/app.js` (runs on all pages):
```javascript
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
```

#### Research Insights: Service Worker

**skipWaiting race condition:**
`skipWaiting()` must be chained *inside* the `waitUntil()` promise, after `addAll()` resolves. Calling it as a parallel statement races against the cache population — on a slow mobile connection the new SW can activate and claim all clients while the cache is still mid-flight, serving requests before the shell assets are available.

**clients.claim() + skipWaiting:**
With both present, open tabs are hijacked mid-session when a new SW deploys. For a session-auth app this is generally safe (no state in the SW), but the combination can interrupt in-progress API sequences. The fixed implementation above uses `skipWaiting()` + `clients.claim()` only after the cache is fully warm.

**Cache versioning without a build step:**
`RAILWAY_GIT_COMMIT_SHA` is injected as an environment variable on every Railway deploy. For auto-versioning, inject it as a template variable in `dashboard.html`:
```python
# app.py
import os
SW_VERSION = os.getenv('RAILWAY_GIT_COMMIT_SHA', 'dev')[:8]
```
Then render it in templates and pass to the SW registration.

---

### Phase 3 — Manifest & Icons

**Files:** `static/manifest.json`, `static/img/`

The manifest already exists and is linked in all three templates. Add missing fields:

```json
{
  "name": "Monitorz",
  "short_name": "Monitorz",
  "description": "Surveillance de billets et commandes Vinted",
  "start_url": "/dashboard",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#0f1117",
  "theme_color": "#0f1117",
  "lang": "fr",
  "icons": [
    {
      "src": "/static/img/logo.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/img/logo.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/img/logo.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    }
  ],
  "prefer_related_applications": false
}
```

Fields added: `scope`, `lang`, `prefer_related_applications`, `purpose: "any"` + `purpose: "maskable"`.

**Note on maskable icons:** The `maskable` purpose requires at least 10% safe-zone padding around the logo. If `logo.png` has no padding, create a padded variant at `static/img/logo-maskable.png` and reference it separately.

Also add a Flask route to serve manifest with correct MIME type:
```python
@app.route('/manifest.json')
def manifest():
    return send_from_directory(app.static_folder, 'manifest.json',
                               mimetype='application/manifest+json')
```

---

### Phase 4 — Bottom Navigation UX

**Files:** `static/css/style.css`, `templates/dashboard.html`

The existing CSS already transforms the sidebar into a bottom navigation bar at 480px. Key improvements needed:

- [ ] Add `-webkit-overflow-scrolling: touch` for iOS momentum scroll
- [ ] Hide scrollbar for cleaner mobile UX
- [ ] Verify active state visual feedback on current section

```css
@media (max-width: 480px) {
  .sidebar {
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }
  .sidebar::-webkit-scrollbar {
    display: none;
  }
}
```

---

### Phase 5 — Responsive Component Audit

**Files:** `static/css/style.css`

Section-by-section responsive audit at 375px:

| Section | Issue | Fix |
|---------|-------|-----|
| `#overview` | Stats grid 3-col overflow | `grid-template-columns: repeat(2, 1fr)` at 480px |
| `#gmail` | Account cards side-by-side | Stack vertically |
| `#sheet` | Input+button overflow | Stack with `flex-direction: column` |
| `#history` | Table overflow | `overflow-x: auto` wrapper |
| `#wts` | Template preview too wide | `max-width: 100%; overflow-x: auto` |
| `#selltime` | Table overflow | `overflow-x: auto` wrapper |
| `#hashtags` | Hashtag grid overflow | 2-col grid |
| `#notifications` | Cards overflow | Full-width cards |
| `#extension` | Extension install card | Already responsive |
| `#settings` | Form inputs | Already full-width |

---

### Phase 6 — Flask Route for Service Worker

**Files:** `app.py`

Service workers must be served from the root scope. The clean solution is a dedicated Flask route.

```python
import os
from flask import send_from_directory, make_response

@app.route('/sw.js')
def service_worker():
    """Serve service worker from root scope for full PWA coverage."""
    response = make_response(
        send_from_directory(app.static_folder, 'sw.js',
                            mimetype='application/javascript')
    )
    # Browser must always revalidate SW to detect updates
    response.headers['Cache-Control'] = 'no-cache'
    # Allow SW to control all routes under /
    response.headers['Service-Worker-Allowed'] = '/'
    return response
```

**Why `app.static_folder` not `'static'`:**
`send_from_directory('static', ...)` resolves the path relative to cwd. On Railway, the process may start from a different directory, silently returning a 404. `app.static_folder` is an absolute path Flask already resolved correctly at startup.

**Why `Cache-Control: no-cache`:**
Flask's default for static files is `max-age=43200` (12 hours). If the SW is cached for 12 hours, users won't receive bug fixes for 12 hours. `no-cache` forces revalidation on every page load (but still uses cached copy if unchanged — it sends `If-Modified-Since`, not re-downloading the whole file).

**Why `Service-Worker-Allowed: /`:**
Without it, the browser restricts the SW scope to `/static/`. Since we're serving from `/sw.js`, this adds an explicit scope grant and prevents any CDN or proxy from inferring a narrower scope.

**Do NOT add auth to this route** — the browser fetches SW files without session cookies. Auth middleware would redirect to `/login` and silently break PWA install.

#### Research Insights: Flask Route

**Placement in app.py:**
Add this route near the other utility routes (`/health`, `/robots.txt`), before blueprint registrations. The existing `set_security_headers` `after_request` hook will still run — but since we set `Cache-Control` on the response object before returning, it takes precedence over any hook that doesn't explicitly overwrite it.

---

## Technical Considerations

- **Cookie session auth**: The existing `/api/*` endpoints use Flask session cookies. PWA in standalone mode uses the same cookies as the browser — works correctly on iOS Safari. No changes needed.
- **iOS Safari PWA limitations**: No Web Push API (only newer iOS 16.4+ with explicit permission), no background sync. These don't affect ticket/vinted monitoring.
- **`display: standalone`**: On iOS, standalone mode hides the address bar. Safe-area insets already applied in Phase 1.
- **No separate build step needed**: All changes are vanilla CSS/JS/HTML — no bundler required.
- **HTTPS required**: Service workers only work over HTTPS (and localhost). Railway provides HTTPS — no action needed. Local dev on localhost also works.
- **Flask compress**: Add `flask-compress` if not already installed to gzip CSS/JS — reduces 90KB CSS to ~15KB on mobile networks.

## Acceptance Criteria

- [ ] App opens correctly on iPhone 14 (375px) and iPhone 15 Pro (393px) in Safari
- [ ] Bottom navigation bar is fully visible and functional on mobile (momentum scroll)
- [ ] All 10 dashboard sections accessible via bottom nav
- [ ] "Add to Home Screen" on iOS shows Monitorz logo and opens in standalone mode (no browser chrome)
- [ ] No horizontal overflow/scroll on any section at 375px
- [ ] Modals open and close correctly on mobile
- [ ] Tables scroll horizontally within their containers (not full-page overflow)
- [ ] Safe-area insets applied — content not hidden behind iPhone home indicator
- [ ] Service worker registered — DevTools > Application > Service Workers shows active SW
- [ ] `manifest.json` valid — Chrome DevTools PWA audit passes (no critical errors)
- [ ] Login page responsive — both activity cards stack vertically on mobile
- [ ] Landing page responsive — hero, pricing, features sections adapt to mobile
- [ ] After logout, visiting PWA does NOT show previous user's data (cached HTML cleared)
- [ ] `/sw.js` returns 200 with `Cache-Control: no-cache` header

## Files to Modify

```
backend/
├── static/
│   ├── css/style.css          ← Phase 1 (CSS fix: transform !important → explicit 0), Phase 4 (iOS scroll), Phase 5 (responsive audit)
│   ├── js/app.js              ← Phase 2 (SW registration on window load)
│   ├── sw.js                  ← Phase 2 (NEW — service worker, static-assets-only cache)
│   └── manifest.json          ← Phase 3 (add scope, lang, maskable purpose)
├── templates/
│   ├── dashboard.html         ← already has PWA meta tags ✓
│   ├── login.html             ← already has PWA meta tags ✓
│   └── landing.html           ← already has PWA meta tags ✓
└── app.py                     ← Phase 6 (/sw.js route + /manifest.json route)
```

## References

- Existing responsive CSS: `static/css/style.css:3535-3607` (MOBILE PWA FIXES block)
- Bottom nav CSS: `static/css/style.css:3272-3332` (480px sidebar → bottom bar)
- Dashboard template: `templates/dashboard.html:1-50` (PWA meta tags already added)
- Current manifest: `static/manifest.json`
- Flask security headers: `app.py:85` (`set_security_headers` after_request hook)
- CSRF check: `app.py` `_csrf_check` before_request (GET-only safe, no impact on SW route)
