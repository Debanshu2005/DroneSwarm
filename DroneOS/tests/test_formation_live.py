"""
DroneOS/tests/test_formation_live.py

New unit tests for the live formation execution path.
Covers requirements A-J from the fix specification.

Existing tests in test_formation.py are preserved unchanged.
"""
import asyncio
import math
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from DroneOS.core.formation_manager import (
    FormationManager, FormationType, convert_local_offset_to_global, global_offset_local_m,
)
from DroneOS.core.formation_engine import FormationEngine, _DEFAULT_MIN_SEP_M, _ANCHOR_STALE_SEC
from DroneOS.core.swarm_manager import PeerStateManager, SwarmMembership
from DroneOS.shared.protocol.messages import TelemetryData, TelemetryMessage
from DroneOS.core.intents import IntentSource, IntentAction, FlightIntent
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.flight_pipeline import Arbiter, CommandWriter


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

MEMBERS_4 = ["drone1", "drone2", "drone3", "drone4"]
SLOTS_4   = {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3}

V_PARAMS = {
    "type": "V",
    "spacing": 20.0,   # must be >= 1.5 * max(min_sep=8m, min_ca=2m) = 12.0m
    "members": MEMBERS_4,
    "slot_assignments": SLOTS_4,
    "speed": 2.0,
    "repulsion_radius_m": 1.0,
}


def _peer(lat=40.0, lon=-75.0, alt=10.0, age=0.0):
    p = PeerStateManager("_")
    p.lat = lat
    p.lon = lon
    p.alt = alt
    p.last_position_time = time.time() - age
    p.last_seen = time.time() - age
    p.is_active = True
    return p


def _telem(lat=40.0, lon=-75.0, alt=10.0, gps=True):
    return TelemetryData(
        flight_mode="GUIDED", gps_valid=gps,
        latitude=lat, longitude=lon, altitude=alt,
    )


def _make_engine(my_id, peers=None, min_sep=None):
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


# ---------------------------------------------------------------------------
# A. FORMATION_UPDATE activates formation state
# ---------------------------------------------------------------------------

def test_formation_update_activates_state():
    """FlightManager.formation_update stores params and returns True."""
    from DroneOS.core.flight_manager import FlightManager
    fc = MagicMock()
    fc.config = MagicMock()
    store = FlightStateStore()
    fm = FlightManager(fc, store)
    sm = MagicMock()
    sm.identity.drone_id = "drone1"
    fm.set_swarm_manager(sm)

    import asyncio
    result = asyncio.run(fm.formation_update(V_PARAMS))

    assert result is True
    assert fm.formation_params is not None
    assert fm.formation_params["type"] == "V"
    assert fm.formation_params["spacing"] == 20.0


# ---------------------------------------------------------------------------
# B. FormationEngine produces MOVE_VELOCITY for a valid non-anchor drone
# ---------------------------------------------------------------------------

