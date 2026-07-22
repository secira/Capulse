// Capulse — Service Worker SELF-DESTRUCT (2026-05-20)
//
// Previous SW versions intercepted /api/* requests with a networkFirst
// strategy that, after the first slow Dhan-backed request hung, started
// blocking subsequent /api/nse/quote/* calls on /dashboard/trade-now from
// ever reaching the server. The "live price" spinner therefore never
// resolved.
//
// We do not currently need any SW-driven offline / caching behaviour for
// this app, so the simplest robust fix is to turn the SW into a no-op
// that:
//   1. Activates immediately and takes over all clients.
//   2. Deletes every cache it knows about.
//   3. Unregisters itself.
// After this runs once, the browser falls back to its normal network
// stack for everything — no interception, no stale caching — and the
// existing pages start working again on their very next request.

self.addEventListener('install', function(event) {
    self.skipWaiting();
});

self.addEventListener('activate', function(event) {
    event.waitUntil((async function() {
        try {
            const keys = await caches.keys();
            await Promise.all(keys.map(function(k) { return caches.delete(k); }));
        } catch (e) { /* ignore */ }
        try {
            const clientsList = await self.clients.matchAll({ type: 'window' });
            clientsList.forEach(function(c) {
                try { c.navigate(c.url); } catch (e) { /* ignore */ }
            });
        } catch (e) { /* ignore */ }
        try {
            await self.registration.unregister();
        } catch (e) { /* ignore */ }
    })());
});

// Never intercept any fetch — let the browser handle them directly.
self.addEventListener('fetch', function(event) {
    return;
});
