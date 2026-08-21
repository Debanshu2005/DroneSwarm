import pytest
import asyncio
import time
from unittest.mock import AsyncMock
from DroneOS.core.command_handler import CommandHandler
from DroneOS.shared.protocol.messages import ControlMessage, CommandAction

@pytest.mark.asyncio
async def test_command_handler_extracts_action_error():
    fc = AsyncMock()
    hm = AsyncMock()
    hm.last_heartbeat_time = time.time()
    hm.timeout_seconds = 5.0
    sm = AsyncMock()
    sm.is_failsafe_active = False
    
    class ActionError(Exception):
        pass

    fc.get_telemetry.return_value = AsyncMock()
    fc.get_telemetry.return_value.timestamp = time.time()
    fc.get_telemetry.return_value.is_armable = True
    
    handler = CommandHandler(node_id="test_node", safety_module=sm, flight_controller=fc, health_monitor=hm)
    handler.network = AsyncMock()
    
    async def failing_action(params):
        raise ActionError("PreArm: GPS 1: Bad fix")
        
    handler.register_handler(CommandAction.ARM, failing_action)
    
    msg = ControlMessage(sender_id="GS", target_id="ALL", action=CommandAction.ARM, params={}, cmd_id="cmd_1", timestamp=time.time())
    success = await handler.handle_command(msg)
    await asyncio.sleep(0.1)
    
    assert not success
    
    broadcasts = handler.network.broadcast_message.call_args_list
    assert len(broadcasts) > 0
    
    from DroneOS.shared.protocol.messages import MessageType
    lifecycle_msgs = [call[0][0] for call in broadcasts if call[0][0].msg_type == MessageType.COMMAND_LIFECYCLE]
    failed_msg = next((m for m in lifecycle_msgs if m.stage == "FAILED"), None)
    
    assert failed_msg is not None
    assert "GPS 1: Bad fix" in failed_msg.reason
