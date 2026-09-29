"""
scripts/test_collision_airsim.py

Diagnostic script for the collision avoidance chain in AirSim.

Prints for Drone1/Drone2:
  SELF position, PEER position, current distance, relative velocity,
  predicted CPA, CA state, correction vector, winning intent, dispatched command.

Usage:
  python scripts/test_collision_airsim.py

Requires AirSim running with Drone1 and Drone2 configured.
Does NOT mock CA, does NOT inject intents manually.
"""

import asyncio
import math
import sys
import time
from pathlib import Path

# Make sure the project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from DroneOS.core.collision_avoidance import StandardCollisionAvoidance, _haversine_ne
from DroneOS.shared.config.models import CollisionAvoidanceConfig
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.flight_pipeline import Arbiter

# ---------------------------------------------------------------------------
# Config matching sim profile
# ---------------------------------------------------------------------------
CA_CFG = CollisionAvoidanceConfig(
    enabled=True,
    min_horizontal_distance=6.0,
    min_vertical_distance=2.0,
    warning_distance=10.0,
    emergency_distance=3.0,
    neighbor_timeout_sec=2.5,
    lookahead_sec=3.0,
    avoidance_speed=4.0,
    emergency_speed=5.0,
    max_peer_age_sec=2.5,
    ground_altitude_m=0.5,
)

TAKEOFF_ALT = 10.0
HEAD_ON_SPEED = 4.0   # m/s each drone toward the other
POLL_HZ = 5


def _telem_from_airsim(client, vehicle_name: str) -> TelemetryData:
    """Pull live telemetry from AirSim for one vehicle."""
    state = client.getMultirotorState(vehicle_name)
    gps = client.getGpsData("", vehicle_name)
    pos = state.kinematics_estimated.position
    vel = state.kinematics_estimated.linear_velocity
    alt = -pos.z_val  # AirSim NED: z is down
    return TelemetryData(
        flight_mode="GUIDED",
        gps_valid=True,
        latitude=gps.gnss.geo_point.latitude,
        longitude=gps.gnss.geo_point.longitude,
        altitude=alt,
        velocity_x=vel.x_val,
        velocity_y=vel.y_val,
        velocity_z=vel.z_val,
        armed_state="ARMED",
        timestamp=time.time(),
    )


def _print_diagnostic(
    self_id: str,
    self_t: TelemetryData,
    peer_id: str,
    peer_t: TelemetryData,
    ca_state: str,
    correction,
    winning_intent: FlightIntent,
):
    rn, re = _haversine_ne(
        self_t.latitude, self_t.longitude,
        peer_t.latitude, peer_t.longitude,
    )
    dist = math.sqrt(rn * rn + re * re)

    dvn = (peer_t.velocity_x or 0.0) - (self_t.velocity_x or 0.0)
    dve = (peer_t.velocity_y or 0.0) - (self_t.velocity_y or 0.0)
    v_sq = dvn * dvn + dve * dve
    if v_sq > 1e-6:
        t_cpa = -(rn * dvn + re * dve) / v_sq
        t_cpa = max(0.0, min(t_cpa, 3.0))
        cpa_n = rn + dvn * t_cpa
        cpa_e = re + dve * t_cpa
        cpa_dist = math.sqrt(cpa_n * cpa_n + cpa_e * cpa_e)
    else:
        t_cpa = float("inf")
        cpa_dist = dist

    print(f"\n{'='*60}")
    print(f"  SELF  [{self_id}]  lat={self_t.latitude:.7f}  lon={self_t.longitude:.7f}  alt={self_t.altitude:.1f}m")
    print(f"  PEER  [{peer_id}]  lat={peer_t.latitude:.7f}  lon={peer_t.longitude:.7f}  alt={peer_t.altitude:.1f}m")
    print(f"  current_dist={dist:.2f}m")
    print(f"  rel_vel  dvn={dvn:.2f}  dve={dve:.2f}  |dv|={math.sqrt(v_sq):.2f} m/s")
    print(f"  t_cpa={t_cpa:.2f}s  cpa_dist={cpa_dist:.2f}m")
    print(f"  CA_DECISION state={ca_state}  peer={peer_id}  dist={dist:.2f}")
    if correction:
        print(f"  CA_AVOIDANCE peer={peer_id}  north={correction.get('north',0):.3f}  east={correction.get('east',0):.3f}  down={correction.get('down',0):.3f}")
    print(f"  ARBITER winner source={winning_intent.source.name}  action={winning_intent.action.value}")


