export class WebSocketManager {
    constructor(url = "ws://localhost:8080") {
        this.url = url;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.listeners = {};
        this.connected = false;
        this.onConnectionChange = null;
        this.intentionallyClosed = false;
        this.connectionTimeout = null;
        this.isConnecting = false;
    }

    connect() {
        if (this.isConnecting || (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING))) {
            console.log("WebSocket connection already in progress or established.");
            return;
        }

        console.log(`Connecting to WebSocket at ${this.url}`);
        this.intentionallyClosed = false;
        this.isConnecting = true;
        
        // Android / Capacitor / Secure context check
        const isSecureContext = window.location.protocol === 'https:';
        if (isSecureContext && this.url.startsWith('ws://') && !this.url.includes('localhost') && !this.url.includes('127.0.0.1')) {
            console.warn("Insecure WebSocket might be blocked in secure context");
        }

        if (this.onConnectionChange) this.onConnectionChange("CONNECTING");

        try {
            this.ws = new WebSocket(this.url);
        } catch (e) {
            console.error("Failed to initialize WebSocket:", e);
            this.handleDisconnect();
            return;
        }

        // Connection timeout
        this.connectionTimeout = setTimeout(() => {
            if (this.ws && this.ws.readyState !== WebSocket.OPEN) {
                console.error("WebSocket connection timeout.");
                this.ws.close();
            }
        }, 5000);

        this.ws.onopen = () => {
            clearTimeout(this.connectionTimeout);
            this.isConnecting = false;
            console.log("WebSocket connected");
            this.connected = true;
            this.reconnectAttempts = 0;
            if (this.onConnectionChange) this.onConnectionChange("CONNECTED");
        };

        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                if (!message || typeof message !== 'object' || !message.msg_type) {
                    throw new Error("Invalid message format: missing msg_type");
                }
                this._dispatch(message);
            } catch (e) {
                console.error("Failed to parse incoming WebSocket message:", e, event.data);
            }
        };

        this.ws.onclose = () => {
            console.log("WebSocket disconnected");
            this.handleDisconnect();
        };

        this.ws.onerror = (err) => {
            console.error("WebSocket error:", err);
            // DO NOT THROW HERE. Browser will close the socket and trigger onclose.
        };
    }
    
    handleDisconnect() {
        clearTimeout(this.connectionTimeout);
        this.isConnecting = false;
        this.connected = false;
        this.ws = null;
        
        if (this.onConnectionChange) this.onConnectionChange("DISCONNECTED");
        
        if (this.intentionallyClosed) return;
        
        // Reconnect logic with exponential backoff (max 10s)
        let timeout = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 10000);
        console.log(`Will attempt reconnect in ${timeout}ms (Attempt ${this.reconnectAttempts + 1})`);
        
        setTimeout(() => {
            if (!this.intentionallyClosed) {
                this.reconnectAttempts++;
                this.connect();
            }
        }, timeout);
    }

    disconnect() {
        this.intentionallyClosed = true;
        this.isConnecting = false;
        clearTimeout(this.connectionTimeout);
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.connected = false;
        if (this.onConnectionChange) this.onConnectionChange("OFFLINE");
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
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            try {
                this.ws.send(JSON.stringify(message));
                return true;
            } catch (e) {
                console.error("Error sending message:", e);
                return false;
            }
        } else {
            console.warn("Attempted to send message while WebSocket is not connected.");
            return false;
        }
    }
}
