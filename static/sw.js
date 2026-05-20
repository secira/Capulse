// Target Capital Service Worker for PWA functionality
const CACHE_NAME = 'tcapital-v1.0.2';
const STATIC_CACHE = 'tcapital-static-v1.0.2';
const DYNAMIC_CACHE = 'tcapital-dynamic-v1.0.2';

// Static assets to cache immediately
const STATIC_ASSETS = [
    '/',
    '/static/css/style.css',
    '/static/css/dashboard.css',
    '/static/js/main.js',
    '/static/js/mobile-responsive.js',
    '/static/js/pwa-handler.js',
    '/static/img/tcapital-logo.svg',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css'
];

// Dynamic assets to cache on demand
const CACHE_STRATEGIES = {
    '/api/': 'networkFirst',
    '/ws': 'networkOnly',
    '/static/': 'cacheFirst',
    '/dashboard/': 'networkFirst',
    default: 'networkFirst'
};

// Install event - cache static assets
self.addEventListener('install', function(event) {
    console.log('🔧 Service Worker installing...');
    
    event.waitUntil(
        caches.open(STATIC_CACHE)
            .then(function(cache) {
                console.log('📦 Caching static assets...');
                return cache.addAll(STATIC_ASSETS);
            })
            .then(function() {
                console.log('✅ Static assets cached successfully');
                return self.skipWaiting();
            })
            .catch(function(error) {
                console.error('❌ Static asset caching failed:', error);
            })
    );
});

// Activate event - clean up old caches
self.addEventListener('activate', function(event) {
    console.log('🚀 Service Worker activating...');
    
    event.waitUntil(
        caches.keys()
            .then(function(cacheNames) {
                return Promise.all(
                    cacheNames.map(function(cacheName) {
                        if (cacheName !== STATIC_CACHE && cacheName !== DYNAMIC_CACHE) {
                            console.log('🗑️ Deleting old cache:', cacheName);
                            return caches.delete(cacheName);
                        }
                    })
                );
            })
            .then(function() {
                console.log('✅ Service Worker activated');
                return self.clients.claim();
            })
    );
});

// Fetch event - handle requests with different strategies
self.addEventListener('fetch', function(event) {
    const url = new URL(event.request.url);
    const path = url.pathname;

    // Skip non-GET requests
    if (event.request.method !== 'GET') {
        return;
    }

    // Skip WebSocket connections
    if (event.request.headers.get('upgrade') === 'websocket') {
        return;
    }

    // CRITICAL: never intercept real-time data endpoints — these must
    // always hit the network fresh and must never be served from the
    // SW cache. A stale/hung SW fetch on these paths was the cause of
    // "Fetching live price…" sticking on /dashboard/trade-now.
    if (
        path.startsWith('/api/nse/') ||
        path.startsWith('/api/equities/') ||
        path.startsWith('/api/market/') ||
        path.startsWith('/api/market-pulse/') ||
        path.startsWith('/api/data-quality') ||
        path.startsWith('/api/live-data-status')
    ) {
        return; // let the browser handle it directly, no SW intercept
    }

    // Determine caching strategy
    const strategy = getCacheStrategy(path);

    event.respondWith(
        handleRequest(event.request, strategy)
    );
});

function getCacheStrategy(path) {
    for (const [pattern, strategy] of Object.entries(CACHE_STRATEGIES)) {
        if (path.startsWith(pattern)) {
            return strategy;
        }
    }
    return CACHE_STRATEGIES.default;
}

async function handleRequest(request, strategy) {
    switch (strategy) {
        case 'cacheFirst':
            return cacheFirst(request);
        case 'networkFirst':
            return networkFirst(request);
        case 'networkOnly':
            return networkOnly(request);
        default:
            return networkFirst(request);
    }
}

// Cache First strategy - good for static assets
async function cacheFirst(request) {
    try {
        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
            return cachedResponse;
        }
        
        const networkResponse = await fetch(request);
        
        // Cache successful responses
        if (networkResponse.ok) {
            const cache = await caches.open(getAppropriateCache(request.url));
            cache.put(request, networkResponse.clone());
        }
        
        return networkResponse;
    } catch (error) {
        console.error('Cache first strategy failed:', error);
        
        // Return cached version if available
        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
            return cachedResponse;
        }
        
        // Return offline page for navigation requests
        if (request.mode === 'navigate') {
            return caches.match('/offline.html') || new Response('Offline', { status: 503 });
        }
        
        throw error;
    }
}

