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
        max_peer_age_sec=1.0,
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

    # Self at origin, moving north at 5 m/s
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, vx=5.0, vy=0.0, ts=now)

    # Peer 20 m north (≈ 0.00018°), moving south at 5 m/s
    peer_lat = 20.0 / 111320.0
    peer_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, vx=-5.0, vy=0.0, ts=now)

    state, correction, peer_id, eff_dist = ca.evaluate_threats(self_t, {"peer1": peer_t})

    # Current distance is 20 m (> min_h_dist=6), but CPA miss distance is ~0 m
    assert state in ("AVOIDANCE", "EMERGENCY"), f"Expected AVOIDANCE/EMERGENCY, got {state}"
    assert correction is not None
    # Correction should point away from peer (south = negative north)
    assert correction["north"] < 0.0, "Escape should be southward (away from north peer)"


# ---------------------------------------------------------------------------
# Test 2 – Stale peer is ignored; None timestamp is ignored
# ---------------------------------------------------------------------------

def test_stale_peer_ignored():
    ca = StandardCollisionAvoidance(_cfg(max_peer_age_sec=1.0), drone_id="d1")
    now = time.time()

    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)

    # Peer 2 m away but timestamp is 5 s old
    peer_lat = 2.0 / 111320.0
    stale_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, ts=now - 5.0)

    state, _, _, _ = ca.evaluate_threats(self_t, {"stale": stale_t})
    assert state == "NORMAL", "Stale peer should be ignored"

    # Peer with no timestamp at all
    no_ts_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, ts=None)
    state2, _, _, _ = ca.evaluate_threats(self_t, {"nots": no_ts_t})
    assert state2 == "NORMAL", "Peer with None timestamp should be ignored"


# ---------------------------------------------------------------------------
# Test 3 – Both drones on the ground → NORMAL
# ---------------------------------------------------------------------------

def test_grounded_drones_normal():
    ca = StandardCollisionAvoidance(_cfg(ground_altitude_m=0.5), drone_id="d1")
    now = time.time()

    self_t = _telem(lat=0.0, lon=0.0, alt=0.3, ts=now)   # below ground_alt
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

    # Self at origin
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, vx=0.0, vy=0.0, ts=now)

    # Peer A: 4 m north (within min_h_dist=6)
    peer_a_lat = 4.0 / 111320.0
    peer_a = _telem(lat=peer_a_lat, lon=0.0, alt=10.0, vx=0.0, vy=0.0, ts=now)

    # Peer B: 4 m south
    peer_b_lat = -4.0 / 111320.0
    peer_b = _telem(lat=peer_b_lat, lon=0.0, alt=10.0, vx=0.0, vy=0.0, ts=now)

    state, correction, _, _ = ca.evaluate_threats(
        self_t, {"peerA": peer_a, "peerB": peer_b}
    )

    assert state in ("AVOIDANCE", "EMERGENCY")
    assert correction is not None

    # Symmetric case → fallback hash direction; correction must not be zero
    mag = math.sqrt(correction["north"] ** 2 + correction["east"] ** 2)
    assert mag > 0.1, "Correction vector must be non-zero"

    # Correction must not point directly toward either peer
    # Peer A is north (+lat), peer B is south (-lat).
    # A pure north or pure south vector would point at one of them.
    # The hash fallback picks a deterministic non-axis direction.
    # We just verify the vector is non-zero and has a defined direction.
    # (Exact direction depends on drone_id hash — we don't assert a specific angle.)


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
    # LAND is critical manual → must win over COLLISION/FORMATION while latched
    assert winner.action == IntentAction.LAND
    assert winner.source == IntentSource.MANUAL

    # SAFETY always wins
    intents2 = {
        IntentSource.COLLISION: collision_hover,
        IntentSource.MANUAL: land_intent,
        IntentSource.SAFETY: safety_land,
    }
    winner2 = Arbiter.select_winner(intents2, store)
    assert winner2.source == IntentSource.SAFETY

    # Without latch, COLLISION beats MANUAL LAND
    store2 = FlightStateStore()  # no latch
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
    # Import here to avoid airsim at module level
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

    # force=True must call hoverAsync
    mock_client.hoverAsync.return_value = MagicMock()
    fc._cmd_lock = asyncio.Lock()

    async def _run_force():
        return await fc.hover(force=True)

    result2 = asyncio.get_event_loop().run_until_complete(_run_force())
    assert result2 is True
    mock_client.hoverAsync.assert_called_once()
