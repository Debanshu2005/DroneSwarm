import { WebSocketManager } from './WebSocketManager.js';

export class MultiWebSocketManager {
    constructor(authToken = "") {
        this.connections = {}; // { [ip]: WebSocketManager }
        this.listeners = {};
        this.onConnectionChange = null; // Callback for tracking individual connection status
        this.authToken = authToken || "";
        this.connectionStates = {};
    }

    setAuthToken(authToken = "") {
        this.authToken = authToken || "";
        Object.values(this.connections).forEach(ws => {
            ws.authToken = this.authToken;
        });
    }

    normalizeConnectionUrl(ipOrUrl, port = 8080) {
        const raw = String(ipOrUrl || "").trim();
        if (!raw) return null;

        const withScheme = raw.includes("://") ? raw : `ws://${raw}`;
        try {
            const parsed = new URL(withScheme);
            parsed.protocol = parsed.protocol === "wss:" ? "wss:" : "ws:";
            if (!parsed.port) parsed.port = String(port || 8080);
            parsed.pathname = "";
            parsed.search = "";
            parsed.hash = "";
            let host = parsed.hostname;
            if (host === "localhost") {
                host = "127.0.0.1";
            }
            return `${parsed.protocol}//${host}:${parsed.port}`;
        } catch (e) {
            let host = raw.replace(/^wss?:\/\//, "").split("/")[0].split(":")[0];
            if (!host) return null;
            if (host === "localhost") host = "127.0.0.1";
            return `ws://${host}:${port || 8080}`;
        }
    }

    addConnection(ip, port = 8080) {
        const url = this.normalizeConnectionUrl(ip, port);
        if (!url) return;
        if (this.connections[url]) return; // Already exists

        const wsManager = new WebSocketManager(url, this.authToken);
        
        wsManager.onConnectionChange = (state, detail = {}) => {
            this.connectionStates[url] = { state, detail };
            if (this.onConnectionChange) {
                this.onConnectionChange(url, state, detail);
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
            delete this.connectionStates[url];
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
                    // Deprecated: GFS daemon is headless UDP on 41458, not WS 8088
                    if (typeof url === 'string' && (url.includes(':8088') || url.endsWith('/8088'))) return;
                    this.addConnection(url);
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
        const type = message.msg_type || message.type;
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
                console.info("SEND", {
                    target_id: message.target_id,
                    action: message.action,
                    cmd_id: message.cmd_id,
                    timestamp: message.timestamp,
                    websocketUrl: ws.url,
                });
                const success = ws.send(message);
                if (success) sent = true;
            }
        });
        return sent;
    }

    sendTo(url, message) {
        const normUrl = this.normalizeConnectionUrl(url);
        const ws = (normUrl && this.connections[normUrl]) || this.connections[url];
        if (ws && ws.connected) {
            console.info("SEND_TO", {
                target_url: ws.url,
                type: message.type || message.msg_type,
                timestamp: message.timestamp,
            });
            return ws.send(message);
        }
        return false;
    }

    disconnectAll() {
        Object.values(this.connections).forEach(ws => ws.disconnect());
        Object.keys(this.connectionStates).forEach(url => {
            this.connectionStates[url] = { state: 'OFFLINE', detail: {} };
        });
    }
}
