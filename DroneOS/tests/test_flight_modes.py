"""
Unit tests for DroneOS/adapters/flight_modes.py.
Verifies mode definitions, alias normalizations, heartbeat decoding,
and DO_SET_MODE parameter generation for PX4 and ArduPilot.
"""

import pytest
from DroneOS.adapters.flight_modes import (
    PX4_MODE_COMMANDS,
    PX4_SETTABLE_MODES,
    ARDUPILOT_MODE_COMMANDS,
    ARDUPILOT_SETTABLE_MODES,
    normalize_firmware,
    normalize_mode_alias,
    get_settable_modes,
    decode_px4_custom_mode,
    decode_ardupilot_custom_mode,
    decode_custom_mode,
    get_do_set_mode_params,
    detect_autopilot_from_heartbeat,
    MAV_AUTOPILOT_ARDUPILOTMEGA,
    MAV_AUTOPILOT_PX4,
)


class TestFlightModesTables:
    def test_px4_tables_verified_against_docs(self):
        """Verify PX4 custom mode mappings (main << 16 | sub << 24)."""
        assert PX4_MODE_COMMANDS["MANUAL"] == (1, 0)
        assert PX4_MODE_COMMANDS["ALTCTL"] == (2, 0)
        assert PX4_MODE_COMMANDS["POSCTL"] == (3, 0)
        assert PX4_MODE_COMMANDS["HOLD"] == (4, 3)
        assert PX4_MODE_COMMANDS["ACRO"] == (5, 0)
        assert PX4_MODE_COMMANDS["OFFBOARD"] == (6, 0)
        assert PX4_MODE_COMMANDS["STABILIZED"] == (7, 0)
        assert PX4_MODE_COMMANDS["RTL"] == (4, 5)
        assert PX4_MODE_COMMANDS["LAND"] == (4, 6)

        # Ensure AUTO/MISSION are not in settable modes
        settable = get_settable_modes("px4")
        assert "AUTO" not in settable
        assert "MISSION" not in settable
        assert "HOLD" in settable
        assert "STABILIZED" in settable
        assert "MANUAL" in settable

    def test_ardupilot_tables_verified_against_docs(self):
        """Verify ArduCopter mode numbers."""
        assert ARDUPILOT_MODE_COMMANDS["STABILIZE"] == 0
        assert ARDUPILOT_MODE_COMMANDS["ACRO"] == 1
        assert ARDUPILOT_MODE_COMMANDS["ALT_HOLD"] == 2
        assert ARDUPILOT_MODE_COMMANDS["GUIDED"] == 4
        assert ARDUPILOT_MODE_COMMANDS["LOITER"] == 5
        assert ARDUPILOT_MODE_COMMANDS["RTL"] == 6
        assert ARDUPILOT_MODE_COMMANDS["LAND"] == 9
        assert ARDUPILOT_MODE_COMMANDS["POSHOLD"] == 16
        assert ARDUPILOT_MODE_COMMANDS["BRAKE"] == 17
        assert ARDUPILOT_MODE_COMMANDS["SMART_RTL"] == 21

        settable = get_settable_modes("ardupilot")
        assert "AUTO" not in settable
        assert "MISSION" not in settable
        assert "LOITER" in settable
        assert "STABILIZE" in settable
        assert "ALT_HOLD" in settable


