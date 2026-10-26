"""
Flight mode definitions, tables, encoders, and decoders for PX4 and ArduPilot.

Official Sources Checked:
1. PX4-Autopilot:
   - File: `src/modules/mavlink/mavlink_main.h` and `px4_custom_mode.h` in PX4-Autopilot repo
   - MAVLink custom_mode 32-bit field representation:
     `(main_mode << 16) | (sub_mode << 24)`
   - Main Modes:
     PX4_CUSTOM_MAIN_MODE_MANUAL = 1
     PX4_CUSTOM_MAIN_MODE_ALTCTL = 2
     PX4_CUSTOM_MAIN_MODE_POSCTL = 3
     PX4_CUSTOM_MAIN_MODE_AUTO = 4
     PX4_CUSTOM_MAIN_MODE_ACRO = 5
     PX4_CUSTOM_MAIN_MODE_OFFBOARD = 6
     PX4_CUSTOM_MAIN_MODE_STABILIZED = 7
     PX4_CUSTOM_MAIN_MODE_RATTITUDE = 8
     PX4_CUSTOM_MAIN_MODE_SIMPLE = 9
   - Auto Sub-Modes:
     PX4_CUSTOM_SUB_MODE_AUTO_READY = 1
     PX4_CUSTOM_SUB_MODE_AUTO_TAKEOFF = 2
     PX4_CUSTOM_SUB_MODE_AUTO_LOITER = 3 (HOLD)
     PX4_CUSTOM_SUB_MODE_AUTO_MISSION = 4
     PX4_CUSTOM_SUB_MODE_AUTO_RTL = 5
     PX4_CUSTOM_SUB_MODE_AUTO_LAND = 6
     PX4_CUSTOM_SUB_MODE_AUTO_RTGS = 7
     PX4_CUSTOM_SUB_MODE_AUTO_FOLLOW_TARGET = 8
     PX4_CUSTOM_SUB_MODE_AUTO_PRECLAND = 9

2. ArduPilot (ArduCopter):
   - Documentation: https://ardupilot.org/copter/docs/flight-modes.html
   - File: `ArduCopter/mode.h` and `defines.h` in ArduPilot repo
   - Custom Mode Numbers:
     STABILIZE = 0
     ACRO = 1
     ALT_HOLD = 2
     AUTO = 3
     GUIDED = 4
     LOITER = 5
     RTL = 6
     CIRCLE = 7
     LAND = 9
     DRIFT = 11
     SPORT = 13
     FLIP = 14
     AUTOTUNE = 15
     POSHOLD = 16
     BRAKE = 17
     THROW = 18
     AVOID_ADSB = 19
     GUIDED_NOGPS = 20
     SMART_RTL = 21
     FLOWHOLD = 22
     FOLLOW = 23
     ZIGZAG = 24
     SYSTEMID = 25
     AUTOROTATE = 26
     AUTO_RTL = 27
     TURTLE = 28
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# MAVLink Autopilot Type Constants (from MAV_AUTOPILOT enum in common.xml)
MAV_AUTOPILOT_ARDUPILOTMEGA = 3
MAV_AUTOPILOT_PX4 = 12

# MAVLink Command Constants
MAV_CMD_DO_SET_MODE = 176
MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1

# ---------------------------------------------------------------------------
# PX4 Mode Specifications
# ---------------------------------------------------------------------------
# Format: mode_name -> (main_mode, sub_mode)
PX4_MODE_COMMANDS: Dict[str, Tuple[int, int]] = {
    "MANUAL": (1, 0),
    "ALTCTL": (2, 0),
    "POSCTL": (3, 0),
    "HOLD": (4, 3),       # AUTO.LOITER
    "ACRO": (5, 0),
    "OFFBOARD": (6, 0),
    "STABILIZED": (7, 0),
    "RTL": (4, 5),        # AUTO.RTL
    "LAND": (4, 6),       # AUTO.LAND
}

PX4_SETTABLE_MODES: List[str] = [
    "MANUAL",
    "STABILIZED",
    "ALTCTL",
    "POSCTL",
    "HOLD",
    "OFFBOARD",
    "RTL",
    "LAND",
    "ACRO",
]

# ---------------------------------------------------------------------------
# ArduPilot (ArduCopter) Mode Specifications
# ---------------------------------------------------------------------------
ARDUPILOT_MODE_COMMANDS: Dict[str, int] = {
    "STABILIZE": 0,
    "ACRO": 1,
    "ALT_HOLD": 2,
    "GUIDED": 4,
    "LOITER": 5,
    "RTL": 6,
    "LAND": 9,
    "POSHOLD": 16,
    "BRAKE": 17,
    "SMART_RTL": 21,
}

ARDUPILOT_SETTABLE_MODES: List[str] = [
    "STABILIZE",
    "ACRO",
    "ALT_HOLD",
    "GUIDED",
    "LOITER",
    "RTL",
    "LAND",
    "POSHOLD",
    "BRAKE",
    "SMART_RTL",
]

ARDUPILOT_ALL_MODES: Dict[int, str] = {
    0: "STABILIZE",
    1: "ACRO",
    2: "ALT_HOLD",
    3: "AUTO",
    4: "GUIDED",
    5: "LOITER",
    6: "RTL",
    7: "CIRCLE",
    9: "LAND",
    11: "DRIFT",
    13: "SPORT",
    14: "FLIP",
    15: "AUTOTUNE",
    16: "POSHOLD",
    17: "BRAKE",
    18: "THROW",
    19: "AVOID_ADSB",
    20: "GUIDED_NOGPS",
    21: "SMART_RTL",
    22: "FLOWHOLD",
    23: "FOLLOW",
    24: "ZIGZAG",
    25: "SYSTEMID",
    26: "AUTOROTATE",
    27: "AUTO_RTL",
    28: "TURTLE",
}

# ---------------------------------------------------------------------------
# Cross-Firmware Alias Normalization Tables
# ---------------------------------------------------------------------------
PX4_ALIASES: Dict[str, str] = {
    "LOITER": "HOLD",
    "HOLD": "HOLD",
    "AUTO.LOITER": "HOLD",
    "ALT_HOLD": "ALTCTL",
    "ALTHOLD": "ALTCTL",
    "ALTCTL": "ALTCTL",
    "ALTITUDE": "ALTCTL",
    "GUIDED": "OFFBOARD",
    "OFFBOARD": "OFFBOARD",
    "RTL": "RTL",
    "RETURN": "RTL",
    "RETURN_TO_LAUNCH": "RTL",
    "AUTO.RTL": "RTL",
    "LAND": "LAND",
    "AUTO.LAND": "LAND",
    "POSITION": "POSCTL",
    "POSHOLD": "POSCTL",
    "POSCTL": "POSCTL",
    "STABILIZE": "STABILIZED",
    "STABILIZED": "STABILIZED",
    "MANUAL": "MANUAL",
    "ACRO": "ACRO",
}

ARDUPILOT_ALIASES: Dict[str, str] = {
    "HOLD": "LOITER",
    "LOITER": "LOITER",
    "AUTO.LOITER": "LOITER",
    "ALTCTL": "ALT_HOLD",
    "ALT_HOLD": "ALT_HOLD",
    "ALTHOLD": "ALT_HOLD",
    "ALTITUDE": "ALT_HOLD",
    "OFFBOARD": "GUIDED",
    "GUIDED": "GUIDED",
    "RTL": "RTL",
    "RETURN": "RTL",
    "RETURN_TO_LAUNCH": "RTL",
    "AUTO.RTL": "RTL",
    "LAND": "LAND",
    "AUTO.LAND": "LAND",
    "POSITION": "POSHOLD",
    "POSCTL": "POSHOLD",
    "POSHOLD": "POSHOLD",
    "STABILIZED": "STABILIZE",
    "STABILIZE": "STABILIZE",
    "ACRO": "ACRO",
    "BRAKE": "BRAKE",
    "SMART_RTL": "SMART_RTL",
}


def normalize_firmware(firmware: Optional[str]) -> str:
    """Normalize firmware identifier string to 'px4' or 'ardupilot'."""
    if not firmware:
        return "px4"
    fw = firmware.strip().lower()
    if "ardu" in fw:
        return "ardupilot"
    return "px4"


def normalize_mode_alias(mode: str, firmware: str = "px4") -> str:
    """
    Normalizes a flight mode name according to the target firmware.
    Handles cross-firmware aliases (e.g. LOITER/HOLD, GUIDED/OFFBOARD, ALTCTL/ALT_HOLD).
    """
    if not mode:
        return ""
    clean = mode.strip().upper().replace("-", "_").replace(" ", "_")
    fw = normalize_firmware(firmware)
    if fw == "ardupilot":
        return ARDUPILOT_ALIASES.get(clean, clean)
    return PX4_ALIASES.get(clean, clean)


def get_settable_modes(firmware: str = "px4") -> List[str]:
    """Returns the list of settable flight modes for the given firmware (excluding AUTO/MISSION)."""
    fw = normalize_firmware(firmware)
    if fw == "ardupilot":
        return list(ARDUPILOT_SETTABLE_MODES)
    return list(PX4_SETTABLE_MODES)


def decode_px4_custom_mode(custom_mode: int) -> str:
    """
    Decodes a 32-bit PX4 custom_mode from a MAVLink HEARTBEAT message.
    Format: (main_mode << 16) | (sub_mode << 24).
    """
    main = (custom_mode >> 16) & 0xFF
    sub = (custom_mode >> 24) & 0xFF
    if main == 1:
        return "MANUAL"
    if main == 2:
        return "ALTCTL"
    if main == 3:
        return "POSCTL"
    if main == 4:
        sub_modes = {
            1: "AUTO.READY",
            2: "TAKEOFF",
            3: "HOLD",
            4: "MISSION",
            5: "RTL",
            6: "LAND",
            7: "RTGS",
            8: "FOLLOW_TARGET",
            9: "PRECLAND",
        }
        return sub_modes.get(sub, f"AUTO({sub})")
    if main == 5:
        return "ACRO"
    if main == 6:
        return "OFFBOARD"
    if main == 7:
        return "STABILIZED"
    if main == 8:
        return "RATTITUDE"
    if main == 9:
        return "SIMPLE"
    return f"PX4_CUSTOM({main},{sub})"


def decode_ardupilot_custom_mode(custom_mode: int) -> str:
    """
    Decodes an ArduPilot custom_mode uint32 from a MAVLink HEARTBEAT message.
    """
    return ARDUPILOT_ALL_MODES.get(custom_mode, f"ARDUPILOT_CUSTOM({custom_mode})")


def decode_custom_mode(custom_mode: int, firmware: str = "px4") -> str:
    """
    Decodes custom_mode from MAVLink HEARTBEAT based on the active firmware.
    """
    fw = normalize_firmware(firmware)
    if fw == "ardupilot":
        return decode_ardupilot_custom_mode(custom_mode)
    return decode_px4_custom_mode(custom_mode)


def get_do_set_mode_params(mode: str, firmware: str = "px4") -> Optional[Tuple[float, float, float]]:
    """
    Calculates param1, param2, param3 for MAV_CMD_DO_SET_MODE (command 176).
    Returns (param1, param2, param3) as floats or None if mode is not settable.
    - param1: MAV_MODE_FLAG_CUSTOM_MODE_ENABLED (1.0)
    - param2: main_mode (PX4) or mode_number (ArduPilot)
    - param3: sub_mode (PX4) or 0.0 (ArduPilot)
    """
    fw = normalize_firmware(firmware)
    norm = normalize_mode_alias(mode, fw)

    if fw == "ardupilot":
        if norm not in ARDUPILOT_MODE_COMMANDS:
            return None
        mode_num = ARDUPILOT_MODE_COMMANDS[norm]
        return (float(MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), float(mode_num), 0.0)
    else:
        if norm not in PX4_MODE_COMMANDS:
            return None
        main_mode, sub_mode = PX4_MODE_COMMANDS[norm]
        return (float(MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), float(main_mode), float(sub_mode))


def detect_autopilot_from_heartbeat(fields: dict) -> Optional[str]:
    """
    Inspects HEARTBEAT message fields to detect the autopilot type.
    Returns 'ardupilot', 'px4', or None if unknown.
    """
    if not isinstance(fields, dict):
        return None
    ap = fields.get("autopilot")
    if ap == MAV_AUTOPILOT_ARDUPILOTMEGA:
        return "ardupilot"
    if ap == MAV_AUTOPILOT_PX4:
        return "px4"
    return None
