import asyncio
import time
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.interfaces import IFlightController
from DroneOS.core.smart_rtl_engine import SmartRtlEngine
from DroneOS.core.flight_state import is_critical_manual_intent

logger = setup_logger("FlightPipeline")

_LATCHED_SOURCES = {IntentSource.FORMATION, IntentSource.MISSION, IntentSource.COLLISION}
_latch_log_time: float = 0.0


class Arbiter:
    @staticmethod
    def select_winner(intents: dict, state_store: FlightStateStore = None) -> FlightIntent:
        valid_intents = [i for i in intents.values() if not i.is_expired()]

        if not valid_intents:
            return FlightIntent(IntentSource.IDLE, IntentAction.IDLE)

        latched = state_store is not None and state_store.is_landing_latched()

        if latched:
            # While latch is active: only SAFETY and critical-manual LAND/RTL may win
            global _latch_log_time
            allowed = []
            for intent in valid_intents:
                if intent.source == IntentSource.SAFETY:
                    allowed.append(intent)
                elif is_critical_manual_intent(intent):
                    allowed.append(intent)
                else:
                    now = time.monotonic()
                    if now - _latch_log_time > 1.0:
                        logger.debug(
                            "LANDING_LATCH ignoring source=%s action=%s",
                            intent.source.name, intent.action.value
                        )
                        _latch_log_time = now
            valid_intents = allowed if allowed else [FlightIntent(IntentSource.IDLE, IntentAction.IDLE)]

        protective = [i for i in valid_intents if i.source in {IntentSource.SAFETY, IntentSource.COLLISION}]
        if protective:
            return max(protective, key=lambda i: i.source)

        critical_manual = [i for i in valid_intents if is_critical_manual_intent(i)]
        if critical_manual:
            return critical_manual[0]

        valid_intents.sort(key=lambda i: i.source, reverse=True)
        return valid_intents[0]


class SafetyFilter:
    def __init__(self, config):
        self.config = config

    def validate(self, intent: FlightIntent, telemetry) -> FlightIntent:
        if intent.action == IntentAction.EMERGENCY_KILL:
            return intent
        if intent.source == IntentSource.SAFETY:
            return intent
        if intent.action == IntentAction.MOVE_VELOCITY:
            max_h = 5.0
            max_v = 3.0
            if self.config and getattr(self.config, "safety_limits", None):
                max_h = float(self.config.safety_limits.max_horizontal_velocity)
                max_v = float(self.config.safety_limits.max_vertical_velocity)
            try:
                vx = max(-max_h, min(max_h, float(intent.params.get("vx", 0.0))))
                vy = max(-max_h, min(max_h, float(intent.params.get("vy", 0.0))))
                vz = max(-max_v, min(max_v, float(intent.params.get("vz", 0.0))))
                yaw_rate = max(-90.0, min(90.0, float(intent.params.get("yaw_rate", 0.0))))
            except (TypeError, ValueError):
                vx = vy = vz = yaw_rate = 0.0
            intent.params.update({"vx": vx, "vy": vy, "vz": vz, "yaw_rate": yaw_rate})
        return intent


class CommandWriter:
    def __init__(self, fc: IFlightController):
        self.fc = fc
        self.last_action_time = 0.0

    async def execute(self, intent: FlightIntent):
        self.last_action_time = time.time()
        if intent.action != IntentAction.IDLE:
            vehicle = getattr(self.fc, "vehicle_name", getattr(self.fc, "vehicle_id", "unknown"))
            logger.info("FLIGHT_EXEC %s source=%s vehicle=%s params=%s",
                        intent.action.value, intent.source.name, vehicle, intent.params)
        try:
            if intent.action == IntentAction.EMERGENCY_KILL:
                if hasattr(self.fc, "kill"):
                    await self.fc.kill()

            elif intent.action == IntentAction.IDLE:
                return True

            elif intent.action == IntentAction.HOVER:
                logger.info("FC invoking hover")
                # SAFETY-source hovers pass force=True so they override LAND/RTL mode
                force = (intent.source == IntentSource.SAFETY)
                if force and hasattr(self.fc, "hover"):
                    import inspect
                    sig = inspect.signature(self.fc.hover)
                    if "force" in sig.parameters:
                        result = await self.fc.hover(force=True)
                    else:
                        result = await self.fc.hover()
                else:
                    result = await self.fc.hover()
                logger.info("FC invoke hover returned success=%s", result)
                return bool(result)

            elif intent.action == IntentAction.LAND:
                logger.info("FC invoking land")
                result = await self.fc.land()
                logger.info("FC invoke land returned success=%s", result)
                return bool(result)

            elif intent.action == IntentAction.RTL:
                logger.info("FC invoking rtl")
                result = await self.fc.rtl()
                logger.info("FC invoke rtl returned success=%s", result)
                return bool(result)

            elif intent.action == IntentAction.MOVE_VELOCITY:
                vx = intent.params.get("vx", 0.0)
                vy = intent.params.get("vy", 0.0)
                vz = intent.params.get("vz", 0.0)
                yaw_rate = intent.params.get("yaw_rate", 0.0)
                logger.info("FC invoking move_velocity")
                result = await self.fc.move_velocity(vx, vy, vz, 0.1, yaw_rate)
                logger.info("FC invoke move_velocity returned success=%s", result)
                return bool(result)

            elif intent.action == IntentAction.MOVE_VELOCITY_NED:
                north = intent.params.get("north", 0.0)
                east = intent.params.get("east", 0.0)
                down = intent.params.get("down", 0.0)
                yaw_rate = intent.params.get("yaw_rate", 0.0)
                logger.info("FC invoking move_velocity_ned")
                result = await self.fc.move_velocity_ned(north, east, down, 0.1, yaw_rate)
                logger.info("FC invoke move_velocity_ned returned success=%s", result)
                return bool(result)

            elif intent.action == IntentAction.GOTO:
                lat = intent.params.get("lat")
                lon = intent.params.get("lon")
                alt = intent.params.get("alt")
                yaw = intent.params.get("yaw", 0.0)
                if lat is not None and lon is not None and alt is not None:
                    result = await self.fc.goto_location(lat, lon, alt, yaw=yaw)
                    logger.info("FC invoke goto_location returned success=%s", result)
                    return bool(result)
                return False

            elif intent.action == IntentAction.GOTO_NED:
                north = intent.params.get("north", 0.0)
                east = intent.params.get("east", 0.0)
                down = intent.params.get("down", 0.0)
                yaw = intent.params.get("yaw", 0.0)
                result = await self.fc.goto_local_ned(north, east, down, yaw=yaw)
                logger.info("FC invoke goto_local_ned returned success=%s", result)
                return bool(result)

            elif intent.action == IntentAction.TAKEOFF:
                alt = intent.params.get("altitude", 5.0)
                logger.info("FC invoking takeoff altitude=%s", alt)
                result = await self.fc.takeoff(alt)
                logger.info("FC invoke takeoff altitude=%s returned success=%s", alt, result)
                return bool(result)

            return False

        except Exception as e:
            logger.error("CommandWriter failed to execute %s: %s", intent.action, e)
            return False


