import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from DroneOS.core.intents import IntentSource, IntentAction

packages = ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]

@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", packages)
async def test_parity_formation_update_no_slot(pkg_name):
    # dynamic import
    FlightManager = __import__(f"{pkg_name}.core.flight_manager", fromlist=['FlightManager']).FlightManager
    
    fc = AsyncMock()
    store = MagicMock()
    fm = FlightManager(fc, store)
    sm = MagicMock()
    sm.identity.drone_id = "drone1"
    fm.set_swarm_manager(sm)
    
    fm.formation_params = {"old": "data"}
    params = {"type": "V"}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.formation_params == {"old": "data"}

@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", packages)
async def test_parity_formation_update_missing_my_slot(pkg_name):
    FlightManager = __import__(f"{pkg_name}.core.flight_manager", fromlist=['FlightManager']).FlightManager
    
    fc = AsyncMock()
    store = MagicMock()
    fm = FlightManager(fc, store)
    sm = MagicMock()
    sm.identity.drone_id = "drone1"
    fm.set_swarm_manager(sm)
    
    fm.formation_params = {"old": "data"}
    params = {"type": "V", "slot_assignments": {"drone2": 0}}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.formation_params == {"old": "data"}

@pytest.mark.parametrize("pkg_name", packages)
def test_parity_formation_engine_no_slot(pkg_name):
    FormationEngine = __import__(f"{pkg_name}.core.formation_engine", fromlist=['FormationEngine']).FormationEngine
    
    sm = MagicMock()
    sm.identity.drone_id = "drone4"
    store = MagicMock()
    engine = FormationEngine(sm, store)
    
    telemetry = MagicMock()
    telemetry.gps_valid = True
    telemetry.latitude = 10.0
    telemetry.longitude = 10.0
    
    params = {"type": "V", "slot_assignments": {"drone1": 0}}
    intent = engine.compute_intent(telemetry, {}, params)
    
    assert intent.source == IntentSource.IDLE
    assert intent.action == IntentAction.IDLE
