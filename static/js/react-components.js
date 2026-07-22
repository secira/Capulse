/**
 * React-style Components for Capulse using Vanilla JavaScript
 * Provides React-like component architecture with WebSocket integration
 * Optimized for production scalability and real-time updates
 */
(function() {
'use strict';

class Component {
    constructor(element, props) {
        props = props || {};
        this.element = typeof element === 'string' ? document.querySelector(element) : element;
        this.props = props;
        this.state = {};
        this.refs = {};
        
        if (this.element) {
            this.init();
        }
    }
    
    setState(newState) {
        this.state = { ...this.state, ...newState };
        this.render();
    }
    
    init() {
    }
    
    render() {
    }
    
    destroy() {
        if (this.element) {
            this.element.innerHTML = '';
        }
    }
}
window.Component = Component;

// WebSocket Manager (disabled for production - user-initiated data only)
class WebSocketManager {
    constructor() {
        this.connections = new Map();
    }
    
    connect(url, onMessage, options = {}) {
        console.log('WebSocket connections disabled');
        return null;
    }
    
    disconnect(connectionId) {}
    send(connectionId, data) { return false; }
    disconnectAll() { this.connections.clear(); }
}
window.WebSocketManager = WebSocketManager;

if (!window.wsManager) {
    window.wsManager = new WebSocketManager();
}
var wsManager = window.wsManager;

// Real-time Market Data Component (user-initiated data only)
class RealTimeMarketData extends Component {
    init() {
        this.state = {
            marketData: {},
            isConnected: false,
            lastUpdate: null,
            connectionStatus: 'ready'
        };
        this.render();
    }
    
    render() {
        if (!this.element) return;
        
        const { marketData, isConnected, lastUpdate, connectionStatus } = this.state;
        
        const statusColor = {
            'connected': 'success',
            'connecting': 'warning', 
            'disconnected': 'danger',
            'error': 'danger'
        }[connectionStatus];
        
        this.element.innerHTML = `
            <div class="real-time-market-container">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h5 class="mb-0">📊 Live Market Data</h5>
                    <div class="connection-status">
                        <span class="badge bg-${statusColor}">
                            <i class="fas fa-circle me-1"></i>${connectionStatus}
                        </span>
                        ${lastUpdate ? `<small class="text-muted ms-2">Updated: ${lastUpdate}</small>` : ''}
                    </div>
                </div>
                
                <div class="row" id="market-indices">
                    ${this.renderMarketIndices(marketData)}
                </div>
                
                <div class="mt-3" id="stock-updates">
                    ${this.renderStockUpdates(marketData)}
                </div>
            </div>
        `;
    }
    
    renderMarketIndices(data) {
        const indices = data.indices || {};
        
        return Object.entries(indices).map(([symbol, indexData]) => `
            <div class="col-md-6 mb-3">
                <div class="d-flex justify-content-between align-items-center p-3 bg-light rounded index-card cursor-pointer" 
                     style="transition: all 0.2s ease; border: 1px solid #e1e5e9;">
                    <div>
                        <h6 class="fw-bold mb-1">${symbol}</h6>
                        <small class="text-muted">NSE Index</small>
                    </div>
                    <div class="text-end">
                        <div class="fw-bold fs-5">${indexData.value || 'N/A'}</div>
                        <small class="text-${indexData.change >= 0 ? 'success' : 'danger'}">
                            <i class="fas fa-arrow-${indexData.change >= 0 ? 'up' : 'down'} me-1"></i>
                            ${indexData.change >= 0 ? '+' : ''}${indexData.change}%
                        </small>
                        <div class="mt-1">
                            <i class="fas fa-chart-line text-primary small" title="Live data"></i>
                        </div>
                    </div>
                </div>
            </div>
        `).join('');
    }
    
    renderStockUpdates(data) {
        const stocks = data.stocks || {};
        
        if (Object.keys(stocks).length === 0) {
            return '<div class="text-center text-muted py-3">No stock updates available</div>';
        }
        
        return `
            <div class="table-responsive">
                <table class="table table-sm">
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Price</th>
                            <th>Change</th>
                            <th>Volume</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${Object.entries(stocks).map(([symbol, stockData]) => `
                            <tr>
                                <td class="fw-bold">${symbol}</td>
                                <td>₹${stockData.price || 'N/A'}</td>
                                <td>
                                    <span class="badge bg-${stockData.change >= 0 ? 'success' : 'danger'} bg-opacity-10 text-${stockData.change >= 0 ? 'success' : 'danger'}">
                                        ${stockData.change >= 0 ? '+' : ''}${stockData.change}%
                                    </span>
                                </td>
                                <td class="text-muted">${stockData.volume || 'N/A'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }
    
    destroy() {
        if (this.wsConnection) {
            wsManager.disconnect(this.wsConnection);
        }
        super.destroy();
    }
}

// Trading Interface Component
class TradingInterface extends Component {
    init() {
        this.state = {
            orderStatus: {},
            portfolio: {},
            isConnected: false,
            pendingOrders: []
        };
        
        this.connectTradingWebSocket();
        this.setupOrderForm();
        this.render();
    }
    
    connectTradingWebSocket() {
        this.wsConnection = wsManager.connect(
            'ws://localhost:8002',
            (data) => this.handleTradingUpdate(data),
            {
                onOpen: () => this.setState({ isConnected: true }),
                onClose: () => this.setState({ isConnected: false }),
                autoReconnect: true
            }
        );
    }
    
    handleTradingUpdate(data) {
        switch (data.type) {
            case 'order_update':
                this.updateOrderStatus(data.order);
                break;
            case 'portfolio_update':
                this.setState({ portfolio: data.portfolio });
                break;
            case 'position_update':
                this.updatePosition(data.position);
                break;
        }
    }
    
    updateOrderStatus(order) {
        const updatedOrders = { ...this.state.orderStatus };
        updatedOrders[order.id] = order;
        this.setState({ orderStatus: updatedOrders });
    }
    
    setupOrderForm() {
        // React-style event handling
        if (this.element) {
            this.element.addEventListener('submit', (e) => this.handleOrderSubmit(e));
        }
    }
    
    async handleOrderSubmit(e) {
        e.preventDefault();
        
        const formData = new FormData(e.target);
        const order = {
            symbol: formData.get('symbol'),
            quantity: parseInt(formData.get('quantity')),
            side: formData.get('side'),
            order_type: formData.get('order_type'),
            price: formData.get('price') ? parseFloat(formData.get('price')) : null
        };
        
        try {
            // Send order to FastAPI trading engine
            const response = await fetch('http://localhost:8000/api/orders', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.getAuthToken()}`
                },
                body: JSON.stringify(order)
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.showNotification('Order placed successfully', 'success');
                e.target.reset();
            } else {
                this.showNotification(`Order failed: ${result.message}`, 'error');
            }
            
        } catch (error) {
            console.error('Order submission error:', error);
            this.showNotification('Order submission failed', 'error');
        }
    }
    
    getAuthToken() {
        // Extract auth token from session or localStorage
        return localStorage.getItem('auth_token') || '';
    }
    
    showNotification(message, type) {
        // Suppress "Market Data Connected" notifications
        if (message.includes('Market Data Connected')) return;
        
        // React-style notification system
        const notification = document.createElement('div');
        notification.className = `alert alert-${type === 'success' ? 'success' : 'danger'} alert-dismissible fade show position-fixed`;
        notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
        notification.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        document.body.appendChild(notification);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (notification.parentNode) {
                notification.remove();
            }
        }, 5000);
    }
    
    render() {
        // Component rendering logic would go here
        // For now, enhance existing forms with real-time capabilities
    }
}

// Portfolio Component with Real-time Updates
class PortfolioRealtime extends Component {
    init() {
        this.state = {
            holdings: {},
            totalValue: 0,
            dayChange: 0,
            dayChangePercent: 0,
            isLoading: true,
            lastSync: null
        };
        
        // Auto-loading disabled - all requests must be user-initiated
        this.render();
    }
    
    connectPortfolioWebSocket() {
        this.wsConnection = wsManager.connect(
            'ws://localhost:8003',
            (data) => this.handlePortfolioUpdate(data),
            {
                autoReconnect: true,
                onOpen: () => this.requestPortfolioSync()
            }
        );
    }
    
    handlePortfolioUpdate(data) {
        if (data.type === 'portfolio_sync') {
            this.setState({
                holdings: data.holdings || {},
                totalValue: data.total_value || 0,
                dayChange: data.day_change || 0,
                dayChangePercent: data.day_change_percent || 0,
                lastSync: new Date().toLocaleTimeString(),
                isLoading: false
            });
        }
    }
    
    async loadPortfolioData() {
        try {
            const response = await fetch('/api/portfolio');
            const data = await response.json();
            
            if (data.success) {
                this.setState({
                    holdings: data.holdings || {},
                    totalValue: data.total_value || 0,
                    isLoading: false
                });
            }
        } catch (error) {
            console.error('Portfolio load error:', error);
            this.setState({ isLoading: false });
        }
    }
    
    requestPortfolioSync() {
        if (this.wsConnection) {
            wsManager.send(this.wsConnection, {
                type: 'sync_portfolio',
                timestamp: Date.now()
            });
        }
    }
    
    render() {
        if (!this.element) return;
        
        const { holdings, totalValue, dayChange, isLoading, lastSync } = this.state;
        
        if (isLoading) {
            this.element.innerHTML = `
                <div class="text-center py-4">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                </div>
            `;
            return;
        }
        
        this.element.innerHTML = `
            <div class="portfolio-container">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h5 class="mb-0">💼 Portfolio Overview</h5>
                    ${lastSync ? `<small class="text-muted">Last sync: ${lastSync}</small>` : ''}
                </div>
                
                <div class="row mb-3">
                    <div class="col-md-6">
                        <div class="card bg-primary text-white">
                            <div class="card-body">
                                <h6 class="card-title">Total Value</h6>
                                <h4>₹${totalValue.toLocaleString()}</h4>
                            </div>
                        </div>
                    </div>
                    <div class="col-md-6">
                        <div class="card bg-${dayChange >= 0 ? 'success' : 'danger'} text-white">
                            <div class="card-body">
                                <h6 class="card-title">Day P&L</h6>
                                <h4>${dayChange >= 0 ? '+' : ''}₹${Math.abs(dayChange).toLocaleString()}</h4>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="holdings-list">
                    ${this.renderHoldings(holdings)}
                </div>
            </div>
        `;
    }
    
    renderHoldings(holdings) {
        if (Object.keys(holdings).length === 0) {
            return '<div class="text-center text-muted py-3">No holdings found</div>';
        }
        
        return Object.entries(holdings).map(([symbol, holding]) => `
            <div class="holding-item p-3 border-bottom">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="mb-1">${symbol}</h6>
                        <small class="text-muted">${holding.quantity || 0} shares</small>
                    </div>
                    <div class="text-end">
                        <div>₹${(holding.current_value || 0).toLocaleString()}</div>
                        <small class="badge bg-${holding.day_change >= 0 ? 'success' : 'danger'}">
                            ${holding.day_change >= 0 ? '+' : ''}${holding.day_change}%
                        </small>
                    </div>
                </div>
            </div>
        `).join('');
    }
    
    destroy() {
        if (this.wsConnection) {
            wsManager.disconnect(this.wsConnection);
        }
        super.destroy();
    }
}

// AI Trading Signals Component
class AITradingSignals extends Component {
    init() {
        this.state = {
            signals: [],
            isLoading: true,
            autoRefresh: true,
            lastUpdate: null
        };
        
        // Auto-loading disabled - all requests must be user-initiated
        this.render();
    }
    
    async loadSignals() {
        try {
            const response = await fetch('/api/trading-signals');
            const data = await response.json();
            
            this.setState({
                signals: data.signals || [],
                isLoading: false,
                lastUpdate: new Date().toLocaleTimeString()
            });
            
        } catch (error) {
            console.error('AI signals load error:', error);
            this.setState({ isLoading: false });
        }
    }
    
    setupAutoRefresh() {
        // Auto-refresh disabled - all requests must be user-initiated
        console.log('⏱️ Auto-refresh disabled');
    }
    
    render() {
        if (!this.element) return;
        
        const { signals, isLoading, lastUpdate, autoRefresh } = this.state;
        
        this.element.innerHTML = `
            <div class="ai-signals-container">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h5 class="mb-0">🤖 AI Trading Signals</h5>
                    <div class="controls">
                        <button class="btn btn-sm btn-outline-primary me-2" onclick="this.parentElement.parentElement.parentElement.component.loadSignals()">
                            <i class="fas fa-refresh"></i>
                        </button>
                        <div class="form-check form-switch d-inline-block">
                            <input class="form-check-input" type="checkbox" id="autoRefreshToggle" ${autoRefresh ? 'checked' : ''}>
                            <label class="form-check-label" for="autoRefreshToggle">Auto-refresh</label>
                        </div>
                    </div>
                </div>
                
                ${isLoading ? this.renderLoader() : this.renderSignals(signals)}
                
                ${lastUpdate ? `<div class="text-end"><small class="text-muted">Last updated: ${lastUpdate}</small></div>` : ''}
            </div>
        `;
        
        // Store component reference for event handling
        this.element.component = this;
        
        // Setup auto-refresh toggle
        const toggle = this.element.querySelector('#autoRefreshToggle');
        if (toggle) {
            toggle.addEventListener('change', (e) => {
                this.setState({ autoRefresh: e.target.checked });
            });
        }
    }
    
    renderLoader() {
        return `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading AI signals...</span>
                </div>
            </div>
        `;
    }
    
    renderSignals(signals) {
        if (signals.length === 0) {
            return '<div class="text-center text-muted py-3">No trading signals available</div>';
        }
        
        return signals.map(signal => `
            <div class="signal-card card mb-2">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-start">
                        <div>
                            <h6 class="card-title">${signal.symbol}</h6>
                            <p class="card-text">${signal.reasoning || 'AI analysis recommendation'}</p>
                        </div>
                        <div class="text-end">
                            <span class="badge bg-${signal.action === 'BUY' ? 'success' : signal.action === 'SELL' ? 'danger' : 'secondary'} mb-1">
                                ${signal.action}
                            </span>
                            <div class="text-muted">
                                <small>Confidence: ${signal.confidence}%</small>
                            </div>
                        </div>
                    </div>
                    
                    ${signal.target_price ? `
                        <div class="mt-2 pt-2 border-top">
                            <div class="row">
                                <div class="col-6">
                                    <small class="text-muted">Target: ₹${signal.target_price}</small>
                                </div>
                                <div class="col-6">
                                    <small class="text-muted">Stop Loss: ₹${signal.stop_loss || 'N/A'}</small>
                                </div>
                            </div>
                        </div>
                    ` : ''}
                </div>
            </div>
        `).join('');
    }
    
    destroy() {
        if (this.wsConnection) {
            wsManager.disconnect(this.wsConnection);
        }
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
        }
        super.destroy();
    }
}

// Export components
window.CapitalComponents = {
    RealTimeMarketData,
    TradingInterface,
    Portfolio: PortfolioRealtime,
    PortfolioRealtime,
    AITradingSignals
};
window.CapulseComponents = window.CapitalComponents;

// Component Manager for React-style mounting/unmounting
class ComponentManager {
    constructor() {
        this.mountedComponents = new Map();
    }
    
    mount(selector, ComponentClass, props = {}) {
        const element = document.querySelector(selector);
        if (element) {
            // Unmount existing component if present
            this.unmount(selector);
            
            // Mount new component
            const component = new ComponentClass(element, props);
            this.mountedComponents.set(selector, component);
            
            return component;
        }
        return null;
    }
    
    unmount(selector) {
        const component = this.mountedComponents.get(selector);
        if (component && component.destroy) {
            component.destroy();
            this.mountedComponents.delete(selector);
        }
    }
    
    unmountAll() {
        for (const [selector, component] of this.mountedComponents) {
            if (component.destroy) {
                component.destroy();
            }
        }
        this.mountedComponents.clear();
    }
    
    getComponent(selector) {
        return this.mountedComponents.get(selector);
    }
}

// Global component manager
const componentManager = new ComponentManager();

// Auto-mount components only on dashboard pages
document.addEventListener('DOMContentLoaded', () => {
    const isDashboardPage = window.location.pathname.startsWith('/dashboard');
    if (!isDashboardPage) return;
    
    const marketDataElement = document.querySelector('[data-component="realtime-market"]');
    if (marketDataElement) {
        componentManager.mount('[data-component="realtime-market"]', RealTimeMarketData);
    }
    
    const tradingElement = document.querySelector('[data-component="trading-interface"]');
    if (tradingElement) {
        componentManager.mount('[data-component="trading-interface"]', TradingInterface);
    }
    
    const portfolioElement = document.querySelector('[data-component="portfolio"]');
    if (portfolioElement) {
        componentManager.mount('[data-component="portfolio"]', PortfolioRealtime);
    }
    
    const signalsElement = document.querySelector('[data-component="ai-signals"]');
    if (signalsElement) {
        componentManager.mount('[data-component="ai-signals"]', AITradingSignals);
    }
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    componentManager.unmountAll();
    wsManager.disconnectAll();
});

// Export for global access
window.CapulseComponents = {
    RealTimeMarketData: RealTimeMarketData,
    TradingInterface: TradingInterface,
    Portfolio: PortfolioRealtime,
    PortfolioRealtime: PortfolioRealtime,
    AITradingSignals: AITradingSignals,
    ComponentManager: ComponentManager,
    WebSocketManager: WebSocketManager,
    componentManager: componentManager,
    wsManager: wsManager
};

})();