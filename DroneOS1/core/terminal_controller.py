import asyncio
import logging
import sys
import math
import time

from DroneOS1.core.interfaces import IFlightController
from DroneOS1.core.command_handler import CommandHandler
from DroneOS1.shared.protocol.messages import ControlMessage, CommandAction, StatusMessage
from DroneOS1.shared.nlp.trajectory_engine import (
    parse_task_sequence, 
    TaskAction, 
    ParsedTask, 
    build_trajectory, 
    GlobalTarget,
    global_distance_m,
    TargetFrame,
    LocalTarget,
    local_distance_m
)
from DroneOS1.shared.nlp.telemetry_bridge import build_nav_context

logger = logging.getLogger(__name__)

class TerminalController:
    def __init__(self, command_handler: CommandHandler, flight_controller: IFlightController, node_id: str):
        self.command_handler = command_handler
        self.flight_controller = flight_controller
        self.node_id = node_id
        self.network = None

    async def run_repl(self) -> None:
        logger.info("Starting Terminal Controller REPL...")
        loop = asyncio.get_running_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                await self.process_text(line, sender_id=self.node_id)
            except asyncio.CancelledError:
                logger.info("Terminal Controller REPL cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in REPL loop: {e}")

    async def process_text(self, text: str, sender_id: str) -> None:
        try:
            sequence = parse_task_sequence(text)
        except ValueError as e:
            logger.error(f"Failed to parse command: {e}")
            return

        if self.network is not None:
            # sequence.action_names works because it was added in parse_mission logic
            summary = f"Parsed: {', '.join(t.action for t in sequence.tasks)}"
            echo_msg = StatusMessage(
                sender_id=self.node_id, 
                target_id=sender_id, 
                status_text=summary, 
                severity="info", 
                timestamp=time.time()
            )
            asyncio.create_task(self.network.broadcast_message(echo_msg))

        for task in sequence.tasks:
            success = await self._execute_task(task, sender_id)
            if not success:
                logger.warning(f"Task {task.action} failed, aborting sequence.")
                break

    async def _execute_task(self, task: ParsedTask, sender_id: str) -> bool:
        if task.action == TaskAction.TAKEOFF:
            # Check if we need to auto-arm
            telemetry = await self.flight_controller.get_telemetry()
            if getattr(telemetry, 'armed_state', None) != "ARMED":
                arm_msg = ControlMessage(
                    action=CommandAction.ARM,
                    params={},
                    sender_id=sender_id,
                    timestamp=time.time()
                )
                arm_success = await self.command_handler.handle_command(arm_msg)
                if not arm_success:
                    return False

                start_time = time.monotonic()
                while True:
                    if time.monotonic() - start_time > getattr(self, 'ARM_TIMEOUT', 10.0):
                        logger.warning("Timed out waiting for armed_state confirmation after ARM.")
                        return False
                    telemetry = await self.flight_controller.get_telemetry()
                    if getattr(telemetry, 'armed_state', None) == "ARMED":
                        break
                    await asyncio.sleep(0.5)
            
            msg = ControlMessage(
                action=CommandAction.TAKEOFF,
                params={"altitude_m": task.params.get("h", 5.0)},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
        
        elif task.action == TaskAction.LAND:
            msg = ControlMessage(
                action=CommandAction.LAND,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            success = await self.command_handler.handle_command(msg)
            if not success:
                return False
                
            # Wait for landing to complete before auto-disarming
            start_time = time.monotonic()
            while True:
                if time.monotonic() - start_time > 45.0:
                    logger.warning("Land timeout while waiting to auto-disarm")
                    break
                telemetry = await self.flight_controller.get_telemetry()
                current_alt = getattr(telemetry, 'altitude', 10.0) or 10.0
                if current_alt <= 0.2:
                    break
                await asyncio.sleep(0.5)
                
            await asyncio.sleep(2.0)
            disarm_msg = ControlMessage(
                action=CommandAction.DISARM,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(disarm_msg)
            
        elif task.action == TaskAction.RTL:
            msg = ControlMessage(
                action=CommandAction.RTL,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
            
        elif task.action == TaskAction.SRTL:
            msg = ControlMessage(
                action=CommandAction.SRTL,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
            
        elif task.action == TaskAction.FORMATION:
            msg = ControlMessage(
                action=CommandAction.FORMATION_UPDATE,
                params={"type": task.params.get("type", "CIRCLE"), "spacing": task.params.get("spacing", 5.0)},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
            
        elif task.action == TaskAction.HOVER:
            msg = ControlMessage(
                action=CommandAction.HOVER,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
            
        elif task.action == TaskAction.SET_MODE:
            mode_str = "UNKNOWN"
            if task.notes and task.notes[0].startswith("mode="):
                mode_str = task.notes[0].replace("mode=", "")
            msg = ControlMessage(
                action=CommandAction.SET_MODE,
                params={"mode": mode_str},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
            
        elif task.action == TaskAction.ARM:
            msg = ControlMessage(
                action=CommandAction.ARM,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
            
        elif task.action == TaskAction.DISARM:
            msg = ControlMessage(
                action=CommandAction.DISARM,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
            
        elif task.action == TaskAction.HOLD:
            msg = ControlMessage(
                action=CommandAction.HOVER,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg)
            
        elif task.action in [TaskAction.FORWARD, TaskAction.BACKWARD, TaskAction.LEFT, TaskAction.RIGHT, TaskAction.UP, TaskAction.DOWN]:
            vx, vy, vz = 0.0, 0.0, 0.0
            speed = 1.0 # m/s
            
            if task.action == TaskAction.FORWARD: vx = speed
            elif task.action == TaskAction.BACKWARD: vx = -speed
            elif task.action == TaskAction.RIGHT: vy = speed
            elif task.action == TaskAction.LEFT: vy = -speed
            elif task.action == TaskAction.DOWN: vz = speed
            elif task.action == TaskAction.UP: vz = -speed
                
            # Send continuous velocity setpoints for 2 seconds (like holding down the D-Pad)
            end_time = time.time() + 2.0
            while time.time() < end_time:
                msg = ControlMessage(
                    action=CommandAction.MOVE,
                    params={"vx": vx, "vy": vy, "vz": vz, "yaw_rate": 0.0},
                    sender_id=sender_id,
                    timestamp=time.time()
                )
                success = await self.command_handler.handle_command(msg)
                if not success:
                    return False
                await asyncio.sleep(0.1)
                
            # Finish by hovering to stop movement
            hover_msg = ControlMessage(
                action=CommandAction.HOVER,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(hover_msg)
            
        elif task.action == TaskAction.TAKEOFF_LAND:
            target_alt = task.params.get("h", 5.0)
            msg1 = ControlMessage(
                action=CommandAction.TAKEOFF,
                params={"altitude_m": target_alt},
                sender_id=sender_id,
                timestamp=time.time()
            )
            success = await self.command_handler.handle_command(msg1)
            if not success:
                return False
                
            start_time = time.monotonic()
            while True:
                if time.monotonic() - start_time > 30.0:
                    logger.warning("Takeoff timeout during TAKEOFF_LAND sequence")
                    break
                telemetry = await self.flight_controller.get_telemetry()
                current_alt = getattr(telemetry, 'altitude', 0.0) or 0.0
                if current_alt >= target_alt * 0.9:
                    break
                await asyncio.sleep(0.5)
                
            await asyncio.sleep(2.0)
            
            msg2 = ControlMessage(
                action=CommandAction.LAND,
                params={},
                sender_id=sender_id,
                timestamp=time.time()
            )
            return await self.command_handler.handle_command(msg2)
            
        else:
            telemetry = await self.flight_controller.get_telemetry()
            report, origin = build_nav_context(telemetry)
            
            try:
                plan = build_trajectory(task, report, origin)
            except ValueError as e:
                logger.error(f"Trajectory rejected: {e}")
                return False
                
            if plan.frame == TargetFrame.LOCAL_NED:
                return await self._run_local_waypoints(plan.local_targets, sender_id)
            else:
                return await self._run_waypoints(plan.global_targets, sender_id)

    async def _run_waypoints(self, targets: list[GlobalTarget], sender_id: str,
                             acceptance_radius_m: float = 2.5,
                             timeout_s: float = 60.0) -> bool:
        for target in targets:
            msg = ControlMessage(
                action=CommandAction.GOTO,
                params={
                    "lat": target.lat_deg,
                    "lon": target.lon_deg,
                    "alt": target.relative_alt_m
                },
                sender_id=sender_id,
                timestamp=time.time()
            )
            
            accepted = await self.command_handler.handle_command(msg)
            if not accepted:
                logger.error(f"GOTO command rejected for waypoint {target.name}")
                return False
                
            start_time = time.monotonic()
            reached = False
            
            while True:
                if time.monotonic() - start_time > timeout_s:
                    logger.error(f"Timeout reaching waypoint {target.name}")
                    return False
                    
                telemetry = await self.flight_controller.get_telemetry()
                if telemetry.latitude is not None and telemetry.longitude is not None:
                    dist = global_distance_m(
                        telemetry.latitude, telemetry.longitude,
                        target.lat_deg, target.lon_deg
                    )
                    if dist <= acceptance_radius_m:
                        reached = True
                        break
                        
                await asyncio.sleep(0.5)
                
            if target.hold_s > 0:
                await asyncio.sleep(target.hold_s)
                
        return True

    async def _run_local_waypoints(self, targets: list[LocalTarget], sender_id: str,
                                   timeout_s: float = 60.0) -> bool:
        telemetry = await self.flight_controller.get_telemetry()
        _, origin = build_nav_context(telemetry)
        
        last_north = origin.local_north_m or 0.0
        last_east = origin.local_east_m or 0.0
        last_down = origin.local_down_m or 0.0
        
        for target in targets:
            msg = ControlMessage(
                action=CommandAction.GOTO_LOCAL,
                params={
                    "north": target.north_m,
                    "east": target.east_m,
                    "down": target.down_m
                },
                sender_id=sender_id,
                timestamp=time.time()
            )
            
            accepted = await self.command_handler.handle_command(msg)
            if not accepted:
                logger.error(f"GOTO_LOCAL command rejected for waypoint {target.name}")
                return False
                
            dist = math.sqrt((target.north_m - last_north)**2 + 
                             (target.east_m - last_east)**2 + 
                             (target.down_m - last_down)**2)
            
            # Assume ~1.0 m/s travel speed
            travel_time = max(1.0, dist / 1.0)
            await asyncio.sleep(travel_time)
            
            last_north = target.north_m
            last_east = target.east_m
            last_down = target.down_m
            
            if target.hold_s > 0:
                await asyncio.sleep(target.hold_s)
                
        return True
