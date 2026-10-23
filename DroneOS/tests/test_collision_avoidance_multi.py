import math
import time
import pytest
from unittest.mock import MagicMock
from hypothesis import given, settings, strategies as st

from DroneOS.core.collision_avoidance import StandardCollisionAvoidance
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.shared.config.models import CollisionAvoidanceConfig

R_EARTH = 6371000.0


def _meters_to_lat_lon(north_m: float, east_m: float, ref_lat: float = 0.0, ref_lon: float = 0.0):
    dlat = (north_m / R_EARTH) * (180.0 / math.pi)
    dlon = (east_m / (R_EARTH * math.cos(math.radians(ref_lat)))) * (180.0 / math.pi)
    return ref_lat + dlat, ref_lon + dlon


def _make_telem(drone_id="drone1", north_m=0.0, east_m=0.0, alt=10.0, vx=0.0, vy=0.0, vz=0.0, ts=None, gps_valid=True):
    lat, lon = _meters_to_lat_lon(north_m, east_m)
    return TelemetryData(
        drone_id=drone_id,
        flight_mode="GUIDED",
        gps_valid=gps_valid,
        latitude=lat,
        longitude=lon,
        altitude=alt,
        velocity_x=vx,
        velocity_y=vy,
        velocity_z=vz,
        timestamp=ts if ts is not None else time.time()
    )


# ---------------------------------------------------------------------------
# Test 1: Flag off golden cases (identical to baseline)
# ---------------------------------------------------------------------------

def test_flag_off_golden_cases():
    cfg_off = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        multi_threat_avoidance=False
    )
    ca = StandardCollisionAvoidance(cfg_off, drone_id="drone1")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        self_t = _make_telem("drone1", north_m=0.0, east_m=0.0, alt=10.0, ts=now)

        # Case A: Two peers, one nearer at 3.5m north, one at 5.0m south
        # Flag off must pick ONLY the nearest peer (3.5m north) and flee south
        p1 = _make_telem("drone2", north_m=3.5, east_m=0.0, alt=10.0, ts=now)
        p2 = _make_telem("drone3", north_m=-5.0, east_m=0.0, alt=10.0, ts=now)

        state, corr, peer, dist = ca.evaluate_threats(self_t, {"drone2": p1, "drone3": p2})
        assert state == "AVOIDANCE"
        assert peer == "drone2"
        assert pytest.approx(dist, abs=0.05) == 3.5
        assert corr is not None
        # Flees south (-north) at full avoidance speed
        assert corr["north"] == pytest.approx(-4.0, abs=1e-3)
        assert corr["east"] == pytest.approx(0.0, abs=1e-3)
        assert corr["down"] == 0.0
        assert corr["duration"] == 1.0


# ---------------------------------------------------------------------------
# Test 2: Single threat equivalence (Section 3, item 4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("distance_m,expected_state", [
    (8.0, "WARNING"),
    (4.5, "AVOIDANCE"),
    (1.5, "EMERGENCY"),
])
def test_single_threat_equivalence(distance_m, expected_state):
    cfg_off = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        multi_threat_avoidance=False
    )
    cfg_on = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        multi_threat_avoidance=True,
        vertical_escape_enabled=True,
        vertical_escape_speed=1.5
    )

    ca_off = StandardCollisionAvoidance(cfg_off, drone_id="drone1")
    ca_on = StandardCollisionAvoidance(cfg_on, drone_id="drone1")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        # Peer at distance_m East
        self_t = _make_telem("drone1", north_m=0.0, east_m=0.0, alt=10.0, ts=now)
        peer_t = _make_telem("drone2", north_m=0.0, east_m=distance_m, alt=10.0, ts=now)

        state_off, corr_off, peer_off, dist_off = ca_off.evaluate_threats(self_t, {"drone2": peer_t})
        state_on, corr_on, peer_on, dist_on = ca_on.evaluate_threats(self_t, {"drone2": peer_t})

        assert state_off == expected_state
        assert state_on == expected_state
        assert peer_on == peer_off
        assert abs(dist_on - dist_off) <= 1e-9

        assert corr_off is not None
        assert corr_on is not None
        assert abs(corr_on["north"] - corr_off["north"]) <= 1e-9
        assert abs(corr_on["east"] - corr_off["east"]) <= 1e-9
        assert abs(corr_on["down"] - corr_off["down"]) <= 1e-9
        assert abs(corr_on["duration"] - corr_off["duration"]) <= 1e-9


