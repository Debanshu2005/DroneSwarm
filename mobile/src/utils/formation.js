/**
 * mobile/src/utils/formation.js
 *
 * Single source of truth for building formation command params.
 * MUST stay consistent with DroneOS/core/formation_manager.py build_slot_assignments().
 * Both sort drone IDs alphabetically and assign slot = sorted index (slot 0 = anchor).
 *
 * @param {Object[]} drones - Array of drone objects from DroneContext (each has .id, .status)
 * @param {string}   type   - Formation type string (case-insensitive, e.g. "V", "circle")
 * @param {number}   spacing - Desired spacing in metres
 * @returns {{ type: string, spacing: number, members: string[], slot_assignments: Object }}
 */
export function buildFormationParams(drones, type, spacing) {
  // Only include drones that are reachable
  const online = drones.filter(
    (d) => d.status === 'CONNECTED' || d.status === 'DEGRADED'
  );

  // Sort IDs - must match Python: sorted(set(drone_ids))
  const members = online.map((d) => d.id).sort();

  const slot_assignments = {};
  members.forEach((id, idx) => {
    slot_assignments[id] = idx;
  });

  return {
    type: String(type).toUpperCase(),
    spacing: Number(spacing) || 10,
    members,
    slot_assignments,
  };
}
