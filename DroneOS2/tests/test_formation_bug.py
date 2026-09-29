import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from DroneOS2.core.flight_manager import FlightManager
from DroneOS2.core.formation_engine import FormationEngine
from DroneOS2.core.terminal_controller import TerminalController
from DroneOS2.core.intents import IntentAction, IntentSource
from DroneOS2.shared.nlp.trajectory_engine import TaskAction, ParsedTask

@pytest.mark.asyncio
async def test_formation_update_no_slot_assignments():
    fc = AsyncMock()
    store = MagicMock()
    fm = FlightManager(fc, store)
    sm = MagicMock()
    sm.identity.drone_id = "drone1"
    fm.set_swarm_manager(sm)
    
    fm.formation_params = {"old": "data"}
    
    # Missing slot_assignments
    params = {"type": "V"}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.formation_params == {"old": "data"}
    
@pytest.mark.asyncio
async def test_formation_update_missing_my_slot():
    fc = AsyncMock()
    store = MagicMock()
    fm = FlightManager(fc, store)
    sm = MagicMock()
    sm.identity.drone_id = "drone1"
    fm.set_swarm_manager(sm)
    
    fm.formation_params = {"old": "data"}
    
    # Missing my drone in slot_assignments
    params = {"type": "V", "slot_assignments": {"drone2": 0, "drone3": 1}}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.formation_params == {"old": "data"}

def test_formation_engine_no_slot_idle():
    sm = MagicMock()
    sm.identity.drone_id = "drone4"
    store = MagicMock()
    engine = FormationEngine(sm, store)
    
    telemetry = MagicMock()
    telemetry.gps_valid = True
    telemetry.latitude = 10.0
    telemetry.longitude = 10.0
    
    params = {"type": "V", "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2}}
    intent = engine.compute_intent(telemetry, {}, params)
    
    assert intent.source == IntentSource.IDLE
    assert intent.action == IntentAction.IDLE

@pytest.mark.asyncio
async def test_terminal_formation_slot_assignments():
    ch = AsyncMock()
    fc = AsyncMock()
    tc = TerminalController(ch, fc, "drone2")
    
    sm = MagicMock()
    # Mock registry
    sm.registry.get_all_peers.return_value = ["drone1", "drone3", "drone4"]
    peer1 = MagicMock(); peer1.last_seen = 1e9
    peer3 = MagicMock(); peer3.last_seen = 1e9
    peer4 = MagicMock(); peer4.last_seen = 1e9
    
    def get_peer(pid):
        if pid == "drone1": return peer1
        if pid == "drone3": return peer3
        if pid == "drone4": return peer4
        return None
        
    sm.registry.get_peer.side_effect = get_peer
    sm.heartbeat_mgr.timeout_sec = 100.0  # Make sure they are fresh
    import time
    time.time = MagicMock(return_value=1e9)
    
    tc.swarm_manager = sm
    tc.network = AsyncMock()
    
    task = ParsedTask(action=TaskAction.FORMATION, params={"type": "CIRCLE", "spacing": 15.0}, raw_text="")
    await tc._execute_task(task, "drone2")
    
    # Check what was sent to command_handler
    ch.handle_command.assert_called_once()
    msg = ch.handle_command.call_args[0][0]
    
    assert msg.params["type"] == "CIRCLE"
    assert msg.params["spacing"] == 15.0
    assert msg.params["members"] == ["drone1", "drone2", "drone3", "drone4"]
    assert msg.params["slot_assignments"] == {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3}