class TestAliasNormalization:
    def test_px4_aliases(self):
        # STABILIZE / STABILIZED
        assert normalize_mode_alias("STABILIZE", "px4") == "STABILIZED"
        assert normalize_mode_alias("stabilized", "px4") == "STABILIZED"
        # LOITER / HOLD
        assert normalize_mode_alias("LOITER", "px4") == "HOLD"
        assert normalize_mode_alias("HOLD", "px4") == "HOLD"
        # ALTCTL / ALT_HOLD
        assert normalize_mode_alias("ALT_HOLD", "px4") == "ALTCTL"
        assert normalize_mode_alias("ALTHOLD", "px4") == "ALTCTL"
        assert normalize_mode_alias("ALTCTL", "px4") == "ALTCTL"
        # GUIDED / OFFBOARD
        assert normalize_mode_alias("GUIDED", "px4") == "OFFBOARD"
        assert normalize_mode_alias("OFFBOARD", "px4") == "OFFBOARD"
        # RTL / RETURN_TO_LAUNCH
        assert normalize_mode_alias("RETURN_TO_LAUNCH", "px4") == "RTL"
        assert normalize_mode_alias("RETURN", "px4") == "RTL"
        assert normalize_mode_alias("RTL", "px4") == "RTL"
        # POSITION / POSCTL
        assert normalize_mode_alias("POSITION", "px4") == "POSCTL"
        assert normalize_mode_alias("POSHOLD", "px4") == "POSCTL"
        assert normalize_mode_alias("POSCTL", "px4") == "POSCTL"

    def test_ardupilot_aliases(self):
        # STABILIZED / STABILIZE
        assert normalize_mode_alias("STABILIZED", "ardupilot") == "STABILIZE"
        assert normalize_mode_alias("stabilize", "ardupilot") == "STABILIZE"
        # HOLD / LOITER
        assert normalize_mode_alias("HOLD", "ardupilot") == "LOITER"
        assert normalize_mode_alias("LOITER", "ardupilot") == "LOITER"
        # ALTCTL / ALT_HOLD
        assert normalize_mode_alias("ALTCTL", "ardupilot") == "ALT_HOLD"
        assert normalize_mode_alias("ALT_HOLD", "ardupilot") == "ALT_HOLD"
        # OFFBOARD / GUIDED
        assert normalize_mode_alias("OFFBOARD", "ardupilot") == "GUIDED"
        assert normalize_mode_alias("GUIDED", "ardupilot") == "GUIDED"
        # RTL / RETURN_TO_LAUNCH
        assert normalize_mode_alias("RETURN_TO_LAUNCH", "ardupilot") == "RTL"
        assert normalize_mode_alias("RTL", "ardupilot") == "RTL"
        # POSCTL / POSITION / POSHOLD
        assert normalize_mode_alias("POSCTL", "ardupilot") == "POSHOLD"
        assert normalize_mode_alias("POSITION", "ardupilot") == "POSHOLD"
        assert normalize_mode_alias("POSHOLD", "ardupilot") == "POSHOLD"


class TestHeartbeatDecoding:
    def test_decode_px4_custom_mode(self):
        # MANUAL: (1 << 16) | (0 << 24)
        assert decode_px4_custom_mode(1 << 16) == "MANUAL"
        # ALTCTL: (2 << 16) | (0 << 24)
        assert decode_px4_custom_mode(2 << 16) == "ALTCTL"
        # POSCTL: (3 << 16) | (0 << 24)
        assert decode_px4_custom_mode(3 << 16) == "POSCTL"
        # HOLD: (4 << 16) | (3 << 24)
        assert decode_px4_custom_mode((4 << 16) | (3 << 24)) == "HOLD"
        # RTL: (4 << 16) | (5 << 24)
        assert decode_px4_custom_mode((4 << 16) | (5 << 24)) == "RTL"
        # LAND: (4 << 16) | (6 << 24)
        assert decode_px4_custom_mode((4 << 16) | (6 << 24)) == "LAND"
        # ACRO: (5 << 16)
        assert decode_px4_custom_mode(5 << 16) == "ACRO"
        # OFFBOARD: (6 << 16)
        assert decode_px4_custom_mode(6 << 16) == "OFFBOARD"
        # STABILIZED: (7 << 16)
        assert decode_px4_custom_mode(7 << 16) == "STABILIZED"

    def test_decode_ardupilot_custom_mode(self):
        assert decode_ardupilot_custom_mode(0) == "STABILIZE"
        assert decode_ardupilot_custom_mode(1) == "ACRO"
        assert decode_ardupilot_custom_mode(2) == "ALT_HOLD"
        assert decode_ardupilot_custom_mode(4) == "GUIDED"
        assert decode_ardupilot_custom_mode(5) == "LOITER"
        assert decode_ardupilot_custom_mode(6) == "RTL"
        assert decode_ardupilot_custom_mode(9) == "LAND"
        assert decode_ardupilot_custom_mode(16) == "POSHOLD"
        assert decode_ardupilot_custom_mode(17) == "BRAKE"
        assert decode_ardupilot_custom_mode(21) == "SMART_RTL"

    def test_decode_custom_mode_wrapper(self):
        assert decode_custom_mode(1 << 16, "px4") == "MANUAL"
        assert decode_custom_mode(5, "ardupilot") == "LOITER"


