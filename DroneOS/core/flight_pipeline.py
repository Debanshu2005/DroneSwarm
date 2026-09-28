import asyncio
import time
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.interfaces import IFlightController
from DroneOS.core.smart_rtl_engine import SmartRtlEngine
from DroneOS.core.flight_state import is_critical_manual_intent

logger = setup_logger("FlightPipeline")

class Arbiter:
    @staticmethod
    def select_winner(intents: dict[IntentSource, FlightIntent]) -> FlightIntent:
        valid_intents = []
        for source, intent in intents.items():
            if not intent.is_expired():
                valid_intents.append(intent)

        if not valid_intents:
            return FlightIntent(IntentSource.IDLE, IntentAction.IDLE)

        protective_intents = [intent for intent in valid_intents if intent.source in {IntentSource.SAFETY, IntentSource.COLLISION}]
        if protective_intents:
            return max(protective_intents, key=lambda intent: intent.source)
        critical_manual = [intent for intent in valid_intents if is_critical_manual_intent(intent)]
        if critical_manual:
            return critical_manual[0]
        # Sort by IntentSource enum value (highest wins)
        valid_intents.sort(key=lambda i: i.source, reverse=True)
        return valid_intents[0]

class SafetyFilter:
    def __init__(self, config):
        self.config = config

    def validate(self, intent: FlightIntent, telemetry) -> FlightIntent:
        # If EMERGENCY_KILL, pass it through instantly
        if intent.action == IntentAction.EMERGENCY_KILL:
            return intent
            
        # If safety triggered LAND or RTL, allow it
        if intent.source == IntentSource.SAFETY:
            return intent
            
        # For movement commands, check limits
        if intent.action == IntentAction.MOVE_VELOCITY:
            max_h = 5.0
            max_v = 3.0
            if self.config and getattr(self.config, 'safety_limits', None):
                max_h = float(self.config.safety_limits.max_horizontal_velocity)
                max_v = float(self.config.safety_limits.max_vertical_velocity)
                
            try:
                vx = max(-max_h, min(max_h, float(intent.params.get('vx', 0.0))))
                vy = max(-max_h, min(max_h, float(intent.params.get('vy', 0.0))))
                vz = max(-max_v, min(max_v, float(intent.params.get('vz', 0.0))))
                yaw_rate = max(-90.0, min(90.0, float(intent.params.get('yaw_rate', 0.0))))
            except (TypeError, ValueError):
                vx = vy = vz = yaw_rate = 0.0

            intent.params.update({'vx': vx, 'vy': vy, 'vz': vz, 'yaw_rate': yaw_rate})
            
        return intent

