from DroneOS.core.interfaces import IFlightController
from DroneOS.shared.utils.logger import setup_logger
from typing import Dict, Any

logger = setup_logger("FlightManager")

class FlightManager:
    """
    High-level state machine and wrapper around the abstract IFlightController.
    Handles sequence of operations (e.g., must be armed before takeoff).
    """
    def __init__(self, flight_controller: IFlightController, min_srtl_altitude_m: float = 2.0):
        self.fc = flight_controller
        self._last_move_time = 0.0
        self._deadman_task = None
        self._active_flight_task = None
        self.swarm_manager = None
        self._active_navigation_frame = None
        self._min_srtl_altitude_m = min_srtl_altitude_m

    def set_swarm_manager(self, swarm_manager):
        self.swarm_manager = swarm_manager

    async def arm(self, params: Dict[str, Any] = None) -> bool:
        success = await self.fc.arm()
        if success:
            logger.info("Drone arm command accepted.")
        return success

    async def disarm(self, params: Dict[str, Any] = None) -> bool:
        success = await self.fc.disarm()
        if success:
            logger.info("Drone disarm command accepted.")
        return success

    async def takeoff(self, params: Dict[str, Any] = None) -> bool:
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot takeoff: Drone telemetry indicates it is not ARMED.")
            return False
            
        altitude = getattr(self.fc.config, "takeoff_altitude", 5.0) if hasattr(self.fc, "config") else 5.0
        if params and 'altitude_m' in params:
            try:
                parsed = float(params['altitude_m'])
                if 1.0 <= parsed <= 10.0:
                    altitude = parsed
                else:
                    logger.error(f"Cannot takeoff: Altitude {parsed} is out of bounds (1.0 - 10.0m)")
                    return False
            except (ValueError, TypeError):
                logger.error("Cannot takeoff: Invalid altitude parameter format.")
                return False
                
        success = await self.fc.takeoff(altitude)
        if success:
            logger.info(f"Takeoff command accepted to {altitude}m.")
        return success

    def _cancel_active_task(self):
        if self._active_flight_task and not self._active_flight_task.done():
            self._active_flight_task.cancel()
            self._active_flight_task = None

    async def land(self, params: Dict[str, Any] = None) -> bool:
        self._cancel_active_task()
        success = await self.fc.land()
        if success:
            logger.info("Land command accepted.")
        return success

    async def rtl(self, params: Dict[str, Any] = None) -> bool:
        self._cancel_active_task()
        success = await self.fc.rtl()
        if success:
            logger.info("RTL command accepted.")
        return success

    async def smart_rtl(self, params: Dict[str, Any] = None) -> bool:
        self._cancel_active_task()

        telemetry = await self.fc.get_telemetry()
        if telemetry.altitude is None or telemetry.altitude < self._min_srtl_altitude_m:
            logger.error(f"SRTL rejected: altitude too low ({telemetry.altitude}m, "
                          f"minimum {self._min_srtl_altitude_m}m) for a safe same-altitude return.")
            return False

        home = await self.fc.get_home_position()
        if home is None:
            logger.error("SRTL rejected: home position not available.")
            return False
        home_lat, home_lon, _ = home

        logger.warning(
            "SRTL initiated: returning at current altitude "
            f"({telemetry.altitude:.1f}m) WITHOUT climbing. This does not have standard "
            "RTL's obstacle-clearance margin — operator is responsible for confirming "
            "the direct path home is clear."
        )

        import asyncio
        self._active_flight_task = asyncio.create_task(self._smart_rtl_loop(home_lat, home_lon))
        return True

    async def _smart_rtl_loop(self, home_lat: float, home_lon: float):
        """
        Structural note: This polling loop is kept separate from TerminalController._run_waypoints.
        While they share distance-checking logic, they operate at different layers:
        _run_waypoints passively monitors arrival after issuing a one-off command,
        while this loop actively recalculates and re-issues goto_location with fresh altitude
        on every iteration, and must gate all operations on GPS validity.
        """
        import asyncio, time
        from DroneOS.shared.nlp.trajectory_engine import global_distance_m
        
        acceptance_radius_m = 2.5
        timeout_s = 60.0
        start_time = time.monotonic()
        
        try:
            while True:
                if time.monotonic() - start_time > timeout_s:
                    logger.error("SRTL timeout reached.")
                    await self.fc.hover()
                    return
                
                telemetry = await self.fc.get_telemetry()
                
                if not telemetry.gps_valid:
                    await asyncio.sleep(0.5)
                    continue
                
                if telemetry.latitude is not None and telemetry.longitude is not None:
                    dist = global_distance_m(telemetry.latitude, telemetry.longitude, home_lat, home_lon)
                    if dist <= acceptance_radius_m:
                        logger.info("SRTL arrived at home position, landing.")
                        await self.fc.land()
                        return
                
                alt = telemetry.altitude if telemetry.altitude is not None else self._min_srtl_altitude_m
                await self.fc.goto_location(home_lat, home_lon, alt, yaw=0.0)
                    
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.info("SRTL loop cancelled.")
            await self.fc.hover()

    async def hover(self, params: Dict[str, Any] = None) -> bool:
        self._cancel_active_task()
        return await self.fc.hover()
        
    async def stop(self, params: Dict[str, Any] = None) -> bool:
        self._cancel_active_task()
        if hasattr(self.fc, 'kill'):
            return await self.fc.kill()
        return await self.fc.hover()
        
    async def _move_deadman_monitor(self):
        import asyncio, time
        try:
            while True:
                await asyncio.sleep(0.1)
                if time.time() - self._last_move_time > 0.5:
                    logger.info("Deadman timeout reached. Stopping movement.")
                    if hasattr(self.fc, 'stop_movement'):
                        await self.fc.stop_movement()
                    await self.fc.hover()
                    self._deadman_task = None
                    break
        except asyncio.CancelledError:
            pass

    async def move(self, params: Dict[str, Any]) -> bool:
        self._cancel_active_task()
        self._active_navigation_frame = "LOCAL_NED"
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot move: Drone is not ARMED.")
            return False
            
        vx = float(params.get('vx', 0.0))
        vy = float(params.get('vy', 0.0))
        vz = float(params.get('vz', 0.0))
        yaw_rate = float(params.get('yaw_rate', 0.0))
        
        # Enforce configurable/safe speed limits
        vx = max(-5.0, min(5.0, vx))
        vy = max(-5.0, min(5.0, vy))
        vz = max(-3.0, min(3.0, vz))
        yaw_rate = max(-90.0, min(90.0, yaw_rate))

        import time, asyncio
        self._last_move_time = time.time()
        
        if self._deadman_task is None or self._deadman_task.done():
            self._deadman_task = asyncio.create_task(self._move_deadman_monitor())

        # duration is managed by the deadman now, pass 0.5 for fc internal loop if needed
        return await self.fc.move_velocity(vx, vy, vz, 0.5, yaw_rate)

    async def goto(self, params: Dict[str, Any]) -> bool:
        self._active_navigation_frame = "GLOBAL_RELATIVE_ALT"
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot goto: Drone is not ARMED.")
            return False
            
        lat = params.get('lat')
        lon = params.get('lon')
        alt = params.get('alt')
        if lat is None or lon is None or alt is None:
            logger.error("Goto requires lat, lon, and alt.")
            return False
            
        # Basic Validation
        if not telemetry.gps_valid:
            logger.error("Goto failed: GPS is invalid.")
            return False
        if telemetry.battery_level is not None and telemetry.battery_level < 10.0:
            logger.error("Goto failed: Battery too low for mission.")
            return False
            
        # Call FC adapter goto (which should compile a temporary mission)
        return await self.fc.goto_location(lat, lon, alt)

    async def goto_local(self, params: Dict[str, Any]) -> bool:
        self._active_navigation_frame = "LOCAL_NED"
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot goto_local: Drone is not ARMED.")
            return False
            
        north = params.get('north')
        east = params.get('east')
        down = params.get('down')
        if north is None or east is None or down is None:
            logger.error("Goto local requires north, east, and down parameters.")
            return False
            
        # Basic Validation
        if not telemetry.local_pos_valid:
            logger.error("Goto local failed: Local position (Optical Flow) is invalid.")
            return False
        if telemetry.battery_level is not None and telemetry.battery_level < 10.0:
            logger.error("Goto local failed: Battery too low for mission.")
            return False
            
        # Call FC adapter goto_local_ned
        if hasattr(self.fc, 'goto_local_ned'):
            return await self.fc.goto_local_ned(north, east, down)
        else:
            logger.error("Flight Controller does not support goto_local_ned")
            return False

    async def set_mode(self, params: Dict[str, Any]) -> bool:
        mode = params.get('mode')
        if not mode:
            logger.error("Cannot set mode: No mode specified.")
            return False
            
        success = await self.fc.set_mode(mode)
        if success:
            logger.info(f"Mode set to {mode} accepted.")
        return success

    async def _formation_flight_loop(self, params: Dict[str, Any]):
        import asyncio, time, math
        from DroneOS.core.formation_manager import FormationManager, FormationType, convert_local_offset_to_global
        
        form_mgr = FormationManager()
        f_type_str = params.get('type', 'V').upper()
        try:
            f_type = FormationType(f_type_str)
        except ValueError:
            logger.error(f"Invalid formation type: {f_type_str}")
            return
            
        spacing = float(params.get('spacing', 2.0))
        speed = float(params.get('speed', 0.5))
        form_mgr.set_formation(f_type, spacing)
        
        my_node_id = self.swarm_manager.identity.drone_id
        
        logger.info(f"Starting formation loop: {f_type_str}, spacing: {spacing}m, speed: {speed}m/s")
        
        last_total_drones = 0
        
        try:
            while True:
                # 1. Get active peers (healthy in last 3 seconds)
                now = time.time()
                active_peers = [pid for pid, peer in self.swarm_manager.registry.peers.items() if (now - peer.last_seen) < 3.0]
                if my_node_id not in active_peers:
                    active_peers.append(my_node_id)
                active_peers.sort() # Sorting gives deterministic indexing
                
                my_index = active_peers.index(my_node_id)
                total_drones = len(active_peers)
                
                # Separation safety check
                if total_drones != last_total_drones:
                    last_total_drones = total_drones
                    points = [form_mgr.get_offset(i, total_drones) for i in range(total_drones)]
                    min_dist = float('inf')
                    for i in range(total_drones):
                        for j in range(i+1, total_drones):
                            dist = math.sqrt((points[i][0]-points[j][0])**2 + (points[i][1]-points[j][1])**2)
                            if dist < min_dist:
                                min_dist = dist
                    if min_dist < spacing / 2.0 and total_drones > 1:
                        logger.warning(f"Formation safety check: minimum separation {min_dist:.1f}m is less than half spacing {spacing/2.0:.1f}m")
                
                anchor_id = active_peers[0]
                telemetry = await self.fc.get_telemetry()
                
                if not telemetry.gps_valid:
                    logger.warning("Formation loop waiting: GPS invalid.")
                    await self.fc.hover()
                    await asyncio.sleep(0.5)
                    continue

                if my_node_id == anchor_id:
                    # Anchor node holds position (could be extended to track a commanded destination)
                    await self.fc.hover()
                else:
                    # Non-anchor looks up anchor position
                    anchor_peer = self.swarm_manager.registry.get_peer(anchor_id)
                    anchor_pos_valid = (
                        anchor_peer and 
                        anchor_peer.last_position_time is not None and 
                        (now - anchor_peer.last_position_time) < 3.0 and
                        anchor_peer.lat is not None and anchor_peer.lon is not None and anchor_peer.alt is not None
                    )
                    
                    if not anchor_pos_valid:
                        logger.warning(f"Anchor {anchor_id} position stale or missing. Hovering.")
                        await self.fc.hover()
                    else:
                        dx_north, dy_east, dz_down = form_mgr.get_offset(my_index, total_drones)
                        target_lat, target_lon, target_alt = convert_local_offset_to_global(
                            anchor_peer.lat, anchor_peer.lon, anchor_peer.alt, dx_north, dy_east
                        )
                        await self.fc.goto_location(target_lat, target_lon, target_alt, yaw=0.0)
                
                await asyncio.sleep(0.5)
                
        except asyncio.CancelledError:
            logger.info("Formation loop cancelled.")
            await self.fc.hover()

    async def formation_update(self, params: Dict[str, Any]) -> bool:
        if not self.swarm_manager:
            logger.error("Cannot start formation: SwarmManager not injected.")
            return False
            
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot start formation: Drone is not ARMED.")
            return False
            
        self._cancel_active_task()
        import asyncio
        self._active_navigation_frame = "GLOBAL_RELATIVE_ALT"
        self._active_flight_task = asyncio.create_task(self._formation_flight_loop(params))
        return True

    def is_gps_dependent_navigation_active(self, telemetry=None) -> bool:
        if self._active_navigation_frame == "GLOBAL_RELATIVE_ALT":
            return True
        if self._active_navigation_frame == "LOCAL_NED":
            return False

        mode = getattr(telemetry, "flight_mode", "") or ""
        mode = mode.upper()
        return mode in {"AUTO", "MISSION", "GUIDED", "LOITER", "RTL", "HOLD", "POSCTL", "POSITION", "OFFBOARD"}
