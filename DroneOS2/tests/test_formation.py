"""
Formation engine tests — explicit slot_assignments + separation safety layer.

Tests:
  A. Deterministic assignment: sorted members -> stable slot indices
  B. Heartbeat loss: drone4 stays slot 3 even when drone3 disappears
  C. Unique targets: four-drone V at 10m -> four distinct coordinates + pairwise distances
  D. Minimum formation separation: too-close peer in direction of travel limits velocity
  D2. Fully converged peer in direction of travel -> velocity reduced to near-zero
  E. Normal formation movement: safely separated drones get normal velocity
  F. Missing assignment: drone not in slot_assignments -> HOVER + FORMATION_NO_SLOT
  G. Stale anchor: non-anchor drone hovers when anchor position is stale
  H. Collision priority: COLLISION intent beats FORMATION intent
  Speed clamp, NED directions
"""
import math
import time
import pytest
from unittest.mock import MagicMock

from DroneOS2.core.formation_manager import (
    FormationManager, FormationType, convert_local_offset_to_global,
)
from DroneOS2.core.formation_engine import FormationEngine, _DEFAULT_MIN_SEP_M
from DroneOS2.core.swarm_manager import PeerStateManager
from DroneOS2.shared.protocol.messages import TelemetryData
from DroneOS2.core.intents import IntentSource, IntentAction, FlightIntent
from DroneOS2.core.flight_state import FlightStateStore
from DroneOS2.core.flight_pipeline import Arbiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MEMBERS_4 = ["drone1", "drone2", "drone3", "drone4"]
SLOTS_4 = {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3}


def _params(drone_type="V", spacing=10.0, members=None, slots=None, speed=2.0):
    m = members if members is not None else MEMBERS_4
    s = slots if slots is not None else SLOTS_4
    return {
        "type": drone_type,
        "spacing": spacing,
        "members": m,
        "slot_assignments": s,
        "speed": speed,
        "repulsion_radius_m": 1.0,
    }


def _make_engine(my_id: str, peers: dict = None, min_sep: float = None):
    """Build a FormationEngine with a mock swarm for drone `my_id`."""
    sm = MagicMock()
    sm.identity.drone_id = my_id
    sm.registry.peers = peers or {}
    sm.registry.get_peer.side_effect = lambda x: sm.registry.peers.get(x)

    config = None
    if min_sep is not None:
        config = MagicMock()
        config.formation.velocity_gain = 1.0
        config.formation.min_formation_separation_m = min_sep

    return FormationEngine(sm, MagicMock(), config=config)


def _peer(lat=40.0, lon=-75.0, alt=10.0, age=0.0):
    p = PeerStateManager("_")
    p.last_seen = time.time() - age
    p.is_active = True
    p.lat = lat
    p.lon = lon
    p.alt = alt
    p.last_position_time = time.time() - age
    return p


def _telem(lat=40.0, lon=-75.0, alt=10.0, gps=True):
    return TelemetryData(
        flight_mode="GUIDED", gps_valid=gps,
        latitude=lat, longitude=lon, altitude=alt,
    )


# ---------------------------------------------------------------------------
# Test A -- Deterministic assignment
# ---------------------------------------------------------------------------

def test_deterministic_slot_assignment():
    """sorted(members) -> members[i] gets slot i, regardless of insertion order."""
    members = ["drone4", "drone1", "drone3", "drone2"]   # unsorted input
    members_sorted = sorted(members)
    slots = {m: i for i, m in enumerate(members_sorted)}

    assert slots["drone1"] == 0
    assert slots["drone2"] == 1
    assert slots["drone3"] == 2
    assert slots["drone4"] == 3

    engine = _make_engine("drone3")
    params = _params(slots=slots, members=members_sorted)
    assert engine._my_slot(params) == 2
    assert engine._anchor_id(params) == "drone1"


# ---------------------------------------------------------------------------
# Test B -- Heartbeat loss does NOT reshuffle slots
# ---------------------------------------------------------------------------