class CommandWriter:
    def __init__(self, fc: IFlightController):
        self.fc = fc
        self.last_action_time = 0.0

    async def execute(self, intent: FlightIntent):
        self.last_action_time = time.time()
        
        try:
            if intent.action == IntentAction.EMERGENCY_KILL:
                if hasattr(self.fc, 'kill'):
                    await self.fc.kill()
            
            elif intent.action == IntentAction.IDLE:
                return
                    
            elif intent.action == IntentAction.HOVER:
                logger.info("FC invoking hover")
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
                vx = intent.params.get('vx', 0.0)
                vy = intent.params.get('vy', 0.0)
                vz = intent.params.get('vz', 0.0)
                yaw_rate = intent.params.get('yaw_rate', 0.0)
                logger.info("FC invoking move_velocity")
                result = await self.fc.move_velocity(vx, vy, vz, 0.1, yaw_rate)
                logger.info("FC invoke move_velocity returned success=%s", result)
                return bool(result)
                
            elif intent.action == IntentAction.MOVE_VELOCITY_NED:
                north = intent.params.get('north', 0.0)
                east = intent.params.get('east', 0.0)
                down = intent.params.get('down', 0.0)
                yaw_rate = intent.params.get('yaw_rate', 0.0)
                logger.info("FC invoking move_velocity_ned")
                result = await self.fc.move_velocity_ned(north, east, down, 0.1, yaw_rate)
                logger.info("FC invoke move_velocity_ned returned success=%s", result)
                return bool(result)
                
            elif intent.action == IntentAction.GOTO:
                lat = intent.params.get('lat')
                lon = intent.params.get('lon')
                alt = intent.params.get('alt')
                yaw = intent.params.get('yaw', 0.0)
                if lat is not None and lon is not None and alt is not None:
                    result = await self.fc.goto_location(lat, lon, alt, yaw=yaw)
                    logger.info("FC invoke goto_location returned success=%s", result)
                    return bool(result)
                return False
                    
            elif intent.action == IntentAction.GOTO_NED:
                north = intent.params.get('north', 0.0)
                east = intent.params.get('east', 0.0)
                down = intent.params.get('down', 0.0)
                yaw = intent.params.get('yaw', 0.0)
                result = await self.fc.goto_local_ned(north, east, down, yaw=yaw)
                logger.info("FC invoke goto_local_ned returned success=%s", result)
                return bool(result)
                    
            elif intent.action == IntentAction.TAKEOFF:
                alt = intent.params.get('altitude', 5.0)
                logger.info("FC invoking takeoff altitude=%s", alt)
                result = await self.fc.takeoff(alt)
                logger.info("FC invoke takeoff altitude=%s returned success=%s", alt, result)
                return bool(result)

            return False

        except Exception as e:
            logger.error(f"CommandWriter failed to execute {intent.action}: {e}")
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
        self.last_winning_key = None

    async def run_pipeline_loop(self):
        self._running = True
        loop_interval = 1.0 / self._hz
        
        logger.info(f"FlightPipeline starting at {self._hz} Hz")
        while self._running:
            start_time = time.monotonic()
            
            # 1. Update latest telemetry from FC into state store
            try:
                telemetry = await self.fc.get_telemetry()
                self.state_store.update_local_telemetry(telemetry)
            except Exception as e:
                logger.error(f"Failed to get telemetry: {e}")
                telemetry = self.state_store.local_telemetry
                
            # 2. Evaluate Engines
            # Synchronously evaluate all other intent producers first
            await self.decision_engine.evaluate_tick(telemetry)
            
            srtl_intent = self.srtl_engine.compute_intent(self.state_store)
            if srtl_intent:
                self.state_store.submit_intent(srtl_intent)
                
            # 3. Arbitrate
            intents = self.state_store.get_intents()
            winning_intent = self.arbiter.select_winner(intents)
            
            winning_key = (winning_intent.source, winning_intent.action)
            if winning_key != self.last_winning_key:
                self.last_winning_key = winning_key
                logger.info("Winning intent source=%s action=%s", winning_intent.source.name, winning_intent.action.value)
                if is_critical_manual_intent(winning_intent):
                    preempted = [source.name for source, intent in intents.items() if source in {IntentSource.FORMATION, IntentSource.MISSION} and not intent.is_expired()]
                    if preempted:
                        logger.warning("Critical manual %s preempts active %s control.", winning_intent.action.value, ", ".join(preempted))
                if self.on_intent_change:
                    await self.on_intent_change(winning_intent.source.name if winning_intent.source else "IDLE")
            
            # 4. Safety Filter
            safe_intent = self.safety_filter.validate(winning_intent, telemetry)
            
            # 5. Command Writer
            result = await self.command_writer.execute(safe_intent)
            if is_critical_manual_intent(winning_intent):
                cleared = self.state_store.complete_intent(winning_intent)
                logger.info("Critical manual %s completed success=%s cleared=%s", winning_intent.action.value, result, cleared)
            
            # 6. Wait for next tick
            elapsed = time.monotonic() - start_time
            sleep_time = max(0.0, loop_interval - elapsed)
            await asyncio.sleep(sleep_time)

    def stop(self):
        self._running = False
