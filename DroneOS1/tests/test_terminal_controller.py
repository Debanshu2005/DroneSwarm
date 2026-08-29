import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
import asyncio
import math

from DroneOS1.core.terminal_controller import TerminalController
from DroneOS1.core.interfaces import IFlightController
from DroneOS1.core.command_handler import CommandHandler
from DroneOS1.shared.protocol.messages import CommandAction, TelemetryData
from DroneOS1.shared.nlp.trajectory_engine import parse_task_sequence

@pytest.fixture
def mocks():
    command_handler = MagicMock(spec=CommandHandler)
    command_handler.handle_command = AsyncMock(return_value=True)
    
    flight_controller = MagicMock(spec=IFlightController)
    default_telemetry = TelemetryData(
        flight_mode="GUIDED",
        armed_state="ARMED",
        gps_valid=True,
        global_pos_valid=True,
        local_pos_valid=True,
        latitude=0.0,
        longitude=0.0,
        altitude=0.1
    )
    flight_controller.get_telemetry = AsyncMock(return_value=default_telemetry)
    
    return command_handler, flight_controller

@pytest.fixture
def terminal_controller(mocks):
    command_handler, flight_controller = mocks
    return TerminalController(command_handler, flight_controller, "test-node")

@pytest.mark.asyncio
async def test_simple_sequence_success_and_early_abort(terminal_controller, mocks):
    command_handler, flight_controller = mocks
    
    # "takeoff to 5m, hover for 2 seconds, and land"
    sequence = parse_task_sequence("takeoff to 5m, hover for 2 seconds, and land")
    
    # 1. Test full success
    for task in sequence.tasks:
        success = await terminal_controller._execute_task(task, "test-node")
        assert success is True
        
    assert command_handler.handle_command.call_count == 4
    calls = command_handler.handle_command.call_args_list
    assert calls[0].args[0].action == CommandAction.TAKEOFF
    assert calls[0].args[0].params == {"altitude_m": 5.0}
    assert calls[1].args[0].action == CommandAction.HOVER
    assert calls[2].args[0].action == CommandAction.LAND
    assert calls[3].args[0].action == CommandAction.DISARM
    
    command_handler.handle_command.reset_mock()
    
    # 2. Test early abort (takeoff fails)
    command_handler.handle_command.return_value = False
    
    # Simulating the run_repl loop logic
    executed = 0
    for task in sequence.tasks:
        success = await terminal_controller._execute_task(task, "test-node")
        executed += 1
        if not success:
            break
            
    assert executed == 1
    assert command_handler.handle_command.call_count == 1
    assert command_handler.handle_command.call_args.args[0].action == CommandAction.TAKEOFF

@pytest.mark.asyncio
async def test_circle_gps_valid(terminal_controller, mocks):
    command_handler, flight_controller = mocks
    
    # GPS-valid telemetry fixture
    telemetry = TelemetryData(
        flight_mode="GUIDED",
        gps_valid=True,
        global_pos_valid=True,
        local_pos_valid=True,
        latitude=47.397742,
        longitude=8.545594,
        altitude=10.0
    )
    
    # For _run_waypoints, it polls get_telemetry() to check distance.
    # We mock get_telemetry to return the target's position immediately so it doesn't wait.
    # We will just make it return the target lat/lon dynamically, but since the target changes, 
    # we can make a side_effect that checks the latest GOTO command.
    
    last_target = {"lat": 47.397742, "lon": 8.545594}
    
    async def mock_handle_command(msg):
        if msg.action == CommandAction.GOTO:
            last_target["lat"] = msg.params["lat"]
            last_target["lon"] = msg.params["lon"]
        return True
        
    command_handler.handle_command.side_effect = mock_handle_command
    
    async def mock_get_telemetry():
        return TelemetryData(
            flight_mode="GUIDED",
            gps_valid=True,
            global_pos_valid=True,
            local_pos_valid=True,
            latitude=last_target["lat"],
            longitude=last_target["lon"],
            altitude=10.0
        )
        
    flight_controller.get_telemetry.side_effect = mock_get_telemetry

    sequence = parse_task_sequence("circle with 3m radius")
    assert len(sequence.tasks) == 1
    
    success = await terminal_controller._execute_task(sequence.tasks[0], "test-node")
    assert success is True
    
    # Circle generates multiple waypoints (e.g. 37 for n=36 segments)
    # Check that multiple GOTO calls were made
    assert command_handler.handle_command.call_count > 10
    for call_args in command_handler.handle_command.call_args_list:
        assert call_args.args[0].action == CommandAction.GOTO

