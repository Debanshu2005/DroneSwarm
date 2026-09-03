import pytest
import math
import asyncio
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any
import time

from DroneOS1.core.formation_manager import FormationManager, FormationType, convert_local_offset_to_global
from DroneOS1.core.formation_engine import FormationEngine
from DroneOS1.core.swarm_manager import PeerStateManager
from DroneOS1.shared.protocol.messages import TelemetryData
from DroneOS1.core.intents import IntentSource, IntentAction

def test_convert_local_offset_to_global():
    anchor_lat = 40.0
    anchor_lon = -75.0
    anchor_alt = 10.0
    
    target_lat, target_lon, target_alt = convert_local_offset_to_global(anchor_lat, anchor_lon, anchor_alt, 100.0, 0.0)
    
    assert target_alt == 10.0
    assert target_lon == anchor_lon 
    
    lat_diff = target_lat - anchor_lat
    expected_lat_diff = 100.0 / 6371000.0 * (180.0 / math.pi)
    assert abs(lat_diff - expected_lat_diff) < 1e-9

def test_formation_circle_geometry():
    mgr = FormationManager()
    mgr.set_formation(FormationType.CIRCLE, 10.0)
    
    for n in [1, 2, 3, 4, 5, 8]:
        points = [mgr.get_offset(i, n) for i in range(n)]
        
        for pt in points:
            dist_to_center = math.sqrt(pt[0]**2 + pt[1]**2)
            assert abs(dist_to_center - 10.0) < 1e-5

@pytest.fixture
def swarm_manager():
    sm = MagicMock()
    sm.identity = MagicMock()
    sm.identity.drone_id = "drone1"
    sm.registry = MagicMock()
    sm.registry.peers = {}
    sm.registry.get_peer.side_effect = lambda x: sm.registry.peers.get(x)
    return sm

@pytest.fixture
def engine(swarm_manager):
    return FormationEngine(swarm_manager, MagicMock())

def test_formation_engine_stale_anchor(engine, swarm_manager):
    now = time.time()
    
    # Anchor (drone0) has a stale position
    peer_anchor = PeerStateManager("drone0")
    peer_anchor.last_seen = now
    peer_anchor.is_active = True
    peer_anchor.lat = 40.0
    peer_anchor.lon = -75.0
    peer_anchor.alt = 10.0
    peer_anchor.last_position_time = now - 5.0 # Stale
    
    peer_self = PeerStateManager("drone1")
    peer_self.last_seen = now
    peer_self.is_active = True
    
    swarm_manager.registry.peers = {"drone0": peer_anchor, "drone1": peer_self}
    
    telemetry = TelemetryData(flight_mode="GUIDED", gps_valid=True)
    params = {'type': 'V', 'spacing': 2.0}
    
    intent = engine.compute_intent(telemetry, None, params)
    
    # Because anchor is stale, should hover
    assert intent.action == IntentAction.HOVER
    assert intent.source == IntentSource.FORMATION

def test_formation_engine_properly_spaced(engine, swarm_manager):
    now = time.time()
    
    peer_anchor = PeerStateManager("drone0")
    peer_anchor.last_seen = now
    peer_anchor.is_active = True
    peer_anchor.lat = 40.0
    peer_anchor.lon = -75.0
    peer_anchor.alt = 10.0
    peer_anchor.last_position_time = now
    
    peer_self = PeerStateManager("drone1")
    peer_self.last_seen = now
    peer_self.is_active = True
    peer_self.lat = 40.0
    peer_self.lon = -75.0
    
    swarm_manager.registry.peers = {"drone0": peer_anchor, "drone1": peer_self}
    
    telemetry = TelemetryData(flight_mode="GUIDED", gps_valid=True, latitude=40.0, longitude=-75.0)
    params = {'type': 'V', 'spacing': 5.0, 'repulsion_radius_m': 2.5}
    
    intent = engine.compute_intent(telemetry, None, params)
    
    assert intent.action == IntentAction.MOVE_VELOCITY
    assert intent.source == IntentSource.FORMATION
    
    # In V formation, drone1 (index 1) is behind and to the right of anchor
    # dx = -tier*s, dy = tier*s -> dx < 0, dy > 0
    # Since drone1 is exactly AT the anchor's position, it needs to move South and East
    assert intent.params['vx'] < 0
    assert intent.params['vy'] > 0

