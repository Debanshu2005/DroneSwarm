import math
from DroneOS1.shared.utils.logger import setup_logger
from DroneOS1.shared.protocol.messages import TelemetryData
from DroneOS1.core.flight_state import FlightStateStore
from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction

logger = setup_logger("NavigationManager")

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    
    theta = math.atan2(y, x)
    return (math.degrees(theta) + 360) % 360

class NavigationManager:
    """
    Converts high-level mission waypoints into concrete velocity and yaw vectors.
    Submits intents rather than commanding the flight manager directly.
    """
    def __init__(self, flight_manager, state_store: FlightStateStore):
        self.flight_manager = flight_manager  # Kept for compatibility if it has any other methods needed
        self.state_store = state_store
        self.waypoint_tolerance: float = 2.0  # meters

    def navigate_to_waypoint(self, current_telemetry: TelemetryData, target_lat: float, target_lon: float, target_alt: float, target_speed: float) -> bool:
        if current_telemetry.latitude is None or current_telemetry.longitude is None:
            logger.warning("Cannot navigate without GPS lock.")
            # Emit HOVER intent to stop if GPS lost
            intent = FlightIntent(IntentSource.MISSION, IntentAction.HOVER, ttl_seconds=1.0)
            self.state_store.submit_intent(intent)
            return False

        dist = haversine_distance(current_telemetry.latitude, current_telemetry.longitude, target_lat, target_lon)
        
        # Check if waypoint reached
        alt_diff = abs((current_telemetry.altitude or 0.0) - target_alt)
        if dist < self.waypoint_tolerance and alt_diff < self.waypoint_tolerance:
            logger.info("Waypoint reached.")
            return True

        # Calculate Velocity Vector
        bearing = calculate_bearing(current_telemetry.latitude, current_telemetry.longitude, target_lat, target_lon)
        bearing_rad = math.radians(bearing)
        
        speed = min(target_speed, dist * 0.5)
        if speed < 0.5:
            speed = 0.5
            
        vx = speed * math.cos(bearing_rad)
        vy = speed * math.sin(bearing_rad)
        
        vz = 0.0
        if current_telemetry.altitude is not None:
            vz_error = target_alt - current_telemetry.altitude
            vz = max(-2.0, min(2.0, vz_error * 0.5))
            vz = -vz 
            
        yaw_rate = 0.0 

        intent = FlightIntent(
            IntentSource.MISSION,
            IntentAction.MOVE_VELOCITY,
            ttl_seconds=1.0,
            params={
                "vx": vx,
                "vy": vy,
                "vz": vz,
                "yaw_rate": yaw_rate
            }
        )
        self.state_store.submit_intent(intent)
        
        return False 

