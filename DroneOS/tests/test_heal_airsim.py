"""
AirSim integration test: self-healing formation when Drone4 fails.

Requirements: 4.1 – 4.9

PREREQUISITES
─────────────
1. Copy sim/airsim/settings.json to %USERPROFILE%\Documents\AirSim\settings.json
   and launch your AirSim/Unreal environment. You should see Drone1–Drone4 spawn.
2. From the repo root, run the TEST profile launcher (coordination active + armed):
       sim\launch_sim_test.bat
   This sets DRONEOS_PROFILE=test, which loads flight.yaml + flight.sim.yaml +
   flight.test.yaml, enabling the healing coordinator on all four nodes.
3. Set AIRSIM_HOST=127.0.0.1 in your test terminal.
4. Optionally set DRONE4_PID to the PID of the DroneOS3 process to have the test
   kill it when it terminates Drone4. Without it, only the motors are cut via AirSim.

RUNNING
───────
    set AIRSIM_HOST=127.0.0.1
    python -m pytest DroneOS/tests/test_heal_airsim.py -m integration -v
"""

import asyncio
import json
import math
import os
import signal
import subprocess
import time
from itertools import combinations

import pytest

AIRSIM_HOST = os.environ.get("AIRSIM_HOST", "")
AIRSIM_PORT = int(os.environ.get("AIRSIM_PORT", "41451"))
VEHICLE_NAMES = ["Drone1", "Drone2", "Drone3", "Drone4"]
DRONE_IDS = ["drone1", "drone2", "drone3", "drone4"]

# Formation params
FORMATION_TYPE = "V"
FORMATION_SPACING = 15.0
PRE_HEAL_SLOTS = {DRONE_IDS[i]: i for i in range(4)}
POST_HEAL_SLOTS = {DRONE_IDS[i]: i for i in range(3)}  # drone4 removed

# Timeouts and tolerances (matching flight.test.yaml timings)
# dead_missed_beats=4, heartbeat_interval=1.0, reslot_cooldown_s=5 → ~9s minimum
# We allow 20s for broadcast + 60s total window
CONVERGENCE_TOLERANCE_M = 3.0
HEAL_BROADCAST_TIMEOUT_S = 20.0
POST_HEAL_CONVERGENCE_S = 30.0
TOTAL_HEAL_TIMEOUT_S = 60.0
ANCHOR_STABILITY_M = 2.0
MIN_FORMATION_SEPARATION_M = 3.0  # matches flight.test.yaml formation.min_formation_separation_m


# ──────────────────────────────────────────────────────────────────────────────
# Skip guards (Requirements 4.8, 4.9)
# ──────────────────────────────────────────────────────────────────────────────

def _airsim_client():
    """Return a connected airsim.MultirotorClient or None if unavailable."""
    if not AIRSIM_HOST:
        return None
    try:
        import airsim  # type: ignore
        client = airsim.MultirotorClient(ip=AIRSIM_HOST, port=AIRSIM_PORT)
        client.confirmConnection()
        return client
    except Exception:
        return None


@pytest.fixture(scope="module")
def airsim_client():
    """Fixture: skip if AIRSIM_HOST is not set (Req 4.8) or host unreachable (Req 4.9)."""
    if not AIRSIM_HOST:
        pytest.skip("AIRSIM_HOST not set — skipping AirSim integration tests")

    client = _airsim_client()
    if client is None:
        pytest.skip(
            f"AirSim host {AIRSIM_HOST}:{AIRSIM_PORT} is unreachable — "
            "start AirSim before running integration tests"
        )
    yield client


# ──────────────────────────────────────────────────────────────────────────────
# Helper: position retrieval
# ──────────────────────────────────────────────────────────────────────────────

def get_ned_position(client, vehicle_name: str):
    """Return (north_m, east_m, down_m) from AirSim kinematics."""
    import airsim  # type: ignore
    state = client.getMultirotorState(vehicle_name=vehicle_name)
    pos = state.kinematics_estimated.position
    return (pos.x_val, pos.y_val, pos.z_val)


def horizontal_distance(p1, p2) -> float:
    """Euclidean distance in the horizontal (NE) plane."""
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


# ──────────────────────────────────────────────────────────────────────────────
# Helper: DroneOS command sender
# ──────────────────────────────────────────────────────────────────────────────

