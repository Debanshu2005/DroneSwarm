import pytest
from unittest.mock import AsyncMock, MagicMock

from DroneOS.core.terminal_controller import TerminalController
from DroneOS.core.interfaces import IFlightController
from DroneOS.core.command_handler import CommandHandler
from DroneOS.shared.protocol.messages import CommandAction, TelemetryData
from DroneOS.shared.nlp.trajectory_engine import parse_task_sequence

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
    
    # Success sends "Parsed: ..." then "Done"
    assert terminal_controller.network.broadcast_message.call_count == 2
    msgs = [c[0][0] for c in terminal_controller.network.broadcast_message.call_args_list]
    assert "parsed: takeoff" in msgs[0].status_text.lower()
    assert msgs[1].status_text == "Done"
    assert msgs[1].severity == "info"

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

@pytest.mark.asyncio
async def test_formation_update(terminal_controller, mocks):
    command_handler, flight_controller = mocks
    from unittest.mock import MagicMock
    terminal_controller.swarm_manager = MagicMock()
    terminal_controller.swarm_manager.registry.get_all_peers.return_value = []
    terminal_controller.swarm_manager.heartbeat_mgr.timeout_sec = 100.0
    terminal_controller.network = AsyncMock()
    
    # "form circle spacing 15m" (must be >= 12m for default config)
    await terminal_controller.process_text("form circle spacing 15m", sender_id="test-1")
    
    assert command_handler.handle_command.call_count == 1
    call_args = command_handler.handle_command.call_args[0][0]
    
    assert call_args.action == CommandAction.FORMATION_UPDATE
    assert call_args.params["type"] == "CIRCLE"
    assert call_args.params["spacing"] == 15.0


@pytest.mark.asyncio
async def test_parse_failure_sends_error_status(terminal_controller):
    terminal_controller.network = MagicMock()
    terminal_controller.network.broadcast_message = AsyncMock()

    # "goto" without coordinates/distance raises ValueError in parser
    await terminal_controller.process_text("goto", sender_id="gs-phone-1")

    assert terminal_controller.network.broadcast_message.call_count == 1
    msg = terminal_controller.network.broadcast_message.call_args[0][0]
    assert msg.severity == "error"
    assert msg.target_id == "gs-phone-1"
    assert msg.status_text.startswith("Could not understand: goto")


@pytest.mark.asyncio
async def test_task_failure_sends_error_status(terminal_controller, mocks):
    command_handler, _ = mocks
    terminal_controller.network = MagicMock()
    terminal_controller.network.broadcast_message = AsyncMock()

    command_handler.handle_command.return_value = False

    await terminal_controller.process_text("takeoff to 5m", sender_id="gs-phone-1")

    # Call 0: Parsed: TAKEOFF, Call 1: Error status
    assert terminal_controller.network.broadcast_message.call_count == 2
    msgs = [c[0][0] for c in terminal_controller.network.broadcast_message.call_args_list]
    assert msgs[0].severity == "info"
    assert "parsed: takeoff" in msgs[0].status_text.lower()
    assert msgs[1].severity == "error"
    assert msgs[1].target_id == "gs-phone-1"
    assert "Task TAKEOFF failed:" in msgs[1].status_text


@pytest.mark.asyncio
async def test_task_arm_timeout_sends_error_status(terminal_controller, mocks):
    _, flight_controller = mocks
    terminal_controller.network = MagicMock()
    terminal_controller.network.broadcast_message = AsyncMock()

    # Drone stays DISARMED
    disarmed = TelemetryData(
        flight_mode="GUIDED",
        armed_state="DISARMED",
        gps_valid=True,
        global_pos_valid=True,
        local_pos_valid=True,
        latitude=0.0,
        longitude=0.0,
        altitude=0.0
    )
    flight_controller.get_telemetry.return_value = disarmed
    terminal_controller.ARM_TIMEOUT = 0.05

    await terminal_controller.process_text("takeoff to 5m", sender_id="gs-phone-1")

    assert terminal_controller.network.broadcast_message.call_count == 2
    msgs = [c[0][0] for c in terminal_controller.network.broadcast_message.call_args_list]
    assert msgs[1].severity == "error"
    assert "arm timeout" in msgs[1].status_text


