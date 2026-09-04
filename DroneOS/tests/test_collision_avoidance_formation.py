import pytest
import math
from unittest.mock import Mock, MagicMock
from DroneOS.core.decision_engine import LocalDecisionEngine
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.core.intents import IntentSource

class MockPeer:
    def __init__(self, lat, lon, alt):
        self.is_active = True
        self.telemetry = TelemetryData(flight_mode="HOLD")
        self.telemetry.latitude = lat
        self.telemetry.longitude = lon
        import time
        self.last_seen = time.time()
        self.telemetry.altitude = alt

class MockSwarmRegistry:
    def __init__(self):
        self.peers = {}

    def get_all_peers(self):
        return list(self.peers.keys())

    def get_peer(self, peer_id):
        return self.peers.get(peer_id)

class MockSwarm:
    def __init__(self):
        self.registry = MockSwarmRegistry()
        self.identity = Mock()
        self.identity.drone_id = "drone1"

class MockFlightManager:
    def __init__(self):
        self.formation_params = None

class MockNav:
    def __init__(self):
        self.flight_manager = MockFlightManager()

@pytest.mark.asyncio
async def test_formation_mate_within_tolerance():
    engine = LocalDecisionEngine(Mock(), MockSwarm(), Mock(), Mock(), Mock(), Mock())
    engine.nav = MockNav()
    engine.safety.is_failsafe_active = False
    engine.nav.flight_manager.formation_params = {'type': 'V', 'spacing': 2.0, 'repulsion_radius_m': 2.5}
    
    current_telemetry = TelemetryData(flight_mode="HOLD")
    current_telemetry.gps_valid = True
    current_telemetry.latitude = 37.0
    current_telemetry.longitude = -122.0
    
    # Mock CA returning a threat by default if not filtered
    engine.ca.evaluate_threats = MagicMock(return_value=("AVOIDANCE", None, "drone2", 1.0))
    
    # Mock Formation Engine expected positions
    engine.formation_engine.get_expected_positions = MagicMock(return_value={"drone2": (37.00001, -122.00001)})
    
    # Peer within tolerance (very close to expected)
    peer2 = MockPeer(37.00001, -122.00001, 10.0)
    engine.swarm.registry.peers = {"drone2": peer2}
    
    await engine.evaluate_tick(current_telemetry)
    
    # The filtered peer_telemetry should be empty, so evaluate_threats should be called with {}
    args, kwargs = engine.ca.evaluate_threats.call_args
    assert "drone2" not in args[1]

@pytest.mark.asyncio
async def test_formation_mate_beyond_tolerance():
    engine = LocalDecisionEngine(Mock(), MockSwarm(), Mock(), Mock(), Mock(), Mock())
    engine.nav = MockNav()
    engine.safety.is_failsafe_active = False
    engine.nav.flight_manager.formation_params = {'type': 'V', 'spacing': 2.0, 'repulsion_radius_m': 2.5}
    
    current_telemetry = TelemetryData(flight_mode="HOLD")
    current_telemetry.gps_valid = True
    current_telemetry.latitude = 37.0
    current_telemetry.longitude = -122.0
    
    # Expected at 37.0, -122.0. Peer is far away from expected but close to drone1 (so it's a threat)
    engine.formation_engine.get_expected_positions = MagicMock(return_value={"drone2": (38.0, -122.0)})
    
    # Peer close to current drone but FAR from its expected position
    peer2 = MockPeer(37.00001, -122.00001, 10.0)
    engine.swarm.registry.peers = {"drone2": peer2}
    
    engine.ca.evaluate_threats = MagicMock(return_value=("NORMAL", None, None, 10.0))
    
    await engine.evaluate_tick(current_telemetry)
    
    # Should NOT be filtered out because error_dist > tolerance
    args, kwargs = engine.ca.evaluate_threats.call_args
    assert "drone2" in args[1]

@pytest.mark.asyncio
async def test_non_formation_peer_close_range():
    engine = LocalDecisionEngine(Mock(), MockSwarm(), Mock(), Mock(), Mock(), Mock())
    engine.nav = MockNav()
    engine.safety.is_failsafe_active = False
    engine.nav.flight_manager.formation_params = {'type': 'V', 'spacing': 2.0}
    
    current_telemetry = TelemetryData(flight_mode="HOLD")
    current_telemetry.gps_valid = True
    current_telemetry.latitude = 37.0
    current_telemetry.longitude = -122.0
    
    # Expected positions only has drone2
    engine.formation_engine.get_expected_positions = MagicMock(return_value={"drone2": (37.0, -122.0)})
    
    # drone3 is NOT in formation
    peer3 = MockPeer(37.00001, -122.00001, 10.0)
    engine.swarm.registry.peers = {"drone3": peer3}
    
    engine.ca.evaluate_threats = MagicMock(return_value=("NORMAL", None, None, 10.0))
    
    await engine.evaluate_tick(current_telemetry)
    
    # drone3 must be evaluated since it's not in the expected_positions (not in formation)
    args, kwargs = engine.ca.evaluate_threats.call_args
    assert "drone3" in args[1]

@pytest.mark.asyncio
async def test_no_active_formation():
    engine = LocalDecisionEngine(Mock(), MockSwarm(), Mock(), Mock(), Mock(), Mock())
    engine.nav = MockNav()
    engine.safety.is_failsafe_active = False
    engine.nav.flight_manager.formation_params = None # NO FORMATION
    
    current_telemetry = TelemetryData(flight_mode="HOLD")
    current_telemetry.gps_valid = True
    current_telemetry.latitude = 37.0
    current_telemetry.longitude = -122.0
    
    peer2 = MockPeer(37.00001, -122.00001, 10.0)
    engine.swarm.registry.peers = {"drone2": peer2}
    
    engine.ca.evaluate_threats = MagicMock(return_value=("NORMAL", None, None, 10.0))
    engine.formation_engine.get_expected_positions = MagicMock(side_effect=Exception("Should not be called"))
    
    await engine.evaluate_tick(current_telemetry)
    
    # Should be passed directly to evaluate_threats without filtering
    args, kwargs = engine.ca.evaluate_threats.call_args
    assert "drone2" in args[1]
