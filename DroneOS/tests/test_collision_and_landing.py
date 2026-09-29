"""
Tests for Part A (landing latch) and Part B (collision avoidance CPA).
DroneOS instance (drone1).
"""
import math
import time
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from DroneOS.core.collision_avoidance import StandardCollisionAvoidance
from DroneOS.shared.config.models import CollisionAvoidanceConfig
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS.core.flight_pipeline import Arbiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**kw) -> CollisionAvoidanceConfig:
    defaults = dict(
        enabled=True,
        min_horizontal_distance=6.0,
        min_vertical_distance=2.0,
        warning_distance=10.0,
        emergency_distance=3.0,
        neighbor_timeout_sec=1.0,
        lookahead_sec=3.0,
        avoidance_speed=4.0,
        emergency_speed=5.0,
        max_peer_age_sec=2.5,
        ground_altitude_m=0.5,
    )
    defaults.update(kw)
    return CollisionAvoidanceConfig(**defaults)


def _telem(lat=0.0, lon=0.0, alt=10.0, vx=0.0, vy=0.0,
           armed="ARMED", gps=True, ts=None) -> TelemetryData:
    return TelemetryData(
        flight_mode="GUIDED",
        gps_valid=gps,
        latitude=lat,
        longitude=lon,
        altitude=alt,
        velocity_x=vx,
        velocity_y=vy,
        armed_state=armed,
        timestamp=ts,
    )


# ---------------------------------------------------------------------------
# Test 1 – Head-on: CPA triggers AVOIDANCE before physical crossing
# ---------------------------------------------------------------------------

def test_head_on_cpa_avoidance():
    """Two drones 20 m apart closing at 5 m/s each; CPA < min_horizontal_distance."""
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()

    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, vx=5.0, vy=0.0, ts=now)

    peer_lat = 20.0 / 111320.0
    peer_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, vx=-5.0, vy=0.0, ts=now)

    state, correction, peer_id, eff_dist = ca.evaluate_threats(self_t, {"peer1": peer_t})

    assert state in ("AVOIDANCE", "EMERGENCY"), f"Expected AVOIDANCE/EMERGENCY, got {state}"
    assert correction is not None
    assert correction["north"] < 0.0, "Escape should be southward (away from north peer)"


# ---------------------------------------------------------------------------
# Test 2 – Stale peer is ignored; None timestamp is ignored
# ---------------------------------------------------------------------------

def test_stale_peer_ignored():
    ca = StandardCollisionAvoidance(_cfg(max_peer_age_sec=1.0), drone_id="d1")
    now = time.time()

    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)

    peer_lat = 2.0 / 111320.0
    stale_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, ts=now - 5.0)

    state, _, _, _ = ca.evaluate_threats(self_t, {"stale": stale_t})
    assert state == "NORMAL", "Stale peer should be ignored"

    no_ts_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, ts=None)
    state2, _, _, _ = ca.evaluate_threats(self_t, {"nots": no_ts_t})
    assert state2 == "NORMAL", "Peer with None timestamp should be ignored"


# ---------------------------------------------------------------------------
# Test 3 – Both drones on the ground → NORMAL
# ---------------------------------------------------------------------------

def test_grounded_drones_normal():
    ca = StandardCollisionAvoidance(_cfg(ground_altitude_m=0.5), drone_id="d1")
    now = time.time()

    self_t = _telem(lat=0.0, lon=0.0, alt=0.3, ts=now)
    peer_lat = 5.0 / 111320.0
    peer_t = _telem(lat=peer_lat, lon=0.0, alt=0.3, ts=now)

    state, _, _, _ = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state == "NORMAL", "Grounded drones should return NORMAL"


# ---------------------------------------------------------------------------
# Test 4 – Three drones: correction is weighted sum, not toward either neighbor
# ---------------------------------------------------------------------------

