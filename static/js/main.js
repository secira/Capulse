/**
 * Target Capital - Main JavaScript File
 * Handles client-side interactions and enhancements
 */

/* ── Sidebar submenu toggles ───────────────────────────────────────────────
 * Defined here (outside any IIFE / DOMContentLoaded) so they are always
 * accessible from inline onclick attributes in sidebar.html, regardless of
 * which page or base template is in use.
 */
(function() {
    function _sidebarToggle(menuId, iconId) {
        var menu = document.getElementById(menuId);
        var icon = document.getElementById(iconId);
        if (!menu) return;
        var isOpen = menu.style.display !== 'none';
        menu.style.display = isOpen ? 'none' : 'block';
        if (icon) icon.style.transform = isOpen ? '' : 'rotate(180deg)';
    }
    window.toggleFnoMenu       = function() { _sidebarToggle('fnoMenu',          'fnoMenuToggle'); };
    window.toggleBehaviourMenu = function() { _sidebarToggle('behaviourMenu',    'behaviourToggle'); };
    window.togglePortfolioHub  = function() { _sidebarToggle('portfolioHubMenu', 'portfolioHubToggle'); };
    window.toggleAdminMenu     = function() { _sidebarToggle('adminMenu',        'adminMenuToggle'); };
    window.toggleSignalPanel   = function() {
        var panel = document.getElementById('signalPanel');
        if (!panel) return;
        panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    };
    window.markSignalsRead = function() {
        var panel = document.getElementById('signalPanel');
        if (panel) panel.style.display = 'none';
    };
}());
/* ── End sidebar toggles ─────────────────────────────────────────────────── */