# ---------------------------------------------------------------------------
# Test 3: Pincer scenario (3.5 m north, 3.8 m south)
# ---------------------------------------------------------------------------

def test_pincer_scenario():
    cfg_on = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        multi_threat_avoidance=True,
        vertical_escape_enabled=True,
        vertical_escape_speed=1.5
    )
    ca = StandardCollisionAvoidance(cfg_on, drone_id="drone1")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        self_t = _make_telem("drone1", north_m=0.0, east_m=0.0, alt=10.0, ts=now)
        p_north = _make_telem("drone2", north_m=3.5, east_m=0.0, alt=10.0, ts=now)
        p_south = _make_telem("drone3", north_m=-3.8, east_m=0.0, alt=10.0, ts=now)

        state, corr, peer, dist = ca.evaluate_threats(self_t, {"drone2": p_north, "drone3": p_south})

        assert state == "AVOIDANCE"
        assert corr is not None

        # Horizontal magnitude is much smaller than avoidance_speed (4.0)
        h_mag = math.hypot(corr["north"], corr["east"])
        assert h_mag < 0.5 * cfg_on.avoidance_speed
        assert h_mag < 0.5  # In fact ~0.16 m/s

        # Vertical escape triggered
        assert corr["down"] != 0.0

        # Does not point toward either peer (net push is slightly south away from closer 3.5m north peer)
        assert corr["north"] <= 0.0
        assert abs(corr["east"]) < 0.1


# ---------------------------------------------------------------------------
# Test 4: Asymmetric pincer (2 m north, 5 m south)
# ---------------------------------------------------------------------------

def test_asymmetric_pincer():
    cfg_on = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        multi_threat_avoidance=True,
        vertical_escape_enabled=True,
        vertical_escape_speed=1.5
    )
    ca = StandardCollisionAvoidance(cfg_on, drone_id="drone1")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        self_t = _make_telem("drone1", north_m=0.0, east_m=0.0, alt=10.0, ts=now)
        p_north = _make_telem("drone2", north_m=2.0, east_m=0.0, alt=10.0, ts=now)
        p_south = _make_telem("drone3", north_m=-5.0, east_m=0.0, alt=10.0, ts=now)

        state, corr, peer, dist = ca.evaluate_threats(self_t, {"drone2": p_north, "drone3": p_south})

        # Net push is south (negative north) away from the 2m threat
        assert corr["north"] < 0.0

        # Magnitude is smaller than single-peer case with peer 2 alone
        _, single_corr, _, _ = ca.evaluate_threats(self_t, {"drone2": p_north})
        assert abs(corr["north"]) < abs(single_corr["north"])


# ---------------------------------------------------------------------------
# Test 5: Worst-state aggregation (nearer WARNING, farther EMERGENCY)
# ---------------------------------------------------------------------------