def test_formation_engine_repulsion(engine, swarm_manager):
    now = time.time()
    
    peer_anchor = PeerStateManager("drone0")
    peer_anchor.last_seen = now
    peer_anchor.is_active = True
    peer_anchor.lat = 40.0
    peer_anchor.lon = -75.0
    peer_anchor.alt = 10.0
    peer_anchor.last_position_time = now
    
    peer_self = PeerStateManager("drone1")
    peer_self.last_seen = now
    peer_self.is_active = True
    
    # drone2 is 1m North of drone1
    d2_lat, d2_lon, _ = convert_local_offset_to_global(40.0, -75.0, 10.0, 1.0, 0.0)
    peer_close = PeerStateManager("drone2")
    peer_close.last_seen = now
    peer_close.is_active = True
    peer_close.lat = d2_lat
    peer_close.lon = d2_lon
    peer_close.last_position_time = now
    
    swarm_manager.registry.peers = {"drone0": peer_anchor, "drone1": peer_self, "drone2": peer_close}
    
    telemetry = TelemetryData(flight_mode="GUIDED", gps_valid=True, latitude=40.0, longitude=-75.0)
    params = {'type': 'V', 'spacing': 5.0, 'repulsion_radius_m': 2.5}
    
    intent = engine.compute_intent(telemetry, None, params)
    
    assert intent.action == IntentAction.MOVE_VELOCITY
    
    # Without repulsion, drone1 (index 1) has target dx=-s/2 (approx), dy=s/2
    # With drone2 1m North, we should be repelled SOUTH (vx becomes more negative)
    # The actual baseline target is behind anchor, so vx < 0. Repulsion pushes us further South.
    assert intent.params['vx'] < 0

def test_formation_engine_ned_directions(engine, swarm_manager):
    now = time.time()
    peer_anchor = PeerStateManager("drone0")
    peer_anchor.last_seen = now
    peer_anchor.is_active = True
    peer_anchor.alt = 10.0
    peer_anchor.last_position_time = now
    
    peer_self = PeerStateManager("drone1")
    peer_self.last_seen = now
    peer_self.is_active = True
    
    swarm_manager.registry.peers = {"drone0": peer_anchor, "drone1": peer_self}
    params = {'type': 'V', 'spacing': 5.0} 
    
    # Mock offset to 0 so target == anchor position
    engine.form_mgr.get_offset = MagicMock(return_value=(0.0, 0.0, 0.0))
    
    telemetry = TelemetryData(flight_mode="GUIDED", gps_valid=True, latitude=40.0, longitude=-75.0)
    
    # 1. Target North
    peer_anchor.lat = 40.001
    peer_anchor.lon = -75.0
    intent = engine.compute_intent(telemetry, None, params)
    assert intent.params['vx'] > 0
    assert abs(intent.params['vy']) < 1e-5

    # 2. Target South
    peer_anchor.lat = 39.999
    peer_anchor.lon = -75.0
    intent = engine.compute_intent(telemetry, None, params)
    assert intent.params['vx'] < 0
    assert abs(intent.params['vy']) < 1e-5

    # 3. Target East
    peer_anchor.lat = 40.0
    peer_anchor.lon = -74.999
    intent = engine.compute_intent(telemetry, None, params)
    assert abs(intent.params['vx']) < 1e-5
    assert intent.params['vy'] > 0

    # 4. Target West
    peer_anchor.lat = 40.0
    peer_anchor.lon = -75.001
    intent = engine.compute_intent(telemetry, None, params)
    assert abs(intent.params['vx']) < 1e-5
    assert intent.params['vy'] < 0

