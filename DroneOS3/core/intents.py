from enum import IntEnum, Enum
import time
from typing import Dict, Any, Optional

class IntentSource(IntEnum):
    # Higher value = higher priority
    IDLE = 0
    MANUAL = 10
    MISSION = 20
    FORMATION = 30
    COLLISION = 40
    SAFETY = 50

class IntentAction(str, Enum):
    IDLE = "IDLE"
    HOVER = "HOVER"
    LAND = "LAND"
    RTL = "RTL"
    MOVE_VELOCITY = "MOVE_VELOCITY"
    MOVE_VELOCITY_NED = "MOVE_VELOCITY_NED"
    GOTO = "GOTO"
    GOTO_NED = "GOTO_NED"
    TAKEOFF = "TAKEOFF"
    EMERGENCY_KILL = "EMERGENCY_KILL"

class FlightIntent:
    def __init__(
        self,
        source: IntentSource,
        action: IntentAction,
        ttl_seconds: float = 1.0,
        params: Optional[Dict[str, Any]] = None
    ):
        self.source = source
        self.action = action
        self.timestamp = time.monotonic()
        self.ttl_seconds = ttl_seconds
        self.params = params or {}

    def is_expired(self) -> bool:
        return (time.monotonic() - self.timestamp) > self.ttl_seconds

    def __repr__(self):
        return f"<FlightIntent source={self.source.name} action={self.action.value} age={time.monotonic()-self.timestamp:.2f}s>"