def test_worst_state_aggregation():
    cfg_on = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        lookahead_sec=3.0,
        multi_threat_avoidance=True
    )
    ca = StandardCollisionAvoidance(cfg_on, drone_id="drone1")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        self_t = _make_telem("drone1", north_m=0.0, east_m=0.0, alt=10.0, ts=now)

        # Peer 1: stationary at 8m (WARNING)
        p1 = _make_telem("drone2", north_m=8.0, east_m=0.0, alt=10.0, vx=0.0, vy=0.0, ts=now)

        # Peer 2: at 5.5m (initially AVOIDANCE), but closing fast south at -3.0 m/s
        # projected distance = 5.5 - 3.0 * 3.0 = -3.5 -> breaches min_h_dist -> escalated to EMERGENCY
        p2 = _make_telem("drone3", north_m=5.5, east_m=0.0, alt=10.0, vx=-3.0, vy=0.0, ts=now)

        state, corr, peer, dist = ca.evaluate_threats(self_t, {"drone2": p1, "drone3": p2})

        assert state == "EMERGENCY"
        assert peer == "drone3"  # The emergency peer is reported


# ---------------------------------------------------------------------------
# Test 6: Mutual vertical avoidance opposite directions
# ---------------------------------------------------------------------------

def test_mutual_vertical_avoidance():
    cfg_on = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        multi_threat_avoidance=True,
        vertical_escape_enabled=True,
        vertical_escape_speed=1.5
    )

    ca1 = StandardCollisionAvoidance(cfg_on, drone_id="drone1")
    ca2 = StandardCollisionAvoidance(cfg_on, drone_id="drone2")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        # D1 at (0, 0), D2 at (0.02, 0) -> distance < 0.05m forces vertical escape
        t1 = _make_telem("drone1", north_m=0.0, east_m=0.0, alt=10.0, ts=now)
        t2 = _make_telem("drone2", north_m=0.02, east_m=0.0, alt=10.0, ts=now)

        state1, corr1, peer1, _ = ca1.evaluate_threats(t1, {"drone2": t2})
        state2, corr2, peer2, _ = ca2.evaluate_threats(t2, {"drone1": t1})

        # Drone 1 sorts before Drone 2 -> Drone 1 climbs (down < 0)
        assert corr1["down"] < 0.0
        assert corr1["down"] == pytest.approx(-1.5)

        # Drone 2 sorts after Drone 1 -> Drone 2 descends (down > 0)
        assert corr2["down"] > 0.0
        assert corr2["down"] == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Test 7: Safety overrides (altitude floor and ceiling)
# ---------------------------------------------------------------------------

def test_safety_override_altitude_floor():
    cfg_on = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        ground_altitude_m=0.5,
        multi_threat_avoidance=True,
        vertical_escape_enabled=True,
        vertical_escape_speed=1.5
    )
    # Drone 2 will vote to descend when facing Drone 1
    ca2 = StandardCollisionAvoidance(cfg_on, drone_id="drone2")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        # Set altitude near floor: 2.0m (limit is 0.5 + 2.0 = 2.5m)
        t1 = _make_telem("drone1", north_m=0.0, east_m=0.0, alt=2.0, ts=now)
        t2 = _make_telem("drone2", north_m=0.02, east_m=0.0, alt=2.0, ts=now)

        _, corr, _, _ = ca2.evaluate_threats(t2, {"drone1": t1})
        # Cannot descend! down clamped to 0.0
        assert corr["down"] == 0.0


def test_safety_override_max_altitude():
    class ConfigWithMaxAlt(CollisionAvoidanceConfig):
        max_altitude: float = 15.0

    cfg_with_max = ConfigWithMaxAlt(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        multi_threat_avoidance=True,
        vertical_escape_enabled=True,
        vertical_escape_speed=1.5,
        max_altitude=15.0
    )

    # Drone 1 votes to climb when facing Drone 2
    ca1 = StandardCollisionAvoidance(cfg_with_max, drone_id="drone1")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        # Set altitude near ceiling: 14.0m + 1.5 = 15.5m > 15.0m
        t1 = _make_telem("drone1", north_m=0.0, east_m=0.0, alt=14.0, ts=now)
        t2 = _make_telem("drone2", north_m=0.02, east_m=0.0, alt=14.0, ts=now)

        _, corr, _, _ = ca1.evaluate_threats(t1, {"drone2": t2})
        # Cannot climb! down clamped to 0.0
        assert corr["down"] == 0.0


