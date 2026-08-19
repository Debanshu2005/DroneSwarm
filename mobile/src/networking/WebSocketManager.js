export class WebSocketManager {
    constructor(url = "ws://localhost:8080") {
        this.url = url;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.listeners = {};
        this.connected = false;
        this.onConnectionChange = null;
        this.intentionallyClosed = false;
    }

    connect() {
        console.log(`Connecting to WebSocket at ${this.url}`);
        this.intentionallyClosed = false;
        
        // Android / Capacitor / Secure context check
        const isSecureContext = window.location.protocol === 'https:';
        if (isSecureContext && this.url.startsWith('ws://')) {
            // In a Capacitor app (Android), https://localhost blocks ws://
            console.error("Insecure WebSocket / configure secure relay");
            if (this.onConnectionChange) this.onConnectionChange("CONNECTION_ERROR");
            return;
        }

        try {
            this.ws = new WebSocket(this.url);
        } catch (e) {
            console.error("Failed to initialize WebSocket:", e);
            this.connected = false;
            if (this.onConnectionChange) this.onConnectionChange("CONNECTION_ERROR");
            
            // Reconnect logic on initialization failure
            let timeout = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 10000);
            setTimeout(() => {
                this.reconnectAttempts++;
                this.connect();
            }, timeout);
            return;
        }

        this.ws.onopen = () => {
            console.log("WebSocket connected");
            this.connected = true;
            this.reconnectAttempts = 0;
            if (this.onConnectionChange) this.onConnectionChange("CONNECTED");
        };

        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                this._dispatch(message);
            } catch (e) {
                console.error("Failed to parse incoming WebSocket message:", e);
            }
        };

        this.ws.onclose = () => {
            console.log("WebSocket disconnected");
            this.connected = false;
            if (this.onConnectionChange) this.onConnectionChange("DISCONNECTED");
            
            if (this.intentionallyClosed) return;
            
            // Reconnect logic
            let timeout = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 10000);
            setTimeout(() => {
                this.reconnectAttempts++;
                this.connect();
            }, timeout);
        };

        this.ws.onerror = (err) => {
            console.error("WebSocket error:", err);
            // DO NOT THROW HERE. Browser will close the socket and trigger onclose.
            // But we must not crash.
        };
    }
    
    disconnect() {
        this.intentionallyClosed = true;
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    subscribe(messageType, callback) {
        if (!this.listeners[messageType]) {
            this.listeners[messageType] = [];
        }
        this.listeners[messageType].push(callback);
    }

    unsubscribe(messageType, callback) {
        if (this.listeners[messageType]) {
            this.listeners[messageType] = this.listeners[messageType].filter(cb => cb !== callback);
        }
    }

    _dispatch(message) {
        const type = message.msg_type;
        if (this.listeners[type]) {
            this.listeners[type].forEach(cb => cb(message));
        }
        // Also dispatch to a generic 'all' listener if needed
        if (this.listeners['*']) {
            this.listeners['*'].forEach(cb => cb(message));
        }
    }

    send(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
            return true;
        } else {
            console.warn("Attempted to send message while WebSocket is not connected.");
            return false;
        }
    }
}
