import { WebSocketManager } from './WebSocketManager';

export class MultiWebSocketManager {
    constructor(authToken = "") {
        this.connections = {}; // { [ip]: WebSocketManager }
        this.listeners = {};
        this.onConnectionChange = null; // Callback for tracking individual connection status
        this.authToken = authToken || "";
    }

    setAuthToken(authToken = "") {
        this.authToken = authToken || "";
        Object.values(this.connections).forEach(ws => {
            ws.authToken = this.authToken;
        });
    }

    addConnection(ip, port = 8080) {
        const url = `ws://${ip}:${port}`;
        if (this.connections[url]) return; // Already exists

        const wsManager = new WebSocketManager(url, this.authToken);
        
        wsManager.onConnectionChange = (state) => {
            if (this.onConnectionChange) {
                this.onConnectionChange(url, state);
            }
        };

        // Pipe all messages from individual managers up to the global listeners
        wsManager.subscribe('*', (msg) => {
            this._dispatch(msg);
        });

        this.connections[url] = wsManager;
        wsManager.connect();
        this._saveConnections();
    }

    removeConnection(url) {
        if (this.connections[url]) {
            this.connections[url].disconnect();
            delete this.connections[url];
            if (this.onConnectionChange) {
                this.onConnectionChange(url, 'DISCONNECTED');
            }
            this._saveConnections();
        }
    }

    _safeStorageSet(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (e) {
            console.warn(`Storage set failed for ${key}`);
        }
    }
    
    _safeStorageGet(key) {
        try {
            return localStorage.getItem(key);
        } catch (e) {
            console.warn(`Storage get failed for ${key}`);
            return null;
        }
    }

    _saveConnections() {
        const urls = Object.keys(this.connections);
        this._safeStorageSet("PhoneOS_Swarm_Connections", JSON.stringify(urls));
    }

    loadSavedConnections() {
        try {
            const raw = this._safeStorageGet("PhoneOS_Swarm_Connections");
            if (!raw) return;
            const saved = JSON.parse(raw);
            if (Array.isArray(saved)) {
                saved.forEach(url => {
                    const [ip, portStr] = url.replace('ws://', '').split(':');
                    this.addConnection(ip, portStr ? parseInt(portStr) : 8080);
                });
            }
        } catch (e) {
            console.error("Failed to load saved connections", e);
        }
    }

    subscribe(messageType, callback) {
        if (typeof callback !== 'function') return;
        if (!this.listeners[messageType]) {
            this.listeners[messageType] = new Set();
        }
        this.listeners[messageType].add(callback);
    }

    unsubscribe(messageType, callback) {
        if (this.listeners[messageType]) {
            this.listeners[messageType].delete(callback);
        }
    }

    _dispatch(message) {
        const type = message.msg_type;
        if (this.listeners[type]) {
            this.listeners[type].forEach(cb => {
                try { cb(message); } catch (e) { console.error("Error in listener:", e); }
            });
        }
        if (this.listeners['*']) {
            this.listeners['*'].forEach(cb => {
                try { cb(message); } catch (e) { console.error("Error in listener:", e); }
            });
        }
    }

    send(message) {
        // Broadcast to all active connections
        let sent = false;
        Object.values(this.connections).forEach(ws => {
            if (ws.connected) {
                const success = ws.send(message);
                if (success) sent = true;
            }
        });
        return sent;
    }

    disconnectAll() {
        Object.values(this.connections).forEach(ws => ws.disconnect());
    }
}
