import asyncio
import time
from DroneOS1.shared.utils.logger import setup_logger
from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS1.core.flight_state import FlightStateStore
from DroneOS1.core.interfaces import IFlightController
from DroneOS1.core.smart_rtl_engine import SmartRtlEngine

logger = setup_logger("FlightPipeline")

class Arbiter:
    @staticmethod
    def select_winner(intents: dict[IntentSource, FlightIntent]) -> FlightIntent:
        valid_intents = []
        for source, intent in intents.items():
            if not intent.is_expired():
                valid_intents.append(intent)

        if not valid_intents:
            return FlightIntent(IntentSource.IDLE, IntentAction.HOVER)

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
                    
            elif intent.action == IntentAction.HOVER:
                await self.fc.hover()
                
            elif intent.action == IntentAction.LAND:
                await self.fc.land()
                
            elif intent.action == IntentAction.RTL:
                await self.fc.rtl()
                
            elif intent.action == IntentAction.MOVE_VELOCITY:
                vx = intent.params.get('vx', 0.0)
                vy = intent.params.get('vy', 0.0)
                vz = intent.params.get('vz', 0.0)
                yaw_rate = intent.params.get('yaw_rate', 0.0)
                await self.fc.move_velocity(vx, vy, vz, 0.1, yaw_rate)
                
            elif intent.action == IntentAction.GOTO:
                lat = intent.params.get('lat')
                lon = intent.params.get('lon')
                alt = intent.params.get('alt')
                yaw = intent.params.get('yaw', 0.0)
                if lat is not None and lon is not None and alt is not None:
                    await self.fc.goto_location(lat, lon, alt, yaw=yaw)
                    
            elif intent.action == IntentAction.TAKEOFF:
                alt = intent.params.get('altitude', 5.0)
                await self.fc.takeoff(alt)

        except Exception as e:
            logger.error(f"CommandWriter failed to execute {intent.action}: {e}")


class FlightPipeline:
    def __init__(self, state_store: FlightStateStore, fc: IFlightController, config):
        self.state_store = state_store
        self.fc = fc
        self.config = config
        self.arbiter = Arbiter()
        self.safety_filter = SafetyFilter(config)
        self.command_writer = CommandWriter(fc)
        self.srtl_engine = SmartRtlEngine(config)
        self._running = False
        self._hz = 20.0

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
            srtl_intent = self.srtl_engine.compute_intent(self.state_store)
            if srtl_intent:
                self.state_store.submit_intent(srtl_intent)
                
            # 3. Arbitrate
            intents = self.state_store.get_intents()
            winning_intent = self.arbiter.select_winner(intents)
            
            # 3. Safety Filter
            safe_intent = self.safety_filter.validate(winning_intent, telemetry)
            
            # 4. Command Writer
            await self.command_writer.execute(safe_intent)
            
            # 5. Wait for next tick
            elapsed = time.monotonic() - start_time
            sleep_time = max(0.0, loop_interval - elapsed)
            await asyncio.sleep(sleep_time)

    def stop(self):
        self._running = False