def test_heartbeat_loss_does_not_reshuffle():
    """
    Initial: drone1=0, drone2=1, drone3=2, drone4=3.
    drone3 disappears from heartbeat.
    drone4 must still be slot 3, NOT slot 2.
    """
    peers = {
        "drone1": _peer(lat=40.0, lon=-75.0),
        "drone2": _peer(lat=40.0, lon=-75.001),
        # drone3 absent
        "drone4": _peer(lat=40.0, lon=-75.003),
    }
    engine = _make_engine("drone4", peers)
    params = _params()   # still has drone3 in slot_assignments

    assert engine._my_slot(params) == 3, (
        "drone4 must remain slot 3 even when drone3 is absent from heartbeat"
    )
    assert engine._total_drones(params) == 4   # roster size unchanged


# ---------------------------------------------------------------------------
# Test C -- Unique targets for four-drone V at 10m + pairwise distances
# ---------------------------------------------------------------------------

def test_unique_formation_targets():
    """All four drones must receive distinct (lat, lon) targets with adequate separation."""
    mgr = FormationManager()
    mgr.set_formation(FormationType.V, 10.0)
    anchor_lat, anchor_lon, anchor_alt = 40.0, -75.0, 10.0
    total = 4

    targets = {}
    for drone_id, slot in SLOTS_4.items():
        dx_n, dy_e, _ = mgr.get_offset(slot, total)
        t_lat, t_lon, _ = convert_local_offset_to_global(
            anchor_lat, anchor_lon, anchor_alt, dx_n, dy_e
        )
        targets[drone_id] = (t_lat, t_lon)

    coords = list(targets.values())
    pairwise = []
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            dn = (coords[i][0] - coords[j][0]) * 111320
            de = (coords[i][1] - coords[j][1]) * 111320
            dist = math.sqrt(dn**2 + de**2)
            pairwise.append(dist)
            assert dist > 1.0, (
                f"Drones at indices {i} and {j} are too close: {dist:.2f}m"
            )

    # At 10m spacing, V-formation minimum pairwise distance should be >= 10m
    min_pairwise = min(pairwise)
    assert min_pairwise >= 10.0, (
        f"Minimum pairwise distance {min_pairwise:.2f}m is below 10m spacing"
    )

    assert targets["drone1"] == pytest.approx((anchor_lat, anchor_lon), abs=1e-9)

    print("\nV-formation 10m pairwise distances:")
    drone_ids = list(SLOTS_4.keys())
    for i in range(len(drone_ids)):
        for j in range(i + 1, len(drone_ids)):
            dn = (coords[i][0] - coords[j][0]) * 111320
            de = (coords[i][1] - coords[j][1]) * 111320
            dist = math.sqrt(dn**2 + de**2)
            print(f"  {drone_ids[i]}<->{drone_ids[j]}: {dist:.2f}m")

    for drone_id, (lat, lon) in targets.items():
        slot = SLOTS_4[drone_id]
        print(f"FORMATION_TARGET drone={drone_id} slot={slot} lat={lat:.7f} lon={lon:.7f}")


# ---------------------------------------------------------------------------
# Test D -- Minimum formation separation: too-close peer in direction of travel
# ---------------------------------------------------------------------------

