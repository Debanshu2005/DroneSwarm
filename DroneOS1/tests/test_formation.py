import pytest
import math
import asyncio
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any

from DroneOS1.core.formation_manager import FormationManager, FormationType, convert_local_offset_to_global
from DroneOS1.core.flight_manager import FlightManager
from DroneOS1.core.swarm_manager import SwarmMembership, PeerStateManager
from DroneOS1.shared.protocol.messages import TelemetryData

def test_convert_local_offset_to_global():
    # Test flat-earth GPS conversion
    # Latitude: 1 deg = 111,139m. So 111.139m North = 0.001 deg Lat
    # Longitude: 1 deg = 111,139m * cos(lat)
    anchor_lat = 40.0
    anchor_lon = -75.0
    anchor_alt = 10.0
    
    # 100 meters North
    target_lat, target_lon, target_alt = convert_local_offset_to_global(anchor_lat, anchor_lon, anchor_alt, 100.0, 0.0)
    
    assert target_alt == 10.0
    assert target_lon == anchor_lon # Moving pure North doesn't change lon significantly in flat-earth
    
    lat_diff = target_lat - anchor_lat
    expected_lat_diff = 100.0 / 6371000.0 * (180.0 / math.pi)
    assert abs(lat_diff - expected_lat_diff) < 1e-9

    # 100 meters East
    target_lat, target_lon, target_alt = convert_local_offset_to_global(anchor_lat, anchor_lon, anchor_alt, 0.0, 100.0)
    assert target_lat == anchor_lat
    
    lon_diff = target_lon - anchor_lon
    expected_lon_diff = 100.0 / (6371000.0 * math.cos(math.radians(anchor_lat))) * (180.0 / math.pi)
    assert abs(lon_diff - expected_lon_diff) < 1e-9

def test_formation_circle_geometry():
    mgr = FormationManager()
    mgr.set_formation(FormationType.CIRCLE, 10.0)
    
    for n in [1, 2, 3, 4, 5, 8]:
        points = [mgr.get_offset(i, n) for i in range(n)]
        
        # Test 1: all points are exactly `spacing` distance from center (0,0) except if N=0 which is handled
        for pt in points:
            dist_to_center = math.sqrt(pt[0]**2 + pt[1]**2)
            assert abs(dist_to_center - 10.0) < 1e-5
            
        # Test 2: points are mutually distinct (unless N=1)
        if n > 1:
            for i in range(n):
                for j in range(i+1, n):
                    dist = math.sqrt((points[i][0]-points[j][0])**2 + (points[i][1]-points[j][1])**2)
                    assert dist > 1.0 # Should be well separated
                    
def test_formation_square_geometry():
    mgr = FormationManager()
    mgr.set_formation(FormationType.SQUARE, 10.0)
    
    # Square corners: (5, 5), (-5, 5), (-5, -5), (5, -5)
    # N=4 should land exactly on corners
    points_n4 = [mgr.get_offset(i, 4) for i in range(4)]
    expected_corners = [
        (5.0, 5.0, 0.0),
        (-5.0, 5.0, 0.0),
        (-5.0, -5.0, 0.0),
        (5.0, -5.0, 0.0)
    ]
    
    for i, pt in enumerate(points_n4):
        assert abs(pt[0] - expected_corners[i][0]) < 1e-5
        assert abs(pt[1] - expected_corners[i][1]) < 1e-5
        
    for n in [1, 2, 3, 4, 5, 8]:
        points = [mgr.get_offset(i, n) for i in range(n)]
        # Mutually distinct
        if n > 1:
            for i in range(n):
                for j in range(i+1, n):
                    dist = math.sqrt((points[i][0]-points[j][0])**2 + (points[i][1]-points[j][1])**2)
                    assert dist > 1.0
                    
