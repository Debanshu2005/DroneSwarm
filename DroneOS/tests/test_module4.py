import time
from DroneOS.core.swarm_manager import SwarmManager
from DroneOS.shared.protocol.messages import HeartbeatMessage, TelemetryMessage, TelemetryData

from DroneOS.shared.config.loader import load_yaml_config
from DroneOS.shared.config.models import DroneConfig

def test_swarm_manager():
    drone_cfg = load_yaml_config("DroneOS/configs/drone.yaml", DroneConfig)
    sm = SwarmManager(node_id=drone_cfg.drone_id, timeout_seconds=0.1)
    
    hb = HeartbeatMessage(sender_id="drone2", timestamp=time.time(), status="active")
    sm.handle_heartbeat(hb)
    
    active = sm.get_active_drones()
    assert "drone2" in active
    
    tel = TelemetryMessage(
        sender_id="drone2",
        timestamp=time.time(),
        telemetry=TelemetryData(
            battery_level=100.0, altitude=10.0, latitude=0.0, longitude=0.0,
            velocity_x=0.0, velocity_y=0.0, velocity_z=0.0, flight_mode="flying"
        )
    )
    sm.handle_telemetry(tel)
    
    assert sm.peer_db.drones["drone2"].telemetry.altitude == 10.0
    
    # Test timeout
    time.sleep(0.2)
    active = sm.get_active_drones()
    assert "drone2" not in active
