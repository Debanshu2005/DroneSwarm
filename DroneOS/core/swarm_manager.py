import time
from typing import Dict, Optional, List, Any
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.shared.protocol.messages import (
    SwarmHeartbeatMessage, HeartbeatMessage, DroneJoinMessage, DroneLeaveMessage,
    SwarmStateMessage, PeerStateMessage, DroneIdentityMessage, TelemetryMessage, TelemetryData
)

try:
    from DroneOS.perception.diagnostics import (
        log_peer_telemetry_rx,
        log_peer_heartbeat_rx,
        log_peer_lifecycle,
        log_peer_position_status,
    )
except ImportError:
    log_peer_telemetry_rx = None
    log_peer_heartbeat_rx = None
    log_peer_lifecycle = None
    log_peer_position_status = None

logger = setup_logger("SwarmManager")

class DroneIdentityManager:
    def __init__(self, drone_id: str):
        self.drone_id = drone_id
        self.role = "peer"
        self.capabilities: Dict[str, Any] = {"camera": True, "lidar": False}
        
    def get_identity_message(self) -> DroneIdentityMessage:
        return DroneIdentityMessage(
            sender_id=self.drone_id, timestamp=time.time(),
            drone_id=self.drone_id, role=self.role
        )

class PeerStateManager:
    def __init__(self, drone_id: str):
        self.drone_id = drone_id
        self.last_seen: float = time.time()
        self.is_active: bool = True
        self.current_task: Optional[str] = None
        self.telemetry: Optional[TelemetryData] = None
        self.lat: Optional[float] = None
        self.lon: Optional[float] = None
        self.alt: Optional[float] = None
        self.velocity_x: Optional[float] = None
        self.velocity_y: Optional[float] = None
        self.velocity_z: Optional[float] = None
        self.position_valid: bool = False
        self.last_position_time: Optional[float] = None
        self.position_msg_time: Optional[float] = None
        self.battery_level: Optional[float] = None

    @property
    def vx(self) -> Optional[float]:
        return self.velocity_x

    @property
    def vy(self) -> Optional[float]:
        return self.velocity_y

    @property
    def vz(self) -> Optional[float]:
        return self.velocity_z

class SwarmRegistry:
    def __init__(self, my_id: Optional[str] = None):
        self.my_id = my_id or "unknown"
        self.peers: Dict[str, PeerStateManager] = {}

    def get_peer(self, drone_id: str) -> Optional[PeerStateManager]:
        return self.peers.get(drone_id)

    def add_peer(self, drone_id: str):
        if drone_id not in self.peers:
            self.peers[drone_id] = PeerStateManager(drone_id)
            logger.info(f"Registered new peer: {drone_id}")

    def remove_peer(self, drone_id: str):
        if drone_id in self.peers:
            del self.peers[drone_id]
            logger.info(f"Removed peer from registry: {drone_id}")

    def get_all_peers(self) -> List[str]:
        return list(self.peers.keys())

    def check_all_peer_positions(self) -> Dict[str, Dict[str, Any]]:
        """Diagnostic check on freshness and validity of all registered peers."""
        now_curr = time.time()
        results = {}
        for pid, peer in self.peers.items():
            has_pos = peer.lat is not None and peer.lon is not None
            age = (now_curr - peer.last_position_time) if peer.last_position_time else None
            status = "VALID"
            if not has_pos:
                status = "MISSING"
            elif age is not None and age > 2.0:
                status = "STALE"
            results[pid] = {
                "status": status,
                "age_s": round(age, 3) if age is not None else None,
                "lat": peer.lat,
                "lon": peer.lon,
                "alt": peer.alt
            }
            if log_peer_position_status:
                log_peer_position_status(self.my_id, pid, status, age, peer.lat, peer.lon, peer.alt)
        return results

class DroneDiscoveryManager:
    def __init__(self, registry: SwarmRegistry, my_id: Optional[str] = None):
        self.registry = registry
        self.my_id = my_id or getattr(registry, "my_id", "unknown")

    def handle_join(self, msg: DroneJoinMessage):
        self.registry.add_peer(msg.sender_id)
        if log_peer_lifecycle:
            log_peer_lifecycle(self.my_id, msg.sender_id, "JOIN", f"joined from {msg.drone_ip}:{msg.drone_port}")
        logger.info(f"Discovery: {msg.sender_id} joined from {msg.drone_ip}:{msg.drone_port}")

class DroneRemovalManager:
    def __init__(self, registry: SwarmRegistry, my_id: Optional[str] = None):
        self.registry = registry
        self.my_id = my_id or getattr(registry, "my_id", "unknown")

    def handle_leave(self, msg: DroneLeaveMessage):
        self.registry.remove_peer(msg.sender_id)
        if log_peer_lifecycle:
            log_peer_lifecycle(self.my_id, msg.sender_id, "LEAVE", f"left due to: {msg.reason}")
        logger.info(f"Removal: {msg.sender_id} left due to: {msg.reason}")

