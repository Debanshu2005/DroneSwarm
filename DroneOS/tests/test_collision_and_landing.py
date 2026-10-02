"""
Tests for collision avoidance (8f5b44c baseline), pipeline priority,
and real DecisionEngine integration.
DroneOS instance (drone1).
"""
import math
import time
import asyncio
from unittest.mock import AsyncMock, MagicMock

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
        neighbor_timeout_sec=5.0,
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
# CA unit tests
# ---------------------------------------------------------------------------

def test_avoidance_within_min_horizontal_distance():
    """Peer inside min_horizontal_distance triggers AVOIDANCE with a correction."""
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    # ~5.5 m south — inside min_h_dist=6.0
    peer_t = _telem(lat=-0.00005, lon=0.0, alt=10.0, ts=now)
    state, correction, peer_id, dist = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state == "AVOIDANCE"
    assert correction is not None
    assert correction["north"] > 0.0   # escape northward (away from south peer)


def test_emergency_within_emergency_distance():
    """Peer inside emergency_distance triggers EMERGENCY."""
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    # ~2.2 m south — inside emg_dist=3.0
    peer_t = _telem(lat=-0.00002, lon=0.0, alt=10.0, ts=now)
    state, correction, peer_id, dist = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state == "EMERGENCY"
    assert correction is not None


def test_warning_between_min_h_and_warn_dist():
    """Peer between min_h_dist and warn_dist triggers WARNING with no correction."""
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    # ~7.7 m south — between min_h_dist=6 and warn_dist=10
    peer_t = _telem(lat=-0.00007, lon=0.0, alt=10.0, ts=now)
    state, correction, peer_id, dist = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state == "WARNING"
    assert correction is not None


def test_normal_beyond_warn_dist():
    """Peer beyond warn_dist returns NORMAL."""
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    peer_t = _telem(lat=0.001, lon=0.0, alt=10.0, ts=now)   # ~111 m away
    state, correction, _, _ = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state == "NORMAL"
    assert correction is None


def test_stale_peer_ignored():
    """Peer older than neighbor_timeout_sec is ignored."""
    ca = StandardCollisionAvoidance(_cfg(neighbor_timeout_sec=2.0), drone_id="d1")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    peer_t = _telem(lat=-0.00005, lon=0.0, alt=10.0, ts=now - 5.0)
    state, _, _, _ = ca.evaluate_threats(self_t, {"stale": peer_t})
    assert state == "NORMAL"


def test_no_timestamp_peer_evaluated():
    """Peer with no timestamp is NOT filtered (8f5b44c behavior: only skip if stale)."""
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    peer_t = _telem(lat=-0.00005, lon=0.0, alt=10.0, ts=None)
    state, correction, _, _ = ca.evaluate_threats(self_t, {"peer1": peer_t})
    # No timestamp → not stale → evaluated → AVOIDANCE
    assert state == "AVOIDANCE"


def test_vertically_separated_peer_ignored():
    """Peer with alt_diff > min_v_dist is ignored."""
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    peer_t = _telem(lat=-0.00005, lon=0.0, alt=20.0, ts=now)  # 10 m above
    state, _, _, _ = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state == "NORMAL"


def test_invalid_gps_peer_ignored():
    """Peer with gps_valid=False is ignored."""
    ca = StandardCollisionAvoidance(_cfg(), drone_id="d1")
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    peer_t = _telem(lat=-0.00005, lon=0.0, alt=10.0, gps=False, ts=now)
    state, _, _, _ = ca.evaluate_threats(self_t, {"peer1": peer_t})
    assert state == "NORMAL"


# ---------------------------------------------------------------------------
# Arbiter / pipeline tests
# ---------------------------------------------------------------------------

