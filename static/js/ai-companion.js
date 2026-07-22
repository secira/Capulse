/**
 * Capulse AI Companion Mascot
 * Playful financial learning assistant with personality
 */
(function() {
'use strict';

class AICompanion {
    constructor() {
        this.name = 'Finley';
        this.currentMood = 'happy';
        this.isVisible = false;
        this.position = { x: 0, y: 0 };
        this.animations = {
            idle: ['bounce', 'blink', 'nod'],
            talking: ['mouth-move', 'gesture'],
            excited: ['jump', 'sparkle'],
            thinking: ['scratch-head', 'ponder']
        };
        this.learningTips = this.initializeLearningTips();
        this.onboardingSteps = this.initializeOnboarding();
        this.currentStep = 0;
        this.init();
    }

    init() {
        this.createMascotElement();
        this.bindEvents();
        this.checkFirstVisit();
    }

    createMascotElement() {
        const mascotHTML = `
            <div id="ai-companion" class="ai-companion">
                <div class="companion-body">
                    <div class="companion-head">
                        <div class="companion-eyes">
                            <div class="eye left-eye">
                                <div class="pupil"></div>
                            </div>
                            <div class="eye right-eye">
                                <div class="pupil"></div>
                            </div>
                        </div>
                        <div class="companion-mouth"></div>
                    </div>
                    <div class="companion-torso">
                        <div class="companion-logo">₹</div>
                    </div>
                    <div class="companion-arms">
                        <div class="arm left-arm"></div>
                        <div class="arm right-arm"></div>
                    </div>
                </div>
                <div class="companion-speech-bubble" id="speech-bubble">
                    <div class="speech-content">
                        <span class="speech-text"></span>
                        <div class="speech-controls">
                            <button class="btn-skip">Skip</button>
                            <button class="btn-next">Next</button>
                        </div>
                    </div>
                </div>
                <div class="companion-controls">
                    <button class="companion-btn help-btn" title="Get Help">
                        <i class="fas fa-question"></i>
                    </button>
                    <button class="companion-btn tutorial-btn" title="Start Tutorial">
                        <i class="fas fa-graduation-cap"></i>
                    </button>
                    <button class="companion-btn tips-btn" title="Learning Tips">
                        <i class="fas fa-lightbulb"></i>
                    </button>
                    <button class="companion-btn close-btn" title="Hide Companion">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', mascotHTML);
        this.mascotElement = document.getElementById('ai-companion');
        this.speechBubble = document.getElementById('speech-bubble');
    }

    bindEvents() {
        // Control buttons
        document.querySelector('.help-btn').addEventListener('click', () => this.showHelp());
        document.querySelector('.tutorial-btn').addEventListener('click', () => this.startTutorial());
        document.querySelector('.tips-btn').addEventListener('click', () => this.showRandomTip());
        document.querySelector('.close-btn').addEventListener('click', () => this.hide());

        // Speech bubble controls
        document.querySelector('.btn-skip').addEventListener('click', () => this.skipOnboarding());
        document.querySelector('.btn-next').addEventListener('click', () => this.nextStep());

        // Mouse tracking for eye movement
        document.addEventListener('mousemove', (e) => this.trackMouse(e));

        // Page interaction hints
        document.addEventListener('click', (e) => this.handlePageInteraction(e));
    }

    initializeLearningTips() {
        return [
            {
                category: 'investing',
                tip: "🎯 Diversification is key! Don't put all your eggs in one basket. Spread investments across different sectors.",
                animation: 'excited'
            },
            {
                category: 'trading',
                tip: "📊 Always set stop-losses! They're like safety nets that protect your capital from big losses.",
                animation: 'thinking'
            },
            {
                category: 'portfolio',
                tip: "🔄 Regular rebalancing keeps your portfolio healthy. Review and adjust every 3-6 months!",
                animation: 'talking'
            },
            {
                category: 'ai',
                tip: "🤖 Our AI analyzes 245+ stocks in real-time! It never sleeps, so your investments are always monitored.",
                animation: 'excited'
            },
            {
                category: 'risk',
                tip: "⚖️ Risk tolerance varies by age and goals. Young investors can usually take more risks for higher returns.",
                animation: 'thinking'
            },
            {
                category: 'brokers',
                tip: "🔗 Connect multiple brokers for a complete portfolio view. We support 12 major Indian brokers!",
                animation: 'talking'
            }
        ];
    }

    initializeOnboarding() {
        return [
            {
                target: '.sidebar',
                title: "Welcome to Capulse! 👋",
                message: "Hi! I'm Finley, your AI financial companion. Let me show you around this amazing trading platform!",
                position: 'right',
                animation: 'excited'
            },
            {
                target: '[href="/dashboard"]',
                title: "Your Dashboard 📊",
                message: "This is your command center! Here you'll see portfolio overview, market insights, and AI recommendations.",
                position: 'right',
                animation: 'talking'
            },
            {
                target: '[href="/dashboard/ai-advisor"]',
                title: "Meet Your AI Advisor 🤖",
                message: "Your personal AI analyst! Ask me anything about stocks, markets, or investment strategies.",
                position: 'right',
                animation: 'excited'
            },
            {
                target: '[href="/dashboard/stock-picker"]',
                title: "AI Stock Picker 🎯",
                message: "Let AI find the best stocks for you! Get personalized recommendations based on market analysis.",
                position: 'right',
                animation: 'thinking'
            },
            {
                target: '.sidebar-section[data-section="broker"]',
                title: "Broker Management 🔗",
                message: "Connect your broker accounts here. We support 12 major Indian brokers for complete portfolio tracking!",
                position: 'right',
                animation: 'talking'
            },
            {
                target: '.ai-pulse-indicator',
                title: "AI Status Monitor 💚",
                message: "This green dot means our AI is actively monitoring markets and your portfolio 24/7!",
                position: 'left',
                animation: 'excited'
            },
            {
                target: '.stock-counter',
                title: "Live Market Coverage 📈",
                message: "We're tracking hundreds of stocks in real-time to find the best opportunities for you!",
                position: 'left',
                animation: 'talking'
            }
        ];
    }

    checkFirstVisit() {
        // Only show if user specifically requests help - don't auto-trigger
        const hasVisited = localStorage.getItem('tcapital_onboarding_completed');
        const userRequestedHelp = sessionStorage.getItem('tcapital_request_help');
        
        if (!hasVisited && userRequestedHelp === 'true') {
            setTimeout(() => {
                this.show();
                this.startOnboarding();
                sessionStorage.removeItem('tcapital_request_help');
            }, 1000);
        } else {
            // Hide completely by default - don't annoy users with popups
            this.hide();
        }
    }

    show() {
        this.isVisible = true;
        this.mascotElement.classList.add('visible');
        this.setMood('happy');
        this.playAnimation('idle');
    }

    hide() {
        this.isVisible = false;
        this.mascotElement.classList.remove('visible');
        this.hideSpeechBubble();
    }

    setMood(mood) {
        this.currentMood = mood;
        this.mascotElement.className = `ai-companion ${mood}`;
    }

    playAnimation(type) {
        const animations = this.animations[type] || this.animations.idle;
        const animation = animations[Math.floor(Math.random() * animations.length)];
        
        this.mascotElement.classList.add(`anim-${animation}`);
        setTimeout(() => {
            this.mascotElement.classList.remove(`anim-${animation}`);
        }, 1000);
    }

    speak(message, options = {}) {
        const speechText = this.speechBubble.querySelector('.speech-text');
        speechText.textContent = message;
        
        this.speechBubble.classList.add('visible');
        this.setMood(options.mood || 'talking');
        this.playAnimation(options.animation || 'talking');

        // Auto-hide after delay if not onboarding
        if (!options.persistent) {
            setTimeout(() => {
                this.hideSpeechBubble();
            }, 5000);
        }
    }

    hideSpeechBubble() {
        this.speechBubble.classList.remove('visible');
        this.setMood('happy');
    }

    startOnboarding() {
        this.currentStep = 0;
        this.showOnboardingStep();
    }

    showOnboardingStep() {
        if (this.currentStep >= this.onboardingSteps.length) {
            this.completeOnboarding();
            return;
        }

        const step = this.onboardingSteps[this.currentStep];
        const target = document.querySelector(step.target);

        if (target) {
            // Highlight target element
            this.highlightElement(target);
            
            // Position mascot near target
            this.positionNearElement(target, step.position);
            
            // Speak the message
            this.speak(step.message, {
                mood: 'excited',
                animation: step.animation,
                persistent: true
            });

            // Update speech bubble with step info
            const speechText = this.speechBubble.querySelector('.speech-text');
            speechText.innerHTML = `
                <strong>${step.title}</strong><br>
                ${step.message}
                <div class="step-progress">${this.currentStep + 1} of ${this.onboardingSteps.length}</div>
            `;
        } else {
            // Skip this step if target not found
            this.nextStep();
        }
    }

    nextStep() {
        this.clearHighlights();
        this.currentStep++;
        this.showOnboardingStep();
    }

    skipOnboarding() {
        this.clearHighlights();
        this.completeOnboarding();
    }

    completeOnboarding() {
        localStorage.setItem('tcapital_onboarding_completed', 'true');
        
        // Trigger completion event for progress tracking
        document.dispatchEvent(new CustomEvent('tcapital:onboarding:completed'));
        
        this.speak("🎉 Great job! You're all set to start your trading journey. I'll be here whenever you need help!", {
            mood: 'excited',
            animation: 'excited'
        });
        
        setTimeout(() => {
            this.hideSpeechBubble();
            this.mascotElement.style.transform = 'translateY(80px)';
            this.mascotElement.style.opacity = '0.7';
        }, 3000);
    }

    celebrateAchievement(milestone) {
        const celebrationMessages = {
            'first_visit': "🎉 Welcome to Capulse! You're about to discover amazing trading tools!",
            'onboarding_completed': "🏆 Fantastic! You've mastered the basics. Ready to start trading?",
            'dashboard_explored': "📊 You're getting the hang of this! The dashboard is your mission control.",
            'ai_advisor_used': "🤖 Great question! I love helping with financial insights.",
            'stock_picker_used': "🎯 Smart move! Let AI find the best investment opportunities for you.",
            'broker_connected': "🔗 Excellent! Your portfolio data will now sync automatically.",
            'learning_streak_3': "🔥 You're on fire! Three days of consistent learning!",
            'learning_streak_7': "🏆 Incredible dedication! You're becoming a trading pro!"
        };

        const message = celebrationMessages[milestone.name.toLowerCase().replace(' ', '_')] || 
                       `🎉 Amazing! You've achieved ${milestone.name}!`;

        setTimeout(() => {
            this.show();
            this.speak(message, {
                mood: 'excited',
                animation: 'excited'
            });
        }, 1000);
    }

    highlightElement(element) {
        // Remove existing highlights
        this.clearHighlights();
        
        // Add highlight overlay
        const overlay = document.createElement('div');
        overlay.className = 'onboarding-overlay';
        document.body.appendChild(overlay);

        // Add spotlight effect
        const spotlight = document.createElement('div');
        spotlight.className = 'onboarding-spotlight';
        
        const rect = element.getBoundingClientRect();
        spotlight.style.left = (rect.left - 10) + 'px';
        spotlight.style.top = (rect.top - 10) + 'px';
        spotlight.style.width = (rect.width + 20) + 'px';
        spotlight.style.height = (rect.height + 20) + 'px';
        
        document.body.appendChild(spotlight);
        
        // Pulse animation for target
        element.classList.add('onboarding-highlight');
    }

    clearHighlights() {
        document.querySelectorAll('.onboarding-overlay, .onboarding-spotlight').forEach(el => el.remove());
        document.querySelectorAll('.onboarding-highlight').forEach(el => {
            el.classList.remove('onboarding-highlight');
        });
    }

    positionNearElement(element, position = 'right') {
        const rect = element.getBoundingClientRect();
        const mascot = this.mascotElement;
        
        let left, top;
        
        switch (position) {
            case 'right':
                left = rect.right + 20;
                top = rect.top + (rect.height / 2) - 100;
                break;
            case 'left':
                left = rect.left - 200;
                top = rect.top + (rect.height / 2) - 100;
                break;
            case 'bottom':
                left = rect.left + (rect.width / 2) - 100;
                top = rect.bottom + 20;
                break;
            default:
                left = rect.right + 20;
                top = rect.top + (rect.height / 2) - 100;
        }

        // Ensure mascot stays within viewport
        left = Math.max(20, Math.min(left, window.innerWidth - 220));
        top = Math.max(20, Math.min(top, window.innerHeight - 200));

        mascot.style.left = left + 'px';
        mascot.style.top = top + 'px';
        mascot.style.transform = 'translateY(0)';
        mascot.style.opacity = '1';
    }

    trackMouse(e) {
        if (!this.isVisible) return;

        const eyes = document.querySelectorAll('.pupil');
        const mascotRect = this.mascotElement.getBoundingClientRect();
        const mascotCenterX = mascotRect.left + mascotRect.width / 2;
        const mascotCenterY = mascotRect.top + mascotRect.height / 2;

        eyes.forEach(pupil => {
            const eyeRect = pupil.parentElement.getBoundingClientRect();
            const eyeCenterX = eyeRect.left + eyeRect.width / 2;
            const eyeCenterY = eyeRect.top + eyeRect.height / 2;

            const angle = Math.atan2(e.clientY - eyeCenterY, e.clientX - eyeCenterX);
            const distance = Math.min(3, Math.sqrt(Math.pow(e.clientX - eyeCenterX, 2) + Math.pow(e.clientY - eyeCenterY, 2)) / 10);

            const x = Math.cos(angle) * distance;
            const y = Math.sin(angle) * distance;

            pupil.style.transform = `translate(${x}px, ${y}px)`;
        });
    }

    handlePageInteraction(e) {
        if (!this.isVisible) return;

        // Provide contextual tips based on what user clicked
        const target = e.target.closest('[href], button, .card');
        if (target) {
            this.provideContextualTip(target);
        }
    }

    provideContextualTip(element) {
        const tips = {
            'ai-advisor': "💡 Pro tip: Be specific with your questions to get better AI insights!",
            'stock-picker': "🎯 The AI considers your risk profile when suggesting stocks!",
            'trading-signals': "📈 Our signals use advanced algorithms to spot opportunities!",
            'portfolio': "📊 Check your portfolio health score regularly for optimization tips!"
        };

        const href = element.getAttribute('href') || '';
        const tipKey = Object.keys(tips).find(key => href.includes(key));
        
        if (tipKey) {
            setTimeout(() => {
                this.speak(tips[tipKey], { animation: 'thinking' });
            }, 500);
        }
    }

    showHelp() {
        const helpMessages = [
            "🤔 Need help? I'm here to guide you through any feature!",
            "💭 Ask me about stocks, trading strategies, or how to use the platform!",
            "🎓 Want a refresher? I can restart the tutorial anytime!"
        ];
        
        const message = helpMessages[Math.floor(Math.random() * helpMessages.length)];
        this.speak(message, { animation: 'talking' });
    }

    startTutorial() {
        this.currentStep = 0;
        this.speak("🎓 Let's take a tour! I'll show you all the amazing features.", { animation: 'excited' });
        setTimeout(() => this.startOnboarding(), 1000);
    }

    showRandomTip() {
        const tip = this.learningTips[Math.floor(Math.random() * this.learningTips.length)];
        this.speak(tip.tip, { animation: tip.animation });
    }
}

document.addEventListener('DOMContentLoaded', function() {
    window.aiCompanion = new AICompanion();
});

})();