// Network First strategy - good for API calls and dynamic content
async function networkFirst(request) {
    try {
        const networkResponse = await fetch(request);
        
        // Cache successful responses for API calls
        if (networkResponse.ok && request.url.includes('/api/')) {
            const cache = await caches.open(DYNAMIC_CACHE);
            
            // Only cache GET requests with specific content types
            const contentType = networkResponse.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                cache.put(request, networkResponse.clone());
            }
        }
        
        return networkResponse;
    } catch (error) {
        console.log('Network request failed, trying cache:', request.url);
        
        // Try cache as fallback
        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
            // Add header to indicate cached response
            const headers = new Headers(cachedResponse.headers);
            headers.set('X-Cache-Status', 'cached');
            
            return new Response(cachedResponse.body, {
                status: cachedResponse.status,
                statusText: cachedResponse.statusText,
                headers: headers
            });
        }
        
        // Return offline indicator for API calls
        if (request.url.includes('/api/')) {
            return new Response(
                JSON.stringify({ 
                    error: 'Offline', 
                    message: 'Please check your internet connection',
                    cached: false 
                }), 
                { 
                    status: 503,
                    headers: { 'Content-Type': 'application/json' }
                }
            );
        }
        
        throw error;
    }
}

// Network Only strategy - for real-time data
async function networkOnly(request) {
    return fetch(request);
}

function getAppropriateCache(url) {
    if (url.includes('/static/') || url.includes('cdn.jsdelivr.net') || url.includes('cdnjs.cloudflare.com')) {
        return STATIC_CACHE;
    }
    return DYNAMIC_CACHE;
}

// Background sync for trading orders when offline
self.addEventListener('sync', function(event) {
    if (event.tag === 'trading-order-sync') {
        event.waitUntil(syncTradingOrders());
    }
});

async function syncTradingOrders() {
    try {
        // Get pending orders from IndexedDB
        const pendingOrders = await getPendingOrders();
        
        for (const order of pendingOrders) {
            try {
                const response = await fetch('/api/orders', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(order.data)
                });
                
                if (response.ok) {
                    await removePendingOrder(order.id);
                    console.log('✅ Synced trading order:', order.id);
                }
            } catch (error) {
                console.error('❌ Failed to sync order:', order.id, error);
            }
        }
    } catch (error) {
        console.error('❌ Background sync failed:', error);
    }
}

// Push notification handling
self.addEventListener('push', function(event) {
    console.log('📨 Push notification received');
    
    let notificationData = {
        title: 'Target Capital',
        body: 'You have a new trading alert',
        icon: '/static/img/icons/icon-192x192.png',
        badge: '/static/img/icons/badge-72x72.png',
        tag: 'trading-alert',
        requireInteraction: true,
        actions: [
            {
                action: 'view',
                title: 'View',
                icon: '/static/img/icons/view-icon.png'
            },
            {
                action: 'dismiss',
                title: 'Dismiss',
                icon: '/static/img/icons/dismiss-icon.png'
            }
        ]
    };
    
    if (event.data) {
        try {
            const data = event.data.json();
            notificationData = { ...notificationData, ...data };
        } catch (error) {
            console.error('Failed to parse push data:', error);
        }
    }
    
    event.waitUntil(
        self.registration.showNotification(notificationData.title, notificationData)
    );
});

// Notification click handling
self.addEventListener('notificationclick', function(event) {
    console.log('🔔 Notification clicked:', event.notification.tag);
    
    event.notification.close();
    
    if (event.action === 'view' || !event.action) {
        event.waitUntil(
            clients.openWindow('/dashboard')
        );
    }
    
    // Track notification interaction
    if (event.action) {
        event.waitUntil(
            fetch('/api/analytics/notification-interaction', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    action: event.action,
                    tag: event.notification.tag,
                    timestamp: Date.now()
                })
            }).catch(error => {
                console.error('Failed to track notification interaction:', error);
            })
        );
    }
});

// Utility functions for IndexedDB operations
async function getPendingOrders() {
    // Implementation would use IndexedDB to store pending orders
    return [];
}

async function removePendingOrder(orderId) {
    // Implementation would remove order from IndexedDB
    console.log('Removing pending order:', orderId);
}

// Error handling
self.addEventListener('error', function(event) {
    console.error('Service Worker error:', event.error);
});

self.addEventListener('unhandledrejection', function(event) {
    console.error('Service Worker unhandled rejection:', event.reason);
});

console.log('🚀 Target Capital Service Worker loaded successfully');