def test_arbiter_landing_latch():
    store = FlightStateStore()
    store.set_landing_latch(seconds=60.0)
    collision_hover = FlightIntent(IntentSource.COLLISION, IntentAction.HOVER, ttl_seconds=5.0)
    formation_hover = FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=5.0)
    land_intent = FlightIntent(IntentSource.MANUAL, IntentAction.LAND, ttl_seconds=15.0)
    safety_land = FlightIntent(IntentSource.SAFETY, IntentAction.LAND, ttl_seconds=5.0)

    winner = Arbiter.select_winner(
        {IntentSource.COLLISION: collision_hover,
         IntentSource.FORMATION: formation_hover,
         IntentSource.MANUAL: land_intent}, store
    )
    assert winner.action == IntentAction.LAND
    assert winner.source == IntentSource.MANUAL

    winner2 = Arbiter.select_winner(
        {IntentSource.COLLISION: collision_hover,
         IntentSource.MANUAL: land_intent,
         IntentSource.SAFETY: safety_land}, store
    )
    assert winner2.source == IntentSource.SAFETY

    store2 = FlightStateStore()
    winner3 = Arbiter.select_winner(
        {IntentSource.COLLISION: collision_hover, IntentSource.MANUAL: land_intent}, store2
    )
    assert winner3.source == IntentSource.COLLISION


def test_adapter_hover_blocked_during_land():
    from DroneOS.adapters.airsim_adapter import AirSimFlightController
    from DroneOS.shared.config.models import FlightConfig

    cfg = FlightConfig(
        adapter_type="airsim", takeoff_altitude=5.0, max_velocity=5.0,
        px4_connection_string="", airsim_host="127.0.0.1", airsim_port=41451,
    )
    fc = AirSimFlightController("Drone1", cfg)
    fc._connected = True
    fc._mode = "LAND"
    mock_client = MagicMock()
    fc.client = mock_client

    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(fc.hover())
    assert result is True
    mock_client.hoverAsync.assert_not_called()

    mock_client.hoverAsync.return_value = MagicMock()
    fc._cmd_lock = asyncio.Lock()

    async def _run_force():
        return await fc.hover(force=True)

    result2 = loop.run_until_complete(_run_force())
    loop.close()
    assert result2 is True
    mock_client.hoverAsync.assert_called_once()


# ---------------------------------------------------------------------------
# Test 9 – Pipeline: COLLISION beats FORMATION; CommandWriter calls
#           move_velocity_ned
# ---------------------------------------------------------------------------

def test_pipeline_collision_beats_formation():
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

    winner = Arbiter.select_winner(store.get_intents(), store)
    assert winner.source == IntentSource.COLLISION
    assert winner.action == IntentAction.MOVE_VELOCITY_NED

    mock_fc = MagicMock()
    mock_fc.move_velocity_ned = AsyncMock(return_value=True)

    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(CommandWriter(mock_fc).execute(winner))
    loop.close()
    assert result is True
    mock_fc.move_velocity_ned.assert_called_once()
    assert mock_fc.move_velocity_ned.call_args[0][0] == pytest.approx(-4.0)
    assert mock_fc.move_velocity_ned.call_args[0][1] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Test 8 – Real DecisionEngine integration: peer in SwarmRegistry at
#           AVOIDANCE distance → COLLISION intent inserted into FlightStateStore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decision_engine_inserts_collision_intent():
    """
    Real CA (no mocks on evaluate_threats).
    Peer placed directly in SwarmRegistry at ~5 m (inside min_h_dist=6 m).
    evaluate_tick() must insert a COLLISION intent into FlightStateStore.
    """
    from DroneOS.core.decision_engine import LocalDecisionEngine
    from DroneOS.core.collision_avoidance import StandardCollisionAvoidance
    from DroneOS.core.swarm_manager import SwarmMembership, PeerStateManager
    from DroneOS.core.flight_state import FlightStateStore

    # Build real CA with tight thresholds
    ca_cfg = _cfg(
        min_horizontal_distance=6.0,
        emergency_distance=3.0,
        warning_distance=10.0,
        neighbor_timeout_sec=5.0,
    )
    ca = StandardCollisionAvoidance(ca_cfg, drone_id="drone1")

    swarm = SwarmMembership("drone1")
    state_store = FlightStateStore()

    # Minimal mocks for the parts DE touches but we don't care about
    safety = MagicMock()
    safety.is_failsafe_active = False

    mission = MagicMock()
    mission.get_current_state.return_value = "IDLE"

    nav = MagicMock()
    nav.flight_manager.formation_params = None

    engine = LocalDecisionEngine(
        mission_manager=mission,
        swarm_manager=swarm,
        collision_avoidance=ca,
        navigation_manager=nav,
        safety_module=safety,
        state_store=state_store,
    )

    # Self telemetry: airborne, GPS valid
    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)

    # Peer at ~5.5 m south — inside min_h_dist=6 m → AVOIDANCE
    peer_t = _telem(lat=-0.00005, lon=0.0, alt=10.0, ts=now)

    # Inject peer directly into SwarmRegistry
    peer_state = PeerStateManager("drone2")
    peer_state.telemetry = peer_t
    swarm.registry.peers["drone2"] = peer_state

    await engine.evaluate_tick(self_t)

    intents = state_store.get_intents()
    assert IntentSource.COLLISION in intents, (
        "COLLISION intent must be in FlightStateStore after evaluate_tick with close peer"
    )
    collision_intent = intents[IntentSource.COLLISION]
    assert collision_intent.action == IntentAction.MOVE_VELOCITY_NED
    assert collision_intent.params.get("north") is not None


