// Updated: 2026-09-22 (Deployment Auto-Update)
const CACHE_NAME = 'finance-tracker-v32';
const OFFLINE_URL = '/offline/';

const ASSETS_TO_CACHE = [
  '/',
  OFFLINE_URL,
  '/static/style.css',
  '/static/css/tmr_filter.css',
  '/static/css/tmr_filter.css?v=1.5',
  '/static/js/tmr_filter.js',
  '/static/js/tmr_filter.js?v=1.5',
  '/static/icon.svg',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',
  'https://cdn.jsdelivr.net/npm/chart.js'
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
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

// Activate Event - Purge outdated caches immediately
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keyList) => {
      return Promise.all(
        keyList.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch Event
self.addEventListener('fetch', (event) => {
  // Ignore non-GET requests, Razorpay, manifest, and admin pages
  if (event.request.method !== 'GET' || 
      event.request.url.includes('razorpay') || 
      event.request.url.includes('manifest.json') ||
      event.request.url.includes('/admin/')) {
    return; 
  }

  // Don't cache pricing page
  if (event.request.url.includes('/pricing/')) return;

  // Navigation requests (HTML pages) - Network only with offline fallback
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .catch(() => {
          return caches.match(OFFLINE_URL);
        })
    );
    return;
  }

  // Static assets: Network first, fallback to cache for app domain (/static/)
  if (event.request.url.includes('/static/')) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200 && response.type === 'basic') {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        })
        .catch(() => {
          return caches.match(event.request);
        })
    );
    return;
  }

  // Other static assets (CDNs, etc): Cache first, network fallback
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request).catch(() => null);
    })
  );
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