@pytest.mark.asyncio
async def test_formation_rejection_reason_sends_error_status(terminal_controller, mocks):
    command_handler, _ = mocks
    terminal_controller.swarm_manager = MagicMock()
    terminal_controller.swarm_manager.registry.get_all_peers.return_value = []
    terminal_controller.swarm_manager.heartbeat_mgr.timeout_sec = 100.0
    terminal_controller.network = MagicMock()
    terminal_controller.network.broadcast_message = AsyncMock()

    # FlightManager sets last_rejection_reason when rejecting
    fm = MagicMock()
    fm.last_rejection_reason = "formation rejected: no slot for test-node"
    command_handler.flight_manager = fm
    command_handler.handle_command.return_value = False

    await terminal_controller.process_text("form v spacing 15m", sender_id="gs-phone-1")

    msgs = [c[0][0] for c in terminal_controller.network.broadcast_message.call_args_list]
    err_msgs = [m for m in msgs if m.severity == "error"]
    assert len(err_msgs) == 1
    assert "formation rejected: no slot for test-node" in err_msgs[0].status_text


@pytest.mark.asyncio
async def test_formation_spacing_below_minimum_rejected(terminal_controller, mocks):
    terminal_controller.swarm_manager = MagicMock()
    terminal_controller.swarm_manager.registry.get_all_peers.return_value = []
    terminal_controller.swarm_manager.heartbeat_mgr.timeout_sec = 100.0
    terminal_controller.network = MagicMock()
    terminal_controller.network.broadcast_message = AsyncMock()

    # Under default config, min_viable = 12m. Spacing 5m must reject early with guidance.
    await terminal_controller.process_text("form v spacing 5m", sender_id="gs-phone-1")

    msgs = [c[0][0] for c in terminal_controller.network.broadcast_message.call_args_list]
    err_msgs = [m for m in msgs if m.severity == "error"]
    assert len(err_msgs) == 1
    assert "spacing 5 m is below the minimum 12 m; try 'form v spacing 12m'" in err_msgs[0].status_text


@pytest.mark.asyncio
async def test_formation_spacing_sim_config_accepted(terminal_controller, mocks):
    command_handler, _ = mocks
    terminal_controller.swarm_manager = MagicMock()
    terminal_controller.swarm_manager.registry.get_all_peers.return_value = []
    terminal_controller.swarm_manager.heartbeat_mgr.timeout_sec = 100.0
    terminal_controller.network = MagicMock()
    terminal_controller.network.broadcast_message = AsyncMock()

    # Under sim config (min_sep=4.0, min_ca=1.5 -> min_viable=6.0m), spacing 10m is accepted
    sim_cfg = MagicMock()
    sim_cfg.formation.min_formation_separation_m = 4.0
    sim_cfg.collision_avoidance.min_horizontal_distance = 1.5
    fm = MagicMock()
    fm._flight_config = sim_cfg
    command_handler.flight_manager = fm

    await terminal_controller.process_text("form v spacing 10m", sender_id="gs-phone-1")

    msgs = [c[0][0] for c in terminal_controller.network.broadcast_message.call_args_list]
    assert any(m.status_text == "Done" for m in msgs)
    assert command_handler.handle_command.call_count == 1
    call_args = command_handler.handle_command.call_args[0][0]
    assert call_args.params["spacing"] == 10.0


def test_natural_formation_phrasing_and_single_drone_circle():
    from DroneOS.shared.nlp.trajectory_engine import TaskAction

    # Natural swarm formation phrases
    phrases = [
        ("form a circle", TaskAction.FORMATION, "CIRCLE"),
        ("form circle", TaskAction.FORMATION, "CIRCLE"),
        ("formation v", TaskAction.FORMATION, "V"),
        ("make a v formation", TaskAction.FORMATION, "V"),
        ("fly in a wedge formation", TaskAction.FORMATION, "WEDGE"),
        ("line formation spacing 15m", TaskAction.FORMATION, "LINE"),
    ]
    for text, expected_action, expected_shape in phrases:
        seq = parse_task_sequence(text)
        assert len(seq.tasks) >= 1, f"Failed to parse '{text}'"
        form_task = [t for t in seq.tasks if t.action == expected_action][0]
        assert form_task.params["type"] == expected_shape, f"Failed shape for '{text}'"

    # Single-drone circle command MUST NOT parse to SWARM_FORMATION
    circle_seq = parse_task_sequence("circle with 3m radius")
    assert len(circle_seq.tasks) == 1
    assert circle_seq.tasks[0].action == TaskAction.CIRCLE
    assert circle_seq.tasks[0].params["r"] == 3.0


def test_id_casing_consistency_and_normalization():
    from DroneOS.core.formation_manager import build_slot_assignments, normalize_drone_id

    # Mixed casing inputs must normalize and avoid duplicates
    ids = ["Drone1", "drone2", "DRONE3", "Drone2"]
    slots = build_slot_assignments(ids)

    assert slots == {"drone1": 0, "drone2": 1, "drone3": 2}
    assert normalize_drone_id(" Drone1 ") == "drone1"


