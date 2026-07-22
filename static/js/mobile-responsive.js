// Mobile Responsive Enhancements for Capulse
document.addEventListener('DOMContentLoaded', function() {
    
    // Mobile navigation enhancements
    const navbarToggler = document.querySelector('.navbar-toggler');
    const navbarCollapse = document.querySelector('.navbar-collapse');
    
    // Close mobile menu when clicking on a link
    if (navbarCollapse) {
        const navLinks = navbarCollapse.querySelectorAll('.nav-link');
        navLinks.forEach(link => {
            link.addEventListener('click', () => {
                const bsCollapse = new bootstrap.Collapse(navbarCollapse, {
                    hide: true
                });
            });
        });
    }
    
    // Touch-friendly table scrolling
    const tables = document.querySelectorAll('.table-responsive');
    tables.forEach(table => {
        // Add scroll indicators
        const scrollIndicator = document.createElement('div');
        scrollIndicator.className = 'table-scroll-indicator d-md-none';
        scrollIndicator.innerHTML = '<small class="text-muted"><i class="fas fa-arrow-left"></i> Swipe to scroll <i class="fas fa-arrow-right"></i></small>';
        scrollIndicator.style.textAlign = 'center';
        scrollIndicator.style.padding = '0.5rem';
        scrollIndicator.style.backgroundColor = '#f8f9fa';
        scrollIndicator.style.borderRadius = '0 0 8px 8px';
        
        table.parentNode.insertBefore(scrollIndicator, table.nextSibling);
        
        // Hide indicator after first scroll
        table.addEventListener('scroll', function() {
            scrollIndicator.style.display = 'none';
        }, { once: true });
    });
    
    // Improve form interactions on mobile
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        const inputs = form.querySelectorAll('input, textarea, select');
        inputs.forEach(input => {
            // Auto-scroll to input when focused on mobile
            input.addEventListener('focus', function() {
                if (window.innerWidth <= 768) {
                    setTimeout(() => {
                        this.scrollIntoView({
                            behavior: 'smooth',
                            block: 'center'
                        });
                    }, 300);
                }
            });
        });
    });
    
    // Dashboard mobile menu handling
    const dashboardSidebar = document.getElementById('mobileDashboardSidebar');
    if (dashboardSidebar) {
        // Close sidebar when clicking outside
        dashboardSidebar.addEventListener('click', function(e) {
            if (e.target === this) {
                bootstrap.Offcanvas.getInstance(this).hide();
            }
        });
    }
    
    // Optimize images for mobile
    function optimizeImagesForMobile() {
        if (window.innerWidth <= 768) {
            const images = document.querySelectorAll('img:not(.optimized-mobile)');
            images.forEach(img => {
                img.classList.add('optimized-mobile');
                // Add loading="lazy" for better performance
                if (!img.hasAttribute('loading')) {
                    img.setAttribute('loading', 'lazy');
                }
            });
        }
    }
    
    optimizeImagesForMobile();
    window.addEventListener('resize', optimizeImagesForMobile);
    
    // Smooth scrolling for anchor links
    const anchorLinks = document.querySelectorAll('a[href^="#"]');
    anchorLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            // Skip invalid or empty href attributes
            if (!href || href === '#' || href.length <= 1) {
                return;
            }
            e.preventDefault();
            const target = document.querySelector(href);
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
    
    // Handle viewport height changes (iOS Safari)
    function handleViewportChange() {
        const vh = window.innerHeight * 0.01;
        document.documentElement.style.setProperty('--vh', `${vh}px`);
    }
    
    handleViewportChange();
    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('orientationchange', () => {
        setTimeout(handleViewportChange, 100);
    });
    
    // Swipe gestures for dashboard navigation
    if (window.innerWidth <= 768) {
        let startX = 0;
        let startY = 0;
        let isSwipeAction = false;
        
        document.addEventListener('touchstart', function(e) {
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            isSwipeAction = false;
        });
        
        document.addEventListener('touchmove', function(e) {
            if (!startX || !startY) return;
            
            const currentX = e.touches[0].clientX;
            const currentY = e.touches[0].clientY;
            
            const diffX = startX - currentX;
            const diffY = startY - currentY;
            
            // Check if it's a horizontal swipe (not vertical scroll)
            if (Math.abs(diffX) > Math.abs(diffY) && Math.abs(diffX) > 50) {
                isSwipeAction = true;
            }
        });
        
        document.addEventListener('touchend', function(e) {
            if (!isSwipeAction) return;
            
            const currentX = e.changedTouches[0].clientX;
            const diffX = startX - currentX;
            
            // Swipe right to open sidebar (when on dashboard)
            if (diffX < -100 && document.querySelector('.dashboard-main-content')) {
                const sidebarToggle = document.querySelector('[data-bs-target="#mobileDashboardSidebar"]');
                if (sidebarToggle) {
                    sidebarToggle.click();
                }
            }
            
            startX = 0;
            startY = 0;
            isSwipeAction = false;
        });
    }
    
    // Performance monitoring for mobile
    if ('performance' in window) {
        window.addEventListener('load', function() {
            setTimeout(function() {
                const loadTime = performance.timing.loadEventEnd - performance.timing.navigationStart;
                if (loadTime > 3000) {
                    console.log('capulse.tech says: Page load time:', loadTime + 'ms - Consider optimizing for mobile');
                }
            }, 0);
        });
    }
});

// CSS Custom Properties for dynamic viewport will be set via JavaScript

// Utility functions for responsive behavior
window.CapulseMobile = {
    isMobile: () => window.innerWidth <= 768,
    isTablet: () => window.innerWidth > 768 && window.innerWidth <= 1024,
    isTouch: () => 'ontouchstart' in window || navigator.maxTouchPoints > 0,
    
    // Utility to check if element is in viewport
    isInViewport: (element) => {
        const rect = element.getBoundingClientRect();
        return (
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
            rect.right <= (window.innerWidth || document.documentElement.clientWidth)
        );
    },
    
    // Smooth scroll to element with offset for mobile
    scrollToElement: (element, offset = 80) => {
        const elementPosition = element.getBoundingClientRect().top + window.pageYOffset;
        const adjustedPosition = elementPosition - offset;
        
        window.scrollTo({
            top: adjustedPosition,
            behavior: 'smooth'
        });
    }
};