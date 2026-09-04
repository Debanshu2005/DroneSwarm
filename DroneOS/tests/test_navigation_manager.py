import pytest
from DroneOS.core.navigation_manager import NavigationManager
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.intents import IntentSource, IntentAction

def test_navigate_to_waypoint_heading_independent():
    state_store = FlightStateStore()
    nav = NavigationManager(None, state_store)
    
    # Current pos: (0, 0), altitude 10, heading 90 (East)
    telemetry = TelemetryData(
        flight_mode="GUIDED",
        latitude=0.0,
        longitude=0.0,
        altitude=10.0,
        heading=90.0
    )
    
    # Target pos: North of current pos
    # 1 degree of latitude is ~111km, so 0.001 is ~111m North
    target_lat = 0.001
    target_lon = 0.0
    target_alt = 10.0
    target_speed = 5.0
    
    reached = nav.navigate_to_waypoint(telemetry, target_lat, target_lon, target_alt, target_speed)
    
    assert not reached
    
    intent = state_store.get_intents().get(IntentSource.MISSION)
    assert intent is not None
    assert intent.action == IntentAction.MOVE_VELOCITY_NED
    
    north = intent.params.get('north', 0.0)
    east = intent.params.get('east', 0.0)
    down = intent.params.get('down', 0.0)
    
    # It should command movement purely North
    assert north > 0.0
    assert abs(east) < 0.1
    assert abs(down) < 0.1