class FlightPipeline:
    def __init__(self, state_store: FlightStateStore, fc: IFlightController, config, decision_engine):
        self.state_store = state_store
        self.fc = fc
        self.config = config
        self.decision_engine = decision_engine
        self.arbiter = Arbiter()
        self.safety_filter = SafetyFilter(config)
        self.command_writer = CommandWriter(fc)
        self.srtl_engine = SmartRtlEngine(config)
        self._running = False
        self._hz = config.pipeline_hz
        self.on_intent_change = None
        self.on_intent_dispatched = None
        self.on_intent_result = None
        self.last_winning_key = None

    async def run_pipeline_loop(self):
        self._running = True
        loop_interval = 1.0 / self._hz
        logger.info("FlightPipeline starting at %s Hz", self._hz)
        while self._running:
            start_time = time.monotonic()

            try:
                telemetry = await self.fc.get_telemetry()
                self.state_store.update_local_telemetry(telemetry)
            except Exception as e:
                logger.error("Failed to get telemetry: %s", e)
                telemetry = self.state_store.local_telemetry

            await self.decision_engine.evaluate_tick(telemetry)

            srtl_intent = self.srtl_engine.compute_intent(self.state_store)
            if srtl_intent:
                self.state_store.submit_intent(srtl_intent)

            intents = self.state_store.get_intents()
            winning_intent = self.arbiter.select_winner(intents, self.state_store)

            winning_key = (winning_intent.source, winning_intent.action)
            if winning_key != self.last_winning_key:
                self.last_winning_key = winning_key
                logger.info("Winning intent source=%s action=%s",
                            winning_intent.source.name, winning_intent.action.value)
                logger.info("ARBITER winner source=%s action=%s",
                            winning_intent.source.name, winning_intent.action.value)
                if is_critical_manual_intent(winning_intent):
                    logger.warning("CRITICAL_PREEMPT active action=%s", winning_intent.action.value)
                    preempted = [
                        src.name for src, i in intents.items()
                        if src in {IntentSource.FORMATION, IntentSource.MISSION} and not i.is_expired()
                    ]
                    for src in preempted:
                        logger.warning("CRITICAL_PREEMPT suppressing source=%s", src)
                if self.on_intent_change:
                    await self.on_intent_change(
                        winning_intent.source.name if winning_intent.source else "IDLE"
                    )

            safe_intent = self.safety_filter.validate(winning_intent, telemetry)

            if is_critical_manual_intent(winning_intent) and self.on_intent_dispatched:
                try:
                    await self.on_intent_dispatched(winning_intent)
                except Exception:
                    logger.exception("on_intent_dispatched callback failed")

            result = await self.command_writer.execute(safe_intent)
            if safe_intent.action != IntentAction.IDLE:
                logger.info("FLIGHT_EXEC_RESULT %s vehicle=%s success=%s",
                            safe_intent.action.value,
                            getattr(self.fc, "vehicle_name", getattr(self.fc, "vehicle_id", "unknown")),
                            bool(result))

            if is_critical_manual_intent(winning_intent):
                if self.on_intent_result:
                    try:
                        await self.on_intent_result(winning_intent, result)
                    except Exception:
                        logger.exception("on_intent_result callback failed")
                cleared = self.state_store.complete_intent(winning_intent)
                logger.info("Critical manual %s completed success=%s cleared=%s",
                            winning_intent.action.value, result, cleared)

            elapsed = time.monotonic() - start_time
            await asyncio.sleep(max(0.0, loop_interval - elapsed))

    def stop(self):
        self._running = False
