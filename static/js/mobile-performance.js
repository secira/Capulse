/**
 * Mobile Performance Optimizations for Capulse
 * Handles performance monitoring and mobile-specific optimizations
 */

(function() {
    'use strict';

    // Track page performance metrics
    function trackPerformanceMetrics() {
        if (!window.performance) return;

        window.addEventListener('load', function() {
            setTimeout(function() {
                try {
                    var timing = window.performance.timing;
                    var loadTime = (timing.loadEventEnd - timing.navigationStart) / 1000;
                    var domContentLoaded = timing.domContentLoadedEventEnd - timing.navigationStart;
                    var paintEntries = window.performance.getEntriesByType('paint');
                    var firstPaint = 0;
                    var firstContentfulPaint = 0;

                    paintEntries.forEach(function(entry) {
                        if (entry.name === 'first-paint') firstPaint = Math.round(entry.startTime);
                        if (entry.name === 'first-contentful-paint') firstContentfulPaint = Math.round(entry.startTime);
                    });

                    console.log('📊 Page Performance Metrics:', {
                        loadTime: loadTime,
                        domContentLoaded: domContentLoaded,
                        firstPaint: firstPaint,
                        firstContentfulPaint: firstContentfulPaint
                    });
                } catch (e) {
                    // silently ignore metrics errors
                }
            }, 100);
        });
    }

    // Lazy load images for mobile
    function setupLazyLoading() {
        if (!('IntersectionObserver' in window)) return;

        var lazyImages = document.querySelectorAll('img[data-src]');
        var imageObserver = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    var img = entry.target;
                    img.src = img.dataset.src;
                    img.removeAttribute('data-src');
                    imageObserver.unobserve(img);
                }
            });
        });

        lazyImages.forEach(function(img) {
            imageObserver.observe(img);
        });
    }

    // Debounce scroll and resize handlers
    function debounce(fn, delay) {
        var timer;
        return function() {
            clearTimeout(timer);
            timer = setTimeout(fn, delay);
        };
    }

    // Optimize scroll performance
    function optimizeScrollPerformance() {
        var scrollElements = document.querySelectorAll('.scroll-container, .table-responsive');
        scrollElements.forEach(function(el) {
            el.style.webkitOverflowScrolling = 'touch';
        });
    }

    // Prefetch likely next pages (user-driven only, no background fetch)
    function setupPrefetch() {
        var links = document.querySelectorAll('a[data-prefetch]');
        links.forEach(function(link) {
            link.addEventListener('mouseenter', function() {
                var href = link.getAttribute('href');
                if (href && href.startsWith('/')) {
                    var prefetchLink = document.createElement('link');
                    prefetchLink.rel = 'prefetch';
                    prefetchLink.href = href;
                    document.head.appendChild(prefetchLink);
                }
            });
        });
    }

    // Handle window resize with debounce
    var handleResize = debounce(function() {
        optimizeScrollPerformance();
    }, 150);

    window.addEventListener('resize', handleResize);

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            setupLazyLoading();
            optimizeScrollPerformance();
            setupPrefetch();
        });
    } else {
        setupLazyLoading();
        optimizeScrollPerformance();
        setupPrefetch();
    }

    trackPerformanceMetrics();

    // Expose utilities
    window.CapulsePerformance = {
        debounce: debounce,
        optimizeScrollPerformance: optimizeScrollPerformance,
        setupLazyLoading: setupLazyLoading
    };

})();
