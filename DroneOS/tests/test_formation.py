"""
Formation engine tests — explicit slot_assignments.

Tests:
  1. Deterministic assignment: sorted members → stable slot indices
  2. Heartbeat loss: drone4 stays slot 3 even when drone3 disappears
  3. Unique targets: four-drone V at 8m → four distinct coordinates
  4. Missing assignment: drone not in slot_assignments → HOVER + FORMATION_NO_SLOT
  5. Collision priority: COLLISION intent beats FORMATION intent
  6. Stale anchor: non-anchor drone hovers when anchor position is stale
  7. Speed clamp: velocity magnitude never exceeds params['speed']
  8. NED directions: target north/south/east/west produce correct velocity signs
"""
import math
import time
import pytest
from unittest.mock import MagicMock

from DroneOS.core.formation_manager import (
    FormationManager, FormationType, convert_local_offset_to_global,
)
from DroneOS.core.formation_engine import FormationEngine
from DroneOS.core.swarm_manager import PeerStateManager
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.core.intents import IntentSource, IntentAction, FlightIntent
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.flight_pipeline import Arbiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MEMBERS_4 = ["drone1", "drone2", "drone3", "drone4"]
SLOTS_4 = {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3}

def _params(drone_type="V", spacing=8.0, members=None, slots=None, speed=2.0):
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

def _make_engine(my_id: str, peers: dict = None):
    """Build a FormationEngine with a mock swarm for drone `my_id`."""
    sm = MagicMock()
    sm.identity.drone_id = my_id
    sm.registry.peers = peers or {}
    sm.registry.get_peer.side_effect = lambda x: sm.registry.peers.get(x)
    return FormationEngine(sm, MagicMock())

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
# Test 1 — Deterministic assignment
# ---------------------------------------------------------------------------

def test_deterministic_slot_assignment():
    """sorted(members) → members[i] gets slot i, regardless of insertion order."""
    members = ["drone4", "drone1", "drone3", "drone2"]   # unsorted input
    members_sorted = sorted(members)
    slots = {m: i for i, m in enumerate(members_sorted)}

    assert slots["drone1"] == 0
    assert slots["drone2"] == 1
    assert slots["drone3"] == 2
    assert slots["drone4"] == 3

    # Engine reads slot directly from the map — no re-sorting at runtime
    engine = _make_engine("drone3")
    params = _params(slots=slots, members=members_sorted)
    assert engine._my_slot(params) == 2
    assert engine._anchor_id(params) == "drone1"


# ---------------------------------------------------------------------------
# Test 2 — Heartbeat loss does NOT reshuffle slots
# ---------------------------------------------------------------------------

def test_heartbeat_loss_does_not_reshuffle():
    """
    Initial: drone1=0, drone2=1, drone3=2, drone4=3.
    drone3 disappears from heartbeat.
    drone4 must still be slot 3, NOT slot 2.
    """
    # Simulate drone3 gone from registry (stale / removed)
    peers = {
        "drone1": _peer(lat=40.0, lon=-75.0),
        "drone2": _peer(lat=40.0, lon=-75.001),
        # drone3 absent
        "drone4": _peer(lat=40.0, lon=-75.003),
    }
    engine = _make_engine("drone4", peers)
    params = _params()   # still has drone3 in slot_assignments

    # Slot must come from the stored assignment, not from live peer list
    assert engine._my_slot(params) == 3, (
        "drone4 must remain slot 3 even when drone3 is absent from heartbeat"
    )
    assert engine._total_drones(params) == 4   # roster size unchanged


# ---------------------------------------------------------------------------
# Test 3 — Unique targets for four-drone V at 8m
# ---------------------------------------------------------------------------

