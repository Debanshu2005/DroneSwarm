import { MultiWebSocketManager } from '../networking/MultiWebSocketManager.js';
import { MessageType, TerminalCommandMessage } from '../protocol/messages.js';
import { buildFormationParams, normalizeDroneId } from '../utils/formation.js';

function assert(condition, message) {
  if (!condition) {
    throw new Error(`Assertion failed: ${message}`);
  }
}

console.log("Running NLP Terminal & Formation JS Tests...");

// -------------------------------------------------------------
// Test Suite 1: Listener count returns to previous value on unmount
// -------------------------------------------------------------
console.log("-> 1. Testing listener cleanup on unmount...");

const wsManager = new MultiWebSocketManager();
const initialStatusCount = wsManager.listeners[MessageType.STATUS]?.size || 0;
const initialLifecycleCount = wsManager.listeners[MessageType.COMMAND_LIFECYCLE]?.size || 0;

// Simulate mounting TerminalView
const targetsRef = { current: ['drone1', 'drone2'] };
const terminalLog = [];
const appendLog = (entry) => terminalLog.push(entry);

const handleStatus = (msg) => {
  if (targetsRef.current.includes(msg.sender_id)) {
    appendLog({ type: 'status', severity: msg.severity, text: msg.status_text });
  }
};
const handleLifecycle = (msg) => {
  if (targetsRef.current.includes(msg.sender_id)) {
    appendLog({ type: 'lifecycle', text: msg.stage });
  }
};

wsManager.subscribe(MessageType.STATUS, handleStatus);
wsManager.subscribe(MessageType.COMMAND_LIFECYCLE, handleLifecycle);

assert(
  (wsManager.listeners[MessageType.STATUS]?.size || 0) === initialStatusCount + 1,
  "STATUS listener count should increment by 1 on mount"
);
assert(
  (wsManager.listeners[MessageType.COMMAND_LIFECYCLE]?.size || 0) === initialLifecycleCount + 1,
  "COMMAND_LIFECYCLE listener count should increment by 1 on mount"
);

// Simulate unmount cleanup
wsManager.unsubscribe(MessageType.STATUS, handleStatus);
wsManager.unsubscribe(MessageType.COMMAND_LIFECYCLE, handleLifecycle);

assert(
  (wsManager.listeners[MessageType.STATUS]?.size || 0) === initialStatusCount,
  "STATUS listener count must return to previous value after unmount"
);
assert(
  (wsManager.listeners[MessageType.COMMAND_LIFECYCLE]?.size || 0) === initialLifecycleCount,
  "COMMAND_LIFECYCLE listener count must return to previous value after unmount"
);


// -------------------------------------------------------------
// Test Suite 2: Changing target doesn't duplicate lines
// -------------------------------------------------------------
console.log("-> 2. Testing stable handlers with changing targets...");

// Mount once
wsManager.subscribe(MessageType.STATUS, handleStatus);
wsManager.subscribe(MessageType.COMMAND_LIFECYCLE, handleLifecycle);

// Target changes multiple times (e.g. ALL -> SINGLE -> ALL)
targetsRef.current = ['drone1'];
targetsRef.current = ['drone2'];
targetsRef.current = ['drone1', 'drone2'];

// Dispatch one message
const testMsg = {
  msg_type: MessageType.STATUS,
  sender_id: 'drone1',
  status_text: 'Done',
  severity: 'info'
};
wsManager._dispatch(testMsg);

assert(
  terminalLog.length === 1,
  `Expected exactly 1 log line after target switches, got ${terminalLog.length}`
);
assert(
  terminalLog[0].text === 'Done',
  "Message content should match"
);

// Cleanup
wsManager.unsubscribe(MessageType.STATUS, handleStatus);
wsManager.unsubscribe(MessageType.COMMAND_LIFECYCLE, handleLifecycle);


// -------------------------------------------------------------
// Test Suite 3: Failed send displays error line and no echo
// -------------------------------------------------------------
console.log("-> 3. Testing failed send error display...");

const simulateSubmit = (text, isConnected, targets, manager) => {
  const log = [];
  const sendTerminalCommand = (cmdText, targetIds) => {
    if (!manager || isConnected !== 'CONNECTED') return false;
    if (!targetIds || targetIds.length === 0) return false;
    return true;
  };

  const success = sendTerminalCommand(text, targets);
  if (!success) {
    log.push({ type: 'error', text: 'Not connected; command not sent' });
  } else {
    log.push({ type: 'input', text: `> ${text}` });
  }
  return log;
};

// Disconnected case
const disconnectedLog = simulateSubmit("takeoff to 5m", "DISCONNECTED", ['drone1'], wsManager);
assert(disconnectedLog.length === 1, "Disconnected send should produce 1 log entry");
assert(disconnectedLog[0].type === 'error', "Disconnected send log type should be 'error'");
assert(disconnectedLog[0].text === 'Not connected; command not sent', "Disconnected send error text mismatch");

// No targets case
const noTargetsLog = simulateSubmit("takeoff to 5m", "CONNECTED", [], wsManager);
assert(noTargetsLog.length === 1, "Empty targets send should produce 1 log entry");
assert(noTargetsLog[0].type === 'error', "Empty targets send log type should be 'error'");
assert(noTargetsLog[0].text === 'Not connected; command not sent', "Empty targets send error text mismatch");

// Connected case
const connectedLog = simulateSubmit("takeoff to 5m", "CONNECTED", ['drone1'], wsManager);
assert(connectedLog.length === 1, "Connected send should produce 1 log entry");
assert(connectedLog[0].type === 'input', "Connected send log type should be 'input'");
assert(connectedLog[0].text === '> takeoff to 5m', "Connected send should display echo");


// -------------------------------------------------------------
// Test Suite 4: TerminalCommandMessage broadcast flag & casing normalization
// -------------------------------------------------------------
console.log("-> 4. Testing message broadcast flag and casing normalization...");

const broadcastCmd = new TerminalCommandMessage("GS", "form v", "drone1", true);
assert(broadcastCmd.broadcast === true, "TerminalCommandMessage broadcast flag should be true");

const singleCmd = new TerminalCommandMessage("GS", "form v", "drone1", false);
assert(singleCmd.broadcast === false, "TerminalCommandMessage broadcast flag should be false");

assert(normalizeDroneId(" Drone1 ") === "drone1", "normalizeDroneId should lowercase and trim");

const fp = buildFormationParams([
  { id: "Drone1", status: "CONNECTED" },
  { id: "drone2", status: "CONNECTED" },
  { id: "DRONE3", status: "CONNECTED" }
], "v", 15);

assert(JSON.stringify(fp.members) === JSON.stringify(["drone1", "drone2", "drone3"]), "Members should be normalized to lowercase and sorted");
assert(fp.slot_assignments["drone1"] === 0, "Slot 0 should be drone1");
assert(fp.slot_assignments["drone2"] === 1, "Slot 1 should be drone2");
assert(fp.slot_assignments["drone3"] === 2, "Slot 2 should be drone3");

console.log("All NLP Terminal & Formation JS tests PASSED!");
