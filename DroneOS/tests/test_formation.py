import pytest
import math
import asyncio
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any

from DroneOS.core.formation_manager import FormationManager, FormationType, convert_local_offset_to_global
from DroneOS.core.flight_manager import FlightManager
from DroneOS.core.swarm_manager import SwarmMembership, PeerStateManager
from DroneOS.shared.protocol.messages import TelemetryData

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
