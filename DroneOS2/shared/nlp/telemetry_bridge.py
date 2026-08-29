from DroneOS2.shared.protocol.messages import TelemetryData
from DroneOS2.shared.nlp.nav_types import NavigationMode, SensorReport
from DroneOS2.shared.nlp.trajectory_engine import VehicleOrigin

def build_nav_context(telemetry: TelemetryData) -> tuple[SensorReport, VehicleOrigin]:
    if telemetry.gps_valid and telemetry.global_pos_valid:
        mode = NavigationMode.MODE_A_GPS
    elif telemetry.local_pos_valid and not telemetry.gps_valid:
        mode = NavigationMode.MODE_B_LOCAL
    else:
        mode = NavigationMode.MODE_C_DEGRADED
        
    report = SensorReport(mode=mode)
    origin = VehicleOrigin(
        local_north_m=0.0,
        local_east_m=0.0,
        local_down_m=0.0,
        lat_deg=telemetry.latitude,
        lon_deg=telemetry.longitude,
        relative_alt_m=telemetry.altitude
    )
    
    return report, origin
