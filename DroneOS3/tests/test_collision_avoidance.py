import pytest
from DroneOS3.core.collision_avoidance import StandardCollisionAvoidance
from DroneOS3.shared.protocol.messages import TelemetryData
from DroneOS3.shared.config.models import CollisionAvoidanceConfig

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
        gps_valid=True,
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
        gps_valid=True,
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

def test_evaluate_threats_ignores_invalid_gps():
    config = CollisionAvoidanceConfig(enabled=True, min_horizontal_distance=10.0, warning_distance=20.0, emergency_distance=5.0)
    ca = StandardCollisionAvoidance(config)
    self_telemetry = TelemetryData(flight_mode="GUIDED", latitude=0.0, longitude=0.0, altitude=10.0, heading=90.0, timestamp=100.0, gps_valid=True)
    peer_telemetry = TelemetryData(flight_mode="GUIDED", latitude=-0.00005, longitude=0.0, altitude=10.0, heading=0.0, timestamp=100.0)
    import time
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, 'time', lambda: 100.0)
        state, correction, peer, dist = ca.evaluate_threats(self_telemetry, {"peer1": peer_telemetry})
        assert state == "NORMAL"


def test_evaluate_threats_honours_config_speeds():
    config = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=10.0,
        warning_distance=20.0,
        emergency_distance=5.0,
        avoidance_speed=4.5,
        emergency_speed=7.5
    )
    ca = StandardCollisionAvoidance(config)
    
    self_telemetry = TelemetryData(flight_mode="GUIDED", latitude=0.0, longitude=0.0, altitude=10.0, heading=90.0, timestamp=100.0, gps_valid=True)
    
    # Emergency (dist < 5.0)
    peer_emg = TelemetryData(flight_mode="GUIDED", latitude=-0.00002, longitude=0.0, altitude=10.0, heading=0.0, timestamp=100.0, gps_valid=True)
    import time
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, 'time', lambda: 100.0)
        state, correction, peer, dist = ca.evaluate_threats(self_telemetry, {"peer1": peer_emg})
        assert state == "EMERGENCY"
        speed = (correction['north']**2 + correction['east']**2)**0.5
        assert abs(speed - 7.5) < 0.1

    # Avoidance (dist < 10.0)
    peer_avoid = TelemetryData(flight_mode="GUIDED", latitude=-0.00007, longitude=0.0, altitude=10.0, heading=0.0, timestamp=100.0, gps_valid=True)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, 'time', lambda: 100.0)
        state, correction, peer, dist = ca.evaluate_threats(self_telemetry, {"peer1": peer_avoid})
        assert state == "AVOIDANCE"
        speed = (correction['north']**2 + correction['east']**2)**0.5
        assert abs(speed - 4.5) < 0.1

def test_evaluate_threats_warning_correction():
    config = CollisionAvoidanceConfig(
        enabled=True,
        min_horizontal_distance=10.0,
        warning_distance=20.0,
        emergency_distance=5.0,
        avoidance_speed=4.0
    )
    ca = StandardCollisionAvoidance(config)
    
    self_telemetry = TelemetryData(flight_mode="GUIDED", latitude=0.0, longitude=0.0, altitude=10.0, heading=90.0, timestamp=100.0, gps_valid=True)
    # Warning (dist < 20.0)
    peer_warn = TelemetryData(flight_mode="GUIDED", latitude=-0.00014, longitude=0.0, altitude=10.0, heading=0.0, timestamp=100.0, gps_valid=True)
    
    import time
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, 'time', lambda: 100.0)
        state, correction, peer, dist = ca.evaluate_threats(self_telemetry, {"peer1": peer_warn})
        assert state == "WARNING"
        assert correction is not None
        speed = (correction['north']**2 + correction['east']**2)**0.5
        assert abs(speed - 2.0) < 0.1  # 0.5 * 4.0 = 2.0

def test_evaluate_threats_stale_peer_ignored():
    config = CollisionAvoidanceConfig(enabled=True, min_horizontal_distance=10.0, max_peer_age_sec=2.5)
    ca = StandardCollisionAvoidance(config)
    
    self_telemetry = TelemetryData(flight_mode="GUIDED", latitude=0.0, longitude=0.0, altitude=10.0, heading=90.0, timestamp=100.0, gps_valid=True)
    # Stale peer (age 3.0s)
    peer_stale = TelemetryData(flight_mode="GUIDED", latitude=-0.00005, longitude=0.0, altitude=10.0, heading=0.0, timestamp=97.0, gps_valid=True)
    
    import time
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, 'time', lambda: 100.0)
        state, correction, peer, dist = ca.evaluate_threats(self_telemetry, {"peer1": peer_stale})
        assert state == "NORMAL"

def test_evaluate_threats_disabled_ca_returns_normal():
    config = CollisionAvoidanceConfig(enabled=False, min_horizontal_distance=10.0, emergency_distance=5.0)
    ca = StandardCollisionAvoidance(config)
    
    self_telemetry = TelemetryData(flight_mode="GUIDED", latitude=0.0, longitude=0.0, altitude=10.0, heading=90.0, timestamp=100.0, gps_valid=True)
    # Very close peer
    peer_close = TelemetryData(flight_mode="GUIDED", latitude=-0.00002, longitude=0.0, altitude=10.0, heading=0.0, timestamp=100.0, gps_valid=True)
    
    import time
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, 'time', lambda: 100.0)
        state, correction, peer, dist = ca.evaluate_threats(self_telemetry, {"peer1": peer_close})
        assert state == "NORMAL"

def test_evaluate_threats_vertical_separation_ignored():
    config = CollisionAvoidanceConfig(enabled=True, min_horizontal_distance=10.0, min_vertical_distance=2.0)
    ca = StandardCollisionAvoidance(config)
    
    self_telemetry = TelemetryData(flight_mode="GUIDED", latitude=0.0, longitude=0.0, altitude=10.0, heading=90.0, timestamp=100.0, gps_valid=True)
    # Close horizontally, but high vertically (alt diff 3.0m)
    peer_high = TelemetryData(flight_mode="GUIDED", latitude=-0.00002, longitude=0.0, altitude=13.0, heading=0.0, timestamp=100.0, gps_valid=True)
    
    import time
    with pytest.MonkeyPatch.context() as m:
        m.setattr(time, 'time', lambda: 100.0)
        state, correction, peer, dist = ca.evaluate_threats(self_telemetry, {"peer1": peer_high})
        assert state == "NORMAL"
