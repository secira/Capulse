/**
 * React-style Hooks for Capulse Trading Platform
 * Simplified for production - all data fetching is user-initiated
 */
(function() {
'use strict';

if (!window.StateManager) {
    window.StateManager = {
        states: new Map(),
        listeners: new Map(),
        createState: function(key, initialValue) {
            this.states.set(key, initialValue);
            this.listeners.set(key, new Set());
            var self = this;
            var setState = function(newValue) {
                var currentValue = self.states.get(key);
                var nextValue = typeof newValue === 'function' ? newValue(currentValue) : newValue;
                if (nextValue !== currentValue) {
                    self.states.set(key, nextValue);
                    self.listeners.get(key).forEach(function(cb) { cb(nextValue, currentValue); });
                }
            };
            return [function() { return self.states.get(key); }, setState];
        }
    };
}

// React-style useState hook
function useState(initialValue) {
    const stateId = `state_${Date.now()}_${Math.random()}`;
    return window.StateManager.createState(stateId, initialValue);
}

// React-style useEffect hook
function useEffect(callback, dependencies = []) {
    let cleanup = null;
    let lastDeps = [];
    
    const runEffect = () => {
        const changed = dependencies.length !== lastDeps.length ||
                       dependencies.some((d, i) => d !== lastDeps[i]);
        
        if (changed) {
            if (cleanup) cleanup();
            cleanup = callback();
            lastDeps = [...dependencies];
        }
    };
    
    runEffect();
    return () => { if (cleanup) cleanup(); };
}

// Notification hook
function useNotifications() {
    const [getNotifications, setNotifications] = useState([]);
    
    const addNotification = (message, type = 'info', duration = 5000) => {
        const notification = { id: Date.now(), message, type, timestamp: new Date() };
        setNotifications(prev => [...(prev || []), notification]);
        
        if (duration > 0) {
            setTimeout(() => removeNotification(notification.id), duration);
        }
        return notification.id;
    };
    
    const removeNotification = (id) => {
        setNotifications(prev => (prev || []).filter(n => n.id !== id));
    };
    
    return { notifications: getNotifications, addNotification, removeNotification };
}

window.CapulseHooks = { useState: useState, useEffect: useEffect, useNotifications: useNotifications };
window.CapitalHooks = window.CapulseHooks;

})();