# ---------------------------------------------------------------------------
# Test 8: Gates (stale peers, invalid GPS, ground altitude, alt_diff)
# ---------------------------------------------------------------------------

def test_gates_respected():
    cfg_on = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        max_peer_age_sec=2.5,
        ground_altitude_m=0.5,
        min_vertical_distance=2.0,
        multi_threat_avoidance=True
    )
    ca = StandardCollisionAvoidance(cfg_on, drone_id="drone1")

    now = 1000.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, "time", lambda: now)

        # 1. Stale peer
        self_t = _make_telem("drone1", 0.0, 0.0, alt=10.0, ts=now)
        p_stale = _make_telem("drone2", 2.0, 0.0, alt=10.0, ts=now - 5.0)
        state, corr, _, _ = ca.evaluate_threats(self_t, {"drone2": p_stale})
        assert state == "NORMAL"

        # 2. Invalid GPS
        p_nogps = _make_telem("drone2", 2.0, 0.0, alt=10.0, ts=now, gps_valid=False)
        state, corr, _, _ = ca.evaluate_threats(self_t, {"drone2": p_nogps})
        assert state == "NORMAL"

        # 3. Ground altitude (self below ground_altitude_m)
        self_ground = _make_telem("drone1", 0.0, 0.0, alt=0.2, ts=now)
        p_close = _make_telem("drone2", 2.0, 0.0, alt=10.0, ts=now)
        state, corr, _, _ = ca.evaluate_threats(self_ground, {"drone2": p_close})
        assert state == "NORMAL"

        # 4. Vertical separation exceeds min_vertical_distance (2.0m)
        p_high = _make_telem("drone2", 2.0, 0.0, alt=15.0, ts=now)
        state, corr, _, _ = ca.evaluate_threats(self_t, {"drone2": p_high})
        assert state == "NORMAL"


# ---------------------------------------------------------------------------
# Test 9: Hypothesis property test (finite, bounded by emergency_speed, deterministic)
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(
    d2_n=st.floats(min_value=-15.0, max_value=15.0),
    d2_e=st.floats(min_value=-15.0, max_value=15.0),
    d3_n=st.floats(min_value=-15.0, max_value=15.0),
    d3_e=st.floats(min_value=-15.0, max_value=15.0),
)
def test_hypothesis_properties(d2_n, d2_e, d3_n, d3_e):
    cfg_on = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=6.0,
        warning_distance=10.0,
        emergency_distance=2.0,
        avoidance_speed=4.0,
        emergency_speed=6.0,
        multi_threat_avoidance=True,
        vertical_escape_enabled=True,
        vertical_escape_speed=1.5
    )
    ca = StandardCollisionAvoidance(cfg_on, drone_id="drone1")

    now = 1000.0
    self_t = _make_telem("drone1", 0.0, 0.0, alt=10.0, ts=now)
    p2 = _make_telem("drone2", d2_n, d2_e, alt=10.0, ts=now)
    p3 = _make_telem("drone3", d3_n, d3_e, alt=10.0, ts=now)

    swarm = {"drone2": p2, "drone3": p3}

    # Run 1
    state1, corr1, peer1, dist1 = ca.evaluate_threats(self_t, swarm)
    # Run 2 (determinism check)
    state2, corr2, peer2, dist2 = ca.evaluate_threats(self_t, swarm)

    assert state1 == state2
    assert peer1 == peer2
    assert dist1 == dist2

    if corr1 is not None:
        assert corr2 is not None
        assert corr1["north"] == corr2["north"]
        assert corr1["east"] == corr2["east"]
        assert corr1["down"] == corr2["down"]

        h_speed = math.hypot(corr1["north"], corr1["east"])
        assert math.isfinite(h_speed)
        # Bounded by emergency_speed + epsilon
        assert h_speed <= cfg_on.emergency_speed + 1e-6