class SwarmHeartbeatManager:
    def __init__(self, registry: SwarmRegistry, timeout_sec: float = 5.0, my_id: Optional[str] = None):
        self.registry = registry
        self.timeout_sec = timeout_sec
        self.my_id = my_id or getattr(registry, "my_id", "unknown")

    def handle_heartbeat(self, msg: HeartbeatMessage):
        peer = self.registry.get_peer(msg.sender_id)
        was_registered = peer is not None
        if not peer:
            self.registry.add_peer(msg.sender_id)
            peer = self.registry.get_peer(msg.sender_id)
            if log_peer_lifecycle:
                log_peer_lifecycle(self.my_id, msg.sender_id, "REGISTER_ON_HEARTBEAT", "first heartbeat observed")
        if peer:
            peer.last_seen = time.time()
            peer.is_active = (msg.status == "active")
            if getattr(msg, 'lat', None) is not None:
                peer.lat = msg.lat
                peer.lon = msg.lon
                peer.alt = msg.alt
                peer.last_position_time = time.time()
        if log_peer_heartbeat_rx:
            log_peer_heartbeat_rx(
                drone_id=self.my_id,
                sender_id=msg.sender_id,
                in_registry=was_registered,
                status=msg.status,
                is_active=(msg.status == "active"),
                lat=getattr(msg, 'lat', None),
                lon=getattr(msg, 'lon', None),
                alt=getattr(msg, 'alt', None)
            )

    def handle_swarm_heartbeat(self, msg: SwarmHeartbeatMessage):
        peer = self.registry.get_peer(msg.sender_id)
        was_registered = peer is not None
        if not peer:
            self.registry.add_peer(msg.sender_id)
            peer = self.registry.get_peer(msg.sender_id)
            if log_peer_lifecycle:
                log_peer_lifecycle(self.my_id, msg.sender_id, "REGISTER_ON_SWARM_HEARTBEAT", "first swarm heartbeat observed")
        if peer:
            peer.last_seen = time.time()
            peer.is_active = (msg.status == "active")
            if msg.battery_level is not None:
                peer.battery_level = msg.battery_level
        if log_peer_heartbeat_rx:
            log_peer_heartbeat_rx(
                drone_id=self.my_id,
                sender_id=msg.sender_id,
                in_registry=was_registered,
                status=msg.status,
                is_active=(msg.status == "active"),
                lat=None,
                lon=None,
                alt=None
            )

    def purge_stale_peers(self):
        current = time.time()
        stale = [pid for pid, peer in self.registry.peers.items() if current - peer.last_seen > self.timeout_sec]
        for pid in stale:
            self.registry.remove_peer(pid)
            if log_peer_lifecycle:
                log_peer_lifecycle(self.my_id, pid, "PURGE_TIMEOUT", f"stale after {self.timeout_sec}s timeout")
            logger.warning(f"Purged stale peer {pid} after {self.timeout_sec}s timeout")

class PeerSynchronization:
    def __init__(self, registry: SwarmRegistry, my_id: Optional[str] = None):
        self.registry = registry
        self.my_id = my_id or getattr(registry, "my_id", "unknown")

    def handle_peer_state(self, msg: PeerStateMessage):
        peer = self.registry.get_peer(msg.peer_id)
        if peer:
            peer.is_active = msg.is_active
            peer.current_task = msg.current_task

    def handle_telemetry(self, msg: TelemetryMessage):
        peer = self.registry.get_peer(msg.sender_id)
        in_reg = peer is not None
        if not peer:
            self.registry.add_peer(msg.sender_id)
            peer = self.registry.get_peer(msg.sender_id)
            in_reg = True
            if log_peer_lifecycle:
                log_peer_lifecycle(self.my_id, msg.sender_id, "REGISTER_ON_TELEMETRY", "first telemetry observed")

        t = msg.telemetry
        gps_valid = bool(t and getattr(t, 'gps_valid', False))
        lat = getattr(t, 'latitude', None) if t else None
        lon = getattr(t, 'longitude', None) if t else None
        alt = getattr(t, 'altitude', None) if t else None
        has_coords = bool(lat is not None and lon is not None)
        pos_updated = False
        pos_ts = None

        local_now = time.time()
        msg_ts = getattr(t, 'timestamp', None) or getattr(msg, 'timestamp', None) or local_now
        if peer:
            peer.telemetry = t
            peer.last_seen = local_now
            peer.position_msg_time = msg_ts
            if t:
                peer.velocity_x = getattr(t, "velocity_x", getattr(t, "vx", None))
                peer.velocity_y = getattr(t, "velocity_y", getattr(t, "vy", None))
                peer.velocity_z = getattr(t, "velocity_z", getattr(t, "vz", None))
            # Also update position fields used by FormationEngine anchor checks
            if t and gps_valid and has_coords:
                peer.lat = lat
                peer.lon = lon
                peer.alt = alt
                peer.position_valid = True
                peer.last_position_time = local_now
                pos_updated = True
                pos_ts = peer.last_position_time

        if log_peer_telemetry_rx:
            log_peer_telemetry_rx(
                drone_id=self.my_id,
                sender_id=msg.sender_id,
                in_registry=in_reg,
                gps_valid=gps_valid,
                has_lat_lon=has_coords,
                lat=lat,
                lon=lon,
                alt=alt,
                position_updated=pos_updated,
                position_timestamp=pos_ts
            )

class SwarmMembership:
    """
    Coordinates the decentralized swarm network.
    No Master/Slave interactions exist. Every drone evaluates SwarmRegistry identically.
    """
    def __init__(self, drone_id: str):
        self.identity = DroneIdentityManager(drone_id)
        self.registry = SwarmRegistry(my_id=drone_id)
        self.discovery = DroneDiscoveryManager(self.registry, my_id=drone_id)
        self.removal = DroneRemovalManager(self.registry, my_id=drone_id)
        self.heartbeat_mgr = SwarmHeartbeatManager(self.registry, my_id=drone_id)
        self.sync = PeerSynchronization(self.registry, my_id=drone_id)
        
    def generate_swarm_state(self) -> SwarmStateMessage:
        return SwarmStateMessage(
            sender_id=self.identity.drone_id,
            timestamp=time.time(),
            active_drones=len(self.registry.get_all_peers()),
            formation_type="dynamic",
            target_waypoints=[]
        )
