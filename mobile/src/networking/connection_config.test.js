import { BUILT_IN_DEFAULT_WS_URL, isCapacitorPlatform, shouldAutoConnectSimRelays } from './connection_utils.js';
import { MultiWebSocketManager } from './MultiWebSocketManager.js';
import { WebSocketManager } from './WebSocketManager.js';

function assert(condition, message) {
  if (!condition) {
    throw new Error(`Assertion failed: ${message}`);
  }
}

console.log("Running Connection Configuration & Auth Flow Tests...");

// -------------------------------------------------------------
// Test Suite 1: URL Selection & Sim Auto-Connect Rules
// -------------------------------------------------------------
console.log("-> 1. Testing URL Selection & Sim Auto-Connect gating...");

// Mock global window and localStorage
const mockStorage = {};
globalThis.localStorage = {
  getItem: (key) => (key in mockStorage ? mockStorage[key] : null),
  setItem: (key, val) => { mockStorage[key] = String(val); },
  removeItem: (key) => { delete mockStorage[key]; },
  clear: () => { Object.keys(mockStorage).forEach(k => delete mockStorage[k]); }
};

globalThis.window = {
  location: {
    hostname: 'localhost',
    protocol: 'http:',
    search: ''
  }
};

// 1.1 Default URL with ?sim=1 -> true
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "?sim=1", {}),
  "Default URL with ?sim=1 query should auto-connect sim relays"
);

// 1.2 Default URL with ?sim=true -> true
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "?sim=true", {}),
  "Default URL with ?sim=true query should auto-connect sim relays"
);

// 1.3 Default URL with VITE_SIM_MODE='true' -> true
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", { VITE_SIM_MODE: 'true' }),
  "Default URL with VITE_SIM_MODE='true' should auto-connect sim relays"
);

// 1.4 Default URL with VITE_SIM_MODE=true (boolean) -> true
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", { VITE_SIM_MODE: true }),
  "Default URL with boolean VITE_SIM_MODE=true should auto-connect sim relays"
);

// 1.5 Default URL without sim param or sim env -> false
assert(
  !shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", {}),
  "Default URL without sim flags should NOT auto-connect sim relays"
);

// 1.6 Custom URL ending in :8080 must NEVER be treated as default
const customPort8080 = "ws://192.168.1.50:8080";
assert(
  !shouldAutoConnectSimRelays(customPort8080, "?sim=1", { VITE_SIM_MODE: 'true' }),
  "Custom URL ending in :8080 must NEVER be treated as default, even with sim flags"
);

const customDomain8080 = "ws://my-custom-drone.local:8080";
assert(
  !shouldAutoConnectSimRelays(customDomain8080, "?sim=1", {}),
  "Arbitrary custom domain ending in :8080 must not trigger sim auto-connect"
);

// 1.7 Capacitor detection: never auto-switch to sim on Capacitor platform
globalThis.window.Capacitor = { isNativePlatform: () => true };
assert(isCapacitorPlatform(), "Capacitor platform should be detected");
assert(
  !shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "?sim=1", { VITE_SIM_MODE: 'true' }),
  "On Capacitor platform, sim auto-connect must NEVER trigger even with ?sim=1 or VITE_SIM_MODE"
);

delete globalThis.window.Capacitor;
globalThis.window.location.protocol = "capacitor:";
assert(isCapacitorPlatform(), "capacitor: protocol should identify Capacitor");
assert(
  !shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "?sim=1", {}),
  "On capacitor: protocol, sim auto-connect must return false"
);

// Restore normal browser mock
globalThis.window.location.protocol = "http:";
assert(!isCapacitorPlatform(), "Regular browser window should not be Capacitor");

console.log("   ✓ URL selection & sim auto-connect rules passed!");

// -------------------------------------------------------------
// Test Suite 2: MultiWebSocketManager Saved Connection Merging & Reset
// -------------------------------------------------------------
console.log("-> 2. Testing Saved Connection Merging & Reset...");

// Mock WebSocket class for MultiWebSocketManager
class MockWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1; // OPEN
    this.sentMessages = [];
  }
  send(data) {
    this.sentMessages.push(data);
  }
  close(code, reason) {
    this.readyState = 3; // CLOSED
    if (this.onclose) this.onclose({ code: code || 1000, reason: reason || "" });
  }
}
globalThis.WebSocket = MockWebSocket;

// 2.1 Set up initial saved connections in storage
localStorage.setItem("PhoneOS_Swarm_Connections", JSON.stringify([
  "ws://192.168.1.101:8080",
  "ws://192.168.1.102:8080"
]));

const multiManager = new MultiWebSocketManager();
multiManager.loadSavedConnections();

assert(Object.keys(multiManager.connections).length === 2, "Should have loaded 2 saved connections");
assert(multiManager.connections["ws://192.168.1.101:8080"], "Connection 101 should exist");
assert(multiManager.connections["ws://192.168.1.102:8080"], "Connection 102 should exist");

// 2.2 User sets wsUrl in Settings: must add and retain that connection alongside saved
const userConfiguredUrl = "ws://192.168.1.200:8080";
multiManager.addConnection(userConfiguredUrl);