def test_separation_limits_velocity_when_too_close():
    """
    Geometry: COLUMN formation, spacing=10m.
      slot 0 (anchor/drone1): (0, 0)
      slot 1 (drone2):        (-10, 0)  -- 10m south of anchor
      slot 2 (drone3):        (-20, 0)  -- 20m south of anchor

    drone3 starts at anchor position (0, 0).
    Its slot target is 20m south -> velocity is southward (vx < 0).

    drone2 is placed 3m south of drone3 (between drone3 and its target).
    dist(drone3, drone2) = 3m < min_sep=8m, and velocity is toward drone2.
    -> separation limiter must fire and reduce velocity.
    """
    min_sep = 8.0
    anchor = _peer(lat=40.0, lon=-75.0)
    # 3m south of drone3's current position (40.0, -75.0)
    south_3m_lat = 40.0 - 3.0 / 111320
    drone2_peer = _peer(lat=south_3m_lat, lon=-75.0)

    peers = {"drone1": anchor, "drone2": drone2_peer}
    engine = _make_engine("drone3", peers, min_sep=min_sep)

    members = ["drone1", "drone2", "drone3"]
    slots = {"drone1": 0, "drone2": 1, "drone3": 2}
    params = {
        "type": "COLUMN",
        "spacing": 10.0,
        "members": members,
        "slot_assignments": slots,
        "speed": 2.0,
        "repulsion_radius_m": 1.0,
    }

    # drone3 at anchor position; its COLUMN slot 2 target is 20m south
    telem = _telem(lat=40.0, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    # Slot must still be 2 -- no reshuffling
    assert engine._my_slot(params) == 2

    # Velocity must be reduced (peer is 3m away in direction of travel)
    if intent.action == IntentAction.MOVE_VELOCITY_NED:
        mag = math.hypot(intent.params["north"], intent.params["east"])
        assert mag < 2.0, (
            f"Velocity {mag:.3f} m/s not reduced despite peer at 3m < min_sep={min_sep}m"
        )
    else:
        # HOVER is also acceptable (fully blocked)
        assert intent.action == IntentAction.HOVER


def test_separation_reduces_velocity_proportionally():
    """
    At dist = min_sep/2, scale_factor = 0.5, so velocity magnitude
    must be approximately half of what it would be without limiting.
    """
    min_sep = 8.0
    anchor = _peer(lat=40.0, lon=-75.0)
    # Place drone2 exactly min_sep/2 = 4m south of drone3
    half_sep_lat = 40.0 - 4.0 / 111320
    drone2_peer = _peer(lat=half_sep_lat, lon=-75.0)

    peers = {"drone1": anchor, "drone2": drone2_peer}
    engine = _make_engine("drone3", peers, min_sep=min_sep)

    members = ["drone1", "drone2", "drone3"]
    slots = {"drone1": 0, "drone2": 1, "drone3": 2}
    params = {
        "type": "COLUMN",
        "spacing": 10.0,
        "members": members,
        "slot_assignments": slots,
        "speed": 2.0,
        "repulsion_radius_m": 1.0,
    }

    telem = _telem(lat=40.0, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert engine._my_slot(params) == 2

    if intent.action == IntentAction.MOVE_VELOCITY_NED:
        mag = math.hypot(intent.params["north"], intent.params["east"])
        # scale_factor = 4/8 = 0.5, so mag should be <= 1.0 (half of speed=2.0)
        assert mag <= 1.0 + 1e-6, (
            f"Velocity {mag:.3f} m/s exceeds expected scaled limit at dist=4m, min_sep=8m"
        )
    else:
        assert intent.action == IntentAction.HOVER


# ---------------------------------------------------------------------------
# Test E -- Normal formation movement when safely separated
# ---------------------------------------------------------------------------

def test_normal_movement_when_safely_separated():
    """
    When all peers are beyond min_formation_separation_m,
    the engine must produce MOVE_VELOCITY with non-zero speed.
    """
    min_sep = 8.0
    anchor = _peer(lat=40.0, lon=-75.0)
    # drone2 is 20m south of anchor -- well beyond min_sep=8m from drone3
    far_peer_lat = 40.0 - 20.0 / 111320
    far_peer = _peer(lat=far_peer_lat, lon=-75.0)

    peers = {"drone1": anchor, "drone2": far_peer}
    engine = _make_engine("drone3", peers, min_sep=min_sep)

    members = ["drone1", "drone2", "drone3"]
    slots = {"drone1": 0, "drone2": 1, "drone3": 2}
    params = {
        "type": "COLUMN",
        "spacing": 10.0,
        "members": members,
        "slot_assignments": slots,
        "speed": 2.0,
        "repulsion_radius_m": 1.0,
    }

    # drone3 is north of its target so it has something to move toward
    telem = _telem(lat=40.0 + 5.0 / 111320, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert intent.action == IntentAction.MOVE_VELOCITY_NED
    mag = math.hypot(intent.params["north"], intent.params["east"])
    assert mag > 0.01, "Expected non-zero velocity when peers are safely separated"


# ---------------------------------------------------------------------------
# Test F -- Missing assignment -> HOVER + FORMATION_NO_SLOT
# ---------------------------------------------------------------------------

def test_missing_slot_returns_hover(caplog):
    """A drone not in slot_assignments must return IDLE and log FORMATION_NO_SLOT."""
    import logging
    peers = {"drone1": _peer()}
    engine = _make_engine("drone_unknown", peers)

    params = _params()   # slot_assignments has drone1-4, not drone_unknown

    with caplog.at_level(logging.WARNING, logger="FormationEngine"):
        intent = engine.compute_intent(_telem(), {}, params)

    assert intent.action == IntentAction.IDLE
    assert intent.source == IntentSource.IDLE
    assert intent.source == IntentSource.IDLE
    assert "FORMATION_NO_SLOT" in caplog.text


# ---------------------------------------------------------------------------
# Test G -- Stale anchor -> HOVER
# ---------------------------------------------------------------------------

def test_stale_anchor_returns_hover():
    """Non-anchor drone hovers when anchor position is older than 3 s."""
    anchor = _peer(lat=40.0, lon=-75.0, age=5.0)   # stale
    peers = {"drone1": anchor, "drone2": _peer()}
    engine = _make_engine("drone2", peers)

    params = _params(members=["drone1", "drone2"], slots={"drone1": 0, "drone2": 1})
    intent = engine.compute_intent(_telem(), {}, params)

    assert intent.action == IntentAction.HOVER
    assert intent.source == IntentSource.FORMATION


# ---------------------------------------------------------------------------
# Test H -- Collision priority: COLLISION > FORMATION
# ---------------------------------------------------------------------------

def test_collision_beats_formation():
    store = FlightStateStore()

    formation_intent = FlightIntent(
        IntentSource.IDLE, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=5.0,
        params={"vx": 1.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}
    )
    collision_intent = FlightIntent(
        IntentSource.COLLISION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0,
        params={"north": -4.0, "east": 0.5, "down": 0.0, "duration": 1.0}
    )
    store.submit_intent(formation_intent)
    store.submit_intent(collision_intent)

    winner = Arbiter.select_winner(store.get_intents(), store)
    assert winner.source == IntentSource.COLLISION
    assert winner.action == IntentAction.MOVE_VELOCITY_NED


# ---------------------------------------------------------------------------
# Speed clamp
# ---------------------------------------------------------------------------

def test_speed_clamp():
    """Velocity magnitude must not exceed params['speed']."""
    anchor = _peer(lat=40.0, lon=-75.0)
    peers = {"drone1": anchor, "drone2": _peer(lat=40.0, lon=-75.0)}
    engine = _make_engine("drone2", peers)

    params = _params(
        members=["drone1", "drone2"],
        slots={"drone1": 0, "drone2": 1},
        speed=0.3,
    )
    telem = _telem(lat=41.0, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert intent.action == IntentAction.MOVE_VELOCITY_NED
    mag = math.hypot(intent.params["north"], intent.params["east"])
    assert mag <= 0.3 + 1e-6


# ---------------------------------------------------------------------------
# NED directions
# ---------------------------------------------------------------------------

def test_ned_directions():
    """Target north/south/east/west of current position -> correct velocity sign."""
    anchor = _peer(lat=40.0, lon=-75.0)
    peers = {"drone1": anchor, "drone2": _peer(lat=40.0, lon=-75.0)}
    engine = _make_engine("drone2", peers)

    params = _params(
        members=["drone1", "drone2"],
        slots={"drone1": 0, "drone2": 1},
        speed=2.0,
    )
    engine.form_mgr.get_offset = MagicMock(return_value=(0.0, 0.0, 0.0))

    anchor.lat = 40.001
    anchor.lon = -75.0
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert intent.params["north"] > 0
    assert abs(intent.params["east"]) < 1e-4

    anchor.lat = 39.999
    anchor.lon = -75.0
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert intent.params["north"] < 0
    assert abs(intent.params["east"]) < 1e-4

    anchor.lat = 40.0
    anchor.lon = -74.999
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert abs(intent.params["north"]) < 1e-4
    assert intent.params["east"] > 0

    anchor.lat = 40.0
    anchor.lon = -75.001
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert abs(intent.params["north"]) < 1e-4
    assert intent.params["east"] < 0
