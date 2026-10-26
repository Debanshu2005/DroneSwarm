import { resolveModeOptions, resolveModeCommand, DEFAULT_PX4_MODES } from './modeHelper.js';
import { CommandAction } from '../protocol/messages.js';

let testFn = (name, fn) => {
  try {
    fn();
    console.log(`  ✓ ${name}`);
  } catch (err) {
    console.error(`  ✗ ${name}`);
    throw err;
  }
};

let describeFn = (name, fn) => {
  console.log(name);
  fn();
};

try {
  // If running in vitest environment
  const vitest = await import('vitest');
  if (vitest && vitest.test && vitest.describe) {
    testFn = vitest.test;
    describeFn = vitest.describe;
  }
} catch {
  // Running in bare node
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

describeFn('modeHelper dropdown and command routing', () => {
  testFn('fallback when available_modes is null or empty', () => {
    const fallbackNull = resolveModeOptions(null, "POSCTL");
    assert(
      JSON.stringify(fallbackNull) === JSON.stringify(DEFAULT_PX4_MODES),
      `Expected default PX4 modes when available_modes is null, got: ${JSON.stringify(fallbackNull)}`
    );

    const fallbackArray = resolveModeOptions([], "HOLD");
    assert(
      JSON.stringify(fallbackArray) === JSON.stringify(DEFAULT_PX4_MODES),
      `Expected default PX4 modes when available_modes is empty, got: ${JSON.stringify(fallbackArray)}`
    );
  });

  testFn('ArduPilot available modes match exact list', () => {
    const arduModes = ["STABILIZE", "ACRO", "ALT_HOLD", "GUIDED", "LOITER", "RTL", "LAND", "POSHOLD", "BRAKE", "SMART_RTL"];
    const resolvedArdu = resolveModeOptions(arduModes, "LOITER");
    assert(
      JSON.stringify(resolvedArdu) === JSON.stringify(arduModes),
      "ArduPilot modes should match available modes exactly when current is included"
    );
  });

  testFn('current mode appended if not in available_modes', () => {
    const arduModes = ["STABILIZE", "ACRO", "ALT_HOLD", "GUIDED", "LOITER", "RTL", "LAND", "POSHOLD", "BRAKE", "SMART_RTL"];
    const withUnusualCurrent = resolveModeOptions(arduModes, "AUTO");
    assert(
      withUnusualCurrent.includes("AUTO"),
      "Unsettable current mode 'AUTO' must be included so dropdown displays it"
    );
    assert(
      withUnusualCurrent[withUnusualCurrent.length - 1] === "AUTO",
      "Current mode should be appended to options"
    );
  });

  testFn('command resolution: LAND routes to CommandAction.LAND', () => {
    const landCmd = resolveModeCommand("LAND");
    assert(landCmd.action === CommandAction.LAND, "LAND must route to CommandAction.LAND");
    assert(landCmd.params === null, "LAND params should be null");
  });

  testFn('command resolution: RTL and SMART_RTL route to CommandAction.RTL', () => {
    const rtlCmd = resolveModeCommand("RTL");
    assert(rtlCmd.action === CommandAction.RTL, "RTL must route to CommandAction.RTL");
    assert(rtlCmd.params === null, "RTL params should be null");

    const srtlCmd = resolveModeCommand("SMART_RTL");
    assert(srtlCmd.action === CommandAction.RTL, "SMART_RTL must route to CommandAction.RTL");
  });

  testFn('command resolution: other modes route to CommandAction.SET_MODE with params', () => {
    const loiterCmd = resolveModeCommand("LOITER");
    assert(loiterCmd.action === CommandAction.SET_MODE, "LOITER must route to CommandAction.SET_MODE");
    assert(loiterCmd.params.mode === "LOITER", "LOITER params must specify mode");

    const posctlCmd = resolveModeCommand("POSCTL");
    assert(posctlCmd.action === CommandAction.SET_MODE, "POSCTL must route to CommandAction.SET_MODE");
    assert(posctlCmd.params.mode === "POSCTL", "POSCTL params must specify mode");
  });
});
