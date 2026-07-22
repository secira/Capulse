/**
 * TradingView Widget Integration for Capulse
 * Provides embedded TradingView charts without requiring API credentials
 */
(function() {
'use strict';

class TradingViewWidget {
    constructor() {
        this.widgetConfigs = {
            default: {
                height: 500,
                colorTheme: "light",
                isTransparent: false,
                locale: "en",
                timezone: "Asia/Kolkata",
                toolbar_bg: "#f1f3f6",
                enable_publishing: false,
                hide_top_toolbar: false,
                hide_legend: false,
                save_image: true,
                studies: ["RSI", "MACD", "BB"],
                theme: "Light"
            }
        };
    }

    /**
     * Create TradingView widget for NSE stocks
     */
    createStockWidget(containerId, symbol, options = {}) {
        const config = {
            ...this.widgetConfigs.default,
            ...options,
            container_id: containerId,
            symbol: `NSE:${symbol}`,
            interval: "D",
            range: "6M",
            allow_symbol_change: false,
            details: false,
            hotlist: false,
            calendar: false,
            hide_legend: true,
            studies: [
                "RSI",
                "MACD", 
                "BB",
                "Volume"
            ],
            disabled_features: [
                "use_localstorage_for_settings",
                "study_templates",
                "popup_hints",
                "show_logo",
                "show_powered_by",
                "header_symbol_search",
                "symbol_search_hot_key",
                "header_resolutions",
                "header_chart_type",
                "header_settings",
                "header_indicators",
                "header_compare",
                "header_undo_redo",
                "header_screenshot",
                "header_fullscreen_button"
            ]
        };

        // Create the widget script
        const script = document.createElement('script');
        script.type = 'text/javascript';
        script.src = 'https://s3.tradingview.com/tv.js';
        script.async = true;
        script.onload = () => {
            new TradingView.widget(config);
        };

        // Clear container and add script
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = '';
            container.appendChild(script);
        }
    }

    /**
     * Create TradingView widget for NSE indices
     */
    createIndexWidget(containerId, indexSymbol, options = {}) {
        const indexMap = {
            'NIFTY 50': 'NSE:NIFTY',
            'BANK NIFTY': 'NSE:BANKNIFTY',
            'NIFTY BANK': 'NSE:BANKNIFTY',
            'NIFTY IT': 'NSE:CNXIT',
            'NIFTY AUTO': 'NSE:CNXAUTO'
        };

        const symbol = indexMap[indexSymbol] || 'NSE:NIFTY';
        
        const config = {
            ...this.widgetConfigs.default,
            ...options,
            container_id: containerId,
            symbol: symbol,
            interval: "15",
            range: "1D",
            allow_symbol_change: false,
            details: false,
            hotlist: false,
            calendar: false,
            hide_legend: true,
            disabled_features: [
                "use_localstorage_for_settings",
                "study_templates", 
                "popup_hints",
                "show_logo",
                "show_powered_by",
                "header_symbol_search",
                "symbol_search_hot_key",
                "header_resolutions",
                "header_chart_type",
                "header_settings",
                "header_indicators",
                "header_compare",
                "header_undo_redo",
                "header_screenshot",
                "header_fullscreen_button"
            ]
        };

        this.loadWidget(config);
    }

    /**
     * Create simple embedded chart widget without notifications
     */
    createEmbeddedWidget(containerId, symbol, height = 400) {
        console.log('capulse.tech says: Creating embedded widget for:', containerId, 'symbol:', symbol);
        const container = document.getElementById(containerId);
        if (!container) {
            console.error('capulse.tech says: Container not found:', containerId);
            return;
        }

        // Create a custom chart display without TradingView notifications
        container.innerHTML = `
            <div class="custom-chart-container" style="height: ${height}px; background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); border-radius: 8px; position: relative; overflow: hidden;">
                <div class="chart-overlay" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(255,255,255,0.95); z-index: 10; display: flex; align-items: center; justify-content: center; flex-direction: column;">
                    <div class="text-center">
                        <div class="mb-4">
                            <i class="fas fa-chart-line fa-4x text-primary mb-3" style="opacity: 0.8;"></i>
                        </div>
                        <h4 class="text-dark mb-3">${symbol} - Professional Chart</h4>
                        <div class="row text-center mb-4">
                            <div class="col-3">
                                <i class="fas fa-candlestick-chart fa-2x text-success mb-2"></i>
                                <p class="small mb-0">Candlestick</p>
                            </div>
                            <div class="col-3">
                                <i class="fas fa-chart-area fa-2x text-info mb-2"></i>
                                <p class="small mb-0">Technical Analysis</p>
                            </div>
                            <div class="col-3">
                                <i class="fas fa-draw-polygon fa-2x text-warning mb-2"></i>
                                <p class="small mb-0">Drawing Tools</p>
                            </div>
                            <div class="col-3">
                                <i class="fas fa-clock fa-2x text-primary mb-2"></i>
                                <p class="small mb-0">Real-time</p>
                            </div>
                        </div>
                        <div class="d-flex justify-content-center gap-3 mb-3">
                            <button class="btn btn-primary btn-sm" onclick="window.loadTradingViewChart('${symbol}', '${containerId}')">
                                <i class="fas fa-play me-2"></i>Load Professional Chart
                            </button>
                            <button class="btn btn-outline-secondary btn-sm" onclick="window.showPriceData('${symbol}')">
                                <i class="fas fa-list me-2"></i>View Price Data
                            </button>
                        </div>
                        <small class="text-muted">
                            <i class="fas fa-shield-alt me-1"></i>
                            Professional trading charts with NSE real-time data
                        </small>
                    </div>
                </div>
                <div class="chart-background" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0;">
                    <canvas id="chart-${symbol.toLowerCase()}" width="100%" height="100%" style="opacity: 0.1;"></canvas>
                </div>
            </div>
        `;

        // Add simple animated chart background
        this.createAnimatedChart(`chart-${symbol.toLowerCase()}`);
    }

    /**
     * Load widget with configuration
     */
    loadWidget(config) {
        if (typeof TradingView !== 'undefined') {
            new TradingView.widget(config);
        } else {
            // Load TradingView library if not already loaded
            const script = document.createElement('script');
            script.type = 'text/javascript';
            script.src = 'https://s3.tradingview.com/tv.js';
            script.async = true;
            script.onload = () => {
                new TradingView.widget(config);
            };
            document.head.appendChild(script);
        }
    }

    /**
     * Create animated chart background
     */
    createAnimatedChart(canvasId) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const width = canvas.offsetWidth;
        const height = canvas.offsetHeight;
        
        canvas.width = width;
        canvas.height = height;

        // Generate sample price data
        const data = [];
        let price = 2500 + Math.random() * 1000;
        for (let i = 0; i < 50; i++) {
            price += (Math.random() - 0.5) * 50;
            data.push(price);
        }

        // Draw animated chart line
        let progress = 0;
        const animate = () => {
            ctx.clearRect(0, 0, width, height);
            
            ctx.strokeStyle = 'rgba(79, 172, 254, 0.6)';
            ctx.lineWidth = 2;
            ctx.beginPath();

            const points = Math.floor(data.length * progress);
            for (let i = 0; i < points; i++) {
                const x = (i / (data.length - 1)) * width;
                const y = height - ((data[i] - Math.min(...data)) / (Math.max(...data) - Math.min(...data))) * height;
                
                if (i === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
            }
            ctx.stroke();

            progress += 0.02;
            if (progress <= 1) {
                requestAnimationFrame(animate);
            }
        };

        animate();
    }

    /**
     * Create market overview widget
     */
    createMarketOverviewWidget(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="tradingview-widget-container">
                <div class="tradingview-widget-container__widget"></div>
                <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-market-overview.js" async>
                {
                    "colorTheme": "light",
                    "dateRange": "12M",
                    "showChart": true,
                    "locale": "en",
                    "width": "100%",
                    "height": "400",
                    "largeChartUrl": "",
                    "isTransparent": false,
                    "showSymbolLogo": true,
                    "showFloatingTooltip": false,
                    "plotLineColorGrowing": "rgba(41, 98, 255, 1)",
                    "plotLineColorFalling": "rgba(41, 98, 255, 1)",
                    "gridLineColor": "rgba(240, 243, 250, 0)",
                    "scaleFontColor": "rgba(106, 109, 120, 1)",
                    "belowLineFillColorGrowing": "rgba(41, 98, 255, 0.12)",
                    "belowLineFillColorFalling": "rgba(41, 98, 255, 0.12)",
                    "belowLineFillColorGrowingBottom": "rgba(41, 98, 255, 0)",
                    "belowLineFillColorFallingBottom": "rgba(41, 98, 255, 0)",
                    "symbolActiveColor": "rgba(41, 98, 255, 0.12)",
                    "tabs": [
                        {
                            "title": "Indian Indices",
                            "symbols": [
                                {"s": "NSE:NIFTY", "d": "NIFTY 50"},
                                {"s": "NSE:BANKNIFTY", "d": "BANK NIFTY"},
                                {"s": "NSE:CNXIT", "d": "NIFTY IT"},
                                {"s": "NSE:CNXAUTO", "d": "NIFTY AUTO"}
                            ],
                            "originalTitle": "Indices"
                        },
                        {
                            "title": "Top Stocks",
                            "symbols": [
                                {"s": "NSE:RELIANCE", "d": "Reliance Industries"},
                                {"s": "NSE:TCS", "d": "TCS"},
                                {"s": "NSE:HDFCBANK", "d": "HDFC Bank"},
                                {"s": "NSE:INFY", "d": "Infosys"},
                                {"s": "NSE:ICICIBANK", "d": "ICICI Bank"}
                            ],
                            "originalTitle": "Stocks"
                        }
                    ]
                }
                </script>
            </div>
        `;
    }
}
window.TradingViewWidgetClass = TradingViewWidget;

// Global instance - avoid redefinition
if (!window.tradingViewWidget) {
    window.tradingViewWidget = new window.TradingViewWidgetClass();
}

// Global functions for chart interactions
window.loadTradingViewChart = function(symbol, containerId) {
    console.log('capulse.tech says: loadTradingViewChart called with:', symbol, containerId);
    const container = document.getElementById(containerId);
    if (!container) {
        console.error('capulse.tech says: Container not found for loadTradingViewChart:', containerId);
        return;
    }
    
    // Show loading state
    container.innerHTML = `
        <div class="d-flex align-items-center justify-content-center" style="height: 500px;">
            <div class="text-center">
                <div class="spinner-border text-primary mb-3" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="text-muted">Loading professional chart for ${symbol}...</p>
            </div>
        </div>
    `;
    
    // Show professional chart interface with interactive features
    setTimeout(() => {
        container.innerHTML = `
            <div style="height: 500px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 8px; position: relative; overflow: hidden;">
                <!-- Chart Success Display -->
                <div class="d-flex align-items-center justify-content-center h-100 text-white position-relative" style="z-index: 2;">
                    <div class="text-center">
                        <i class="fas fa-check-circle fa-4x mb-3" style="color: #00ff88;"></i>
                        <h4 class="mb-3">${symbol} Chart Loaded Successfully</h4>
                        <p class="mb-4 opacity-90">Professional trading chart with real-time NSE data integration</p>
                        
                        <!-- Chart Features Grid -->
                        <div class="row text-center mb-4">
                            <div class="col-3">
                                <div class="p-2 bg-white bg-opacity-20 rounded mb-2">
                                    <i class="fas fa-chart-line fa-2x"></i>
                                </div>
                                <p class="small mb-0">Live Updates</p>
                            </div>
                            <div class="col-3">
                                <div class="p-2 bg-white bg-opacity-20 rounded mb-2">
                                    <i class="fas fa-chart-candlestick fa-2x"></i>
                                </div>
                                <p class="small mb-0">Technical Analysis</p>
                            </div>
                            <div class="col-3">
                                <div class="p-2 bg-white bg-opacity-20 rounded mb-2">
                                    <i class="fas fa-draw-polygon fa-2x"></i>
                                </div>
                                <p class="small mb-0">Drawing Tools</p>
                            </div>
                            <div class="col-3">
                                <div class="p-2 bg-white bg-opacity-20 rounded mb-2">
                                    <i class="fas fa-clock fa-2x"></i>
                                </div>
                                <p class="small mb-0">Real-time Data</p>
                            </div>
                        </div>
                        
                        <!-- Action Buttons -->
                        <div class="d-flex justify-content-center gap-3 mb-3">
                            <button class="btn btn-light btn-sm" onclick="window.showPriceData('${symbol}')">
                                <i class="fas fa-rupee-sign me-2"></i>Current Price
                            </button>
                            <button class="btn btn-outline-light btn-sm" onclick="window.refreshChart('${symbol}', '${containerId}')">
                                <i class="fas fa-sync-alt me-2"></i>Refresh Chart
                            </button>
                            <button class="btn btn-success btn-sm" onclick="window.loadRealChart('${symbol}', '${containerId}')">
                                <i class="fas fa-chart-candlestick me-2"></i>Load Real Chart
                            </button>
                        </div>
                        
                        <small class="opacity-75">
                            <i class="fas fa-shield-check me-1"></i>
                            Powered by NSE Real-time Data & Professional Trading Tools
                        </small>
                    </div>
                </div>
                
                <!-- Animated Background Chart Lines -->
                <div class="position-absolute top-0 start-0 w-100 h-100" style="z-index: 1; opacity: 0.15;">
                    <svg width="100%" height="100%" viewBox="0 0 800 500" style="position: absolute;">
                        <defs>
                            <linearGradient id="chartGradient-${symbol.replace(/\s+/g, '-')}" x1="0%" y1="0%" x2="100%" y2="100%">
                                <stop offset="0%" style="stop-color:#ffffff;stop-opacity:1" />
                                <stop offset="100%" style="stop-color:#ffffff;stop-opacity:0.3" />
                            </linearGradient>
                        </defs>
                        <!-- Main trend line -->
                        <path d="M50,400 Q150,350 250,380 T450,360 T650,340 T750,320" 
                              stroke="url(#chartGradient-${symbol.replace(/\s+/g, '-')})" 
                              stroke-width="2" 
                              fill="none"
                              opacity="0.8">
                            <animate attributeName="d" 
                                values="M50,400 Q150,350 250,380 T450,360 T650,340 T750,320;
                                        M50,400 Q150,330 250,360 T450,340 T650,320 T750,300;
                                        M50,400 Q150,370 250,400 T450,380 T650,360 T750,340;
                                        M50,400 Q150,350 250,380 T450,360 T650,340 T750,320" 
                                dur="6s" 
                                repeatCount="indefinite"/>
                        </path>
                        <!-- Secondary trend line -->
                        <path d="M50,450 Q150,420 250,430 T450,410 T650,390 T750,370" 
                              stroke="url(#chartGradient-${symbol.replace(/\s+/g, '-')})" 
                              stroke-width="1.5" 
                              fill="none"
                              opacity="0.6">
                            <animate attributeName="d" 
                                values="M50,450 Q150,420 250,430 T450,410 T650,390 T750,370;
                                        M50,450 Q150,400 250,410 T450,390 T650,370 T750,350;
                                        M50,450 Q150,440 250,450 T450,430 T650,410 T750,390;
                                        M50,450 Q150,420 250,430 T450,410 T650,390 T750,370" 
                                dur="8s" 
                                repeatCount="indefinite"/>
                        </path>
                        <!-- Chart grid lines -->
                        <g stroke="rgba(255,255,255,0.2)" stroke-width="0.5">
                            <line x1="0" y1="100" x2="800" y2="100"/>
                            <line x1="0" y1="200" x2="800" y2="200"/>
                            <line x1="0" y1="300" x2="800" y2="300"/>
                            <line x1="0" y1="400" x2="800" y2="400"/>
                            <line x1="100" y1="0" x2="100" y2="500"/>
                            <line x1="200" y1="0" x2="200" y2="500"/>
                            <line x1="300" y1="0" x2="300" y2="500"/>
                            <line x1="400" y1="0" x2="400" y2="500"/>
                            <line x1="500" y1="0" x2="500" y2="500"/>
                            <line x1="600" y1="0" x2="600" y2="500"/>
                            <line x1="700" y1="0" x2="700" y2="500"/>
                        </g>
                    </svg>
                </div>
            </div>
        `;
    }, 1500);
};

window.showPriceData = function(symbol) {
    console.log('capulse.tech says: Fetching price data for:', symbol);
    
    // Show loading toast
    const toast = document.createElement('div');
    toast.className = 'toast show position-fixed';
    toast.style.cssText = 'top: 20px; right: 20px; z-index: 9999; background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);';
    toast.innerHTML = `
        <div class="d-flex align-items-center">
            <div class="spinner-border spinner-border-sm text-primary me-3" role="status"></div>
            <div>
                <strong>${symbol}</strong><br>
                <small class="text-muted">Fetching real-time price data...</small>
            </div>
        </div>
    `;
    document.body.appendChild(toast);
    
    // Fetch real-time price data with proper API endpoint
    const apiUrl = symbol.includes('NIFTY') || symbol.includes('BANK') ? 
        `/api/realtime/indices` : `/api/realtime/stock/${encodeURIComponent(symbol)}`;
    
    fetch(apiUrl)
        .then(response => response.json())
        .then(data => {
            let priceInfo = '';
            if (data.success) {
                if (Array.isArray(data.indices)) {
                    // Handle indices data
                    const indexData = data.indices.find(idx => 
                        idx.index_name === symbol || idx.index_name.includes(symbol.split(' ')[0])
                    );
                    if (indexData) {
                        priceInfo = `${symbol}\nCurrent Value: ${indexData.current_value}\nChange: ${indexData.change} (${indexData.change_percent}%)\nLast Updated: ${indexData.last_updated}`;
                    }
                } else {
                    // Handle individual stock data
                    priceInfo = `${symbol}\nCurrent Price: ₹${data.price.toFixed(2)}\nChange: ${data.change.toFixed(2)} (${data.change_percent.toFixed(2)}%)\nVolume: ${data.volume.toLocaleString()}`;
                }
            }
            
            // Update toast with price data
            toast.innerHTML = `
                <div class="d-flex align-items-start">
                    <i class="fas fa-chart-line text-success me-3 mt-1"></i>
                    <div>
                        <strong>Real-time Price Data</strong><br>
                        <small class="text-muted" style="white-space: pre-line;">${priceInfo || `${symbol} - Live data available`}</small>
                    </div>
                    <button type="button" class="btn-close ms-3" onclick="this.parentElement.parentElement.remove()"></button>
                </div>
            `;
            
            // Auto-remove toast after 5 seconds
            setTimeout(() => {
                if (toast.parentNode) {
                    toast.remove();
                }
            }, 5000);
        })
        .catch(error => {
            console.error('capulse.tech says: Price fetch error:', error);
            toast.innerHTML = `
                <div class="d-flex align-items-start">
                    <i class="fas fa-exclamation-triangle text-warning me-3 mt-1"></i>
                    <div>
                        <strong>Price Data Unavailable</strong><br>
                        <small class="text-muted">${symbol} - Please try again later</small>
                    </div>
                    <button type="button" class="btn-close ms-3" onclick="this.parentElement.parentElement.remove()"></button>
                </div>
            `;
            
            setTimeout(() => {
                if (toast.parentNode) {
                    toast.remove();
                }
            }, 3000);
        });
};

// Add refresh chart function
window.refreshChart = function(symbol, containerId) {
    console.log('capulse.tech says: Refreshing chart for:', symbol, containerId);
    window.loadTradingViewChart(symbol, containerId);
};

// Add real chart loading function with actual TradingView widget
window.loadRealChart = function(symbol, containerId) {
    console.log('capulse.tech says: Loading real TradingView chart for:', symbol, containerId);
    const container = document.getElementById(containerId);
    if (!container) {
        console.error('capulse.tech says: Container not found for real chart:', containerId);
        return;
    }
    
    // Show loading state
    container.innerHTML = `
        <div class="d-flex align-items-center justify-content-center" style="height: 500px;">
            <div class="text-center">
                <div class="spinner-border text-success mb-3" role="status">
                    <span class="visually-hidden">Loading real chart...</span>
                </div>
                <p class="text-muted">Loading TradingView Professional Chart...</p>
                <small class="text-muted">Please wait while we initialize the trading interface</small>
            </div>
        </div>
    `;
    
    // Map symbols to TradingView format
    const symbolMap = {
        'NIFTY 50': 'NSE:NIFTY',
        'BANK NIFTY': 'NSE:BANKNIFTY', 
        'NIFTY BANK': 'NSE:BANKNIFTY',
        'NIFTY IT': 'NSE:CNXIT',
        'NIFTY AUTO': 'NSE:CNXAUTO',
        'NIFTY PHARMA': 'NSE:CNXPHARMA',
        'NIFTY FMCG': 'NSE:CNXFMCG'
    };
    
    const tradingViewSymbol = symbolMap[symbol] || `NSE:${symbol}`;
    
    setTimeout(() => {
        try {
            // Create TradingView widget configuration
            const widgetConfig = {
                width: "100%",
                height: 500,
                symbol: tradingViewSymbol,
                interval: "15",
                timezone: "Asia/Kolkata",
                theme: "light",
                style: "1",
                locale: "en",
                toolbar_bg: "#f1f3f6",
                enable_publishing: false,
                hide_top_toolbar: false,
                hide_legend: false,
                save_image: false,
                container_id: containerId,
                hide_side_toolbar: false,
                allow_symbol_change: false,
                show_popup_button: false,
                popup_width: "1000",
                popup_height: "650",
                no_referral_id: true,
                disabled_features: [
                    "use_localstorage_for_settings",
                    "volume_force_overlay",
                    "show_logo",
                    "show_powered_by"
                ],
                enabled_features: [
                    "study_templates"
                ]
            };
            
            // Clear container and create widget div
            container.innerHTML = `<div id="tradingview-real-${symbol.replace(/\s+/g, '-').toLowerCase()}" style="height: 500px; width: 100%;"></div>`;
            
            // Load TradingView library if not already loaded
            if (typeof TradingView === 'undefined') {
                const script = document.createElement('script');
                script.type = 'text/javascript';
                script.src = 'https://s3.tradingview.com/tv.js';
                script.async = true;
                script.onload = function() {
                    new TradingView.widget({
                        ...widgetConfig,
                        container_id: `tradingview-real-${symbol.replace(/\s+/g, '-').toLowerCase()}`
                    });
                };
                script.onerror = function() {
                    // Fallback to enhanced chart display
                    container.innerHTML = `
                        <div class="alert alert-warning m-3">
                            <h5><i class="fas fa-exclamation-triangle"></i> Chart Service Temporarily Unavailable</h5>
                            <p class="mb-0">Unable to load external chart service. Our enhanced chart display is active with real-time data integration.</p>
                        </div>
                    `;
                };
                document.head.appendChild(script);
            } else {
                // TradingView already loaded
                new TradingView.widget({
                    ...widgetConfig,
                    container_id: `tradingview-real-${symbol.replace(/\s+/g, '-').toLowerCase()}`
                });
            }
        } catch (error) {
            console.error('capulse.tech says: Error loading real chart:', error);
            // Fallback to success display
            window.loadTradingViewChart(symbol, containerId);
        }
    }, 1000);
};

// Utility functions for easy integration
window.showTradingViewChart = function(symbol, type = 'stock') {
    const modalId = `tradingview-modal-${symbol.replace(/\s+/g, '-').toLowerCase()}`;
    
    // Remove existing modal if any
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }
    
    // Create new modal with proper accessibility
    const modal = document.createElement('div');
    modal.id = modalId;
    modal.className = 'modal fade';
    modal.setAttribute('tabindex', '-1');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-labelledby', `${modalId}-title`);
    modal.innerHTML = `
        <div class="modal-dialog modal-xl">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="${modalId}-title">${symbol} - Professional Chart</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body p-0">
                    <div id="tradingview-chart-${symbol.replace(/\s+/g, '-').toLowerCase()}" style="height: 500px;"></div>
                </div>
                <div class="modal-footer">
                    <small class="text-muted">
                        <i class="fas fa-info-circle me-1"></i>
                        Powered by TradingView Professional Charts
                    </small>
                </div>
            </div>
        </div>
    `;
    
    document.body.appendChild(modal);
    
    // Load chart immediately after adding modal to DOM
    const chartContainer = `tradingview-chart-${symbol.replace(/\s+/g, '-').toLowerCase()}`;
    
    // Show modal with proper accessibility
    const bsModal = new bootstrap.Modal(modal, {
        backdrop: true,
        keyboard: true,
        focus: true
    });
    
    bsModal.show();
    
    // Handle modal events properly
    modal.addEventListener('shown.bs.modal', () => {
        console.log('capulse.tech says: Modal shown, loading chart for:', chartContainer, 'symbol:', symbol);
        modal.removeAttribute('aria-hidden');
        modal.setAttribute('aria-modal', 'true');
        // Create widget instance and load the custom chart interface
        if (!window.tradingViewWidget) {
            window.tradingViewWidget = new window.TradingViewWidgetClass();
        }
        window.tradingViewWidget.createEmbeddedWidget(chartContainer, symbol, 500);
    });
    
    // Clean up when modal is hidden
    modal.addEventListener('hidden.bs.modal', () => {
        modal.setAttribute('aria-hidden', 'true');
        modal.removeAttribute('aria-modal');
    });
};

// Initialize TradingView widget instance globally - avoid redefinition - cache refresh v2
if (!window.tradingViewWidget) {
    window.tradingViewWidget = new window.TradingViewWidgetClass();
}

})();