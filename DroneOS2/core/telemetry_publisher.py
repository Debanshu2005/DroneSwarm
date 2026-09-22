import time
import asyncio
from DroneOS2.shared.utils.logger import setup_logger
from DroneOS2.shared.protocol.messages import HeartbeatMessage, TelemetryMessage, SwarmHeartbeatMessage, SwarmStateMessage

logger = setup_logger("TelemetryPublisher")

class TelemetryPublisher:
    """
    Decouples the periodic broadcasting of telemetry and heartbeat 
    from the main application loop, enhancing modularity.
    """
    def __init__(self, node_id, network_adapter, flight_controller, flight_manager, mission_manager,
                 health_monitor=None, telemetry_interval: float = 0.5, heartbeat_interval: float = 1.0,
                 swarm_manager=None, state_store=None):
        self.node_id = node_id
        self.network = network_adapter
        self.fc = flight_controller
        self.fm = flight_manager
        self.mission = mission_manager
        self.health_monitor = health_monitor
        self.telemetry_interval = telemetry_interval
        self.heartbeat_interval = heartbeat_interval
        self.swarm_manager = swarm_manager
        self.state_store = state_store
        
        from DroneOS2.core.formation_engine import FormationEngine
        if self.swarm_manager and self.state_store:
            self.formation_engine = FormationEngine(self.swarm_manager, self.state_store)
        else:
            self.formation_engine = None
            
        self._running = False
        self._active_tasks = set()

    async def _publish_telemetry_loop(self) -> None:
        while self._running:
            try:
                telemetry = await self.fc.get_telemetry()
                telemetry.mission_state = self.mission.get_current_state()
                
                if self.health_monitor and self.health_monitor.last_heartbeat_time is not None:
                    telemetry.heartbeat_age = time.time() - self.health_monitor.last_heartbeat_time
                else:
                    telemetry.heartbeat_age = None
                
                msg = TelemetryMessage(
                    sender_id=self.node_id,
                    timestamp=time.time(),
                    telemetry=telemetry
                )
                await self.network.broadcast_message(msg)
            except asyncio.CancelledError:
                logger.info("Telemetry loop cancelled.")
                break
            except (OSError, RuntimeError, ValueError) as e:
                logger.exception(f"Error publishing telemetry: {e}")
            await asyncio.sleep(self.telemetry_interval)

    async def _publish_heartbeat_loop(self) -> None:
        while self._running:
            try:
                telemetry = await self.fc.get_telemetry()
                is_armed = getattr(telemetry, 'armed_state', None) == "ARMED"
                
                lat, lon, alt = None, None, None
                if telemetry.gps_valid:
                    lat = telemetry.latitude
                    lon = telemetry.longitude
                    alt = telemetry.altitude
                    
                msg = HeartbeatMessage(
                    sender_id=self.node_id,
                    timestamp=time.time(),
                    status="active" if is_armed else "standby",
                    lat=lat,
                    lon=lon,
                    alt=alt
                )
                await self.network.broadcast_message(msg)
                
                swarm_hb_msg = SwarmHeartbeatMessage(
                    sender_id=self.node_id,
                    timestamp=time.time(),
                    status="active" if is_armed else "standby",
                    battery_level=getattr(telemetry, 'battery_level', None)
                )
                await self.network.broadcast_message(swarm_hb_msg)
            except asyncio.CancelledError:
                logger.info("Heartbeat loop cancelled.")
                break
            except (OSError, RuntimeError, ValueError) as e:
                logger.exception(f"Error publishing heartbeat: {e}")
            await asyncio.sleep(self.heartbeat_interval)

    async def _publish_swarm_state_loop(self) -> None:
        while self._running:
            try:
                active_drones = (len(self.swarm_manager.registry.get_all_peers()) + 1) if self.swarm_manager else 1
                formation_type = "dynamic"
                target_waypoints = []
                
                if self.fm and self.fm.formation_params and self.formation_engine:
                    formation_type = self.fm.formation_params.get("type", "dynamic")
                    tel = await self.fc.get_telemetry()
                    wps = self.formation_engine.get_expected_positions(tel, self.fm.formation_params)
                    for pid, (lat, lon) in wps.items():
                        target_waypoints.append({"drone_id": pid, "lat": lat, "lon": lon})
                        
                msg = SwarmStateMessage(
                    sender_id=self.node_id,
                    timestamp=time.time(),
                    active_drones=active_drones,
                    formation_type=formation_type,
                    target_waypoints=target_waypoints
                )
                await self.network.broadcast_message(msg)
            except asyncio.CancelledError:
                logger.info("Swarm state loop cancelled.")
                break
            except Exception as e:
                logger.exception(f"Error publishing swarm state: {e}")
            await asyncio.sleep(2.0)

    def start(self):
        self._running = True
        for t in [
            asyncio.create_task(self._publish_telemetry_loop()),
            asyncio.create_task(self._publish_heartbeat_loop()),
            asyncio.create_task(self._publish_swarm_state_loop())
        ]:
            self._active_tasks.add(t)
            t.add_done_callback(self._active_tasks.discard)
        logger.info("TelemetryPublisher loops started.")
        
    def stop(self):
        self._running = False
        for task in self._active_tasks:
            task.cancel()
