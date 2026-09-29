"""
Master fix script - run from PhoneOS_Swarm root.
Writes all changed files for Part A and Part B across all four instances.
"""
import os, sys

INSTANCES = ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]

def pkg(inst):
    return inst  # import prefix equals folder name

def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print(f"  WROTE {path}")

# ─────────────────────────────────────────────────────────────────────────────
# PART A-1 + PART B-1 : shared/config/models.py  (CollisionAvoidanceConfig)
# ─────────────────────────────────────────────────────────────────────────────
MODELS_TEMPLATE = '''\
from typing import Optional, List
from pydantic import BaseModel

class DroneConfig(BaseModel):
    drone_id: str
    vehicle_name: str

class NetworkConfig(BaseModel):
    host: str
    port: int
    broadcast_address: str
    peer_host: Optional[str] = None
    peer_port: Optional[int] = None
    peer_endpoints: List[tuple[str, int]] = []
    heartbeat_interval: float = 1.0
    telemetry_interval: float = 0.5
    connection_timeout: float = 10.0

class CollisionAvoidanceConfig(BaseModel):
    enabled: bool = True
    min_horizontal_distance: float = 6.0
    min_vertical_distance: float = 2.0
    warning_distance: float = 10.0
    emergency_distance: float = 3.0
    neighbor_timeout_sec: float = 1.0
    lookahead_sec: float = 3.0
    avoidance_speed: float = 4.0
    emergency_speed: float = 5.0
    max_peer_age_sec: float = 1.0
    ground_altitude_m: float = 0.5

class SafetyLimitsConfig(BaseModel):
    max_horizontal_velocity: float = 5.0
    max_vertical_velocity: float = 3.0

class SmartRtlConfig(BaseModel):
    arrival_radius_m: float = 2.0
    timeout_s: float = 60.0

class SimFaultConfig(BaseModel):
    drop_gps: bool = False
    battery_drain_multiplier: float = 0.0

class FlightConfig(BaseModel):
    adapter_type: str
    takeoff_altitude: float
    max_velocity: float
    pipeline_hz: float = 20.0
    px4_connection_string: str
    px4_connection_candidates: Optional[List[str]] = None
    airsim_host: str
    airsim_port: int
    airsim_timeout: float = 5.0
    airsim_retry_count: int = 3
    collision_avoidance: Optional[CollisionAvoidanceConfig] = None
    safety_limits: Optional[SafetyLimitsConfig] = None
    smart_rtl: Optional[SmartRtlConfig] = None
    sim: Optional[SimFaultConfig] = None

class SafetyConfig(BaseModel):
    low_battery_threshold: float = 20.0
    critical_battery_threshold: float = 10.0
    heartbeat_timeout: float = 5.0

class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_file: str = "logs/droneos.log"
    max_bytes: int = 10485760
    backup_count: int = 5

class GSUIConfig(BaseModel):
    gs_id: str = "gs1"
    window_title: str
    theme: str
    refresh_rate_hz: int
    known_drones: List[str]

class MissionConfig(BaseModel):
    mission_storage_dir: str = "missions/"
    auto_start: bool = False
    default_speed: float = 5.0
    completion_action: str = "rtl"
    max_altitude: float = 120.0
    min_altitude: float = 1.0

class MovementConfig(BaseModel):
    max_horizontal_velocity: float = 15.0
    max_vertical_velocity: float = 3.0
    max_yaw_rate: float = 45.0

class FormationConfig(BaseModel):
    default_formation: str = "V"
    spacing: float = 5.0
    velocity_gain: float = 1.0

class GSConfig(BaseModel):
    ui: GSUIConfig
    network: NetworkConfig
    logging: LoggingConfig

class AppConfig(BaseModel):
    drone: Optional[DroneConfig] = None
    network: Optional[NetworkConfig] = None
    flight: Optional[FlightConfig] = None
    safety: Optional[SafetyConfig] = None
    logging: Optional[LoggingConfig] = None
    mission: Optional[MissionConfig] = None
    movement: Optional[MovementConfig] = None
    formation: Optional[FormationConfig] = None
'''

for inst in INSTANCES:
    write(f"{inst}/shared/config/models.py", MODELS_TEMPLATE)

print("models.py done")