(function() {
    'use strict';

    // DOM Ready Handler
    document.addEventListener('DOMContentLoaded', function() {
        initializeApp();
    });

    /**
     * Initialize all app functionality
     */
    function initializeApp() {
        initSmoothScrolling();
        initNavigation();
        initFormValidation();
        initAnimations();
        initTooltips();
        initModals();
        initCounters();
        initFlashMessages();
        initAccordions();
        initNewsletterForm();
        initContactForms();
    }

    /**
     * Smooth scrolling for anchor links
     */
    function initSmoothScrolling() {
        const links = document.querySelectorAll('a[href^="#"]');
        
        links.forEach(link => {
            link.addEventListener('click', function(e) {
                const href = this.getAttribute('href');
                
                // Skip empty anchors or just #
                if (href === '#' || href === '') {
                    e.preventDefault();
                    return;
                }

                const target = document.querySelector(href);
                if (target) {
                    e.preventDefault();
                    
                    const navEl = document.querySelector('.navbar');
                    const navHeight = navEl ? navEl.offsetHeight : 0;
                    const targetPosition = target.offsetTop - navHeight - 20;
                    
                    window.scrollTo({
                        top: targetPosition,
                        behavior: 'smooth'
                    });
                }
            });
        });
    }

    /**
     * Navigation enhancements
     */
    function initNavigation() {
        const navbar = document.querySelector('.navbar');
        const navbarCollapse = document.querySelector('.navbar-collapse');
        const navbarToggler = document.querySelector('.navbar-toggler');
        
        // Add scroll effect to navbar (only if navbar exists)
        if (navbar) {
            let lastScroll = 0;
            window.addEventListener('scroll', function() {
                const currentScroll = window.pageYOffset;
                if (currentScroll > 100) {
                    navbar.classList.add('scrolled');
                } else {
                    navbar.classList.remove('scrolled');
                }
                lastScroll = currentScroll;
            });
        }

        // Close mobile menu when clicking on links
        const navLinks = document.querySelectorAll('.navbar-nav .nav-link');
        navLinks.forEach(link => {
            link.addEventListener('click', function() {
                if (navbarCollapse && navbarCollapse.classList.contains('show') && navbarToggler) {
                    navbarToggler.click();
                }
            });
        });

        // Active nav item highlighting
        highlightActiveNavItem();
    }

    /**
     * Highlight active navigation item based on current page
     */
    function highlightActiveNavItem() {
        const currentPath = window.location.pathname;
        const navLinks = document.querySelectorAll('.navbar-nav .nav-link');
        
        navLinks.forEach(link => {
            const href = link.getAttribute('href');
            if (href === currentPath || (currentPath === '/' && href === '/')) {
                link.classList.add('active');
            }
        });
    }

    /**
     * Form validation and enhancement
     */
    function initFormValidation() {
        const forms = document.querySelectorAll('form');
        
        forms.forEach(form => {
            form.addEventListener('submit', function(e) {
                if (!validateForm(this)) {
                    e.preventDefault();
                    return false;
                }
                
                // Add loading state to submit button
                const submitBtn = this.querySelector('button[type="submit"]');
                if (submitBtn) {
                    const originalText = submitBtn.innerHTML;
                    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Processing...';
                    submitBtn.disabled = true;
                    
                    // Reset button after 3 seconds if form hasn't redirected
                    setTimeout(() => {
                        submitBtn.innerHTML = originalText;
                        submitBtn.disabled = false;
                    }, 3000);
                }
            });
        });
    }

    /**
     * Validate form fields
     * @param {HTMLFormElement} form 
     * @returns {boolean}
     */
    function validateForm(form) {
        let isValid = true;
        const requiredFields = form.querySelectorAll('[required]');
        
        // Clear previous error states
        form.querySelectorAll('.is-invalid').forEach(field => {
            field.classList.remove('is-invalid');
        });
        
        requiredFields.forEach(field => {
            if (!field.value.trim()) {
                field.classList.add('is-invalid');
                isValid = false;
            } else if (field.type === 'email' && !isValidEmail(field.value)) {
                field.classList.add('is-invalid');
                isValid = false;
            }
        });
        
        return isValid;
    }

    /**
     * Email validation
     * @param {string} email 
     * @returns {boolean}
     */
    function isValidEmail(email) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        return emailRegex.test(email);
    }

    /**
     * Initialize scroll animations
     */
    function initAnimations() {
        const observerOptions = {
            threshold: 0.1,
            rootMargin: '0px 0px -50px 0px'
        };

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('fade-in-up');
                    observer.unobserve(entry.target);
                }
            });
        }, observerOptions);

        // Observe elements for animation
        const animateElements = document.querySelectorAll('.card, .feature-item, .service-card, .step-card');
        animateElements.forEach(el => {
            observer.observe(el);
        });
    }

    /**
     * Initialize Bootstrap tooltips
     */
    function initTooltips() {
        if (typeof bootstrap !== 'undefined') {
            const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
            tooltipTriggerList.map(function (tooltipTriggerEl) {
                return new bootstrap.Tooltip(tooltipTriggerEl);
            });
        }
    }

    /**
     * Initialize modal functionality
     */
    function initModals() {
        // Handle demo modal triggers
        const demoButtons = document.querySelectorAll('a[href="#demo"]');
        demoButtons.forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                showDemoModal();
            });
        });

        // Handle trial modal triggers
        const trialButtons = document.querySelectorAll('a[href="#trial"]');
        trialButtons.forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                showTrialModal();
            });
        });
    }

    /**
     * Show demo booking modal
     */
    function showDemoModal() {
        const modalHtml = `
            <div class="modal fade" id="demoModal" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Book a Demo</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <form action="/contact" method="POST">
                                <div class="mb-3">
                                    <label for="demoName" class="form-label">Full Name *</label>
                                    <input type="text" class="form-control" id="demoName" name="name" required>
                                </div>
                                <div class="mb-3">
                                    <label for="demoEmail" class="form-label">Email Address *</label>
                                    <input type="email" class="form-control" id="demoEmail" name="email" required>
                                </div>
                                <div class="mb-3">
                                    <label for="demoCompany" class="form-label">Company</label>
                                    <input type="text" class="form-control" id="demoCompany" name="company">
                                </div>
                                <div class="mb-3">
                                    <label for="demoMessage" class="form-label">Message</label>
                                    <textarea class="form-control" id="demoMessage" name="message" rows="3" placeholder="Tell us about your trading goals and what you'd like to see in the demo..."></textarea>
                                </div>
                                <input type="hidden" name="subject" value="Demo Request">
                            </form>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="submit" class="btn btn-primary" form="demoForm">Book Demo</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        const modal = new bootstrap.Modal(document.getElementById('demoModal'));
        modal.show();
        
        // Clean up modal after it's hidden
        document.getElementById('demoModal').addEventListener('hidden.bs.modal', function() {
            this.remove();
        });
    }

    /**
     * Show trial signup modal
     */
    function showTrialModal() {
        const modalHtml = `
            <div class="modal fade" id="trialModal" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Start Your Free Trial</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="text-center mb-4">
                                <i class="fas fa-rocket fa-3x text-primary mb-3"></i>
                                <h6>30-Day Free Trial</h6>
                                <p class="text-muted">No credit card required</p>
                            </div>
                            <form action="/contact" method="POST">
                                <div class="mb-3">
                                    <label for="trialName" class="form-label">Full Name *</label>
                                    <input type="text" class="form-control" id="trialName" name="name" required>
                                </div>
                                <div class="mb-3">
                                    <label for="trialEmail" class="form-label">Email Address *</label>
                                    <input type="email" class="form-control" id="trialEmail" name="email" required>
                                </div>
                                <div class="mb-3">
                                    <label for="trialExperience" class="form-label">Trading Experience</label>
                                    <select class="form-control" id="trialExperience" name="experience">
                                        <option value="">Select your experience level</option>
                                        <option value="beginner">Beginner (0-1 years)</option>
                                        <option value="intermediate">Intermediate (1-5 years)</option>
                                        <option value="advanced">Advanced (5+ years)</option>
                                        <option value="professional">Professional Trader</option>
                                    </select>
                                </div>
                                <input type="hidden" name="subject" value="Free Trial Request">
                                <input type="hidden" name="message" value="Requesting access to 30-day free trial">
                            </form>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="submit" class="btn btn-primary" form="trialForm">Start Free Trial</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        const modal = new bootstrap.Modal(document.getElementById('trialModal'));
        modal.show();
        
        // Clean up modal after it's hidden
        document.getElementById('trialModal').addEventListener('hidden.bs.modal', function() {
            this.remove();
        });
    }

    /**
     * Initialize counter animations
     */
    function initCounters() {
        const counters = document.querySelectorAll('.display-4');
        const counterObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    animateCounter(entry.target);
                    counterObserver.unobserve(entry.target);
                }
            });
        }, { threshold: 0.5 });

        counters.forEach(counter => {
            if (counter.textContent.match(/\d/)) {
                counterObserver.observe(counter);
            }
        });
    }

    /**
     * Animate counter numbers
     * @param {HTMLElement} element 
     */
    function animateCounter(element) {
        const text = element.textContent;
        const number = text.match(/[\d.]+/);
        
        if (number) {
            const finalValue = parseFloat(number[0]);
            const suffix = text.replace(number[0], '');
            const prefix = text.split(number[0])[0];
            
            let current = 0;
            const increment = finalValue / 100;
            const timer = setInterval(() => {
                current += increment;
                if (current >= finalValue) {
                    current = finalValue;
                    clearInterval(timer);
                }
                
                let displayValue = current;
                if (finalValue >= 1000) {
                    displayValue = current.toFixed(1);
                } else {
                    displayValue = Math.floor(current);
                }
                
                element.textContent = prefix + displayValue + suffix;
            }, 20);
        }
    }

    /**
     * Handle flash messages
     */
    function initFlashMessages() {
        const flashMessages = document.querySelectorAll('.flash-messages .alert');
        
        flashMessages.forEach(message => {
            // Auto-dismiss after 5 seconds
            setTimeout(() => {
                if (message && message.parentNode) {
                    const alert = new bootstrap.Alert(message);
                    alert.close();
                }
            }, 5000);
        });
    }

    /**
     * Initialize accordion functionality
     */
    function initAccordions() {
        const accordionButtons = document.querySelectorAll('.accordion-button');
        
        accordionButtons.forEach(button => {
            button.addEventListener('click', function() {
                const icon = this.querySelector('i');
                if (icon) {
                    // Toggle icon rotation
                    setTimeout(() => {
                        if (this.classList.contains('collapsed')) {
                            icon.style.transform = 'rotate(0deg)';
                        } else {
                            icon.style.transform = 'rotate(180deg)';
                        }
                    }, 150);
                }
            });
        });
    }

    /**
     * Newsletter form enhancements
     */
    function initNewsletterForm() {
        const newsletterForms = document.querySelectorAll('form[action*="newsletter"]');
        
        newsletterForms.forEach(form => {
            form.addEventListener('submit', function(e) {
                const email = this.querySelector('input[type="email"]').value;
                
                if (!isValidEmail(email)) {
                    e.preventDefault();
                    showNotification('Please enter a valid email address.', 'error');
                    return;
                }
                
                // Add visual feedback
                const submitBtn = this.querySelector('button[type="submit"]');
                if (submitBtn) {
                    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
                    submitBtn.disabled = true;
                }
            });
        });
    }

    /**
     * Contact form enhancements
     */
    function initContactForms() {
        const contactForms = document.querySelectorAll('form[action*="contact"]');
        
        contactForms.forEach(form => {
            const submitBtn = form.querySelector('button[type="submit"]');
            
            form.addEventListener('submit', function(e) {
                if (!validateForm(this)) {
                    e.preventDefault();
                    showNotification('Please fill in all required fields correctly.', 'error');
                    return;
                }
                
                if (submitBtn) {
                    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Sending...';
                    submitBtn.disabled = true;
                }
            });
        });
    }

    /**
     * Show notification message
     * @param {string} message 
     * @param {string} type 
     */
    function showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `alert alert-${type === 'error' ? 'danger' : type} alert-dismissible fade show`;
        notification.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        // Add to flash messages container or create one
        let container = document.querySelector('.flash-messages');
        if (!container) {
            container = document.createElement('div');
            container.className = 'flash-messages';
            document.body.appendChild(container);
        }
        
        container.appendChild(notification);
        
        // Auto-dismiss after 5 seconds
        setTimeout(() => {
            if (notification.parentNode) {
                const alert = new bootstrap.Alert(notification);
                alert.close();
            }
        }, 5000);
    }

    /**
     * Copy to clipboard functionality
     */
    window.copyToClipboard = function(text) {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(text).then(() => {
                showNotification('Link copied to clipboard!', 'success');
            });
        } else {
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = text;
            document.body.appendChild(textArea);
            textArea.select();
            document.execCommand('copy');
            document.body.removeChild(textArea);
            showNotification('Link copied to clipboard!', 'success');
        }
    };

    /**
     * Social share functionality
     */
    window.shareArticle = function(platform, url, title) {
        const encodedUrl = encodeURIComponent(url);
        const encodedTitle = encodeURIComponent(title);
        let shareUrl = '';
        
        switch (platform) {
            case 'twitter':
                shareUrl = `https://twitter.com/intent/tweet?url=${encodedUrl}&text=${encodedTitle}`;
                break;
            case 'linkedin':
                shareUrl = `https://www.linkedin.com/sharing/share-offsite/?url=${encodedUrl}`;
                break;
            case 'facebook':
                shareUrl = `https://www.facebook.com/sharer/sharer.php?u=${encodedUrl}`;
                break;
        }
        
        if (shareUrl) {
            window.open(shareUrl, '_blank', 'width=600,height=400');
        }
    };

    window.addEventListener('error', function(e) {
        console.error('targetcapital.ai says: JavaScript error:', e.filename, 'line:', e.lineno, 'col:', e.colno, e.message);
    });

    // Add CSS for animations
    const style = document.createElement('style');
    style.textContent = `
        .navbar.scrolled {
            background: rgba(0, 9, 26, 0.95) !important;
            backdrop-filter: blur(10px);
        }
        
        .fade-in-up {
            animation: fadeInUp 0.6s ease-out forwards;
        }
        
        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(30px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .accordion-button i {
            transition: transform 0.3s ease;
        }
        
        .form-control.is-invalid {
            border-color: #dc3545;
            box-shadow: 0 0 0 0.25rem rgba(220, 53, 69, 0.25);
        }
        
        .notification-enter {
            animation: slideInRight 0.3s ease-out;
        }
        
        @keyframes slideInRight {
            from {
                transform: translateX(100%);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
    `;
    document.head.appendChild(style);

})();