class TestDoSetModeParams:
    def test_px4_do_set_mode_params(self):
        # Param1 must be 1.0 (MAV_MODE_FLAG_CUSTOM_MODE_ENABLED)
        # MANUAL -> (1.0, 1.0, 0.0)
        assert get_do_set_mode_params("MANUAL", "px4") == (1.0, 1.0, 0.0)
        # STABILIZED -> (1.0, 7.0, 0.0)
        assert get_do_set_mode_params("STABILIZED", "px4") == (1.0, 7.0, 0.0)
        assert get_do_set_mode_params("STABILIZE", "px4") == (1.0, 7.0, 0.0)
        # HOLD -> (1.0, 4.0, 3.0)
        assert get_do_set_mode_params("HOLD", "px4") == (1.0, 4.0, 3.0)
        assert get_do_set_mode_params("LOITER", "px4") == (1.0, 4.0, 3.0)
        # RTL -> (1.0, 4.0, 5.0)
        assert get_do_set_mode_params("RTL", "px4") == (1.0, 4.0, 5.0)
        # LAND -> (1.0, 4.0, 6.0)
        assert get_do_set_mode_params("LAND", "px4") == (1.0, 4.0, 6.0)
        # Unknown mode returns None
        assert get_do_set_mode_params("UNKNOWN_MODE", "px4") is None

    def test_ardupilot_do_set_mode_params(self):
        # STABILIZE -> (1.0, 0.0, 0.0)
        assert get_do_set_mode_params("STABILIZE", "ardupilot") == (1.0, 0.0, 0.0)
        assert get_do_set_mode_params("STABILIZED", "ardupilot") == (1.0, 0.0, 0.0)
        # LOITER -> (1.0, 5.0, 0.0)
        assert get_do_set_mode_params("LOITER", "ardupilot") == (1.0, 5.0, 0.0)
        assert get_do_set_mode_params("HOLD", "ardupilot") == (1.0, 5.0, 0.0)
        # ALT_HOLD -> (1.0, 2.0, 0.0)
        assert get_do_set_mode_params("ALT_HOLD", "ardupilot") == (1.0, 2.0, 0.0)
        assert get_do_set_mode_params("ALTCTL", "ardupilot") == (1.0, 2.0, 0.0)
        # GUIDED -> (1.0, 4.0, 0.0)
        assert get_do_set_mode_params("GUIDED", "ardupilot") == (1.0, 4.0, 0.0)
        # SMART_RTL -> (1.0, 21.0, 0.0)
        assert get_do_set_mode_params("SMART_RTL", "ardupilot") == (1.0, 21.0, 0.0)
        # Unknown mode returns None
        assert get_do_set_mode_params("FLY_AWAY", "ardupilot") is None


class TestHeartbeatDetection:
    def test_detect_autopilot(self):
        assert detect_autopilot_from_heartbeat({"autopilot": MAV_AUTOPILOT_ARDUPILOTMEGA}) == "ardupilot"
        assert detect_autopilot_from_heartbeat({"autopilot": MAV_AUTOPILOT_PX4}) == "px4"
        assert detect_autopilot_from_heartbeat({"autopilot": 999}) is None
        assert detect_autopilot_from_heartbeat({}) is None
        assert detect_autopilot_from_heartbeat(None) is None