# ---------------------------------------------------------------------------
# Test 9b – Real pipeline integration: formation active + collision condition
#            → Arbiter selects COLLISION → CommandWriter calls move_velocity_ned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_real_ca_formation_plus_collision():
    """
    Formation intent is active.
    Real CA detects peer at AVOIDANCE distance via evaluate_tick.
    Arbiter must select COLLISION.
    Mocked FC at CommandWriter boundary must receive move_velocity_ned call.
    """
    from DroneOS.core.decision_engine import LocalDecisionEngine
    from DroneOS.core.collision_avoidance import StandardCollisionAvoidance
    from DroneOS.core.swarm_manager import SwarmMembership, PeerStateManager
    from DroneOS.core.flight_state import FlightStateStore
    from DroneOS.core.flight_pipeline import Arbiter, CommandWriter

    ca_cfg = _cfg(
        min_horizontal_distance=6.0,
        emergency_distance=3.0,
        warning_distance=10.0,
        neighbor_timeout_sec=5.0,
    )
    ca = StandardCollisionAvoidance(ca_cfg, drone_id="drone1")

    swarm = SwarmMembership("drone1")
    state_store = FlightStateStore()

    safety = MagicMock()
    safety.is_failsafe_active = False
    mission = MagicMock()
    mission.get_current_state.return_value = "IDLE"
    nav = MagicMock()
    nav.flight_manager.formation_params = None  # formation handled via pre-submitted intent

    engine = LocalDecisionEngine(
        mission_manager=mission,
        swarm_manager=swarm,
        collision_avoidance=ca,
        navigation_manager=nav,
        safety_module=safety,
        state_store=state_store,
    )

    # Pre-submit a FORMATION intent (simulates formation engine output)
    formation_intent = FlightIntent(
        IntentSource.FORMATION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=5.0,
        params={"north": 2.0, "east": 0.0, "down": 0.0}
    )
    state_store.submit_intent(formation_intent)

    now = time.time()
    self_t = _telem(lat=0.0, lon=0.0, alt=10.0, ts=now)
    peer_t = _telem(lat=-0.00005, lon=0.0, alt=10.0, ts=now)  # ~5.5 m → AVOIDANCE

    peer_state = PeerStateManager("drone2")
    peer_state.telemetry = peer_t
    swarm.registry.peers["drone2"] = peer_state

    await engine.evaluate_tick(self_t)

    intents = state_store.get_intents()
    assert IntentSource.COLLISION in intents, "COLLISION intent must be present"

    winner = Arbiter.select_winner(intents, state_store)
    assert winner.source == IntentSource.COLLISION, (
        f"Arbiter must select COLLISION over FORMATION, got {winner.source}"
    )
    assert winner.action == IntentAction.MOVE_VELOCITY_NED

    mock_fc = MagicMock()
    mock_fc.move_velocity_ned = AsyncMock(return_value=True)
    result = await CommandWriter(mock_fc).execute(winner)
    assert result is True
    mock_fc.move_velocity_ned.assert_called_once()
