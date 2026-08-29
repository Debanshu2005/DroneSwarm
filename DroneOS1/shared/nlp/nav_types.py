from enum import Enum
from dataclasses import dataclass

class NavigationMode(str, Enum):
    MODE_A_GPS = "GPS-Enabled"
    MODE_B_LOCAL = "GPS-Denied / Optical Flow"
    MODE_C_DEGRADED = "Degraded"

@dataclass(frozen=True)
class SensorReport:
    mode: NavigationMode