def test_unique_formation_targets():
    """All four drones must receive distinct (lat, lon) targets."""
    mgr = FormationManager()
    mgr.set_formation(FormationType.V, 8.0)
    anchor_lat, anchor_lon, anchor_alt = 40.0, -75.0, 10.0
    total = 4

    targets = {}
    for drone_id, slot in SLOTS_4.items():
        dx_n, dy_e, _ = mgr.get_offset(slot, total)
        t_lat, t_lon, _ = convert_local_offset_to_global(
            anchor_lat, anchor_lon, anchor_alt, dx_n, dy_e
        )
        targets[drone_id] = (t_lat, t_lon)

    # All four coordinates must be distinct
    coords = list(targets.values())
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            dist_n = (coords[i][0] - coords[j][0]) * 111320
            dist_e = (coords[i][1] - coords[j][1]) * 111320
            dist = math.sqrt(dist_n**2 + dist_e**2)
            assert dist > 0.1, (
                f"Drones at indices {i} and {j} have the same target: {coords[i]}"
            )

    # Slot 0 (anchor) is at (0,0) offset → same as anchor position
    assert targets["drone1"] == pytest.approx((anchor_lat, anchor_lon), abs=1e-9)

    # Log for visibility
    for drone_id, (lat, lon) in targets.items():
        slot = SLOTS_4[drone_id]
        print(f"FORMATION_TARGET drone={drone_id} slot={slot} lat={lat:.7f} lon={lon:.7f}")


# ---------------------------------------------------------------------------
# Test 4 — Missing assignment → HOVER + FORMATION_NO_SLOT
# ---------------------------------------------------------------------------

def test_missing_slot_returns_hover(caplog):
    """A drone not in slot_assignments must return HOVER and log FORMATION_NO_SLOT."""
    import logging
    peers = {"drone1": _peer()}
    engine = _make_engine("drone_unknown", peers)

    params = _params()   # slot_assignments has drone1-4, not drone_unknown

    with caplog.at_level(logging.WARNING, logger="FormationEngine"):
        intent = engine.compute_intent(_telem(), {}, params)

    assert intent.action == IntentAction.HOVER
    assert intent.source == IntentSource.FORMATION
    assert "FORMATION_NO_SLOT" in caplog.text


# ---------------------------------------------------------------------------
# Test 5 — Collision priority: COLLISION > FORMATION
# ---------------------------------------------------------------------------

def test_collision_beats_formation():
    store = FlightStateStore()

    formation_intent = FlightIntent(
        IntentSource.FORMATION, IntentAction.MOVE_VELOCITY, ttl_seconds=5.0,
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
# Test 6 — Stale anchor → HOVER
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
# Test 7 — Speed clamp
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
    # Large position error: drone2 is 1 degree away
    telem = _telem(lat=41.0, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert intent.action == IntentAction.MOVE_VELOCITY
    mag = math.hypot(intent.params["vx"], intent.params["vy"])
    assert mag <= 0.3 + 1e-6


# ---------------------------------------------------------------------------
# Test 8 — NED directions
# ---------------------------------------------------------------------------

def test_ned_directions():
    """Target north/south/east/west of current position → correct velocity sign."""
    anchor = _peer(lat=40.0, lon=-75.0)
    peers = {"drone1": anchor, "drone2": _peer(lat=40.0, lon=-75.0)}
    engine = _make_engine("drone2", peers)

    params = _params(
        members=["drone1", "drone2"],
        slots={"drone1": 0, "drone2": 1},
        speed=2.0,
    )
    # Override offset to zero so target == anchor position
    engine.form_mgr.get_offset = MagicMock(return_value=(0.0, 0.0, 0.0))

    # Target North
    anchor.lat = 40.001
    anchor.lon = -75.0
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert intent.params["vx"] > 0
    assert abs(intent.params["vy"]) < 1e-4

    # Target South
    anchor.lat = 39.999
    anchor.lon = -75.0
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert intent.params["vx"] < 0
    assert abs(intent.params["vy"]) < 1e-4

    # Target East
    anchor.lat = 40.0
    anchor.lon = -74.999
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert abs(intent.params["vx"]) < 1e-4
    assert intent.params["vy"] > 0

    # Target West
    anchor.lat = 40.0
    anchor.lon = -75.001
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert abs(intent.params["vx"]) < 1e-4
    assert intent.params["vy"] < 0