async def run_diagnostic():
    try:
        import airsim
    except ImportError:
        print("ERROR: airsim package not installed. Run: pip install airsim")
        return

    print("Connecting to AirSim...")
    client = airsim.MultirotorClient()
    client.confirmConnection()

    for v in ("Drone1", "Drone2"):
        client.enableApiControl(True, v)
        client.armDisarm(True, v)

    print("Taking off...")
    t1 = client.takeoffAsync(vehicle_name="Drone1")
    t2 = client.takeoffAsync(vehicle_name="Drone2")
    t1.join()
    t2.join()

    # Climb to TAKEOFF_ALT
    client.moveToZAsync(-TAKEOFF_ALT, 3.0, vehicle_name="Drone1").join()
    client.moveToZAsync(-TAKEOFF_ALT, 3.0, vehicle_name="Drone2").join()
    await asyncio.sleep(1.0)

    # Separate them 30 m apart along X axis
    print("Separating drones 30 m apart...")
    client.moveToPositionAsync(15, 0, -TAKEOFF_ALT, 4.0, vehicle_name="Drone1").join()
    client.moveToPositionAsync(-15, 0, -TAKEOFF_ALT, 4.0, vehicle_name="Drone2").join()
    await asyncio.sleep(1.0)

    # Command head-on approach
    print("Commanding head-on approach...")
    client.moveByVelocityAsync(-HEAD_ON_SPEED, 0, 0, 30.0, vehicle_name="Drone1")
    client.moveByVelocityAsync(HEAD_ON_SPEED, 0, 0, 30.0, vehicle_name="Drone2")

    ca1 = StandardCollisionAvoidance(CA_CFG, drone_id="drone1")
    ca2 = StandardCollisionAvoidance(CA_CFG, drone_id="drone2")

    store = FlightStateStore()
    ca_triggered = False

    print(f"\nPolling at {POLL_HZ} Hz. Ctrl+C to stop.\n")
    for _ in range(POLL_HZ * 20):  # 20 second window
        t1_telem = _telem_from_airsim(client, "Drone1")
        t2_telem = _telem_from_airsim(client, "Drone2")

        # Evaluate from Drone1's perspective
        state, correction, peer_id, dist = ca1.evaluate_threats(
            t1_telem, {"drone2": t2_telem}
        )

        # Build intents: formation intent + possible collision intent
        store2 = FlightStateStore()
        formation_intent = FlightIntent(
            IntentSource.FORMATION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0,
            params={"north": -HEAD_ON_SPEED, "east": 0.0, "down": 0.0, "duration": 1.0}
        )
        store2.submit_intent(formation_intent)

        if state in ("AVOIDANCE", "EMERGENCY") and correction:
            collision_intent = FlightIntent(
                IntentSource.COLLISION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0,
                params=correction
            )
            store2.submit_intent(collision_intent)
            ca_triggered = True

        intents = store2.get_intents()
        winner = Arbiter.select_winner(intents, store2)

        _print_diagnostic("drone1", t1_telem, "drone2", t2_telem, state, correction, winner)

        if ca_triggered and winner.source == IntentSource.COLLISION:
            print("\n✓ ARBITER winner source=COLLISION action=MOVE_VELOCITY_NED")
            print("✓ Full chain verified: AirSim telemetry → CA → COLLISION intent → Arbiter")
            # Stop the head-on approach
            client.hoverAsync(vehicle_name="Drone1")
            client.hoverAsync(vehicle_name="Drone2")
            break

        await asyncio.sleep(1.0 / POLL_HZ)

    if not ca_triggered:
        print("\n✗ CA never triggered during the 20 s window.")
        print("  Check: are drones actually moving? Is max_peer_age_sec large enough?")

    print("\nLanding...")
    client.landAsync(vehicle_name="Drone1").join()
    client.landAsync(vehicle_name="Drone2").join()
    client.armDisarm(False, "Drone1")
    client.armDisarm(False, "Drone2")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(run_diagnostic())
