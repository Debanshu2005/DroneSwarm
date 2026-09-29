import threading
import time
from typing import Dict
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS.shared.utils.logger import setup_logger

logger = setup_logger("FlightStateStore")
CRITICAL_MANUAL_ACTIONS = {IntentAction.TAKEOFF, IntentAction.LAND, IntentAction.RTL}

def is_critical_manual_intent(intent: FlightIntent) -> bool:
    return intent.source == IntentSource.MANUAL and intent.action in CRITICAL_MANUAL_ACTIONS

class SwarmState:
    def __init__(self):
        self.peer_telemetry: Dict[str, TelemetryData] = {}
        self.lock = threading.Lock()

    def update_peer(self, peer_id: str, telemetry: TelemetryData):
        with self.lock:
            self.peer_telemetry[peer_id] = telemetry

    def get_peer(self, peer_id: str) -> TelemetryData:
        with self.lock:
            return self.peer_telemetry.get(peer_id)

    def get_all_peers(self) -> Dict[str, TelemetryData]:
        with self.lock:
            return dict(self.peer_telemetry)

class FlightStateStore:
    _LANDING_LATCH_DURATION = 45.0

    def __init__(self):
        self.local_telemetry: TelemetryData = TelemetryData(
            battery_level=None, altitude=None, latitude=None, longitude=None,
            velocity_x=None, velocity_y=None, velocity_z=None, flight_mode="disconnected"
        )
        self.swarm_state = SwarmState()
        self.active_intents: Dict[IntentSource, FlightIntent] = {}
        self.intent_lock = threading.Lock()

        self.smart_rtl_active: bool = False
        self.smart_rtl_target = None
        self.smart_rtl_start_time: float = 0.0

        # Landing latch: while active, FORMATION/MISSION/COLLISION intents are suppressed
        self.landing_latch_until: float = 0.0

    def set_landing_latch(self, seconds: float = None) -> None:
        duration = seconds if seconds is not None else self._LANDING_LATCH_DURATION
        self.landing_latch_until = time.monotonic() + duration
        logger.debug("LANDING_LATCH set for %.1fs", duration)

    def clear_landing_latch(self) -> None:
        self.landing_latch_until = 0.0
        logger.debug("LANDING_LATCH cleared")

    def is_landing_latched(self) -> bool:
        return time.monotonic() < self.landing_latch_until

    def update_local_telemetry(self, telemetry: TelemetryData):
        self.local_telemetry = telemetry
        # Auto-clear latch when drone is disarmed or on the ground
        if self.is_landing_latched():
            armed = getattr(telemetry, "armed_state", None)
            alt = telemetry.altitude or 0.0
            if armed == "DISARMED" or alt < 0.3:
                self.clear_landing_latch()

    def submit_intent(self, intent: FlightIntent):
        with self.intent_lock:
            current_manual = self.active_intents.get(IntentSource.MANUAL)
            if (intent.source == IntentSource.MANUAL and current_manual is not None
                    and is_critical_manual_intent(current_manual)
                    and not is_critical_manual_intent(intent)):
                logger.warning(
                    "Ignoring stale manual %s while critical manual %s is active.",
                    intent.action.value, current_manual.action.value
                )
                return False
            self.active_intents[intent.source] = intent
            return True

    def get_intents(self) -> Dict[IntentSource, FlightIntent]:
        with self.intent_lock:
            return dict(self.active_intents)

    def clear_intent(self, source: IntentSource):
        with self.intent_lock:
            self.active_intents.pop(source, None)

    def complete_intent(self, intent: FlightIntent) -> bool:
        with self.intent_lock:
            if self.active_intents.get(intent.source) is intent:
                self.active_intents.pop(intent.source, None)
                return True
        return False