@pytest.mark.asyncio
async def test_circle_degraded(terminal_controller, mocks):
    command_handler, flight_controller = mocks
    
    # MODE_C_DEGRADED telemetry fixture (no GPS, no local pos)
    telemetry = TelemetryData(
        flight_mode="GUIDED",
        gps_valid=False,
        global_pos_valid=False,
        local_pos_valid=False,
        latitude=None,
        longitude=None,
        altitude=0.0
    )
    flight_controller.get_telemetry.return_value = telemetry
    
    sequence = parse_task_sequence("circle with 3m radius")
    
    # Execute should return False because build_trajectory raises ValueError
    success = await terminal_controller._execute_task(sequence.tasks[0], "test-node")
    
    assert success is False
    assert command_handler.handle_command.call_count == 0

@pytest.mark.asyncio
async def test_process_text_network_sender(terminal_controller, mocks):
    command_handler, flight_controller = mocks
    
    # ensure network is mock
    terminal_controller.network = MagicMock()
    terminal_controller.network.broadcast_message = AsyncMock()

    await terminal_controller.process_text("takeoff to 5m", sender_id="gs-phone-1")

    assert command_handler.handle_command.call_count == 1
    call_args = command_handler.handle_command.call_args[0][0]
    assert call_args.action == CommandAction.TAKEOFF
    assert call_args.sender_id == "gs-phone-1"
    
    assert terminal_controller.network.broadcast_message.call_count == 1

@pytest.mark.asyncio
async def test_hold_maps_to_hover_not_stop(terminal_controller, mocks):
    command_handler, flight_controller = mocks
    
    await terminal_controller.process_text("hold", sender_id="test-1")
    
    assert command_handler.handle_command.call_count == 1
    call_args = command_handler.handle_command.call_args[0][0]
    
    # Must explicitly assert it is HOVER and NOT STOP
    assert call_args.action == CommandAction.HOVER
    assert call_args.action != CommandAction.STOP

@pytest.mark.asyncio
async def test_takeoff_arm_race_condition_success(terminal_controller, mocks):
    command_handler, flight_controller = mocks
    
    # Mock telemetry to return DISARMED first, then ARMED on subsequent calls
    telemetry_disarmed = TelemetryData(
        flight_mode="GUIDED",
        armed_state="DISARMED",
        gps_valid=True,
        global_pos_valid=True,
        local_pos_valid=True,
        latitude=0.0,
        longitude=0.0,
        altitude=0.0
    )
    telemetry_armed = TelemetryData(
        flight_mode="GUIDED",
        armed_state="ARMED",
        gps_valid=True,
        global_pos_valid=True,
        local_pos_valid=True,
        latitude=0.0,
        longitude=0.0,
        altitude=0.0
    )
    
    flight_controller.get_telemetry.side_effect = [
        telemetry_disarmed, # Initial check
        telemetry_disarmed, # First poll
        telemetry_armed     # Second poll
    ]
    
    await terminal_controller.process_text("takeoff to 5m", sender_id="test-1")
    
    assert command_handler.handle_command.call_count == 2
    calls = command_handler.handle_command.call_args_list
    assert calls[0].args[0].action == CommandAction.ARM
    assert calls[1].args[0].action == CommandAction.TAKEOFF

@pytest.mark.asyncio
async def test_takeoff_arm_timeout(terminal_controller, mocks):
    command_handler, flight_controller = mocks
    
    # Mock telemetry to always return DISARMED
    telemetry_disarmed = TelemetryData(
        flight_mode="GUIDED",
        armed_state="DISARMED",
        gps_valid=True,
        global_pos_valid=True,
        local_pos_valid=True,
        latitude=0.0,
        longitude=0.0,
        altitude=0.0
    )
    flight_controller.get_telemetry.return_value = telemetry_disarmed
    
    # Use a shorter timeout to speed up the test without patching time (which breaks asyncio)
    terminal_controller.ARM_TIMEOUT = 0.1
    await terminal_controller.process_text("takeoff to 5m", sender_id="test-1")
    
    assert command_handler.handle_command.call_count == 1
    calls = command_handler.handle_command.call_args_list
    assert calls[0].args[0].action == CommandAction.ARM
