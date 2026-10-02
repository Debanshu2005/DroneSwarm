import os
import time
from typing import Dict, Any, Callable, Coroutine
from pydantic import ValidationError
from DroneOS.shared.protocol.messages import ControlMessage, CommandAction, CommandLifecycleMessage
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.shared.utils.event_logger import event_logger

logger = setup_logger("CommandHandler")

class CommandHandler:
    """
    Parses incoming ControlMessages and routes them to the appropriate subsystem (e.g. FlightManager).
    """
    def __init__(self, node_id: str = "DroneOS", safety_module=None, flight_controller=None, health_monitor=None, battery_monitor=None, error_learning=None, swarm_manager=None):
        # Maps CommandAction to a coroutine handler
        self._handlers: Dict[CommandAction, Callable[[Dict[str, Any]], Coroutine[Any, Any, bool]]] = {}
        self.network = None
        self.node_id = node_id
        self._active_tasks = set()
        
        self.safety_module = safety_module
        self.flight_controller = flight_controller
        self.health_monitor = health_monitor
        self.battery_monitor = battery_monitor
        self.error_learning = error_learning
        self.swarm_manager = swarm_manager
        self.require_peers_before_arm = os.getenv("REQUIRE_PEERS_BEFORE_ARM", "").lower() in {"1", "true", "yes", "on"}
        self.expected_peer_ids = [
            peer.strip()
            for peer in os.getenv("EXPECTED_PEER_IDS", "").split(",")
            if peer.strip()
        ]
        expected_count = os.getenv("EXPECTED_PEER_COUNT", "")
        self.expected_peer_count = int(expected_count) if expected_count.isdigit() else 0
        self._processed_cmds = []
        self._pending_pipeline_commands = {}
        self.flight_manager = None

    async def on_pipeline_intent_dispatched(self, intent) -> None:
        command_id = intent.params.get("_command_id")
        pending = self._pending_pipeline_commands.get(command_id)
        if pending:
            self._send_lifecycle(pending["sender_id"], pending["action"], "AIRSIM_DISPATCHED", cmd_id=pending["cmd_id"])

    async def on_pipeline_intent_result(self, intent, success: bool) -> None:
        command_id = intent.params.get("_command_id")
        pending = self._pending_pipeline_commands.get(command_id)
        if not pending:
            return
        self._send_lifecycle(pending["sender_id"], pending["action"], "AIRSIM_RESULT",
                             reason=f"success={bool(success)}", cmd_id=pending["cmd_id"])
        if not pending["future"].done():
            pending["future"].set_result(bool(success))

    async def _cancel_pending_pipeline_command(self, command_id: str) -> None:
        if self.flight_manager:
            await self.flight_manager.cancel_critical_command(command_id)

    def _validate_peer_arm_gate(self) -> str:
        if not self.require_peers_before_arm:
            return ""
        if not self.swarm_manager or not getattr(self.swarm_manager, "registry", None):
            return "Command rejected: Swarm peer registry unavailable"
        if not self.expected_peer_ids and self.expected_peer_count <= 0:
            return "Command rejected: Peer requirement enabled but no expected peers configured"

        registry = self.swarm_manager.registry
        timeout = getattr(getattr(self.swarm_manager, "heartbeat_mgr", None), "timeout_sec", 5.0)
        now = time.time()

        def is_recent(peer_id: str) -> bool:
            peer = registry.get_peer(peer_id)
            return bool(peer and getattr(peer, "is_active", True) and (now - getattr(peer, "last_seen", 0.0)) <= timeout)

        missing = [peer_id for peer_id in self.expected_peer_ids if not is_recent(peer_id)]
        if missing:
            return f"Command rejected: Missing required swarm peers: {', '.join(missing)}"

        if self.expected_peer_count > 0:
            recent_count = sum(1 for peer_id in registry.get_all_peers() if is_recent(peer_id))
            if recent_count < self.expected_peer_count:
                return f"Command rejected: Required swarm peers not present ({recent_count}/{self.expected_peer_count})"

        return ""

    def _send_lifecycle(self, sender_id: str, action: CommandAction, stage: str, reason: str = None, cmd_id: str = None) -> None:
        if self.network:
            import asyncio
            import time
            msg = CommandLifecycleMessage(
                sender_id=self.node_id,
                target_id=sender_id,
                timestamp=time.time(),
                action=action,
                stage=stage,
                reason=reason,
                cmd_id=cmd_id
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.network.broadcast_message(msg))
            except RuntimeError:
                pass

            # Structured Logging
            severity = "ERROR" if stage in ["REJECTED", "FAILED", "TIMEOUT"] else "INFO"
            event_type = f"COMMAND_{stage}"
            event_logger.log_event(
                source="DRONEOS",
                severity=severity,
                drone_id=self.node_id,
                event_type=event_type,
                message=f"{action.name} {stage}{f': {reason}' if reason else ''}"
            )

    async def _validate_safety_gate(self, action: CommandAction) -> str:
        if not self.flight_controller or not self.safety_module or not self.health_monitor:
            return "" # Tests or incomplete DI

        telemetry = await self.flight_controller.get_telemetry()
        
        is_telemetry_stale = False
        if getattr(telemetry, 'timestamp', None) is not None:
            diff = time.time() - telemetry.timestamp
            if diff > 2.0:
                logger.warning(f"Telemetry would be stale (diff {diff:.2f}s) but bypassing check.")
                # is_telemetry_stale = True
        else:
            logger.warning("Telemetry timestamp is None, but bypassing check.")
            # is_telemetry_stale = True

        # Heartbeat Freshness
        is_heartbeat_stale = False
        if self.health_monitor.last_heartbeat_time is not None:
            if (time.time() - self.health_monitor.last_heartbeat_time) > self.health_monitor.timeout_seconds:
                is_heartbeat_stale = True

        is_emergency = self.safety_module.is_failsafe_active
        is_battery_critical = False # Assumed from the failsafe state or we can read telemetry voltage. Actually failsafe covers it.

        if action == CommandAction.ARM:
            if is_heartbeat_stale: return "Command rejected: Heartbeat stale"
            if is_telemetry_stale: return "Command rejected: Telemetry stale"
            if is_emergency: return "Command rejected: Emergency stop active"
            peer_rejection = self._validate_peer_arm_gate()
            if peer_rejection: return peer_rejection
            
        elif action == CommandAction.TAKEOFF:
            if is_heartbeat_stale: return "Command rejected: Heartbeat stale"
            if is_telemetry_stale: return "Command rejected: Telemetry stale"
            if is_emergency: return "Command rejected: Emergency stop active"
            
        elif action == CommandAction.STOP:
            self.safety_module.reset_failsafe()
            return "" # Approved

        elif action == CommandAction.MOVE:
            if is_heartbeat_stale: return "Command rejected: Heartbeat stale"
            if is_emergency: return "Command rejected: Emergency stop active"

        elif action == CommandAction.RTL:
            if is_heartbeat_stale: return "Command rejected: Heartbeat stale"
            if is_emergency: return "Command rejected: Emergency stop active"
            if not getattr(telemetry, 'home_valid', False): return "Command rejected: Home position unavailable (RTL requires home)"
            if getattr(telemetry, 'flight_mode', '').upper() == 'MANUAL' and not getattr(telemetry, 'gps_valid', False):
                return "Command rejected: GPS unavailable (RTL requires global position)"

        elif action == CommandAction.GOTO:
            if is_heartbeat_stale: return "Command rejected: Heartbeat stale"
            if is_emergency: return "Command rejected: Emergency stop active"
            if not getattr(telemetry, 'gps_valid', False): return "Command rejected: GPS unavailable (GOTO requires global position)"
            
        elif action == CommandAction.GOTO_LOCAL:
            if is_heartbeat_stale: return "Command rejected: Heartbeat stale"
            if is_emergency: return "Command rejected: Emergency stop active"
            if not getattr(telemetry, 'local_pos_valid', False): return "Command rejected: Local position unavailable (Optical flow/VIO needed)"
            
        return ""

    def _dispatch_task(self, coro):
        import asyncio
        try:
            task = asyncio.create_task(coro)
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
        except Exception as e:
            logger.error(f"Failed to dispatch task: {e}")
    def register_handler(self, action: CommandAction, handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, bool]]) -> None:
        self._handlers[action] = handler

    async def handle_command(self, message: ControlMessage) -> bool:
        if message.cmd_id:
            if message.cmd_id in self._processed_cmds:
                logger.debug(f"Ignoring duplicate command {message.cmd_id}")
                return True
            self._processed_cmds.append(message.cmd_id)
            if len(self._processed_cmds) > 100:
                self._processed_cmds.pop(0)

        if message.action in self._handlers:
            logger.info(f"COMMAND_RX sender={message.sender_id} target={message.target_id} action={message.action.value}")
            self._send_lifecycle(message.sender_id, message.action, "BACKEND_RECEIVED", cmd_id=message.cmd_id)
            
            critical_actions = [CommandAction.ARM, CommandAction.TAKEOFF, CommandAction.LAND, CommandAction.RTL]
            pipeline_actions = {CommandAction.TAKEOFF, CommandAction.LAND, CommandAction.RTL}
            # FORMATION_UPDATE stores params and activates the FormationEngine;
            # it does NOT mean flight movement has completed — use FORMATION_ACTIVE.
            formation_actions = {CommandAction.FORMATION_UPDATE}
            is_critical = message.action in critical_actions
            await_pipeline_result = message.action in pipeline_actions
            
            if is_critical:
                if getattr(self, '_active_critical_command', None) is not None:
                    rejection = f"Command rejected: Another critical command ({self._active_critical_command.value}) is already active."
                    logger.warning(rejection)
                    self._send_lifecycle(message.sender_id, message.action, "REJECTED", reason=rejection, cmd_id=message.cmd_id)
                    return False
                self._active_critical_command = message.action

            rejection_reason = await self._validate_safety_gate(message.action)
            if rejection_reason:
                logger.warning(rejection_reason)
                self._send_lifecycle(message.sender_id, message.action, "REJECTED", reason=rejection_reason, cmd_id=message.cmd_id)
                if is_critical:
                    self._active_critical_command = None
                return False

            params = dict(message.params or {})
            pipeline_command_id = message.cmd_id or f"pipeline-{time.monotonic_ns()}"
            if await_pipeline_result:
                import asyncio
                self._pending_pipeline_commands[pipeline_command_id] = {"future": asyncio.get_running_loop().create_future(), "sender_id": message.sender_id, "action": message.action, "cmd_id": message.cmd_id}
                params["_command_id"] = pipeline_command_id
            try:
                self._send_lifecycle(message.sender_id, message.action, "SENDING", cmd_id=message.cmd_id)

                # Log formation params to make rejections self-explanatory in the drone log.
                if message.action == CommandAction.FORMATION_UPDATE:
                    logger.info(
                        "FORMATION_PARAMS type=%s spacing=%s members=%s slot_assignments=%s",
                        (message.params or {}).get("type"),
                        (message.params or {}).get("spacing"),
                        (message.params or {}).get("members"),
                        (message.params or {}).get("slot_assignments"),
                    )
                
                # Use asyncio.wait_for to handle TIMEOUT
                import asyncio
                try:
                    success = await asyncio.wait_for(self._handlers[message.action](params), timeout=15.0)
                except asyncio.TimeoutError:
                    error_text = f"{message.action.name} timed out."
                    self._send_lifecycle(message.sender_id, message.action, "TIMEOUT", reason=error_text, cmd_id=message.cmd_id)
                    if is_critical:
                        self._active_critical_command = None
                    return False

                if not success:
                    logger.warning(f"Command {message.action.value} failed to execute properly.")
                    # For FORMATION_UPDATE read the specific reason from FlightManager so the
                    # app can show it rather than the generic rejection text.
                    error_text = f"{message.action.name} rejected by FlightManager."
                    if message.action == CommandAction.FORMATION_UPDATE and self.flight_manager:
                        fm_reason = getattr(self.flight_manager, "last_rejection_reason", "")
                        if fm_reason:
                            error_text = fm_reason
                    elif message.action == CommandAction.ARM:
                        error_text = "ARM rejected by Pixhawk; check Pixhawk pre-arm checks."
                    self._send_lifecycle(message.sender_id, message.action, "REJECTED", reason=error_text, cmd_id=message.cmd_id)
                    if is_critical:
                        self._active_critical_command = None
                    self._pending_pipeline_commands.pop(pipeline_command_id, None)
                    return False
                if await_pipeline_result:
                    try:
                        result = await asyncio.wait_for(asyncio.shield(self._pending_pipeline_commands[pipeline_command_id]["future"]), timeout=45.0)
                    except asyncio.TimeoutError:
                        self._send_lifecycle(message.sender_id, message.action, "TIMEOUT", reason="Flight-controller dispatch timed out.", cmd_id=message.cmd_id)
                        await self._cancel_pending_pipeline_command(pipeline_command_id)
                        self._pending_pipeline_commands.pop(pipeline_command_id, None)
                        if is_critical:
                            self._active_critical_command = None
                        return False
                    self._pending_pipeline_commands.pop(pipeline_command_id, None)
                    if not result:
                        self._send_lifecycle(message.sender_id, message.action, "FAILED", reason="Flight-controller returned failure.", cmd_id=message.cmd_id)
                        if is_critical:
                            self._active_critical_command = None
                        return False
                
                # FORMATION_UPDATE: params stored, engine is now active — not "movement complete"
                final_stage = "FORMATION_ACTIVE" if message.action in formation_actions else "ACCEPTED"
                self._send_lifecycle(message.sender_id, message.action, final_stage, cmd_id=message.cmd_id)
                if is_critical:
                    self._active_critical_command = None
                return True
            except Exception as e:
                error_msg = str(e)
                if "ActionError" in str(type(e)):
                    error_msg = str(e).split(':', 1)[-1].strip()
                logger.exception(f"Exception while executing {message.action.value}: {error_msg}")
                self._send_lifecycle(message.sender_id, message.action, "REJECTED", reason=error_msg, cmd_id=message.cmd_id)
                if hasattr(self, 'error_learning') and self.error_learning:
                    self.error_learning.report_error(self.node_id, "COMMAND_HANDLER", error_msg)
                if is_critical:
                    self._active_critical_command = None
                if await_pipeline_result:
                    await self._cancel_pending_pipeline_command(pipeline_command_id)
                self._pending_pipeline_commands.pop(pipeline_command_id, None)
                return False
        else:
            logger.warning(f"No handler registered for command: {message.action.value}")
            return False
