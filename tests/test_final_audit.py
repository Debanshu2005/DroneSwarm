import pytest
import asyncio
from DroneOS.shared.protocol.messages import ControlMessage, CommandAction, MessageType
from DroneOS.shared.communication.serializers import JsonSerializer
from pydantic import ValidationError

@pytest.mark.asyncio
async def test_arm_serialization():
    serializer = JsonSerializer()
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.ARM)
    data = serializer.serialize(msg)
    assert b"arm" in data
    decoded = serializer.deserialize(data)
    assert decoded.action == CommandAction.ARM
    assert decoded.msg_type == MessageType.CONTROL

@pytest.mark.asyncio
async def test_disarm_serialization():
    serializer = JsonSerializer()
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.DISARM)
    data = serializer.serialize(msg)
    assert b"disarm" in data
    decoded = serializer.deserialize(data)
    assert decoded.action == CommandAction.DISARM

@pytest.mark.asyncio
async def test_move_forward():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.MOVE, params={"vx": 2.0})
    assert msg.params["vx"] == 2.0

@pytest.mark.asyncio
async def test_move_backward():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.MOVE, params={"vx": -2.0})
    assert msg.params["vx"] == -2.0

@pytest.mark.asyncio
async def test_move_left():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.MOVE, params={"vy": -2.0})
    assert msg.params["vy"] == -2.0

@pytest.mark.asyncio
async def test_move_right():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.MOVE, params={"vy": 2.0})
    assert msg.params["vy"] == 2.0

@pytest.mark.asyncio
async def test_move_up():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.MOVE, params={"vz": -1.0})
    assert msg.params["vz"] == -1.0

@pytest.mark.asyncio
async def test_move_down():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.MOVE, params={"vz": 1.0})
    assert msg.params["vz"] == 1.0

@pytest.mark.asyncio
async def test_yaw_left():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.MOVE, params={"yaw_rate": -15.0})
    assert msg.params["yaw_rate"] == -15.0

@pytest.mark.asyncio
async def test_yaw_right():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.MOVE, params={"yaw_rate": 15.0})
    assert msg.params["yaw_rate"] == 15.0

@pytest.mark.asyncio
async def test_hover():
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.HOVER)
    assert msg.action == CommandAction.HOVER

@pytest.mark.asyncio
async def test_lifecycle_validation():
    from DroneOS.shared.protocol.messages import CommandLifecycleMessage
    msg = CommandLifecycleMessage(sender_id="drone1", target_id="gs1", timestamp=0.0, action=CommandAction.ARM, cmd_id="123", stage="SUCCESS")
    assert msg.stage == "SUCCESS"

@pytest.mark.asyncio
async def test_telemetry():
    from DroneOS.shared.protocol.messages import TelemetryMessage
    msg = TelemetryMessage(sender_id="drone1", timestamp=0.0, telemetry={"flight_mode": "HOLD", "battery_level": 95.0, "gps_valid": True})
    assert msg.telemetry.battery_level == 95.0
    assert msg.telemetry.gps_valid is True

@pytest.mark.asyncio
async def test_heartbeat():
    from DroneOS.shared.protocol.messages import HeartbeatMessage
    msg = HeartbeatMessage(sender_id="drone1", timestamp=0.0, status="CONNECTED")
    assert msg.status == "CONNECTED"

@pytest.mark.asyncio
async def test_arm_rejection_propagation():
    from DroneOS.core.command_handler import CommandHandler
    from DroneOS.shared.protocol.messages import ControlMessage, CommandAction
    
    class FakeNetwork:
        def __init__(self):
            self.sent = []
        async def broadcast_message(self, msg):
            self.sent.append(msg)
            
    class FakeFlightController:
        async def get_telemetry(self):
            class T:
                timestamp = 9999999999.9
                home_valid = True
            return T()
        
    class FakeHealthMonitor:
        last_heartbeat_time = 9999999999.9
        timeout_seconds = 5.0
        
    class FakeSafetyModule:
        is_failsafe_active = False

    ch = CommandHandler("drone1", FakeSafetyModule(), FakeFlightController(), FakeHealthMonitor())
    ch.network = FakeNetwork()
    
    async def fake_arm(params):
        raise RuntimeError("ActionError: Pre-arm checks failed")
        
    ch.register_handler(CommandAction.ARM, fake_arm)
    
    msg = ControlMessage(sender_id="gs1", target_id="drone1", timestamp=0.0, action=CommandAction.ARM)
    await ch.handle_command(msg)
    await asyncio.sleep(0.1)
    
    # Check if REJECTED state was sent
    rejections = [m for m in ch.network.sent if getattr(m, 'stage', None) == 'REJECTED']
    assert len(rejections) > 0, "Expected a REJECTED message"
    assert "Pre-arm checks failed" in rejections[0].reason