def send_formation_command(
    host: str,
    port: int,
    drone_id: str,
    f_type: str,
    spacing: float,
    slot_assignments: dict,
):
    """
    Send a FORMATION_SET command to a DroneOS node over UDP.
    Uses the DroneOS shared protocol directly.

    NOTE: This helper sends a raw ControlMessage to the DroneOS instance
    listening on (host, port). Adjust the port per-node as configured.
    """
    import socket
    import struct
    msg = json.dumps({
        "type": "ControlMessage",
        "action": "FORMATION_SET",
        "params": {
            "type": f_type,
            "spacing": spacing,
            "slot_assignments": slot_assignments,
            "members": [k for k, v in slot_assignments.items() if v != 0],
        },
        "sender_id": "test_harness",
        "timestamp": time.time(),
    }).encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(msg, (host, port))


# ──────────────────────────────────────────────────────────────────────────────
# Helper: formation convergence polling
# ──────────────────────────────────────────────────────────────────────────────

def _compute_target_positions(client, slot_assignments: dict, spacing: float, f_type: str):
    """
    Approximate target positions based on AirSim current anchor position + V offsets.
    For V formation: slot 0 is anchor; slots 1,2,3 are offset in NE from anchor.
    """
    # Get anchor (slot 0) current position
    anchor_id = next(k for k, v in slot_assignments.items() if v == 0)
    anchor_vehicle = f"Drone{int(anchor_id.replace('drone',''))}"
    anchor_pos = get_ned_position(client, anchor_vehicle)

    # V formation offsets (relative to anchor, in NE plane):
    # slot 0: (0,0), slot 1: (+spacing,+spacing/2), slot 2: (+spacing,-spacing/2), slot 3: (2*spacing, 0) ...
    def v_offset(slot, n, spacing):
        # Simplified V geometry matching FormationEngine
        if slot == 0:
            return (0.0, 0.0)
        side = 1 if slot % 2 == 1 else -1
        row = (slot + 1) // 2
        return (row * spacing * 0.866, side * row * spacing * 0.5)

    n = len(slot_assignments)
    targets = {}
    for drone_id, slot in slot_assignments.items():
        off = v_offset(slot, n, spacing)
        targets[drone_id] = (anchor_pos[0] + off[0], anchor_pos[1] + off[1])
    return targets