@pytest.mark.asyncio
async def test_formation_flight_loop_staleness():
    # Setup mocks
    mock_fc = AsyncMock()
    mock_fc.get_telemetry.return_value = TelemetryData(gps_valid=True, flight_mode="STABILIZE")
    mock_swarm_manager = MagicMock()
    
    fm = FlightManager(mock_fc)
    fm.set_swarm_manager(mock_swarm_manager)
    fm.swarm_manager.identity = MagicMock()
    fm.swarm_manager.identity.drone_id = "drone1"
    
    # Mock registry peers
    # Anchor is drone0, we are drone1
    import time
    now = time.time()
    
    peer_anchor = PeerStateManager("drone0")
    peer_anchor.last_seen = now
    peer_anchor.is_active = True
    peer_anchor.lat = 40.0
    peer_anchor.lon = -75.0
    peer_anchor.alt = 10.0
    # STALE POSITION!
    peer_anchor.last_position_time = now - 5.0 
    
    peer_self = PeerStateManager("drone1")
    peer_self.last_seen = now
    peer_self.is_active = True
    
    fm.swarm_manager.registry.peers = {
        "drone0": peer_anchor,
        "drone1": peer_self
    }
    fm.swarm_manager.registry.get_peer.side_effect = lambda x: fm.swarm_manager.registry.peers.get(x)
    
    # Intercept hover to stop the loop
    hover_called = asyncio.Event()
    
    async def mock_hover():
        hover_called.set()
        fm._active_flight_task.cancel() # Cancel self to exit loop
        return True
        
    mock_fc.hover = AsyncMock(side_effect=mock_hover)
    
    # Start loop
    params = {'type': 'V', 'spacing': 2.0}
    fm._active_flight_task = asyncio.create_task(fm._formation_flight_loop(params))
    
    # Wait for hover to be called
    try:
        await asyncio.wait_for(hover_called.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("Hover was not called for stale anchor position")
        
    assert mock_fc.hover.called
    assert not mock_fc.goto_location.called

def test_global_offset_local_m_roundtrip():
    from DroneOS1.core.formation_manager import convert_local_offset_to_global, global_offset_local_m
    
    anchor_lat = 40.0
    anchor_lon = -75.0
    anchor_alt = 10.0
    
    orig_dx = 100.0
    orig_dy = 50.0
    
    t_lat, t_lon, t_alt = convert_local_offset_to_global(anchor_lat, anchor_lon, anchor_alt, orig_dx, orig_dy)
    dx_north, dy_east = global_offset_local_m(anchor_lat, anchor_lon, t_lat, t_lon)
    
    assert abs(dx_north - orig_dx) < 1e-5
    assert abs(dy_east - orig_dy) < 1e-5

@pytest.mark.asyncio
async def test_formation_flight_loop_properly_spaced():
    mock_fc = AsyncMock()
    mock_fc.get_telemetry.return_value = TelemetryData(gps_valid=True, flight_mode="STABILIZE", latitude=40.0, longitude=-75.0)
    
    mock_swarm_manager = MagicMock()
    fm = FlightManager(mock_fc)
    fm.set_swarm_manager(mock_swarm_manager)
    fm.swarm_manager.identity = MagicMock()
    fm.swarm_manager.identity.drone_id = "drone1"
    
    import time
    now = time.time()
    
    # Anchor (drone0) at (40.0, -75.0)
    peer_anchor = PeerStateManager("drone0")
    peer_anchor.last_seen = now
    peer_anchor.is_active = True
    peer_anchor.lat = 40.0
    peer_anchor.lon = -75.0
    peer_anchor.alt = 10.0
    peer_anchor.last_position_time = now
    
    # We are drone1. Let's make sure our telemetry is spaced out enough.
    peer_self = PeerStateManager("drone1")
    peer_self.last_seen = now
    peer_self.is_active = True
    peer_self.lat = 40.0
    peer_self.lon = -75.0
    
    fm.swarm_manager.registry.peers = {"drone0": peer_anchor, "drone1": peer_self}
    fm.swarm_manager.registry.get_peer.side_effect = lambda x: fm.swarm_manager.registry.peers.get(x)
    
    goto_called = asyncio.Event()
    target_args = {}
    async def mock_goto(lat, lon, alt, yaw=0.0):
        target_args['lat'] = lat
        target_args['lon'] = lon
        goto_called.set()
        fm._active_flight_task.cancel()
        return True
        
    mock_fc.goto_location = AsyncMock(side_effect=mock_goto)
    
    # Spacing 5.0, Repulsion Radius 2.5
    params = {'type': 'V', 'spacing': 5.0, 'repulsion_radius_m': 2.5}
    fm._active_flight_task = asyncio.create_task(fm._formation_flight_loop(params))
    
    try:
        await asyncio.wait_for(goto_called.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("goto_location was not called")
        
    # Unmodified attractive target check
    from DroneOS1.core.formation_manager import FormationManager, convert_local_offset_to_global
    form_mgr = FormationManager()
    form_mgr.set_formation(FormationType.V, 5.0)
    # drone1 is index 1
    dx, dy, dz = form_mgr.get_offset(1, 2)
    expected_lat, expected_lon, _ = convert_local_offset_to_global(40.0, -75.0, 10.0, dx, dy)
    
    assert abs(target_args['lat'] - expected_lat) < 1e-8
    assert abs(target_args['lon'] - expected_lon) < 1e-8

@pytest.mark.asyncio
async def test_formation_flight_loop_repulsion():
    # Similar to above, but with a drone closer than 2.5m
    mock_fc = AsyncMock()
    # We are at 40.0, -75.0
    mock_fc.get_telemetry.return_value = TelemetryData(gps_valid=True, flight_mode="STABILIZE", latitude=40.0, longitude=-75.0)
    
    mock_swarm_manager = MagicMock()
    fm = FlightManager(mock_fc)
    fm.set_swarm_manager(mock_swarm_manager)
    fm.swarm_manager.identity = MagicMock()
    fm.swarm_manager.identity.drone_id = "drone1"
    
    import time
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
    
    # drone2 is dangerously close to us
    from DroneOS1.core.formation_manager import convert_local_offset_to_global
    d2_lat, d2_lon, _ = convert_local_offset_to_global(40.0, -75.0, 10.0, 1.0, 0.0)
    peer_close = PeerStateManager("drone2")
    peer_close.last_seen = now
    peer_close.is_active = True
    peer_close.lat = d2_lat
    peer_close.lon = d2_lon
    peer_close.last_position_time = now
    
    fm.swarm_manager.registry.peers = {"drone0": peer_anchor, "drone1": peer_self, "drone2": peer_close}
    fm.swarm_manager.registry.get_peer.side_effect = lambda x: fm.swarm_manager.registry.peers.get(x)
    
    goto_called = asyncio.Event()
    target_args = {}
    async def mock_goto(lat, lon, alt, yaw=0.0):
        target_args['lat'] = lat
        target_args['lon'] = lon
        goto_called.set()
        fm._active_flight_task.cancel()
        return True
        
    mock_fc.goto_location = AsyncMock(side_effect=mock_goto)
    
    # Spacing 5.0, Repulsion Radius 2.5
    params = {'type': 'V', 'spacing': 5.0, 'repulsion_radius_m': 2.5}
    fm._active_flight_task = asyncio.create_task(fm._formation_flight_loop(params))
    
    try:
        await asyncio.wait_for(goto_called.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("goto_location was not called")
        
    # Unmodified target
    from DroneOS1.core.formation_manager import FormationManager
    form_mgr = FormationManager()
    form_mgr.set_formation(FormationType.V, 5.0)
    # drone1 is index 1
    dx, dy, dz = form_mgr.get_offset(1, 3)
    
    expected_unmodified_lat, expected_unmodified_lon, _ = convert_local_offset_to_global(40.0, -75.0, 10.0, dx, dy)
    
    # Because drone2 is 1m North, we should be repelled South. So target_lat should be LESS than expected_unmodified_lat
    assert target_args['lat'] < expected_unmodified_lat
    # It should be measurably displaced
    assert abs(target_args['lat'] - expected_unmodified_lat) > 1e-8
