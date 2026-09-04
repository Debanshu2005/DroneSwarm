import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from DroneOS.core.terminal_controller import TerminalController
from DroneOS.core.command_handler import CommandHandler
from DroneOS.core.flight_manager import FlightManager
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.shared.protocol.messages import CommandAction, ControlMessage
from DroneOS.core.intents import IntentSource, IntentAction

@pytest.mark.asyncio
async def test_terminal_goto_local_reaches_intent():
    fc = MagicMock()
    state_store = FlightStateStore()
    fm = FlightManager(fc, state_store)
    
    ch = CommandHandler(fm)
    ch.register_handler(CommandAction.GOTO_LOCAL, fm.goto_local)
    
    tc = TerminalController(ch, fc, "test-node")
    
    # Process a GOTO_LOCAL command through the command handler
    msg = ControlMessage(
        action=CommandAction.GOTO_LOCAL,
        params={"north": 5.0, "east": -2.0, "down": 0.0, "yaw": 0.0},
        sender_id="test",
        timestamp=0.0
    )
    
    result = await ch.handle_command(msg)
    
    assert result is True # No longer False unconditionally
    
    intent = state_store.get_intents().get(IntentSource.MANUAL)
    assert intent is not None
    assert intent.action == IntentAction.GOTO_NED
    assert intent.params["north"] == 5.0
    assert intent.params["east"] == -2.0
    assert intent.params["down"] == 0.0