def wait_formation_converged(
    client,
    slot_assignments: dict,
    spacing: float = FORMATION_SPACING,
    tolerance_m: float = CONVERGENCE_TOLERANCE_M,
    timeout_s: float = 30.0,
    f_type: str = FORMATION_TYPE,
) -> bool:
    """
    Poll until all drones in slot_assignments are within tolerance_m of their target positions.
    Returns True on success; raises AssertionError with diagnostics on timeout (Req 4.3).
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        targets = _compute_target_positions(client, slot_assignments, spacing, f_type)
        not_converged = []
        for drone_id, slot in slot_assignments.items():
            vehicle = f"Drone{int(drone_id.replace('drone',''))}"
            pos = get_ned_position(client, vehicle)
            target = targets[drone_id]
            dist = horizontal_distance(pos, target)
            if dist > tolerance_m:
                not_converged.append(f"{drone_id}: {dist:.1f}m from slot {slot} target")
        if not not_converged:
            return True
        time.sleep(0.2)

    # Timeout — collect final diagnostics
    targets = _compute_target_positions(client, slot_assignments, spacing, f_type)
    diag = []
    for drone_id, slot in slot_assignments.items():
        vehicle = f"Drone{int(drone_id.replace('drone',''))}"
        pos = get_ned_position(client, vehicle)
        target = targets[drone_id]
        dist = horizontal_distance(pos, target)
        diag.append(f"  {drone_id} (slot {slot}): {dist:.2f}m from target (tolerance {tolerance_m}m)")
    raise AssertionError(
        f"Formation did not converge within {timeout_s}s.\n" + "\n".join(diag)
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main integration test
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_heal_on_drone4_failure(airsim_client):
    """
    End-to-end self-healing test: 4 drones in V-formation → kill Drone4 →
    remaining 3 automatically reformat.

    Requirements: 4.1 – 4.9
    """
    import airsim  # type: ignore
    client = airsim_client

    # ── Phase 1: Takeoff all four drones ──────────────────────────────────
    for vehicle in VEHICLE_NAMES:
        client.enableApiControl(True, vehicle_name=vehicle)
        client.armDisarm(True, vehicle_name=vehicle)

    tasks = [
        client.takeoffAsync(vehicle_name=v, timeout_sec=15)
        for v in VEHICLE_NAMES
    ]
    for t in tasks:
        t.join()

    await asyncio.sleep(2.0)  # settle after takeoff

    # ── Phase 2: Command V-formation ──────────────────────────────────────
    # Send FORMATION_SET to the anchor drone (drone1 on default port 14550)
    DRONE1_PORT = int(os.environ.get("DRONE1_CMD_PORT", "14550"))
    send_formation_command(
        host=AIRSIM_HOST,
        port=DRONE1_PORT,
        drone_id="drone1",
        f_type=FORMATION_TYPE,
        spacing=FORMATION_SPACING,
        slot_assignments=PRE_HEAL_SLOTS,
    )

    # Wait for all 4 drones to reach their formation positions (Req 4.2, 4.3)
    wait_formation_converged(
        client,
        PRE_HEAL_SLOTS,
        spacing=FORMATION_SPACING,
        tolerance_m=CONVERGENCE_TOLERANCE_M,
        timeout_s=30.0,
    )

    # ── Phase 3: Record pre-heal positions of survivors ───────────────────
    pre_heal_positions = {}
    for drone_id in ["drone1", "drone2", "drone3"]:
        vehicle = f"Drone{int(drone_id.replace('drone',''))}"
        pre_heal_positions[drone_id] = get_ned_position(client, vehicle)

    # ── Phase 4: Kill Drone4 ──────────────────────────────────────────────
    # Cut motors via AirSim API (Req 4.4)
    kill_time = time.monotonic()
    client.armDisarm(False, vehicle_name="Drone4")

    # Also terminate the DroneOS3 process if PID is available (Req 4.4)
    drone4_pid_str = os.environ.get("DRONE4_PID", "")
    if drone4_pid_str:
        try:
            drone4_pid = int(drone4_pid_str)
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(drone4_pid)], capture_output=True)
            else:
                os.kill(drone4_pid, signal.SIGTERM)
        except Exception as e:
            print(f"WARNING: Could not kill DroneOS3 process: {e}")

    # ── Phase 5: Wait for the swarm to heal (Req 4.4, 4.5) ───────────────
    heal_deadline = kill_time + TOTAL_HEAL_TIMEOUT_S

    # Poll for convergence to the 3-drone formation
    converged = False
    while time.monotonic() < heal_deadline:
        try:
            wait_formation_converged(
                client,
                POST_HEAL_SLOTS,
                spacing=FORMATION_SPACING,
                tolerance_m=4.0,
                timeout_s=0.5,  # short timeout — we poll manually
            )
            converged = True
            break
        except AssertionError:
            await asyncio.sleep(0.5)

    if not converged:
        # Detailed failure message
        wait_formation_converged(
            client,
            POST_HEAL_SLOTS,
            spacing=FORMATION_SPACING,
            tolerance_m=4.0,
            timeout_s=0.01,
        )

    # ── Phase 6: Assertions ───────────────────────────────────────────────

    # Anchor stability: drone1 must not have moved significantly (Req 4.6)
    post_anchor_pos = get_ned_position(client, "Drone1")
    anchor_drift = horizontal_distance(pre_heal_positions["drone1"], post_anchor_pos)
    assert anchor_drift < ANCHOR_STABILITY_M, (
        f"Anchor (drone1) drifted {anchor_drift:.2f}m > {ANCHOR_STABILITY_M}m"
    )

    # Minimum separation: all pairs of survivors must be at least min_sep apart (Req 4.7)
    survivor_positions = {
        drone_id: get_ned_position(client, f"Drone{int(drone_id.replace('drone',''))}")
        for drone_id in ["drone1", "drone2", "drone3"]
    }
    for (id_a, pos_a), (id_b, pos_b) in combinations(survivor_positions.items(), 2):
        sep = horizontal_distance(pos_a, pos_b)
        assert sep >= MIN_FORMATION_SEPARATION_M, (
            f"Post-heal separation between {id_a} and {id_b}: {sep:.2f}m < "
            f"{MIN_FORMATION_SEPARATION_M}m"
        )

    # ── Phase 7: Cleanup ──────────────────────────────────────────────────
    for vehicle in ["Drone1", "Drone2", "Drone3"]:
        client.landAsync(vehicle_name=vehicle, timeout_sec=20).join()
        client.armDisarm(False, vehicle_name=vehicle)


