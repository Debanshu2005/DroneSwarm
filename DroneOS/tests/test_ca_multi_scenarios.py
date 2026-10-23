"""
Pure-Python kinematic simulation checks for multi-threat collision avoidance.

NOTE / DISCLAIMER:
This is kinematic evidence, not real-world flight proof: point-mass dynamics at 10 Hz,
ideal actuator tracking, zero sensor/GPS noise, and zero network latency.
"""

import math
import time
import pytest

from DroneOS.core.collision_avoidance import StandardCollisionAvoidance
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.shared.config.models import CollisionAvoidanceConfig

R_EARTH = 6371000.0


def _run_kinematic_simulation(scenario: str, multi_threat: bool, total_steps: int = 60, dt: float = 0.1):
    """
    Simulates three point-mass drones at 10 Hz feeding evaluate_threats outputs
    back as velocity commands.
    """
    cfg = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=4.0,
        warning_distance=6.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        multi_threat_avoidance=multi_threat,
        vertical_escape_enabled=True,
        vertical_escape_speed=1.5
    )

    # Initial NED coordinates: [north_m, east_m, down_m] (alt = 10.0 - down_m)
    if scenario == "pincer":
        # Middle drone stationary at origin. Outer peers 5m north and 5m south closing at 2 m/s.
        pos = {
            "drone1": [0.0, 0.0, 0.0],
            "drone2": [5.0, 0.0, 0.0],
            "drone3": [-5.0, 0.0, 0.0],
        }
        vel = {
            "drone1": [0.0, 0.0, 0.0],
            "drone2": [-2.0, 0.0, 0.0],
            "drone3": [2.0, 0.0, 0.0],
        }
    elif scenario == "convergence":
        # Three drones on a 6m circle at 120-degree intervals, closing head-on towards origin at 2 m/s.
        r = 6.0
        speed = 2.0
        angles = [0.0, 2.0 * math.pi / 3.0, 4.0 * math.pi / 3.0]
        ids = ["drone1", "drone2", "drone3"]
        pos = {}
        vel = {}
        for did, ang in zip(ids, angles):
            n = r * math.cos(ang)
            e = r * math.sin(ang)
            pos[did] = [n, e, 0.0]
            vel[did] = [-speed * math.cos(ang), -speed * math.sin(ang), 0.0]
    else:
        raise ValueError(f"Unknown scenario: {scenario}")

    cas = {did: StandardCollisionAvoidance(cfg, drone_id=did) for did in pos}
    min_separation_3d = float("inf")
    t_now = time.time()

    for step in range(total_steps):
        # 1. Build telemetry packets
        telemetry = {}
        for did in pos:
            lat = (pos[did][0] / R_EARTH) * (180.0 / math.pi)
            lon = (pos[did][1] / (R_EARTH * math.cos(math.radians(0.0)))) * (180.0 / math.pi)
            alt = 10.0 - pos[did][2]
            telemetry[did] = TelemetryData(
                drone_id=did,
                flight_mode="GUIDED",
                gps_valid=True,
                latitude=lat,
                longitude=lon,
                altitude=alt,
                velocity_x=vel[did][0],
                velocity_y=vel[did][1],
                velocity_z=vel[did][2],
                timestamp=t_now
            )

        # 2. Evaluate CA and feed back corrective velocity commands
        new_vel = {}
        for did in pos:
            swarm_t = {k: v for k, v in telemetry.items() if k != did}
            state, corr, threat_peer, dist = cas[did].evaluate_threats(telemetry[did], swarm_t)
            if state != "NORMAL" and corr is not None:
                new_vel[did] = [corr["north"], corr["east"], corr["down"]]
            else:
                new_vel[did] = vel[did]

        vel = new_vel

        # 3. Kinematic update (10 Hz point-mass)
        for did in pos:
            pos[did][0] += vel[did][0] * dt
            pos[did][1] += vel[did][1] * dt
            pos[did][2] += vel[did][2] * dt

        # 4. Measure 3D separation between all pairs
        drone_ids = list(pos.keys())
        for i in range(len(drone_ids)):
            for j in range(i + 1, len(drone_ids)):
                p1 = pos[drone_ids[i]]
                p2 = pos[drone_ids[j]]
                d3d = math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2 + (p1[2] - p2[2]) ** 2)
                if d3d < min_separation_3d:
                    min_separation_3d = d3d

        t_now += dt

    return min_separation_3d


def test_scenario_pincer_separation():
    """
    Scenario: Pincer (middle drone between two closing peers).
    Assert minimum 3D separation >= emergency_distance (2.0m) with flag on,
    and print flag-off minimum for comparison.
    """
    emergency_dist = 2.0
    min_dist_off = _run_kinematic_simulation("pincer", multi_threat=False)
    min_dist_on = _run_kinematic_simulation("pincer", multi_threat=True)

    print(f"\n[KINEMATIC SCENARIO: Pincer] Flag OFF min separation = {min_dist_off:.3f}m | Flag ON min separation = {min_dist_on:.3f}m")

    assert min_dist_on >= emergency_dist, (
        f"Multi-threat pincer separation ({min_dist_on:.3f}m) breached emergency distance ({emergency_dist}m)"
    )


def test_scenario_convergence_separation():
    """
    Scenario: Three-drone head-on convergence.
    Assert minimum 3D separation >= emergency_distance (2.0m) with flag on,
    and print flag-off minimum for comparison.
    """
    emergency_dist = 2.0
    min_dist_off = _run_kinematic_simulation("convergence", multi_threat=False)
    min_dist_on = _run_kinematic_simulation("convergence", multi_threat=True)

    print(f"\n[KINEMATIC SCENARIO: Convergence] Flag OFF min separation = {min_dist_off:.3f}m | Flag ON min separation = {min_dist_on:.3f}m")

    assert min_dist_on >= emergency_dist, (
        f"Multi-threat convergence separation ({min_dist_on:.3f}m) breached emergency distance ({emergency_dist}m)"
    )
