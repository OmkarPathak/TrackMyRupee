{% load static %}// Updated: 2026-10-10 (navigation preload; precache only what pages actually request, never the dashboard HTML)
const CACHE_NAME = 'finance-tracker-v37';
const OFFLINE_URL = '/offline/';

// Precache the offline page and the content-hashed assets every page needs. The pages reference these
// exact (hashed) URLs, so a repeat launch is served from the cache with no network round trip.
// Never precache '/' : it is the signed-in dashboard (one of the heaviest renders, and per-user data
// must not sit in Cache Storage). Navigations always go to the network.
const ASSETS_TO_CACHE = [
  OFFLINE_URL,
  '{% static "style.css" %}',
  '{% static "css/tmr_filter.css" %}',
  '{% static "js/tmr_filter.js" %}',
  '{% static "icon.svg" %}',
  '{% static "vendor/bootstrap/bootstrap.min.css" %}',
  '{% static "vendor/fonts.css" %}',
  '{% static "vendor/bootstrap-icons/bootstrap-icons.min.css" %}',
  '{% static "vendor/bootstrap-icons/fonts/bootstrap-icons.woff2" %}',
  '{% static "vendor/chartjs/chart-4.4.7.umd.js" %}',
  '{% static "vendor/bootstrap/bootstrap.bundle.min.js" %}',
  '{% static "vendor/htmx/htmx-2.0.4.min.js" %}',
  '{% static "vendor/alpine/alpine-3.14.9.min.js" %}'
];

// Listen for message from client (SKIP_WAITING)
self.addEventListener('message', (event) => {
  if (event.data && (event.data.type === 'SKIP_WAITING' || event.data === 'skipWaiting')) {
    self.skipWaiting();
  }
});

// Install Event
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // One 404 must not abort the whole install (addAll is all-or-nothing)
      return Promise.all(ASSETS_TO_CACHE.map((url) => cache.add(url).catch(() => null)));
    })
  );
  self.skipWaiting();
});

// Activate Event - Purge outdated caches immediately
self.addEventListener('activate', (event) => {
  event.waitUntil(Promise.all([
    // Start the page request in parallel with service worker boot (a cold PWA launch otherwise
    // waits for the worker to start before the network request even begins).
    self.registration.navigationPreload ? self.registration.navigationPreload.enable() : Promise.resolve(),
    caches.keys().then((keyList) => {
      return Promise.all(
        keyList.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  ]));
  self.clients.claim();
});

// Fetch Event
self.addEventListener('fetch', (event) => {
  // Only handle standard HTTP/HTTPS GET requests
  if (event.request.method !== 'GET' || !event.request.url.startsWith('http')) {
    return;
  }

  // Ignore Razorpay, manifest, and admin pages
  if (event.request.url.includes('razorpay') || 
      event.request.url.includes('manifest.json') ||
      event.request.url.includes('/admin/')) {
    return; 
  }

  // Don't cache pricing page
  if (event.request.url.includes('/pricing/')) return;

  const url = new URL(event.request.url);
  const isSameOrigin = url.origin === self.location.origin;

  // Navigation requests (HTML pages) - Network only with offline fallback.
  // Use the preloaded response when the browser already started the request.
  if (event.request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const preloaded = await event.preloadResponse;
        if (preloaded) return preloaded;
        return await fetch(event.request);
      } catch (err) {
        return caches.match(OFFLINE_URL);
      }
    })());
    return;
  }

  // Static assets on same origin (/static/).
  // Content-hashed files (WhiteNoise manifest: name.<12 hex>.ext) can never change under the same URL,
  // so serve them cache-first and skip the network round trip entirely on repeat visits.
  // Anything else (dev server, un-hashed URLs) stays network-first so edits show up immediately.
  if (isSameOrigin && url.pathname.startsWith('/static/')) {
    const isHashed = /\.[0-9a-f]{12}\.[a-z0-9]+$/i.test(url.pathname);
    const fetchAndStore = () => fetch(event.request).then((response) => {
      if (response && response.status === 200 && response.type === 'basic') {
        const responseClone = response.clone();
        caches.open(CACHE_NAME).then((cache) => {
          cache.put(event.request, responseClone);
        });
      }
      return response;
    });
    if (isHashed) {
      event.respondWith(
        caches.match(event.request, { ignoreSearch: true }).then((cached) => cached || fetchAndStore())
      );
    } else {
      event.respondWith(fetchAndStore().catch(() => caches.match(event.request)));
    }
    return;
  }

  // Explicitly cached CDN assets (Bootstrap, Chart.js, jsdelivr): Cache first, network fallback
  const isCachedCdnAsset = ASSETS_TO_CACHE.some(cachedUrl => {
    return cachedUrl.startsWith('http') && event.request.url.startsWith(cachedUrl.split('?')[0]);
  }) || url.hostname.includes('jsdelivr.net');

  if (isCachedCdnAsset) {
    event.respondWith(
      caches.match(event.request).then((response) => {
        return response || fetch(event.request).then((networkResp) => {
          if (networkResp && networkResp.status === 200) {
            const respClone = networkResp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, respClone));
          }
          return networkResp;
        }).catch(() => null);
      })
    );
    return;
  }

  // All other requests (e.g. Speech Recognition APIs, external services, dynamic endpoints):
  // Let browser handle natively without Service Worker interception
});

// Push Notification Event
self.addEventListener('push', function (event) {
    if (event.data) {
        let payload;
        try {
            payload = event.data.json();
        } catch (e) {
            payload = { head: 'TrackMyRupee', body: event.data.text() };
        }
        
        const title = payload.head || 'TrackMyRupee Notification';
        const options = {
            body: payload.body,
            icon: payload.icon || '/static/img/pwa-icon-512.png',
            badge: '/static/img/pwa-icon-512.png',
            image: payload.image || undefined,
            vibrate: [100, 50, 100],
            data: { 
                url: payload.url || '/',
                dateOfArrival: Date.now(),
                primaryKey: 1 
            }
        };
        
        event.waitUntil(
            self.registration.showNotification(title, options)
        );
    }
});

// Notification Click Event
self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            const url = event.notification.data.url;
            
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url.includes(url) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(url);
            }
        })
    );
});