def test_formation_engine_produces_move_velocity_for_non_anchor():
    """Non-anchor drone with valid anchor telemetry must get MOVE_VELOCITY."""
    anchor = _peer(lat=40.0, lon=-75.0)
    peers = {"drone1": anchor}
    engine = _make_engine("drone2", peers)

    params = {
        "type": "V", "spacing": 10.0,
        "members": ["drone1", "drone2"],
        "slot_assignments": {"drone1": 0, "drone2": 1},
        "speed": 2.0, "repulsion_radius_m": 1.0,
    }
    # drone2 is far from its slot target so it has something to move toward
    telem = _telem(lat=40.0, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert intent.source == IntentSource.FORMATION
    assert intent.action == IntentAction.MOVE_VELOCITY_NED
    mag = math.hypot(intent.params["north"], intent.params["east"])
    assert mag > 0.01, f"Expected non-zero velocity, got {mag}"


# ---------------------------------------------------------------------------
# C. Missing anchor telemetry -> HOVER with diagnostic log
# ---------------------------------------------------------------------------

def test_missing_anchor_telemetry_returns_hover_with_log(caplog):
    """Non-anchor drone with no anchor peer -> HOVER, logs warning."""
    import logging
    import time
    from unittest.mock import patch
    peers = {}  # anchor not in registry
    engine = _make_engine("drone2", peers)

    params = {
        "type": "V", "spacing": 10.0,
        "members": ["drone1", "drone2"],
        "slot_assignments": {"drone1": 0, "drone2": 1},
        "speed": 2.0, "repulsion_radius_m": 1.0,
    }
    
    # Initialize the engine to set _formation_activated_at
    engine.compute_intent(_telem(), {}, params)
    
    # Advance time past grace period
    from DroneOS.core.formation_engine import _FORMATION_STARTUP_GRACE_SEC
    with patch("time.time", return_value=time.time() + _FORMATION_STARTUP_GRACE_SEC + 1.0):
        with caplog.at_level(logging.WARNING, logger="FormationEngine"):
            intent = engine.compute_intent(_telem(), {}, params)

    assert intent.action == IntentAction.HOVER
    assert intent.source == IntentSource.FORMATION
    # Must log a warning about stale/missing anchor
    warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("stale or missing" in r.message for r in warning_logs)

def test_missing_anchor_telemetry_returns_hover_with_info_in_grace(caplog):
    """Non-anchor drone with no anchor peer -> HOVER, logs INFO during grace period."""
    import logging
    peers = {}  # anchor not in registry
    engine = _make_engine("drone2", peers)

    params = {
        "type": "V", "spacing": 10.0,
        "members": ["drone1", "drone2"],
        "slot_assignments": {"drone1": 0, "drone2": 1},
        "speed": 2.0, "repulsion_radius_m": 1.0,
    }
    with caplog.at_level(logging.INFO, logger="FormationEngine"):
        intent = engine.compute_intent(_telem(), {}, params)

    assert intent.action == IntentAction.HOVER
    assert intent.source == IntentSource.FORMATION
    info_logs = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("position not yet available" in r.message for r in info_logs)


# ---------------------------------------------------------------------------
# D. Valid anchor telemetry -> non-zero formation movement
# ---------------------------------------------------------------------------

def test_valid_anchor_produces_nonzero_movement():
    """Fresh anchor position must produce non-zero MOVE_VELOCITY."""
    anchor = _peer(lat=40.0, lon=-75.0, age=0.0)
    peers = {"drone1": anchor}
    engine = _make_engine("drone2", peers)

    params = {
        "type": "V", "spacing": 10.0,
        "members": ["drone1", "drone2"],
        "slot_assignments": {"drone1": 0, "drone2": 1},
        "speed": 2.0, "repulsion_radius_m": 1.0,
    }
    # Place drone2 at anchor position so it has a non-zero error to its slot
    telem = _telem(lat=40.0, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert intent.action == IntentAction.MOVE_VELOCITY_NED
    mag = math.hypot(intent.params["north"], intent.params["east"])
    assert mag > 0.01


# ---------------------------------------------------------------------------
# E. FORMATION intent reaches CommandWriter (via FlightStateStore + Arbiter)
# ---------------------------------------------------------------------------

def test_formation_intent_reaches_command_writer():
    """FORMATION MOVE_VELOCITY submitted to store wins arbiter over IDLE."""
    store = FlightStateStore()
    formation_intent = FlightIntent(
        IntentSource.FORMATION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=5.0,
        params={"vx": 1.0, "vy": 0.5, "vz": 0.0, "yaw_rate": 0.0}
    )
    store.submit_intent(formation_intent)

    winner = Arbiter.select_winner(store.get_intents(), store)
    assert winner.source == IntentSource.FORMATION
    assert winner.action == IntentAction.MOVE_VELOCITY_NED


# ---------------------------------------------------------------------------
# F. AirSim adapter receives FORMATION MOVE_VELOCITY
# ---------------------------------------------------------------------------

def test_airsim_adapter_receives_formation_move_velocity():
    """CommandWriter.execute with FORMATION MOVE_VELOCITY calls fc.move_velocity."""
    fc = MagicMock()
    fc.move_velocity = AsyncMock(return_value=True)
    fc.move_velocity_ned = AsyncMock(return_value=True)
    writer = CommandWriter(fc)

    intent = FlightIntent(
        IntentSource.FORMATION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0,
        params={"north": 1.5, "east": -0.5, "down": 0.0, "yaw_rate": 0.0}
    )

    import asyncio
    result = asyncio.run(writer.execute(intent))

    assert result is True
    fc.move_velocity_ned.assert_called_once()
    call_args = fc.move_velocity_ned.call_args[0]
    assert call_args[0] == pytest.approx(1.5)
    assert call_args[1] == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# G. Formation does not remain permanently HOVER due to stale peer data
#    (i.e. fresh telemetry -> movement, not permanent hover)
# ---------------------------------------------------------------------------

def test_fresh_peer_data_does_not_cause_permanent_hover():
    """With fresh anchor data, engine must NOT return HOVER."""
    anchor = _peer(lat=40.0, lon=-75.0, age=0.1)  # 0.1s old — fresh
    peers = {"drone1": anchor}
    engine = _make_engine("drone2", peers)

    params = {
        "type": "V", "spacing": 10.0,
        "members": ["drone1", "drone2"],
        "slot_assignments": {"drone1": 0, "drone2": 1},
        "speed": 2.0, "repulsion_radius_m": 1.0,
    }
    telem = _telem(lat=40.0, lon=-75.0)
    intent = engine.compute_intent(telem, {}, params)

    assert intent.action != IntentAction.HOVER, (
        "Fresh anchor data must not produce HOVER"
    )


# ---------------------------------------------------------------------------
# H. 10m V formation has correct slot geometry
# ---------------------------------------------------------------------------

def test_v_formation_10m_slot_geometry():
    """
    V formation at 10m spacing, 4 drones.
    Formula: tier=ceil(index/2), sign=+1 if odd else -1
      slot 0: (0,    0)    anchor
      slot 1: (-10, +10)   tier=1, sign=+1
      slot 2: (-10, -10)   tier=1, sign=-1
      slot 3: (-20, +20)   tier=2, sign=+1
    All pairwise distances >= 8m (min_formation_separation_m).
    """
    mgr = FormationManager()
    mgr.set_formation(FormationType.V, 10.0)
    total = 4

    s0 = mgr.get_offset(0, total)
    s1 = mgr.get_offset(1, total)
    s2 = mgr.get_offset(2, total)
    s3 = mgr.get_offset(3, total)

    assert s0[:2] == pytest.approx((0.0,   0.0), abs=1e-9)
    assert s1[:2] == pytest.approx((-10.0, +10.0), abs=1e-9)
    assert s2[:2] == pytest.approx((-10.0, -10.0), abs=1e-9)
    assert s3[:2] == pytest.approx((-20.0, +20.0), abs=1e-9)

    # All pairwise distances must be >= min_formation_separation_m=8m
    slots = [s0, s1, s2, s3]
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            dist = math.hypot(slots[i][0] - slots[j][0], slots[i][1] - slots[j][1])
            assert dist >= 8.0, f"Slot {i}<->slot {j} distance {dist:.2f}m < 8m"


# ---------------------------------------------------------------------------
# I. Heartbeat disappearance does not reshuffle slots
# ---------------------------------------------------------------------------

def test_heartbeat_disappearance_does_not_reshuffle_slots():
    """
    drone3 disappears from heartbeat registry.
    drone4 must still be slot 3, not slot 2.
    """
    peers = {
        "drone1": _peer(lat=40.0, lon=-75.0),
        "drone2": _peer(lat=40.0, lon=-75.001),
        # drone3 absent
        "drone4": _peer(lat=40.0, lon=-75.003),
    }
    engine = _make_engine("drone4", peers)

    # slot_assignments still contains drone3 — roster is fixed from FORMATION_UPDATE
    assert engine._my_slot(V_PARAMS) == 3
    assert engine._total_drones(V_PARAMS) == 4


# ---------------------------------------------------------------------------
# J. Collision intent still beats formation
# ---------------------------------------------------------------------------

def test_collision_beats_formation_intent():
    """COLLISION source must win over FORMATION source in Arbiter."""
    store = FlightStateStore()

    store.submit_intent(FlightIntent(
        IntentSource.FORMATION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=5.0,
        params={"vx": 1.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}
    ))
    store.submit_intent(FlightIntent(
        IntentSource.COLLISION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0,
        params={"north": -4.0, "east": 0.5, "down": 0.0}
    ))

    winner = Arbiter.select_winner(store.get_intents(), store)
    assert winner.source == IntentSource.COLLISION
    assert winner.action == IntentAction.MOVE_VELOCITY_NED


# ---------------------------------------------------------------------------
# Telemetry sync fix: PeerSynchronization.handle_telemetry updates position
# ---------------------------------------------------------------------------

def test_handle_telemetry_updates_peer_position():
    """
    After the fix, handle_telemetry must update peer.lat/lon/alt/last_position_time
    so FormationEngine can find valid anchor positions from telemetry messages.
    """
    sm = SwarmMembership("drone2")
    sm.registry.add_peer("drone1")

    telem_data = TelemetryData(
        flight_mode="HOLD", gps_valid=True,
        latitude=40.1234, longitude=-75.5678, altitude=10.0,
    )
    msg = TelemetryMessage(
        sender_id="drone1", timestamp=time.time(), telemetry=telem_data
    )
    sm.sync.handle_telemetry(msg)

    peer = sm.registry.get_peer("drone1")
    assert peer.lat == pytest.approx(40.1234)
    assert peer.lon == pytest.approx(-75.5678)
    assert peer.alt == pytest.approx(10.0)
    assert peer.last_position_time is not None
    assert (time.time() - peer.last_position_time) < 1.0


def test_handle_telemetry_no_update_when_gps_invalid():
    """handle_telemetry must NOT update position when gps_valid is False."""
    sm = SwarmMembership("drone2")
    sm.registry.add_peer("drone1")

    telem_data = TelemetryData(
        flight_mode="HOLD", gps_valid=False,
        latitude=40.1234, longitude=-75.5678, altitude=10.0,
    )
    msg = TelemetryMessage(
        sender_id="drone1", timestamp=time.time(), telemetry=telem_data
    )
    sm.sync.handle_telemetry(msg)

    peer = sm.registry.get_peer("drone1")
    assert peer.lat is None
    assert peer.last_position_time is None


# ---------------------------------------------------------------------------
# Stale anchor (age > _ANCHOR_STALE_SEC) -> HOVER
# ---------------------------------------------------------------------------

def test_stale_anchor_age_returns_hover():
    """Anchor position older than _ANCHOR_STALE_SEC must produce HOVER."""
    anchor = _peer(lat=40.0, lon=-75.0, age=_ANCHOR_STALE_SEC + 0.5)
    peers = {"drone1": anchor}
    engine = _make_engine("drone2", peers)

    params = {
        "type": "V", "spacing": 10.0,
        "members": ["drone1", "drone2"],
        "slot_assignments": {"drone1": 0, "drone2": 1},
        "speed": 2.0, "repulsion_radius_m": 1.0,
    }
    intent = engine.compute_intent(_telem(), {}, params)
    assert intent.action == IntentAction.HOVER


# ---------------------------------------------------------------------------
# Anchor drone (slot 0) always HOVERs
# ---------------------------------------------------------------------------

def test_anchor_drone_returns_idle():
    """Slot-0 anchor drone must always return IDLE from FormationEngine."""
    engine = _make_engine("drone1", {})
    intent = engine.compute_intent(_telem(), {}, V_PARAMS)
    assert intent.action == IntentAction.IDLE
    assert intent.source == IntentSource.IDLE
