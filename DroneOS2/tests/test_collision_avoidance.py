import pytest
from DroneOS2.core.collision_avoidance import StandardCollisionAvoidance
from DroneOS2.shared.protocol.messages import TelemetryData
from DroneOS2.shared.config.models import CollisionAvoidanceConfig

def test_evaluate_threats_heading_independent():
    config = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=10.0,
        warning_distance=20.0,
        emergency_distance=5.0
    )
    ca = StandardCollisionAvoidance(config)
    
    # Own drone at (0, 0), heading 90 (East)
    self_telemetry = TelemetryData(
        flight_mode="GUIDED",
        latitude=0.0,
        longitude=0.0,
        altitude=10.0,
        heading=90.0,
        timestamp=100.0
    )
    
    # Peer drone directly South of us (latitude -0.00005, approx 5.5 meters south)
    # This should trigger AVOIDANCE (dist < 10.0)
    # The escape vector should be North (away from South)
    peer_telemetry = TelemetryData(
        flight_mode="GUIDED",
        latitude=-0.00005,
        longitude=0.0,
        altitude=10.0,
        heading=0.0,
        timestamp=100.0
    )
    
    import time
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, 'time', lambda: 100.0)
        
        state, correction, peer, dist = ca.evaluate_threats(
            self_telemetry,
            {"peer1": peer_telemetry}
        )
        
        assert state == "AVOIDANCE"
        assert correction is not None
        
        north = correction.get('north', 0.0)
        east = correction.get('east', 0.0)
        
        # Escape should be North (positive)
        assert north > 0.0
        assert abs(east) < 0.1
