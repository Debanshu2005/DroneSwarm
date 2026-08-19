import time
from typing import Dict, Callable, Optional, Any

from GroundStation.shared.utils.logger import setup_logger
from GroundStation.shared.communication.interfaces import INetworkAdapter
from GroundStation.shared.protocol.messages import (
    BaseMessage, MessageType, HeartbeatMessage, TelemetryMessage,
    ControlMessage, CommandAction, TelemetryData,
    MissionProgressMessage, MissionStatusMessage, StatusMessage, ErrorMessage
)

logger = setup_logger("GS_NetworkManager")

class DroneInfo:
    def __init__(self, drone_id: str):
        self.drone_id = drone_id
        self.last_heartbeat: float = 0.0
        self.status: str = "unknown"
        self.telemetry: Optional[TelemetryData] = None
        self.packet_count: int = 0
        self.latency: float = 0.0
        self.heartbeat_frequency: float = 0.0
        self.last_message_time: float = 0.0

class GSNetworkManager:
    """
    Manages networking for the GroundStation.
    Tracks discovered drones and provides interfaces for the UI to subscribe to updates.
    """
    def __init__(self, network_adapter: INetworkAdapter, gs_id: str):
        self.network = network_adapter
        self.gs_id = gs_id
        self.drones: Dict[str, DroneInfo] = {}
        
        # Diagnostics
        self.total_packets_received = 0
        self.total_packets_sent = 0
        
        # Callbacks for UI updates
        self.on_drone_discovered: Optional[Callable[[str], None]] = None
        self.on_drone_disconnected: Optional[Callable[[str], None]] = None
        self.on_heartbeat_updated: Optional[Callable[[str, str], None]] = None
        self.on_telemetry_updated: Optional[Callable[[str, TelemetryData], None]] = None
        self.on_mission_progress: Optional[Callable[[str, int, int, float], None]] = None
        self.on_mission_status: Optional[Callable[[str, str], None]] = None
        self.on_status_updated: Optional[Callable[[str, str], None]] = None
        self.on_error_received: Optional[Callable[[str, str], None]] = None
        
        # Pruning configuration
        self.drone_timeout_sec = 5.0
        self._prune_task = None
        
        self.network.register_callback(self._on_message_received)

    async def start(self):
        await self.network.start()
        
        import asyncio
        async def _prune_loop():
            while True:
                await asyncio.sleep(2.0)
                self.prune_disconnected_drones()
        
        self._prune_task = asyncio.create_task(_prune_loop())
        logger.info("GroundStation Network Manager started.")

    async def stop(self):
        if self._prune_task:
            self._prune_task.cancel()
        await self.network.stop()
        logger.info("GroundStation Network Manager stopped.")

    def prune_disconnected_drones(self):
        now = time.time()
        stale_drones = []
        for drone_id, info in self.drones.items():
            if now - info.last_heartbeat > self.drone_timeout_sec:
                stale_drones.append(drone_id)
                
        for drone_id in stale_drones:
            logger.warning(f"Dispatcher: Drone {drone_id} timed out. Removing from registry.")
            del self.drones[drone_id]
            if self.on_drone_disconnected:
                self.on_drone_disconnected(drone_id)
            if self.on_status_updated:
                self.on_status_updated(drone_id, "HEARTBEAT LOST")

    async def _on_message_received(self, msg: BaseMessage) -> None:
        self.total_packets_received += 1
        
        if msg.sender_id != self.gs_id and msg.sender_id.startswith("drone"):
            if msg.sender_id not in self.drones:
                self.drones[msg.sender_id] = DroneInfo(msg.sender_id)
                logger.info(f"Dispatcher: Discovered new drone {msg.sender_id}")
                if self.on_drone_discovered:
                    self.on_drone_discovered(msg.sender_id)
            
            drone = self.drones[msg.sender_id]
            now = time.time()
            if drone.last_message_time > 0:
                dt = now - drone.last_message_time
                if dt > 0:
                    freq = 1.0 / dt
                    drone.heartbeat_frequency = (drone.heartbeat_frequency * 0.9) + (freq * 0.1)
            drone.last_message_time = now
            drone.packet_count += 1
            drone.latency = now - msg.timestamp

        if msg.msg_type == MessageType.HEARTBEAT:
            self._handle_heartbeat(msg)
        elif msg.msg_type == MessageType.TELEMETRY:
            self._handle_telemetry(msg)
        elif msg.msg_type == MessageType.MISSION_PROGRESS:
            self._handle_mission_progress(msg)
        elif msg.msg_type == MessageType.MISSION_STATUS:
            self._handle_mission_status(msg)
        elif msg.msg_type == MessageType.STATUS:
            if self.on_status_updated:
                self.on_status_updated(msg.sender_id, msg.status_text)
        elif msg.msg_type == MessageType.ERROR:
            if self.on_error_received:
                self.on_error_received(msg.sender_id, msg.error_msg)

    def _handle_mission_progress(self, msg: MissionProgressMessage) -> None:
        if self.on_mission_progress:
            pct = 0.0
            if msg.total_waypoints > 0:
                pct = (msg.current_waypoint / msg.total_waypoints) * 100.0
            self.on_mission_progress(msg.mission_id, msg.current_waypoint, msg.total_waypoints, pct)
            
    def _handle_mission_status(self, msg: MissionStatusMessage) -> None:
        if self.on_mission_status:
            self.on_mission_status(msg.status, msg.mission_id)

    def _handle_heartbeat(self, msg: HeartbeatMessage) -> None:
        if msg.sender_id != self.gs_id:
            is_new = msg.sender_id not in self.drones
            if is_new:
                self.drones[msg.sender_id] = DroneInfo(msg.sender_id)
                logger.info(f"Dispatcher: Discovered new drone {msg.sender_id} via heartbeat")
                if self.on_drone_discovered:
                    self.on_drone_discovered(msg.sender_id)
            
            drone = self.drones[msg.sender_id]
            drone.last_heartbeat = time.time()
            drone.status = msg.status
            
            if self.on_heartbeat_updated:
                self.on_heartbeat_updated(msg.sender_id, msg.status)
                
            logger.debug(f"Dispatcher: Processed heartbeat from {msg.sender_id}")
        else:
            logger.debug(f"Dispatcher: Ignored heartbeat from self ({msg.sender_id})")

    def _handle_telemetry(self, msg: TelemetryMessage) -> None:
        if msg.sender_id != self.gs_id:
            if msg.sender_id not in self.drones:
                # Discovered via telemetry instead of heartbeat
                self.drones[msg.sender_id] = DroneInfo(msg.sender_id)
                logger.info(f"Dispatcher: Discovered new drone {msg.sender_id} via telemetry")
                if self.on_drone_discovered:
                    self.on_drone_discovered(msg.sender_id)
            
            drone = self.drones[msg.sender_id]
            drone.telemetry = msg.telemetry
            if self.on_telemetry_updated:
                self.on_telemetry_updated(msg.sender_id, msg.telemetry)
            logger.debug(f"Dispatcher: Processed telemetry from {msg.sender_id}")
        else:
            logger.debug(f"Dispatcher: Ignored telemetry from self ({msg.sender_id})")

    async def send_command(self, target_id: Optional[str], action: CommandAction, params: Optional[Dict[str, Any]] = None) -> None:
        msg = ControlMessage(
            sender_id=self.gs_id,
            timestamp=time.time(),
            action=action,
            params=params,
            target_id=target_id
        )
        logger.info(f"Sending command {action.value} to {target_id or 'ALL'}")
        self.total_packets_sent += 1
        await self.network.send_message(target_id, msg)
        
    async def send_mission_message(self, target_id: Optional[str], msg: BaseMessage) -> None:
        """
        Routes a mission message (Upload, Pause, Resume, Abort) to a specific target or broadcasts if None.
        """
        msg.sender_id = self.gs_id
        msg.timestamp = time.time()
        # To support unicast routing via the UDP adapter we inject target_id dynamically onto the message.
        # This mirrors the behavior of ControlMessage.
        setattr(msg, 'target_id', target_id)
        
        logger.info(f"Sending mission message {msg.msg_type.value} to {target_id or 'ALL'}")
        self.total_packets_sent += 1
        await self.network.send_message(target_id, msg)

    async def broadcast_heartbeat(self) -> None:
        msg = HeartbeatMessage(
            sender_id=self.gs_id,
            timestamp=time.time(),
            status="active"
        )
        self.total_packets_sent += 1
        
        # Broadcast globally for discovery
        await self.network.broadcast_message(msg)
        
        # Explicitly unicast to known drones to ensure reliable heartbeat delivery across subnets/interfaces
        for drone_id in self.drones.keys():
            await self.network.send_message(drone_id, msg)