assert(Object.keys(multiManager.connections).length === 3, "Should now have 3 connections");
assert(multiManager.connections[userConfiguredUrl], "User configured connection must be present");

// Verify storage was updated to include all 3
const savedUrls = JSON.parse(localStorage.getItem("PhoneOS_Swarm_Connections"));
assert(savedUrls.includes(userConfiguredUrl), "Storage must persist newly added user connection");
assert(savedUrls.includes("ws://192.168.1.101:8080"), "Storage must keep previous saved connection");

// 2.3 Reset connections: clears PhoneOS_Swarm_Connections and disconnects all
let reportedStatus = null;
multiManager.onConnectionChange = (url, state) => {
  reportedStatus = state;
};

multiManager.resetConnections();

assert(Object.keys(multiManager.connections).length === 0, "All connections must be removed");
assert(localStorage.getItem("PhoneOS_Swarm_Connections") === null, "Storage key must be cleared");
assert(reportedStatus === "DISCONNECTED", "Connection state change must report DISCONNECTED");

console.log("   ✓ Saved connection merging and reset passed!");

// -------------------------------------------------------------
// Test Suite 3: WebSocketManager Auth ACK Flow & Retry
// -------------------------------------------------------------
console.log("-> 3. Testing WebSocketManager Auth ACK, Failure Reason & Retry...");

// 3.1 Auth with token: must wait for AUTH_ACK before reporting CONNECTED
let connectionState = null;
let lastDetail = null;

const wsManager = new WebSocketManager("ws://127.0.0.1:8080", "secret_token_123");
wsManager.onConnectionChange = (state, detail) => {
  connectionState = state;
  lastDetail = detail;
};

wsManager.connect();
assert(connectionState === "CONNECTING", "Initial state must be CONNECTING");
assert(wsManager.connected === false, "connected property must be false");

// Trigger onopen
wsManager.ws.onopen();
assert(wsManager.ws.sentMessages.length === 1, "Should have sent AUTH message");
const authMsg = JSON.parse(wsManager.ws.sentMessages[0]);
assert(authMsg.type === "AUTH" && authMsg.token === "secret_token_123", "AUTH message payload matches token");

// Crucial: socket is open but auth not yet confirmed -> still NOT connected!
assert(wsManager.connected === false, "Must NOT report CONNECTED before receiving AUTH_ACK");
assert(connectionState === "CONNECTING", "State must remain CONNECTING");

// Send non-auth message (e.g. heartbeat or telemetry) -> must NOT trigger CONNECTED
wsManager.ws.onmessage({ data: JSON.stringify({ type: "heartbeat", status: "active" }) });
assert(wsManager.connected === false, "Must NOT report CONNECTED on arbitrary message when authToken is set");

// Now deliver AUTH_ACK
wsManager.ws.onmessage({ data: JSON.stringify({ type: "AUTH_ACK", status: "ok" }) });
assert(wsManager.connected === true, "Must report connected === true upon AUTH_ACK");
assert(connectionState === "CONNECTED", "Connection state must transition to CONNECTED");

// 3.2 No token needed: first message immediately transitions to CONNECTED
const wsManagerNoToken = new WebSocketManager("ws://127.0.0.1:8080", "");
let noTokenState = null;
wsManagerNoToken.onConnectionChange = (state) => { noTokenState = state; };
wsManagerNoToken.connect();
wsManagerNoToken.ws.onopen();
assert(wsManagerNoToken.connected === false, "Unconnected before first message");

// First message arrives
wsManagerNoToken.ws.onmessage({ data: JSON.stringify({ type: "telemetry", altitude: 10 }) });
assert(wsManagerNoToken.connected === true, "First message when no token connects immediately");
assert(noTokenState === "CONNECTED", "State transitioned to CONNECTED");

// 3.3 Auth failure: code 4001 or reason contains auth -> reports AUTH_FAILED with reason
const wsFailManager = new WebSocketManager("ws://127.0.0.1:8080", "wrong_token");
let failState = null;
let failDetail = null;
wsFailManager.onConnectionChange = (state, detail) => {
  failState = state;
  failDetail = detail;
};
wsFailManager.connect();
wsFailManager.ws.onopen();

// Server closes with code 4001
wsFailManager.ws.close(4001, "Relay authentication rejected: invalid credentials");
assert(failState === "AUTH_FAILED", "Must report AUTH_FAILED state on 4001");
assert(failDetail.reason.includes("invalid credentials"), "Failure detail must expose reason for UI");
assert(wsFailManager.lastError.includes("invalid credentials"), "lastError must be recorded");

// 3.4 Token changed: triggers immediate retry
let retryInitiated = false;
wsFailManager.connect = () => { retryInitiated = true; };
wsFailManager.setAuthToken("new_correct_token");
assert(wsFailManager.authToken === "new_correct_token", "Auth token updated");
assert(retryInitiated, "Updating auth token after failure must trigger connection retry");

console.log("   ✓ WebSocketManager Auth ACK, failure reporting, and token retry passed!");

console.log("All Connection & Auth Tests Passed Successfully! (100% OK)");
