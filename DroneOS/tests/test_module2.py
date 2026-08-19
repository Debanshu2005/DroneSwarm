import pytest
from DroneOS.core.interfaces import IFlightController
from DroneOS.core.flight_manager import FlightManager
from DroneOS.core.command_handler import CommandHandler
from DroneOS.shared.protocol.messages import ControlMessage, CommandAction, TelemetryData
import time

class MockFlightController(IFlightController):
    async def connect(self) -> bool: return True
    async def disconnect(self) -> None: pass
    async def arm(self) -> bool: return True
    async def disarm(self) -> bool: return True
    async def takeoff(self, altitude: float = 10.0) -> bool: return True
    async def land(self) -> bool: return True
    async def rtl(self) -> bool: return True
    async def hover(self) -> bool: return True
    async def move_velocity(self, vx: float, vy: float, vz: float, duration: float) -> bool: return True
    async def get_telemetry(self) -> TelemetryData:
        return TelemetryData(
            battery_level=100.0, altitude=0.0, latitude=0.0, longitude=0.0,
            velocity_x=0.0, velocity_y=0.0, velocity_z=0.0, flight_mode="ground"
        )

@pytest.mark.asyncio
async def test_flight_manager_state():
    mock_fc = MockFlightController()
    fm = FlightManager(mock_fc)
    
    assert not fm.is_armed
    assert not fm.is_flying
    
    # Should fail takeoff if not armed
    success = await fm.takeoff()
    assert not success
    assert not fm.is_flying
    
    # Arm and takeoff
    await fm.arm()
    assert fm.is_armed
    
    success = await fm.takeoff()
    assert success
    assert fm.is_flying
    
    # Move
    success = await fm.move({"vx": 1.0, "vy": 0.0, "vz": 0.0, "duration": 1.0})
    assert success
    
    # Land and disarm
    await fm.land()
    assert not fm.is_flying
    
    await fm.disarm()
    assert not fm.is_armed

@pytest.mark.asyncio
async def test_command_handler():
    mock_fc = MockFlightController()
    fm = FlightManager(mock_fc)
    handler = CommandHandler()
    
    handler.register_handler(CommandAction.ARM, fm.arm)
    handler.register_handler(CommandAction.TAKEOFF, fm.takeoff)
    
    # Send ARM
    msg = ControlMessage(
        sender_id="gs1", 
        timestamp=time.time(), 
        action=CommandAction.ARM
    )
    success = await handler.handle_command(msg)
    
    assert success
    assert fm.is_armed
    
    # Send unsupported command
    msg_unsupported = ControlMessage(
        sender_id="gs1",
        timestamp=time.time(),
        action=CommandAction.LAND
    )
    success = await handler.handle_command(msg_unsupported)
    assert not success
