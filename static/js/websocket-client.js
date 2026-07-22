/**
 * WebSocket Client for Capulse (Disabled for Production)
 * All data fetching is user-initiated only
 */
(function() {
'use strict';

class WebSocketClient {
    constructor(options = {}) {
        this.connectionState = 'disabled';
        this.listeners = new Map();
    }
    
    connect() {
        console.log('WebSocket connections disabled - data requests are user-initiated');
        return Promise.resolve();
    }
    
    disconnect() {}
    
    send(data) {
        return false;
    }
    
    on(event, callback) {
        return () => {};
    }
    
    off(event, callback) {}
    
    get isConnected() {
        return false;
    }
    
    get state() {
        return { connectionState: 'disabled', isConnected: false };
    }
}

// Simplified state management
function useState(initialValue) {
    let value = initialValue;
    const listeners = new Set();
    
    const setValue = (newValue) => {
        value = typeof newValue === 'function' ? newValue(value) : newValue;
        listeners.forEach(listener => listener(value));
    };
    
    return [() => value, setValue];
}

window.WebSocketClient = WebSocketClient;
window.CapulseWebSocket = { WebSocketClient: WebSocketClient, useState: useState };

})();
