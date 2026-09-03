from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
import pytest

from DroneOS2.core.safety import SafetyModule
from DroneOS2.core.intents import IntentSource, IntentAction
from DroneOS2.core.flight_state import FlightStateStore
from DroneOS2.shared.protocol.messages import TelemetryData

@pytest.fixture
def state_store():
    return FlightStateStore()

@pytest.fixture
def safety_module(state_store):
    fc = AsyncMock()
    return SafetyModule(fc, state_store, config=SimpleNamespace(profile="outdoor"))

@pytest.mark.asyncio
async def test_emergency_stop_kills_and_aborts_mission(state_store, safety_module):
    safety_module.set_mission_manager(SimpleNamespace(abort_mission=MagicMock()))

    await safety_module.trigger_emergency_stop()

    intent = state_store.active_intents.get(IntentSource.SAFETY)
    assert intent is not None
    assert intent.action == IntentAction.EMERGENCY_KILL
    
    safety_module.mission_manager.abort_mission.assert_called_once()

@pytest.mark.asyncio
async def test_connection_lost_disarmed_waits(state_store):
    fc = AsyncMock()
    state_store.local_telemetry = TelemetryData(flight_mode="GUIDED", armed_state="DISARMED")
    safety = SafetyModule(fc, state_store, config=SimpleNamespace(profile="outdoor"))

    await safety.trigger_connection_lost_failsafe()

    assert IntentSource.SAFETY not in state_store.active_intents

@pytest.mark.asyncio
async def test_connection_lost_indoor_lands(state_store):
    fc = AsyncMock()
    state_store.local_telemetry = TelemetryData(flight_mode="GUIDED", armed_state="ARMED", home_valid=True)
    safety = SafetyModule(fc, state_store, config=SimpleNamespace(profile="indoor"))

    await safety.trigger_connection_lost_failsafe()

    intent = state_store.active_intents.get(IntentSource.SAFETY)
    assert intent is not None
    assert intent.action == IntentAction.LAND

@pytest.mark.asyncio
async def test_connection_lost_outdoor_uses_rtl_when_home_valid(state_store):
    fc = AsyncMock()
    state_store.local_telemetry = TelemetryData(flight_mode="GUIDED", armed_state="ARMED", home_valid=True)
    safety = SafetyModule(fc, state_store, config=SimpleNamespace(profile="outdoor"))

    await safety.trigger_connection_lost_failsafe()

    intent = state_store.active_intents.get(IntentSource.SAFETY)
    assert intent is not None
    assert intent.action == IntentAction.RTL

@pytest.mark.asyncio
async def test_connection_lost_outdoor_lands_when_home_invalid(state_store):
    fc = AsyncMock()
    state_store.local_telemetry = TelemetryData(flight_mode="GUIDED", armed_state="ARMED", home_valid=False)
    safety = SafetyModule(fc, state_store, config=SimpleNamespace(profile="outdoor"))

    await safety.trigger_connection_lost_failsafe()

    intent = state_store.active_intents.get(IntentSource.SAFETY)
    assert intent is not None
    assert intent.action == IntentAction.LAND

@pytest.mark.asyncio
async def test_low_battery_indoor_lands_and_outdoor_rtls():
    indoor_fc = AsyncMock()
    outdoor_fc = AsyncMock()
    
    indoor_state = FlightStateStore()
    outdoor_state = FlightStateStore()

    await SafetyModule(indoor_fc, indoor_state, config=SimpleNamespace(profile="indoor")).trigger_low_battery_failsafe()
    await SafetyModule(outdoor_fc, outdoor_state, config=SimpleNamespace(profile="outdoor")).trigger_low_battery_failsafe()

    indoor_intent = indoor_state.active_intents.get(IntentSource.SAFETY)
    outdoor_intent = outdoor_state.active_intents.get(IntentSource.SAFETY)
    
    assert indoor_intent.action == IntentAction.LAND
    assert outdoor_intent.action == IntentAction.RTL

@pytest.mark.asyncio
async def test_critical_battery_lands_and_gps_degraded_hovers(state_store, safety_module):
    await safety_module.trigger_critical_battery_failsafe()
    
    intent = state_store.active_intents.get(IntentSource.SAFETY)
    assert intent.action == IntentAction.LAND
    
    # Reset intent
    state_store.active_intents.clear()
    
    await safety_module.trigger_gps_degraded_failsafe()
    intent = state_store.active_intents.get(IntentSource.SAFETY)
    assert intent.action == IntentAction.HOVER
