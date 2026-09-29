"""
Formation engine tests for DroneOS3 — explicit slot_assignments + separation safety layer.

Tests:
  A. Deterministic slot assignment
  B. Heartbeat loss does not reshuffle slots
  C. Unique target positions (V formation, 10m spacing)
  D. Minimum formation separation: too-close peer limits velocity
  D2. Proportional velocity reduction at dist = min_sep/2
  E. Normal formation movement when safely separated
  F. Missing assignment -> HOVER + FORMATION_NO_SLOT
  G. Stale anchor -> HOVER
  H. Collision priority: COLLISION > FORMATION
  Speed clamp, NED directions
"""
import math
import time
import pytest
from unittest.mock import MagicMock

from DroneOS3.core.formation_manager import (
    FormationManager, FormationType, convert_local_offset_to_global,
)
from DroneOS3.core.formation_engine import FormationEngine, _DEFAULT_MIN_SEP_M
from DroneOS3.core.swarm_manager import PeerStateManager
from DroneOS3.shared.protocol.messages import TelemetryData
from DroneOS3.core.intents import IntentSource, IntentAction, FlightIntent
from DroneOS3.core.flight_state import FlightStateStore
from DroneOS3.core.flight_pipeline import Arbiter


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
    members = ["drone4", "drone1", "drone3", "drone2"]
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
    peers = {
        "drone1": _peer(lat=40.0, lon=-75.0),
        "drone2": _peer(lat=40.0, lon=-75.001),
        # drone3 absent
        "drone4": _peer(lat=40.0, lon=-75.003),
    }
    engine = _make_engine("drone4", peers)
    params = _params()

    assert engine._my_slot(params) == 3
    assert engine._total_drones(params) == 4


# ---------------------------------------------------------------------------
# Test C -- Unique targets for four-drone V at 10m + pairwise distances
# ---------------------------------------------------------------------------

def test_unique_formation_targets():
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
            assert dist > 1.0

    assert min(pairwise) >= 10.0
    assert targets["drone1"] == pytest.approx((anchor_lat, anchor_lon), abs=1e-9)


# ---------------------------------------------------------------------------
# Test D -- Minimum formation separation: too-close peer limits velocity
# ---------------------------------------------------------------------------

def test_separation_limits_velocity_when_too_close():
    min_sep = 8.0
    anchor = _peer(lat=40.0, lon=-75.0)
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

    telem = _telem(lat=40.0, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert engine._my_slot(params) == 2

    if intent.action == IntentAction.MOVE_VELOCITY:
        mag = math.hypot(intent.params["vx"], intent.params["vy"])
        assert mag < 2.0
    else:
        assert intent.action == IntentAction.HOVER


def test_separation_reduces_velocity_proportionally():
    min_sep = 8.0
    anchor = _peer(lat=40.0, lon=-75.0)
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

    if intent.action == IntentAction.MOVE_VELOCITY:
        mag = math.hypot(intent.params["vx"], intent.params["vy"])
        assert mag <= 1.0 + 1e-6
    else:
        assert intent.action == IntentAction.HOVER


# ---------------------------------------------------------------------------
# Test E -- Normal formation movement when safely separated
# ---------------------------------------------------------------------------

def test_normal_movement_when_safely_separated():
    min_sep = 8.0
    anchor = _peer(lat=40.0, lon=-75.0)
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

    telem = _telem(lat=40.0 + 5.0 / 111320, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert intent.action == IntentAction.MOVE_VELOCITY
    mag = math.hypot(intent.params["vx"], intent.params["vy"])
    assert mag > 0.01


# ---------------------------------------------------------------------------
# Test F -- Missing assignment -> HOVER + FORMATION_NO_SLOT
# ---------------------------------------------------------------------------

def test_missing_slot_returns_hover(caplog):
    import logging
    peers = {"drone1": _peer()}
    engine = _make_engine("drone_unknown", peers)

    params = _params()

    with caplog.at_level(logging.WARNING, logger="FormationEngine"):
        intent = engine.compute_intent(_telem(), {}, params)

    assert intent.action == IntentAction.HOVER
    assert intent.source == IntentSource.FORMATION
    assert "FORMATION_NO_SLOT" in caplog.text


# ---------------------------------------------------------------------------
# Test G -- Stale anchor -> HOVER
# ---------------------------------------------------------------------------

def test_stale_anchor_returns_hover():
    anchor = _peer(lat=40.0, lon=-75.0, age=5.0)
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
# Speed clamp
# ---------------------------------------------------------------------------

def test_speed_clamp():
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

    assert intent.action == IntentAction.MOVE_VELOCITY
    mag = math.hypot(intent.params["vx"], intent.params["vy"])
    assert mag <= 0.3 + 1e-6


# ---------------------------------------------------------------------------
# NED directions
# ---------------------------------------------------------------------------

def test_ned_directions():
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
    assert intent.params["vx"] > 0
    assert abs(intent.params["vy"]) < 1e-4

    anchor.lat = 39.999
    anchor.lon = -75.0
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert intent.params["vx"] < 0
    assert abs(intent.params["vy"]) < 1e-4

    anchor.lat = 40.0
    anchor.lon = -74.999
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert abs(intent.params["vx"]) < 1e-4
    assert intent.params["vy"] > 0

    anchor.lat = 40.0
    anchor.lon = -75.001
    intent = engine.compute_intent(_telem(lat=40.0, lon=-75.0), {}, params)
    assert abs(intent.params["vx"]) < 1e-4
    assert intent.params["vy"] < 0
