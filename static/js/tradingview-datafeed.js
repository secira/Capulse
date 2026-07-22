/**
 * TradingView Datafeed Implementation for Capulse
 * Connects NSE data to TradingView charts
 */
(function() {
'use strict';

class CapulseDatafeed {
    constructor() {
        this.baseUrl = '/api/tradingview';
        this.debug = true;
    }

    // Required method - provides datafeed configuration (no network call)
    onReady(callback) {
        callback({
            supported_resolutions: ['1', '5', '15', '30', '60', '1D', '1W', '1M'],
            supports_marks: false,
            supports_timescale_marks: false,
            supports_time: true
        });
    }

    // Symbol search
    searchSymbols(userInput, exchange, symbolType, onResultReadyCallback) {
        this.log('searchSymbols called', { userInput, exchange, symbolType });
        
        const params = new URLSearchParams({
            query: userInput,
            exchange: exchange || '',
            type: symbolType || '',
            limit: '30'
        });
        
        fetch(`${this.baseUrl}/search?${params}`)
            .then(response => response.json())
            .then(data => {
                this.log('Search results', data);
                onResultReadyCallback(data.symbols || []);
            })
            .catch(error => {
                this.log('Search error', error);
                onResultReadyCallback([]);
            });
    }

    // Symbol resolution
    resolveSymbol(symbolName, onSymbolResolvedCallback, onResolveErrorCallback) {
        this.log('resolveSymbol called', symbolName);
        
        fetch(`${this.baseUrl}/symbols?symbol=${encodeURIComponent(symbolName)}`)
            .then(response => response.json())
            .then(data => {
                if (data.symbol) {
                    this.log('Symbol resolved', data.symbol);
                    onSymbolResolvedCallback(data.symbol);
                } else {
                    this.log('Symbol not found', symbolName);
                    onResolveErrorCallback('Symbol not found');
                }
            })
            .catch(error => {
                this.log('Resolve error', error);
                onResolveErrorCallback('Symbol resolution failed');
            });
    }

    // Get historical data
    getBars(symbolInfo, resolution, from, to, onHistoryCallback, onErrorCallback, firstDataRequest) {
        this.log('getBars called', {
            symbol: symbolInfo.name,
            resolution,
            from: new Date(from * 1000),
            to: new Date(to * 1000),
            firstDataRequest
        });

        const params = new URLSearchParams({
            symbol: symbolInfo.name,
            resolution: resolution,
            from: from.toString(),
            to: to.toString()
        });

        fetch(`${this.baseUrl}/history?${params}`)
            .then(response => response.json())
            .then(data => {
                if (data.s === 'ok' && data.t) {
                    // Convert arrays to bar objects
                    const bars = [];
                    for (let i = 0; i < data.t.length; i++) {
                        bars.push({
                            time: data.t[i] * 1000,
                            low: data.l[i],
                            high: data.h[i],
                            open: data.o[i],
                            close: data.c[i],
                            volume: data.v ? data.v[i] : 0
                        });
                    }
                    
                    this.log(`Received ${bars.length} bars`);
                    onHistoryCallback(bars, { noData: false });
                } else if (data.s === 'no_data') {
                    this.log('No data available');
                    onHistoryCallback([], { noData: true });
                } else {
                    this.log('History error', data);
                    onErrorCallback('Failed to get historical data');
                }
            })
            .catch(error => {
                this.log('History fetch error', error);
                onErrorCallback('Network error');
            });
    }

    // Real-time data subscription
    subscribeBars(symbolInfo, resolution, onRealtimeCallback, subscribeUID, onResetCacheNeededCallback) {
        this.log('subscribeBars called', {
            symbol: symbolInfo.name,
            resolution,
            subscribeUID
        });
        
        // Store subscription info
        this.subscriptions = this.subscriptions || {};
        this.subscriptions[subscribeUID] = {
            symbolInfo,
            resolution,
            onRealtimeCallback,
            lastBar: null
        };

        // Start polling for real-time updates
        this.startRealTimeUpdates(subscribeUID);
    }

    // Unsubscribe from real-time data
    unsubscribeBars(subscribeUID) {
        this.log('unsubscribeBars called', subscribeUID);
        
        if (this.subscriptions && this.subscriptions[subscribeUID]) {
            // Clear any polling intervals
            if (this.subscriptions[subscribeUID].pollInterval) {
                clearInterval(this.subscriptions[subscribeUID].pollInterval);
            }
            delete this.subscriptions[subscribeUID];
        }
    }

    // Real-time update polling disabled - user must manually refresh
    startRealTimeUpdates(subscribeUID) {
        console.log('Real-time polling disabled - data refreshes on user action only');
    }

    // Update real-time data
    updateRealTimeData(subscribeUID) {
        const subscription = this.subscriptions[subscribeUID];
        if (!subscription) return;

        const { symbolInfo, onRealtimeCallback } = subscription;
        
        fetch(`${this.baseUrl}/realtime?symbol=${encodeURIComponent(symbolInfo.name)}`)
            .then(response => response.json())
            .then(data => {
                if (data.success && data.price) {
                    const now = Math.floor(Date.now() / 1000);
                    const bar = {
                        time: now * 1000,
                        open: data.price,
                        high: data.price,
                        low: data.price,
                        close: data.price,
                        volume: data.volume || 0
                    };

                    this.log('Real-time update', bar);
                    onRealtimeCallback(bar);
                }
            })
            .catch(error => {
                this.log('Real-time update error', error);
            });
    }

    // Helper method for logging
    log(message, data = null) {
        if (this.debug) {
            if (data) {
                console.log(`[capulse.tech says] ${message}`, data);
            } else {
                console.log(`[capulse.tech says] ${message}`);
            }
        }
    }
}

window.CapulseDatafeed = CapulseDatafeed;

})();