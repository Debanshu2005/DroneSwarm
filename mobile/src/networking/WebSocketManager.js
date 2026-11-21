export class WebSocketManager {
    constructor(url = "ws://localhost:8080", authToken = "") {
        this.url = url;
        this.authToken = authToken || "";
        this.ws = null;
        this.reconnectAttempts = 0;
        this.listeners = {};
        this.connected = false;
        this.onConnectionChange = null;
        this.intentionallyClosed = false;
        this.connectionTimeout = null;
        this.authGraceTimer = null;
        this.isConnecting = false;
        this.lastCloseCode = null;
        this.lastCloseReason = "";
        this.lastError = "";
    }

    setAuthToken(authToken = "") {
        const changed = this.authToken !== authToken;
        this.authToken = authToken || "";
        if (changed) {
            // Allow retry after token is changed: clear previous auth errors and connect/reconnect
            this.lastError = "";
            this.lastCloseReason = "";
            this.lastCloseCode = null;
            if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
            this.reconnectAttempts = 0;
            if (this.ws) {
                this.ws.close();
            } else {
                this.connect();
            }
        }
    }

    _markConnected() {
        if (this.authGraceTimer) {
            clearTimeout(this.authGraceTimer);
            this.authGraceTimer = null;
        }
        if (!this.connected) {
            this.connected = true;
            this.reconnectAttempts = 0;
            if (this.onConnectionChange) this.onConnectionChange("CONNECTED");
        }
    }

    connect() {
        const WS = typeof WebSocket !== 'undefined' ? WebSocket : (globalThis.WebSocket || null);
        const openState = WS ? WS.OPEN : 1;
        const connectingState = WS ? WS.CONNECTING : 0;

        if (this.isConnecting || (this.ws && (this.ws.readyState === openState || this.ws.readyState === connectingState))) {
            console.log("WebSocket connection already in progress or established.");
            return;
        }

        console.log(`Connecting to WebSocket at ${this.url}`);
        this.intentionallyClosed = false;
        this.isConnecting = true;
        this.lastError = "";
        
        // Android / Capacitor / Secure context check
        const isSecureContext = typeof window !== 'undefined' && window.location?.protocol === 'https:';
        if (isSecureContext && this.url.startsWith('ws://') && !this.url.includes('localhost') && !this.url.includes('127.0.0.1')) {
            console.warn("Insecure WebSocket might be blocked in secure context");
        }

        if (this.onConnectionChange) this.onConnectionChange("CONNECTING");

        try {
            if (!WS) throw new Error("WebSocket implementation not found");
            this.ws = new WS(this.url);
        } catch (e) {
            console.error("Failed to initialize WebSocket:", e);
            this.handleDisconnect();
            return;
        }

        // Connection timeout
        this.connectionTimeout = setTimeout(() => {
            const currentWS = typeof WebSocket !== 'undefined' ? WebSocket : (globalThis.WebSocket || null);
            const currentOpen = currentWS ? currentWS.OPEN : 1;
            if (this.ws && this.ws.readyState !== currentOpen) {
                console.error("WebSocket connection timeout.");
                this.ws.close();
            }
        }, 5000);

        this.ws.onopen = () => {
            clearTimeout(this.connectionTimeout);
            this.isConnecting = false;
            console.log(`WebSocket open: ${this.url}`);
            if (!this.authToken) {
                // Without a token: onopen must set connected = true and fire onConnectionChange("CONNECTED") immediately
                this._markConnected();
            } else {
                // With a token: send AUTH on open.
                this.ws.send(JSON.stringify({ type: "AUTH", token: this.authToken }));
                
                // (c) ~1500 ms after open with the socket still OPEN and no close event: mark CONNECTED
                if (this.authGraceTimer) clearTimeout(this.authGraceTimer);
                this.authGraceTimer = setTimeout(() => {
                    const currentWS = typeof WebSocket !== 'undefined' ? WebSocket : (globalThis.WebSocket || null);
                    const currentOpen = currentWS ? currentWS.OPEN : 1;
                    if (this.ws && this.ws.readyState === currentOpen && !this.connected) {
                        console.log("Auth grace timer expired with socket open; marking CONNECTED");
                        this._markConnected();
                    }
                }, 1500);
            }
        };

        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                if (!message || typeof message !== 'object' || (!message.msg_type && !message.type)) {
                    throw new Error("Invalid message format: missing msg_type or type");
                }
                const msgType = message.msg_type || message.type;
                const isAuthAck = msgType === "AUTH_ACK" || message.type === "AUTH_ACK";

                // Mark CONNECTED on the first of:
                // (a) an AUTH_ACK message
                // (b) any other valid message
                if (!this.connected) {
                    this._markConnected();
                }

                // (a) an AUTH_ACK message (consume it, don't dispatch it to handlers)
                if (!isAuthAck) {
                    this._dispatch(message);
                }
            } catch (e) {
                console.error("Failed to parse incoming WebSocket message:", e, event.data);
            }
        };

        this.ws.onclose = (event) => {
            if (this.authGraceTimer) {
                clearTimeout(this.authGraceTimer);
                this.authGraceTimer = null;
            }
            const reason = event.reason || "";
            const isAuthFailure = event.code === 4001 || reason.toLowerCase().includes("auth");
            console.log(`WebSocket disconnected${event.code ? ` (${event.code})` : ""}${reason ? `: ${reason}` : ""}`);
            const failureReason = isAuthFailure ? (reason || "Relay authentication rejected") : reason;
            this.handleDisconnect(isAuthFailure ? "AUTH_FAILED" : "DISCONNECTED", {
                code: event.code,
                reason: failureReason
            });
        };

        this.ws.onerror = (err) => {
            console.error("WebSocket error:", err);
            if (this.authGraceTimer) {
                clearTimeout(this.authGraceTimer);
                this.authGraceTimer = null;
            }
            // DO NOT THROW HERE. Browser will close the socket and trigger onclose.
        };
    }
    
    handleDisconnect(state = "DISCONNECTED", detail = {}) {
        clearTimeout(this.connectionTimeout);
        if (this.authGraceTimer) {
            clearTimeout(this.authGraceTimer);
            this.authGraceTimer = null;
        }
        this.isConnecting = false;
        this.connected = false;
        this.lastCloseCode = detail.code || null;
        this.lastCloseReason = detail.reason || "";
        if (state === "AUTH_FAILED") {
            this.lastError = this.lastCloseReason || "Relay authentication failed";
        }
        this.ws = null;
        
        if (this.onConnectionChange) this.onConnectionChange(state, {
            code: this.lastCloseCode,
            reason: this.lastCloseReason,
            error: this.lastError
        });
        
        if (this.intentionallyClosed || state === "AUTH_FAILED") return;
        
        // Reconnect logic with exponential backoff (max 10s)
        let timeout = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 10000);
        console.log(`Will attempt reconnect in ${timeout}ms (Attempt ${this.reconnectAttempts + 1})`);
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => {
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
        if (this.authGraceTimer) {
            clearTimeout(this.authGraceTimer);
            this.authGraceTimer = null;
        }
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
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
        const WS = typeof WebSocket !== 'undefined' ? WebSocket : (globalThis.WebSocket || null);
        const openState = WS ? WS.OPEN : 1;
        if (this.ws && this.ws.readyState === openState) {
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
