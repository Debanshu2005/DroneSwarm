import { CommandAction } from '../protocol/messages.js';

export const DEFAULT_PX4_MODES = [
  "STABILIZED",
  "ALTCTL",
  "POSCTL",
  "HOLD",
  "OFFBOARD",
  "RTL",
  "LAND"
];

/**
 * Resolves the list of flight modes to display in the mode dropdown.
 * - Uses available_modes if provided by telemetry, otherwise falls back to DEFAULT_PX4_MODES.
 * - Ensures any current mode reported by FC is included in the dropdown so it can be selected/shown.
 * - Never includes non-settable modes unless it is the active one.
 */
export function resolveModeOptions(availableModes, currentMode) {
  const base = (availableModes && Array.isArray(availableModes) && availableModes.length > 0)
    ? [...availableModes]
    : [...DEFAULT_PX4_MODES];

  if (currentMode && !base.includes(currentMode)) {
    base.push(currentMode);
  }
  return base;
}

/**
 * Resolves which command action to dispatch when a mode is selected.
 * LAND and RTL are routed directly to CommandAction.LAND and CommandAction.RTL.
 * All other modes dispatch CommandAction.SET_MODE.
 */
export function resolveModeCommand(selectedMode) {
  if (selectedMode === 'LAND') {
    return { action: CommandAction.LAND, params: null };
  }
  if (selectedMode === 'RTL' || selectedMode === 'SMART_RTL') {
    return { action: CommandAction.RTL, params: null };
  }
  return { action: CommandAction.SET_MODE, params: { mode: selectedMode } };
}
