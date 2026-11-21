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

// 1.1 Sim auto-connect on localhost and LAN with default URL (no sim query or env needed)
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", {}, "localhost"),
  "Default URL on localhost must auto-connect sim relays"
);
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", {}, "127.0.0.1"),
  "Default URL on 127.0.0.1 must auto-connect sim relays"
);
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", {}, ""),
  "Default URL on empty hostname (e.g. file:// / dev) must auto-connect sim relays"
);
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", {}, "192.168.1.100"),
  "Default URL on LAN (192.168.*) must auto-connect sim relays"
);
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", {}, "10.0.1.5"),
  "Default URL on LAN (10.*) must auto-connect sim relays"
);
assert(
  shouldAutoConnectSimRelays("", "", {}, "localhost"),
  "Unset/empty URL on localhost must auto-connect sim relays"
);
assert(
  shouldAutoConnectSimRelays(null, "", {}, "localhost"),
  "Null URL on localhost must auto-connect sim relays"
);

// 1.2 Extra triggers: ?sim=1 and VITE_SIM_MODE on external/cloud hostname
const externalHost = "app.phoneos.io";
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "?sim=1", {}, externalHost),
  "Default URL with ?sim=1 query must auto-connect sim relays on external host"
);
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "?sim=true", {}, externalHost),
  "Default URL with ?sim=true query must auto-connect sim relays on external host"
);
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", { VITE_SIM_MODE: 'true' }, externalHost),
  "Default URL with VITE_SIM_MODE='true' must auto-connect sim relays on external host"
);
assert(
  shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", { VITE_SIM_MODE: true }, externalHost),
  "Default URL with boolean VITE_SIM_MODE=true must auto-connect sim relays on external host"
);

// 1.3 External host without sim flags must NOT auto-connect
assert(
  !shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", {}, externalHost),
  "Default URL on external host without sim flags must NOT auto-connect sim relays"
);

// 1.4 Custom URL must NEVER be treated as default, even on localhost or with sim flags
const customPort8080 = "ws://192.168.1.50:8080";
assert(
  !shouldAutoConnectSimRelays(customPort8080, "?sim=1", { VITE_SIM_MODE: 'true' }, "localhost"),
  "Custom URL ending in :8080 must NEVER auto-connect sim relays, even on localhost with sim flags"
);
const customDomain8080 = "ws://my-custom-drone.local:8080";
assert(
  !shouldAutoConnectSimRelays(customDomain8080, "", {}, "localhost"),
  "Arbitrary custom domain must never auto-connect sim relays"
);

// 1.5 Capacitor detection: never auto-connect sims on Capacitor (the phone app)
globalThis.window.Capacitor = { isNativePlatform: () => true };
assert(isCapacitorPlatform(), "Capacitor platform should be detected");
assert(
  !shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "?sim=1", { VITE_SIM_MODE: 'true' }, "localhost"),
  "On Capacitor platform, sim auto-connect must NEVER trigger even on localhost with ?sim=1 or VITE_SIM_MODE"
);

delete globalThis.window.Capacitor;
globalThis.window.location.protocol = "capacitor:";
assert(isCapacitorPlatform(), "capacitor: protocol should identify Capacitor");
assert(
  !shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "?sim=1", {}, "localhost"),
  "On capacitor: protocol, sim auto-connect must return false"
);

globalThis.window.location.protocol = "ionic:";
assert(isCapacitorPlatform(), "ionic: protocol should identify Capacitor");
assert(
  !shouldAutoConnectSimRelays(BUILT_IN_DEFAULT_WS_URL, "", {}, "localhost"),
  "On ionic: protocol, sim auto-connect must return false"
);

// Restore normal browser mock
globalThis.window.location.protocol = "http:";
globalThis.window.location.hostname = "localhost";
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
MockWebSocket.OPEN = 1;
MockWebSocket.CONNECTING = 0;
MockWebSocket.CLOSING = 2;
MockWebSocket.CLOSED = 3;
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
// Test Suite 3: WebSocketManager Auth ACK, Grace Period & Retry Flow
// -------------------------------------------------------------
console.log("-> 3. Testing WebSocketManager Auth Flow, Grace Period & Retry...");

// 3.1 Without a token: onopen must set connected = true and fire onConnectionChange("CONNECTED") immediately
const wsManagerNoToken = new WebSocketManager("ws://127.0.0.1:8080", "");
let noTokenState = null;
wsManagerNoToken.onConnectionChange = (state) => { noTokenState = state; };
wsManagerNoToken.connect();
assert(noTokenState === "CONNECTING", "Must start in CONNECTING state");
assert(wsManagerNoToken.connected === false, "Not connected before onopen");

// onopen fires
wsManagerNoToken.ws.onopen();
assert(wsManagerNoToken.connected === true, "Without token: onopen must set connected = true immediately");
assert(noTokenState === "CONNECTED", "Without token: onopen must fire onConnectionChange('CONNECTED') immediately");
assert(wsManagerNoToken.send({ type: "heartbeat" }) === true, "send() must work immediately when CONNECTED");

