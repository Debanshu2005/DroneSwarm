from unittest.mock import AsyncMock, MagicMock
import pytest
import asyncio

from DroneOS.shared.nlp.trajectory_engine import parse_task_sequence, TaskAction
from DroneOS.core.flight_manager import FlightManager
from DroneOS.core.intents import IntentSource, IntentAction
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.interfaces import IFlightController
from DroneOS.shared.protocol.messages import TelemetryData

@pytest.fixture
def mock_fc():
    fc = MagicMock(spec=IFlightController)
    fc.get_telemetry = AsyncMock()
    fc.get_home_position = AsyncMock()
    fc.goto_location = AsyncMock()
    fc.land = AsyncMock()
    fc.hover = AsyncMock()
    return fc

@pytest.fixture
def state_store():
    return FlightStateStore()

@pytest.mark.asyncio
async def test_srtl_parsing():
    seq1 = parse_task_sequence("srtl")
    assert seq1.tasks[0].action == TaskAction.SRTL

    seq2 = parse_task_sequence("smart rtl")
    assert seq2.tasks[0].action == TaskAction.SRTL

    seq3 = parse_task_sequence("rtl")
    assert seq3.tasks[0].action == TaskAction.RTL

@pytest.mark.asyncio
async def test_smart_rtl_rejection_low_altitude(mock_fc, state_store):
    manager = FlightManager(mock_fc, state_store, min_srtl_altitude_m=2.0)
    
    # Inject telemetry into state store directly
    state_store.local_telemetry = TelemetryData(
        altitude=1.5, flight_mode="GUIDED", gps_valid=True
    )
    
    result = await manager.smart_rtl()
    assert result is False
    assert IntentSource.MANUAL not in state_store.active_intents

@pytest.mark.asyncio
async def test_smart_rtl_rejection_no_home(mock_fc, state_store):
    manager = FlightManager(mock_fc, state_store, min_srtl_altitude_m=2.0)
    
    state_store.local_telemetry = TelemetryData(
        altitude=5.0, flight_mode="GUIDED", gps_valid=True
    )
    mock_fc.get_home_position.return_value = None
    
    result = await manager.smart_rtl()
    assert result is False
    assert IntentSource.MANUAL not in state_store.active_intents

@pytest.mark.asyncio
async def test_smart_rtl_state_machine_lifecycle(mock_fc, state_store):
    manager = FlightManager(mock_fc, state_store, min_srtl_altitude_m=2.0)
    
    # 1. Trigger Smart RTL
    state_store.local_telemetry = TelemetryData(
        altitude=5.0, flight_mode="GUIDED", gps_valid=True,
        latitude=10.0, longitude=20.0, armed_state="ARMED"
    )
    mock_fc.get_home_position.return_value = (10.0005, 20.0, 0.0) # Approx 55 meters North
    
    result = await manager.smart_rtl()
    assert result is True
    assert state_store.smart_rtl_active is True
    assert state_store.smart_rtl_target == (10.0005, 20.0, 5.0)
    
    # 2. Evaluate engine - expect GOTO (NAVIGATING)
    from DroneOS.core.smart_rtl_engine import SmartRtlEngine
    from DroneOS.shared.config.models import SmartRtlConfig
    class MockConfig:
        smart_rtl = SmartRtlConfig(arrival_radius_m=2.0, timeout_s=60.0)
    
    engine = SmartRtlEngine(MockConfig())
    intent = engine.compute_intent(state_store)
    
    assert intent is not None
    assert intent.action == IntentAction.GOTO
    assert intent.params["lat"] == 10.0005
    assert intent.params["alt"] == 5.0 # Maintains altitude
    assert engine.internal_state == "NAVIGATING"
    
    # 3. Inject telemetry inside arrival radius
    state_store.local_telemetry.latitude = 10.000499 # Very close to 10.0005
    state_store.local_telemetry.longitude = 20.0
    
    intent = engine.compute_intent(state_store)
    assert intent is not None
    assert intent.action == IntentAction.LAND
    assert engine.internal_state == "LANDING"
    
    # 4. Inject disarmed/landed state
    state_store.local_telemetry.armed_state = "DISARMED"
    intent = engine.compute_intent(state_store)
    
    assert intent is None
    assert state_store.smart_rtl_active is False
    assert engine.internal_state == "COMPLETE"