def test_three_drone_correction_not_toward_neighbors():
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()

    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, vx=0.0, vy=0.0, ts=now)

    peer_a_lat = 4.0 / 111320.0
    peer_a = _telem(lat=peer_a_lat, lon=0.0, alt=10.0, vx=0.0, vy=0.0, ts=now)

    peer_b_lat = -4.0 / 111320.0
    peer_b = _telem(lat=peer_b_lat, lon=0.0, alt=10.0, vx=0.0, vy=0.0, ts=now)

    state, correction, _, _ = ca.evaluate_threats(
        self_t, {"peerA": peer_a, "peerB": peer_b}
    )

    assert state in ("AVOIDANCE", "EMERGENCY")
    assert correction is not None

    mag = math.sqrt(correction["north"] ** 2 + correction["east"] ** 2)
    assert mag > 0.1, "Correction vector must be non-zero"


# ---------------------------------------------------------------------------
# Test 5 – Arbiter with landing latch
# ---------------------------------------------------------------------------

def test_arbiter_landing_latch():
    store = FlightStateStore()
    store.set_landing_latch(seconds=60.0)

    collision_hover = FlightIntent(IntentSource.COLLISION, IntentAction.HOVER, ttl_seconds=5.0)
    formation_hover = FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=5.0)
    land_intent = FlightIntent(IntentSource.MANUAL, IntentAction.LAND, ttl_seconds=15.0)
    safety_land = FlightIntent(IntentSource.SAFETY, IntentAction.LAND, ttl_seconds=5.0)

    intents = {
        IntentSource.COLLISION: collision_hover,
        IntentSource.FORMATION: formation_hover,
        IntentSource.MANUAL: land_intent,
    }

    winner = Arbiter.select_winner(intents, store)
    assert winner.action == IntentAction.LAND
    assert winner.source == IntentSource.MANUAL

    intents2 = {
        IntentSource.COLLISION: collision_hover,
        IntentSource.MANUAL: land_intent,
        IntentSource.SAFETY: safety_land,
    }
    winner2 = Arbiter.select_winner(intents2, store)
    assert winner2.source == IntentSource.SAFETY

    store2 = FlightStateStore()
    winner3 = Arbiter.select_winner(
        {IntentSource.COLLISION: collision_hover, IntentSource.MANUAL: land_intent},
        store2,
    )
    assert winner3.source == IntentSource.COLLISION


# ---------------------------------------------------------------------------
# Test 6 – Adapter hover() does not call hoverAsync while _mode == "LAND"
# ---------------------------------------------------------------------------

def test_adapter_hover_blocked_during_land():
    """hover() must return True without calling hoverAsync when _mode is LAND."""
    from DroneOS.adapters.airsim_adapter import AirSimFlightController
    from DroneOS.shared.config.models import FlightConfig

    cfg = FlightConfig(
        adapter_type="airsim",
        takeoff_altitude=5.0,
        max_velocity=5.0,
        px4_connection_string="",
        airsim_host="127.0.0.1",
        airsim_port=41451,
    )

    fc = AirSimFlightController("Drone1", cfg)
    fc._connected = True
    fc._mode = "LAND"

    mock_client = MagicMock()
    fc.client = mock_client

    result = asyncio.get_event_loop().run_until_complete(fc.hover())
    assert result is True
    mock_client.hoverAsync.assert_not_called()

    mock_client.hoverAsync.return_value = MagicMock()
    fc._cmd_lock = asyncio.Lock()

    async def _run_force():
        return await fc.hover(force=True)

    result2 = asyncio.get_event_loop().run_until_complete(_run_force())
    assert result2 is True
    mock_client.hoverAsync.assert_called_once()


# ---------------------------------------------------------------------------
# Test 9 – Pipeline: COLLISION intent beats FORMATION intent;
#           CommandWriter calls move_velocity_ned
# ---------------------------------------------------------------------------

