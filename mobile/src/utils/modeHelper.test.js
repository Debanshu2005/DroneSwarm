import { resolveModeOptions, resolveModeCommand, DEFAULT_PX4_MODES } from './modeHelper.js';
import { CommandAction } from '../protocol/messages.js';

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

console.log("Running Mode Dropdown Helper Tests...");

// 1. Fallback when available_modes is missing or empty
const fallbackEmpty = resolveModeOptions(null, "POSCTL");
assert(
  JSON.stringify(fallbackEmpty) === JSON.stringify(DEFAULT_PX4_MODES),
  `Expected default PX4 modes when available_modes is null, got: ${JSON.stringify(fallbackEmpty)}`
);

const fallbackArray = resolveModeOptions([], "HOLD");
assert(
  JSON.stringify(fallbackArray) === JSON.stringify(DEFAULT_PX4_MODES),
  `Expected default PX4 modes when available_modes is empty, got: ${JSON.stringify(fallbackArray)}`
);

// 2. ArduPilot available modes
const arduModes = ["STABILIZE", "ACRO", "ALT_HOLD", "GUIDED", "LOITER", "RTL", "LAND", "POSHOLD", "BRAKE", "SMART_RTL"];
const resolvedArdu = resolveModeOptions(arduModes, "LOITER");
assert(
  JSON.stringify(resolvedArdu) === JSON.stringify(arduModes),
  "ArduPilot modes should match available modes exactly when current is included"
);

// 3. Current mode included if not in available_modes (e.g. MANUAL or TAKEOFF)
const withUnusualCurrent = resolveModeOptions(arduModes, "AUTO");
assert(
  withUnusualCurrent.includes("AUTO"),
  "Unsettable current mode 'AUTO' must be included so dropdown displays it"
);
assert(
  withUnusualCurrent[withUnusualCurrent.length - 1] === "AUTO",
  "Current mode should be appended to options"
);

// 4. Command resolution: LAND dispatches CommandAction.LAND
const landCmd = resolveModeCommand("LAND");
assert(landCmd.action === CommandAction.LAND, "LAND must route to CommandAction.LAND");
assert(landCmd.params === null, "LAND params should be null");

// 5. Command resolution: RTL dispatches CommandAction.RTL
const rtlCmd = resolveModeCommand("RTL");
assert(rtlCmd.action === CommandAction.RTL, "RTL must route to CommandAction.RTL");
assert(rtlCmd.params === null, "RTL params should be null");

const srtlCmd = resolveModeCommand("SMART_RTL");
assert(srtlCmd.action === CommandAction.RTL, "SMART_RTL must route to CommandAction.RTL");

// 6. Other modes dispatch CommandAction.SET_MODE with params
const loiterCmd = resolveModeCommand("LOITER");
assert(loiterCmd.action === CommandAction.SET_MODE, "LOITER must route to CommandAction.SET_MODE");
assert(loiterCmd.params.mode === "LOITER", "LOITER params must specify mode");

const posctlCmd = resolveModeCommand("POSCTL");
assert(posctlCmd.action === CommandAction.SET_MODE, "POSCTL must route to CommandAction.SET_MODE");
assert(posctlCmd.params.mode === "POSCTL", "POSCTL params must specify mode");

console.log("All mode helper tests passed successfully!");
