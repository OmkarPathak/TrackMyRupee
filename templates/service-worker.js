// Updated: 2026-03-25 10:45 (Force Refresh)
const CACHE_NAME = 'finance-tracker-v18';
const OFFLINE_URL = '/offline/';

const ASSETS_TO_CACHE = [
  '/',
  OFFLINE_URL,
  '/static/style.css',
  '/static/icon.svg',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',
  'https://cdn.jsdelivr.net/npm/chart.js'
];

// Install Event
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // Force cache offline page first
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

// Activate Event
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
  // Fix: Ignore non-GET requests (like Razorpay POST) to prevent Cache API errors
  // Also ignore manifest.json to prevent load failures during install, and admin pages
  if (event.request.method !== 'GET' || 
      event.request.url.includes('razorpay') || 
      event.request.url.includes('manifest.json') ||
      event.request.url.includes('/admin/')) {
    return; 
  }

  // Fix: Don't cache pricing page to ensure fresh API keys (prevents 400 Bad Request)
  if (event.request.url.includes('/pricing/')) return;

  // Navigation requests (HTML pages)
  // CRITICAL FIX: Do NOT cache HTML pages (navigation requests) in the runtime cache.
  // This prevents one user's dashboard (e.g. at '/') from being served to another user after logout/login.
  // Strategy: Network Only -> Fallback to Offline Page
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .catch(() => {
          return caches.match(OFFLINE_URL);
        })
    );
    return;
  }

  // Static assets (Cache first, network fallback)
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});

// Push Notification Event
self.addEventListener('push', function (event) {
    console.log('[Service Worker] Push Received.');
    
    if (event.data) {
        let payload;
        try {
            payload = event.data.json();
            console.log('[Service Worker] Payload (JSON):', payload);
        } catch (e) {
            payload = { head: 'TrackMyRupee', body: event.data.text() };
            console.log('[Service Worker] Payload (Text):', payload);
        }
        
        const title = payload.head || 'TrackMyRupee Notification';
        const options = {
            body: payload.body,
            icon: payload.icon || '/static/img/pwa-icon-512.png',
            badge: '/static/img/pwa-icon-512.png',
            vibrate: [100, 50, 100],
            data: { 
                url: payload.url || '/',
                dateOfArrival: Date.now(),
                primaryKey: 1 
            }
        };
        
        event.waitUntil(
            self.registration.showNotification(title, options)
                .then(() => console.log('[Service Worker] Notification shown.'))
                .catch(err => console.error('[Service Worker] Error showing notification:', err))
        );
    } else {
        console.warn('[Service Worker] Push event but no data');
    }
});

// Notification Click Event
self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            const url = event.notification.data.url;
            
            // If the window is already open, focus it
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url.includes(url) && 'focus' in client) {
                    return client.focus();
                }
            }
            // Otherwise open a new window
            if (clients.openWindow) {
                return clients.openWindow(url);
            }
        })
    );
});
