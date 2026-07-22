/**
 * Progressive Web App (PWA) Handler for Capulse
 * Provides app-like experience on mobile devices
 */
(function() {
'use strict';

class PWAHandler {
    constructor() {
        this.installPrompt = null;
        this.isStandalone = false;
        this.init();
    }

    init() {
        this.checkStandaloneMode();
        this.registerServiceWorker();
        this.setupPushNotifications();
        this.optimizeForMobile();
    }

    checkStandaloneMode() {
        // Check if app is running in standalone mode
        this.isStandalone = window.matchMedia('(display-mode: standalone)').matches || 
                           window.navigator.standalone === true;
        
        if (this.isStandalone) {
            document.body.classList.add('pwa-standalone');
            console.log('🚀 Capulse running in PWA mode');
        }
    }

    async registerServiceWorker() {
        if ('serviceWorker' in navigator) {
            try {
                var registrations = await navigator.serviceWorker.getRegistrations();
                for (var reg of registrations) {
                    await reg.unregister();
                }
            } catch (error) {
            }
        }
    }

    setupPushNotifications() {
        if ('Notification' in window && 'serviceWorker' in navigator && 'PushManager' in window) {
            // Setup push notifications for trading alerts
            this.initializePushNotifications();
        }
    }

    async initializePushNotifications() {
        try {
            const permission = await Notification.requestPermission();
            if (permission === 'granted') {
                console.log('✅ Push notifications enabled');
                
                // Subscribe to push notifications
                const registration = await navigator.serviceWorker.ready;
                const subscription = await registration.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: this.urlBase64ToUint8Array(
                        'BEl62iUYgUivxIkv69yViEuiBIa40HI80NqIUHI41nD4ZiL7f3O3J3W1BmLWPL2Q4Y4J1o1R1r4K2qL3O7mWpig'
                    )
                });
                
                // Send subscription to server
                await this.sendSubscriptionToServer(subscription);
            }
        } catch (error) {
            console.error('❌ Push notification setup failed:', error);
        }
    }

    urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/\-/g, '+').replace(/_/g, '/');
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        
        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    async sendSubscriptionToServer(subscription) {
        try {
            await fetch('/api/push-subscription', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(subscription)
            });
        } catch (error) {
            console.error('❌ Failed to send subscription to server:', error);
        }
    }

    optimizeForMobile() {
        // Mobile-specific optimizations
        if (this.isMobileDevice()) {
            this.optimizeViewport();
            this.handleNetworkChanges();
            this.optimizeTouch();
            this.optimizePerformance();
        }
    }

    optimizeViewport() {
        // Ensure proper viewport handling
        let viewport = document.querySelector('meta[name="viewport"]');
        if (!viewport) {
            viewport = document.createElement('meta');
            viewport.name = 'viewport';
            document.head.appendChild(viewport);
        }
        viewport.content = 'width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes';
        
        // Handle viewport changes
        window.addEventListener('resize', this.handleViewportChange.bind(this));
        window.addEventListener('orientationchange', this.handleViewportChange.bind(this));
    }

    handleViewportChange() {
        // Recalculate viewport-dependent elements
        setTimeout(() => {
            // Trigger reflow for charts and responsive elements
            const event = new Event('resize');
            window.dispatchEvent(event);
        }, 100);
    }

    handleNetworkChanges() {
        if ('connection' in navigator) {
            const connection = navigator.connection;
            
            const updateConnectionStatus = () => {
                const isSlowConnection = connection.effectiveType === 'slow-2g' || 
                                       connection.effectiveType === '2g';
                
                if (isSlowConnection) {
                    this.enableSlowConnectionMode();
                } else {
                    this.disableSlowConnectionMode();
                }
            };
            
            connection.addEventListener('change', updateConnectionStatus);
            updateConnectionStatus();
        }
    }

    enableSlowConnectionMode() {
        document.body.classList.add('slow-connection');
        console.log('🐌 Slow connection detected - optimizing experience');
        
        // Disable non-essential animations
        const style = document.createElement('style');
        style.id = 'slow-connection-optimizations';
        style.textContent = `
            .slow-connection * {
                animation-duration: 0s !important;
                animation-delay: 0s !important;
                transition-duration: 0s !important;
            }
        `;
        document.head.appendChild(style);
    }

    disableSlowConnectionMode() {
        document.body.classList.remove('slow-connection');
        const style = document.getElementById('slow-connection-optimizations');
        if (style) style.remove();
    }

    optimizeTouch() {
        // Improve touch responsiveness
        document.addEventListener('touchstart', function() {}, { passive: true });
        document.addEventListener('touchmove', function() {}, { passive: true });
        
        // Add touch feedback
        const style = document.createElement('style');
        style.textContent = `
            .btn:active, .nav-link:active, .dropdown-item:active {
                transform: scale(0.98);
                transition: transform 0.1s ease;
            }
        `;
        document.head.appendChild(style);
    }

    optimizePerformance() {
        // Lazy load images below the fold
        if ('IntersectionObserver' in window) {
            const imageObserver = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const img = entry.target;
                        img.src = img.dataset.src;
                        img.classList.remove('lazy');
                        imageObserver.unobserve(img);
                    }
                });
            });

            document.querySelectorAll('img[data-src]').forEach(img => {
                imageObserver.observe(img);
            });
        }

        // Preload critical resources
        this.preloadCriticalResources();
    }

    preloadCriticalResources() {
        const criticalResources = [
            '/static/css/dashboard.css',
            '/static/js/mobile-responsive.js'
        ];

        criticalResources.forEach(resource => {
            const link = document.createElement('link');
            link.rel = 'prefetch';
            link.href = resource;
            document.head.appendChild(link);
        });
    }

    isMobileDevice() {
        return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) ||
               window.innerWidth <= 768;
    }

    showUpdateAvailable() {
        const notification = document.createElement('div');
        notification.className = 'update-notification';
        notification.innerHTML = `
            <div class="update-content">
                <strong>Update Available</strong>
                <p>A new version of Capulse is available</p>
                <button class="btn btn-primary btn-sm" onclick="window.location.reload()">Update Now</button>
            </div>
        `;
        
        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            .update-notification {
                position: fixed;
                top: 20px;
                right: 20px;
                background: #0066cc;
                color: white;
                padding: 16px;
                border-radius: 8px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.15);
                z-index: 10000;
                animation: slideDown 0.3s ease-out;
            }
            .update-content strong {
                display: block;
                margin-bottom: 4px;
            }
            .update-content p {
                margin: 0 0 12px 0;
                font-size: 14px;
            }
            @keyframes slideDown {
                from { transform: translateY(-100px); opacity: 0; }
                to { transform: translateY(0); opacity: 1; }
            }
        `;
        document.head.appendChild(style);
        document.body.appendChild(notification);
    }

    trackInstallation() {
        // Track PWA installation for analytics
        if (typeof gtag !== 'undefined') {
            gtag('event', 'pwa_install', {
                event_category: 'PWA',
                event_label: 'Installation'
            });
        }
    }
}

// Initialize PWA handler when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new PWAHandler();
});

window.PWAHandler = PWAHandler;

})();