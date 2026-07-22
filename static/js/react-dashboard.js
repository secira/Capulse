/**
 * React-style Dashboard Components for Capulse
 * High-performance real-time dashboard with WebSocket integration
 */
(function() {
'use strict';

class DashboardApp {
    constructor() {
        this.state = {
            user: null,
            marketData: {},
            portfolio: {},
            tradingSignals: [],
            connectionStatus: {},
            notifications: [],
            isLoading: true
        };
        
        this.components = new Map();
        this.websockets = new Map();
        this.init();
    }
    
    async init() {
        try {
            // Initialize WebSocket connections
            await this.initializeWebSockets();
            
            // Load initial data
            await this.loadInitialData();
            
            // Mount components
            this.mountComponents();
            
            // Start real-time updates
            this.startRealTimeUpdates();
            
            this.setState({ isLoading: false });
            
        } catch (error) {
            console.error('Dashboard initialization error:', error);
            this.setState({ isLoading: false });
        }
    }
    
    initializeWebSockets() {
        console.log('🔌 WebSocket connections disabled - waiting for user interaction');
        // Initial state update
        this.updateConnectionStatus('market', 'disconnected');
        this.updateConnectionStatus('trading', 'disconnected');
    }
    
    async loadInitialData() {
        // Data loading disabled - all requests must be user-initiated
        console.log('📋 Initial data loading disabled - waiting for user interaction');
    }
    
    mountComponents() {
        // Mount real-time market component
        const marketElement = document.querySelector('[data-component="realtime-market"]');
        if (marketElement) {
            const marketComponent = new RealTimeMarketData(marketElement, {
                initialData: this.state.marketData
            });
            this.components.set('market', marketComponent);
        }
        
        // Mount portfolio component
        const portfolioElement = document.querySelector('[data-component="portfolio"]');
        if (portfolioElement) {
            const portfolioComponent = new Portfolio(portfolioElement, {
                initialData: this.state.portfolio
            });
            this.components.set('portfolio', portfolioComponent);
        }
        
        // Mount AI signals component
        const signalsElement = document.querySelector('[data-component="ai-signals"]');
        if (signalsElement) {
            const signalsComponent = new AITradingSignals(signalsElement, {
                initialData: this.state.tradingSignals
            });
            this.components.set('signals', signalsComponent);
        }
    }
    
    startRealTimeUpdates() {
        // Disabled auto-polling completely
        console.log('⏱️ Auto-polling disabled');
    }
    
    async refreshCriticalData() {
        // Only refresh if explicitly called by user action
        console.log('🔄 Data refresh requested');
    }

    async refreshMarketData() {
        // Only refresh if explicitly called by user action
        console.log('📊 Market data refresh requested');
    }
    
    isMarketOpen() {
        const now = new Date();
        const hour = now.getHours();
        const minute = now.getMinutes();
        const day = now.getDay();
        
        // Indian market hours: 9:15 AM to 3:30 PM, Monday to Friday
        const isWeekday = day >= 1 && day <= 5;
        const isMarketHours = (hour > 9 || (hour === 9 && minute >= 15)) && 
                             (hour < 15 || (hour === 15 && minute <= 30));
        
        return isWeekday && isMarketHours;
    }
    
    setState(newState) {
        this.state = { ...this.state, ...newState };
        this.notifyComponents();
    }
    
    notifyComponents() {
        // Notify all components of state changes
        for (const [name, component] of this.components) {
            if (component.onStateChange) {
                component.onStateChange(this.state);
            }
        }
    }
    
    updateConnectionStatus(service, status) {
        const connectionStatus = { ...this.state.connectionStatus };
        connectionStatus[service] = status;
        this.setState({ connectionStatus });
    }
    
    handleOrderUpdate(order) {
        // Update portfolio if order affects it
        if (order.status === 'filled') {
            this.refreshCriticalData();
        }
        
        // Show notification
        this.addNotification(
            `Order ${order.status}: ${order.symbol} ${order.side} ${order.quantity}`,
            order.status === 'filled' ? 'success' : 'info'
        );
    }
    
    handleTradingSignal(signal) {
        // Add new signal to the list
        const signals = [...this.state.tradingSignals];
        signals.unshift(signal);
        
        // Keep only latest 50 signals
        if (signals.length > 50) {
            signals.splice(50);
        }
        
        this.setState({ tradingSignals: signals });
        
        // Suppress initial connection signal if it contains specific text
        if (signal.message === 'Market Data Connected') return;
        
        // Send to non-intrusive signal manager instead of popup
        if (signal.confidence >= 80 && window.signalManager) {
            window.signalManager.handleNewSignal(signal);
        }
    }
    
    addNotification(message, type = 'info', duration = 5000) {
        // Suppress "Market Data Connected" and "Market data updated" notifications
        if (message.includes('Market Data Connected') || message.includes('Market data updated')) {
            return;
        }
        const notification = {
            id: Date.now(),
            message,
            type,
            timestamp: new Date()
        };
        
        const notifications = [...this.state.notifications, notification];
        this.setState({ notifications });
        
        // Auto-remove
        if (duration > 0) {
            setTimeout(() => {
                this.removeNotification(notification.id);
            }, duration);
        }
        
        // Show toast notification
        this.showToast(message, type);
    }
    
    removeNotification(id) {
        const notifications = this.state.notifications.filter(n => n.id !== id);
        this.setState({ notifications });
    }
    
    showToast(message, type) {
        const toast = document.createElement('div');
        toast.className = `toast align-items-center text-bg-${type} border-0`;
        toast.setAttribute('role', 'alert');
        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        
        // Add to toast container
        let toastContainer = document.querySelector('.toast-container');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
            toastContainer.style.zIndex = '9999';
            document.body.appendChild(toastContainer);
        }
        
        toastContainer.appendChild(toast);
        
        // Initialize and show toast
        const bsToast = new bootstrap.Toast(toast);
        bsToast.show();
        
        // Remove from DOM after hide
        toast.addEventListener('hidden.bs.toast', () => {
            toast.remove();
        });
    }
    
    getConnectionStatusSummary() {
        const statuses = Object.values(this.state.connectionStatus);
        const connected = statuses.filter(s => s === 'connected').length;
        const total = statuses.length;
        
        if (connected === total && total > 0) return 'all-connected';
        if (connected > 0) return 'partial-connected';
        return 'disconnected';
    }
    
    destroy() {
        // Cleanup all WebSocket connections
        for (const [name, ws] of this.websockets) {
            ws.disconnect();
        }
        
        // Cleanup all components
        for (const [name, component] of this.components) {
            if (component.destroy) {
                component.destroy();
            }
        }
        
        this.websockets.clear();
        this.components.clear();
    }
}

// Real-time Status Component
class RealTimeStatus {
    constructor(element) {
        this.element = element;
        this.render();
        // Auto-update disabled
    }
    
    render() {
        if (!this.element || !window.dashboardApp) return;
        
        const app = window.dashboardApp;
        const statusSummary = app.getConnectionStatusSummary();
        const marketOpen = app.isMarketOpen();
        
        const statusConfig = {
            'all-connected': { color: 'success', text: 'All Systems Online', icon: 'check-circle' },
            'partial-connected': { color: 'warning', text: 'Partial Connection', icon: 'exclamation-triangle' },
            'disconnected': { color: 'danger', text: 'Connection Issues', icon: 'times-circle' }
        };
        
        const config = statusConfig[statusSummary];
        
        this.element.innerHTML = `
            <div class="d-flex align-items-center">
                <div class="me-3">
                    <span class="badge bg-${config.color}">
                        <i class="fas fa-${config.icon} me-1"></i>
                        ${config.text}
                    </span>
                </div>
                <div>
                    <span class="badge bg-${marketOpen ? 'success' : 'secondary'}">
                        <i class="fas fa-clock me-1"></i>
                        Market ${marketOpen ? 'Open' : 'Closed'}
                    </span>
                </div>
            </div>
        `;
    }
}

// Performance Metrics Component (simplified)
class PerformanceMetrics {
    constructor(element) {
        this.element = element;
    }
}

// Initialize Dashboard App only on dashboard pages
document.addEventListener('DOMContentLoaded', () => {
    // Only initialize on dashboard pages (not landing page)
    const isDashboardPage = window.location.pathname.startsWith('/dashboard');
    if (!isDashboardPage) {
        return;
    }
    
    window.dashboardApp = new DashboardApp();
    
    const statusElement = document.querySelector('[data-component="realtime-status"]');
    if (statusElement) {
        new RealTimeStatus(statusElement);
    }
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (window.dashboardApp) {
        window.dashboardApp.destroy();
    }
});

// Export for global access
window.CapulseDashboard = {
    DashboardApp: DashboardApp,
    RealTimeStatus: RealTimeStatus,
    PerformanceMetrics: PerformanceMetrics
};

})();