def test_pipeline_collision_beats_formation():
    """
    FORMATION intent + COLLISION intent => COLLISION wins,
    and CommandWriter calls fc.move_velocity_ned with the collision params.
    """
    from DroneOS.core.flight_pipeline import CommandWriter

    store = FlightStateStore()

    formation_intent = FlightIntent(
        IntentSource.FORMATION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=5.0,
        params={"north": 1.0, "east": 0.0, "down": 0.0, "duration": 1.0}
    )
    collision_intent = FlightIntent(
        IntentSource.COLLISION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0,
        params={"north": -4.0, "east": 0.5, "down": 0.0, "duration": 1.0}
    )

    store.submit_intent(formation_intent)
    store.submit_intent(collision_intent)

    intents = store.get_intents()
    winner = Arbiter.select_winner(intents, store)

    assert winner.source == IntentSource.COLLISION, (
        f"Expected COLLISION to win, got {winner.source}"
    )
    assert winner.action == IntentAction.MOVE_VELOCITY_NED

    mock_fc = MagicMock()
    mock_fc.move_velocity_ned = AsyncMock(return_value=True)

    async def _run():
        cw = CommandWriter(mock_fc)
        return await cw.execute(winner)

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert result is True
    mock_fc.move_velocity_ned.assert_called_once()
    call_args = mock_fc.move_velocity_ned.call_args
    assert call_args[0][0] == pytest.approx(-4.0)   # north
    assert call_args[0][1] == pytest.approx(0.5)    # east


# ---------------------------------------------------------------------------
# Test 10 – Real CA (no mocks): formation peer on head-on trajectory triggers CA
# ---------------------------------------------------------------------------

def test_real_ca_formation_peer_not_exempt():
    """
    A formation peer currently in its slot (20 m away, > emergency_distance*2)
    but closing head-on must still trigger CA.
    Under the old filtered_peer_telemetry logic this peer would be removed
    and CA would return NORMAL.
    """
    ca = StandardCollisionAvoidance(
        _cfg(min_horizontal_distance=6.0, emergency_distance=3.0, lookahead_sec=3.0),
        drone_id="d1"
    )
    now = time.time()

    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, vx=5.0, vy=0.0, ts=now)

    # 20 m north, moving south — head-on, CPA ~0 m in 2 s
    peer_lat = 20.0 / 111320.0
    peer_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, vx=-5.0, vy=0.0, ts=now)

    state, correction, peer_id, eff_dist = ca.evaluate_threats(
        self_t, {"formation_peer": peer_t}
    )

    assert state in ("AVOIDANCE", "EMERGENCY"), (
        f"Formation peer on head-on trajectory must trigger CA, got {state}"
    )
    assert correction is not None
    assert peer_id == "formation_peer"
    assert correction["north"] < 0.0, "Escape must be away from north peer"


# ---------------------------------------------------------------------------
# Test 11 – Formation peer in slot, closing, CA fires
#           (would fail under old filtered_peer_telemetry)
# ---------------------------------------------------------------------------

def test_formation_peer_in_slot_closing_triggers_ca():
    """
    Peer is 8 m north (> emergency_distance*2=6 m, so old code would exempt it),
    both drones closing at 4 m/s each.  CPA in ~1 s at ~0 m miss distance.
    The fixed DE passes ALL peers to CA — this must produce AVOIDANCE/EMERGENCY.
    """
    ca = StandardCollisionAvoidance(
        _cfg(
            min_horizontal_distance=6.0,
            emergency_distance=3.0,
            warning_distance=10.0,
            lookahead_sec=3.0,
            max_peer_age_sec=2.5,
        ),
        drone_id="d1"
    )
    now = time.time()

    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, vx=4.0, vy=0.0, ts=now)

    peer_lat = 8.0 / 111320.0
    peer_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, vx=-4.0, vy=0.0, ts=now)

    state, correction, peer_id, eff_dist = ca.evaluate_threats(
        self_t, {"peer_in_slot": peer_t}
    )

    assert state in ("AVOIDANCE", "EMERGENCY"), (
        f"Peer in formation slot closing head-on must trigger CA, got {state}. "
        f"eff_dist={eff_dist:.2f}m"
    )
    assert correction is not None, "CA must produce a correction vector"
    assert peer_id == "peer_in_slot"