// 3.2 With a token + AUTH_ACK: sends AUTH, waits, and marks CONNECTED on AUTH_ACK
let tokenState = null;
const wsManagerToken = new WebSocketManager("ws://127.0.0.1:8080", "secret_token_123");
wsManagerToken.onConnectionChange = (state) => { tokenState = state; };
wsManagerToken.connect();
wsManagerToken.ws.onopen();

assert(wsManagerToken.ws.sentMessages.length === 1, "Should have sent AUTH message on open");
const authMsg = JSON.parse(wsManagerToken.ws.sentMessages[0]);
assert(authMsg.type === "AUTH" && authMsg.token === "secret_token_123", "AUTH payload must match token");
assert(wsManagerToken.connected === false, "With token: must NOT mark connected immediately on open");
assert(tokenState === "CONNECTING", "State must remain CONNECTING awaiting auth");

// AUTH_ACK arrives
let dispatchedAuthAck = false;
wsManagerToken.subscribe("AUTH_ACK", () => { dispatchedAuthAck = true; });
wsManagerToken.ws.onmessage({ data: JSON.stringify({ type: "AUTH_ACK", status: "ok" }) });

assert(wsManagerToken.connected === true, "Must mark connected = true upon AUTH_ACK");
assert(tokenState === "CONNECTED", "Connection state must transition to CONNECTED on AUTH_ACK");
assert(!dispatchedAuthAck, "AUTH_ACK message must be consumed and NOT dispatched to handlers");

// 3.3 With a token + NO AUTH_ACK: connects after grace period (~1500 ms) using fake timers
let fakeTimerCallback = null;
let fakeTimerDelay = 0;
const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;

globalThis.setTimeout = (cb, delay) => {
  if (delay === 1500) {
    fakeTimerCallback = cb;
    fakeTimerDelay = delay;
    return 12345;
  }
  return originalSetTimeout(cb, delay);
};
globalThis.clearTimeout = (id) => {
  if (id === 12345) {
    fakeTimerCallback = null;
  } else {
    originalClearTimeout(id);
  }
};

const wsManagerGrace = new WebSocketManager("ws://127.0.0.1:8080", "legacy_token");
let graceState = null;
wsManagerGrace.onConnectionChange = (state) => { graceState = state; };
wsManagerGrace.connect();
wsManagerGrace.ws.onopen();

assert(wsManagerGrace.connected === false, "Still unconnected while grace period runs");
assert(fakeTimerCallback !== null && fakeTimerDelay === 1500, "Grace timer (~1500ms) must be registered");

// Grace period expires while socket is still open
fakeTimerCallback();
assert(wsManagerGrace.connected === true, "Must mark CONNECTED on grace period expiration for legacy relays");
assert(graceState === "CONNECTED", "State transitioned to CONNECTED after grace period");

// Restore real timers
globalThis.setTimeout = originalSetTimeout;
globalThis.clearTimeout = originalClearTimeout;

// 3.4 With a token + valid message before grace timer: marks CONNECTED immediately
const wsManagerMsgFirst = new WebSocketManager("ws://127.0.0.1:8080", "msg_first_token");
let msgFirstState = null;
let receivedTelemetry = null;
wsManagerMsgFirst.onConnectionChange = (state) => { msgFirstState = state; };
wsManagerMsgFirst.subscribe("telemetry", (msg) => { receivedTelemetry = msg; });
wsManagerMsgFirst.connect();
wsManagerMsgFirst.ws.onopen();

assert(wsManagerMsgFirst.connected === false, "Unconnected before message");
wsManagerMsgFirst.ws.onmessage({ data: JSON.stringify({ type: "telemetry", altitude: 25.5 }) });

assert(wsManagerMsgFirst.connected === true, "Valid non-auth message must mark CONNECTED");
assert(msgFirstState === "CONNECTED", "Must fire CONNECTED state on message");
assert(receivedTelemetry && receivedTelemetry.altitude === 25.5, "Valid message must be dispatched to handlers");

// 3.5 Auth failure: code 4001 gives AUTH_FAILED and clears grace timer
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
assert(wsFailManager.authGraceTimer === null, "Grace timer must be cleared on close");

// 3.6 Token changed: triggers immediate retry
let retryInitiated = false;
wsFailManager.connect = () => { retryInitiated = true; };
wsFailManager.setAuthToken("new_correct_token");
assert(wsFailManager.authToken === "new_correct_token", "Auth token updated");
assert(retryInitiated, "Updating auth token after failure must trigger connection retry");

console.log("   ✓ WebSocketManager Auth ACK, grace period, failure reporting, and token retry passed!");

console.log("All Connection & Auth Tests Passed Successfully! (100% OK)");
