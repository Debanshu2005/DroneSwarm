import pytest
import time
from unittest.mock import AsyncMock, MagicMock
from GroundStation.core.network_manager import GSNetworkManager
from GroundStation.shared.protocol.messages import HeartbeatMessage, TelemetryMessage, TelemetryData, CommandAction

@pytest.fixture
def mock_network_adapter():
    adapter = MagicMock()
    adapter.start = AsyncMock()
    adapter.stop = AsyncMock()
    adapter.broadcast_message = AsyncMock()
    adapter.register_callback = MagicMock()
    return adapter

@pytest.mark.asyncio
async def test_gs_network_manager(mock_network_adapter):
    nm = GSNetworkManager(mock_network_adapter)
    
    # Capture the registered callback
    callback = mock_network_adapter.register_callback.call_args[0][0]
    
    discovered_drones = []
    telemetry_updates = []
    
    nm.on_drone_discovered = lambda d_id: discovered_drones.append(d_id)
    nm.on_telemetry_updated = lambda d_id, tel: telemetry_updates.append((d_id, tel))
    
    # Test Heartbeat reception (Drone discovery)
    hb = HeartbeatMessage(sender_id="drone1", timestamp=time.time())
    await callback(hb)
    
    assert "drone1" in discovered_drones
    assert "drone1" in nm.drones
    
    # Test Telemetry reception
    tel_data = TelemetryData(
        battery_level=90.0, altitude=5.0, latitude=0.0, longitude=0.0,
        velocity_x=0.0, velocity_y=0.0, velocity_z=0.0, flight_mode="flying"
    )
    tel_msg = TelemetryMessage(sender_id="drone1", timestamp=time.time(), telemetry=tel_data)
    await callback(tel_msg)
    
    assert len(telemetry_updates) == 1
    assert telemetry_updates[0][0] == "drone1"
    assert telemetry_updates[0][1].altitude == 5.0
    
    # Test Sending Command
    await nm.send_command("drone1", CommandAction.TAKEOFF, {"altitude": 20.0})
    mock_network_adapter.broadcast_message.assert_called_once()
    sent_msg = mock_network_adapter.broadcast_message.call_args[0][0]
    assert sent_msg.msg_type == "control"
    assert sent_msg.action == "takeoff"
    assert sent_msg.params["altitude"] == 20.0
