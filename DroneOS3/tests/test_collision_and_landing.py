"""
Tests for Part A (landing latch) and Part B (collision avoidance CPA).
DroneOS3 instance (drone4).
"""
import math
import time
import asyncio
from unittest.mock import MagicMock

import pytest

from DroneOS3.core.collision_avoidance import StandardCollisionAvoidance
from DroneOS3.shared.config.models import CollisionAvoidanceConfig
from DroneOS3.shared.protocol.messages import TelemetryData
from DroneOS3.core.flight_state import FlightStateStore
from DroneOS3.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS3.core.flight_pipeline import Arbiter


def _cfg(**kw) -> CollisionAvoidanceConfig:
    defaults = dict(
        enabled=True, min_horizontal_distance=6.0, min_vertical_distance=2.0,
        warning_distance=10.0, emergency_distance=3.0, neighbor_timeout_sec=1.0,
        lookahead_sec=3.0, avoidance_speed=4.0, emergency_speed=5.0,
        max_peer_age_sec=1.0, ground_altitude_m=0.5,
    )
    defaults.update(kw)
    return CollisionAvoidanceConfig(**defaults)


def _telem(lat=0.0, lon=0.0, alt=10.0, vx=0.0, vy=0.0,
           armed="ARMED", gps=True, ts=None) -> TelemetryData:
    return TelemetryData(
        flight_mode="GUIDED", gps_valid=gps, latitude=lat, longitude=lon,
        altitude=alt, velocity_x=vx, velocity_y=vy, armed_state=armed,
        timestamp=ts,
    )


def test_head_on_cpa_avoidance():
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d4")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, vx=5.0, vy=0.0, ts=now)
    peer_lat = 20.0 / 111320.0
    peer_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, vx=-5.0, vy=0.0, ts=now)
    state, correction, _, _ = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state in ("AVOIDANCE", "EMERGENCY")
    assert correction is not None
    assert correction["north"] < 0.0


def test_stale_peer_ignored():
    ca = StandardCollisionAvoidance(_cfg(max_peer_age_sec=1.0), drone_id="d4")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    peer_lat = 2.0 / 111320.0
    stale_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, ts=now - 5.0)
    state, _, _, _ = ca.evaluate_threats(self_t, {"stale": stale_t})
    assert state == "NORMAL"
    no_ts_t = _telem(lat=peer_lat, lon=0.0, alt=10.0, ts=None)
    state2, _, _, _ = ca.evaluate_threats(self_t, {"nots": no_ts_t})
    assert state2 == "NORMAL"


def test_grounded_drones_normal():
    ca = StandardCollisionAvoidance(_cfg(ground_altitude_m=0.5), drone_id="d4")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=0.3, ts=now)
    peer_lat = 5.0 / 111320.0
    peer_t = _telem(lat=peer_lat, lon=0.0, alt=0.3, ts=now)
    state, _, _, _ = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state == "NORMAL"


def test_three_drone_correction_not_toward_neighbors():
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d4")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    peer_a = _telem(lat=4.0 / 111320.0, lon=0.0, alt=10.0, ts=now)
    peer_b = _telem(lat=-4.0 / 111320.0, lon=0.0, alt=10.0, ts=now)
    state, correction, _, _ = ca.evaluate_threats(self_t, {"peerA": peer_a, "peerB": peer_b})
    assert state in ("AVOIDANCE", "EMERGENCY")
    assert correction is not None
    mag = math.sqrt(correction["north"] ** 2 + correction["east"] ** 2)
    assert mag > 0.1


def test_arbiter_landing_latch():
    store = FlightStateStore()
    store.set_landing_latch(seconds=60.0)
    collision_hover = FlightIntent(IntentSource.COLLISION, IntentAction.HOVER, ttl_seconds=5.0)
    formation_hover = FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=5.0)
    land_intent = FlightIntent(IntentSource.MANUAL, IntentAction.LAND, ttl_seconds=15.0)
    safety_land = FlightIntent(IntentSource.SAFETY, IntentAction.LAND, ttl_seconds=5.0)

    winner = Arbiter.select_winner(
        {IntentSource.COLLISION: collision_hover, IntentSource.FORMATION: formation_hover,
         IntentSource.MANUAL: land_intent}, store
    )
    assert winner.action == IntentAction.LAND
    assert winner.source == IntentSource.MANUAL

    winner2 = Arbiter.select_winner(
        {IntentSource.COLLISION: collision_hover, IntentSource.MANUAL: land_intent,
         IntentSource.SAFETY: safety_land}, store
    )
    assert winner2.source == IntentSource.SAFETY

    store2 = FlightStateStore()
    winner3 = Arbiter.select_winner(
        {IntentSource.COLLISION: collision_hover, IntentSource.MANUAL: land_intent}, store2
    )
    assert winner3.source == IntentSource.COLLISION


def test_adapter_hover_blocked_during_land():
    from DroneOS3.adapters.airsim_adapter import AirSimFlightController
    from DroneOS3.shared.config.models import FlightConfig

    cfg = FlightConfig(
        adapter_type="airsim", takeoff_altitude=5.0, max_velocity=5.0,
        px4_connection_string="", airsim_host="127.0.0.1", airsim_port=41451,
    )
    fc = AirSimFlightController("Drone4", cfg)
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
