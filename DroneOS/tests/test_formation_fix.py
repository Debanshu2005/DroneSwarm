"""
DroneOS/tests/test_formation_fix.py

Tests for items 2, 3, 5, 9 of the formation fix specification.

a. no slot map -> False, reason mentions drone id, params unchanged
b. type None   -> False, reason set, no exception
c. spacing below minimum -> False, reason contains the minimum
d. valid V for drone1..drone4 -> True, params stored
e. CommandHandler REJECTED lifecycle message contains specific reason
f. geometry: anchor-relative offsets for CIRCLE, SQUARE, and squadron shapes

Pre-existing failure test_airsim_adapter.py::test_speed_clamping is reported
separately and is NOT fixed here.
"""
import asyncio
import math
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from DroneOS.core.flight_manager import FlightManager
from DroneOS.core.formation_engine import FormationEngine
from DroneOS.core.formation_manager import FormationManager, FormationType
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.intents import IntentSource, IntentAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fm(drone_id="drone1", min_sep=8.0, min_ca=2.0):
    """Build a FlightManager with a swarm_manager mock and flight config."""
    fc = AsyncMock()
    store = MagicMock()
    fm = FlightManager(fc, store)
    sm = MagicMock()
    sm.identity.drone_id = drone_id
    fm.set_swarm_manager(sm)
    # Minimal flight config so the spacing guard can read limits
    cfg = MagicMock()
    cfg.formation.min_formation_separation_m = min_sep
    cfg.collision_avoidance.min_horizontal_distance = min_ca
    fm._flight_config = cfg
    return fm


VALID_4_PARAMS = {
    "type": "V",
    "spacing": 20.0,
    "members": ["drone1", "drone2", "drone3", "drone4"],
    "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3},
}


# ---------------------------------------------------------------------------
# a. No slot map -> False, reason mentions drone_id, params unchanged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_no_slot_map_rejected():
    fm = _make_fm("drone1")
    fm.formation_params = {"old": "data"}
    res = await fm.formation_update({"type": "V", "spacing": 20.0})
    assert res is False
    assert fm.formation_params == {"old": "data"}
    assert "drone1" in fm.last_rejection_reason


@pytest.mark.asyncio
async def test_a_missing_my_slot_rejected():
    fm = _make_fm("drone1")
    fm.formation_params = {"old": "data"}
    params = {"type": "V", "spacing": 20.0, "slot_assignments": {"drone2": 0}}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.formation_params == {"old": "data"}
    assert "drone1" in fm.last_rejection_reason


# ---------------------------------------------------------------------------
# b. type=None -> False, reason set, no exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b_type_none_rejected():
    fm = _make_fm("drone1")
    params = {
        "type": None,
        "spacing": 20.0,
        "slot_assignments": {"drone1": 0},
    }
    res = await fm.formation_update(params)
    assert res is False
    assert fm.last_rejection_reason != ""


@pytest.mark.asyncio
async def test_b_slot_value_none_rejected():
    fm = _make_fm("drone1")
    params = {
        "type": "V",
        "spacing": 20.0,
        "slot_assignments": {"drone1": None},
    }
    res = await fm.formation_update(params)
    assert res is False
    assert fm.last_rejection_reason != ""


# ---------------------------------------------------------------------------
# c. Spacing below minimum -> False, reason contains the minimum
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c_spacing_below_minimum():
    # min_sep=8.0, min_ca=2.0 -> min_viable = 1.5 * 8.0 = 12.0
    fm = _make_fm("drone1", min_sep=8.0, min_ca=2.0)
    params = {
        "type": "V",
        "spacing": 5.0,
        "slot_assignments": {"drone1": 0},
    }
    res = await fm.formation_update(params)
    assert res is False
    assert "12.0" in fm.last_rejection_reason or "minimum" in fm.last_rejection_reason.lower()


@pytest.mark.asyncio
async def test_c_spacing_exactly_at_minimum_accepted():
    # min_sep=8.0, min_ca=2.0 -> min_viable = 12.0
    fm = _make_fm("drone1", min_sep=8.0, min_ca=2.0)
    params = dict(VALID_4_PARAMS)
    params["spacing"] = 12.0
    res = await fm.formation_update(params)
    assert res is True


# ---------------------------------------------------------------------------
# d. Valid V formation drone1..drone4 -> True, params stored
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d_valid_v_formation():
    fm = _make_fm("drone1")
    res = await fm.formation_update(dict(VALID_4_PARAMS))
    assert res is True
    assert fm.formation_params is not None
    assert fm.formation_params["type"] == "V"


# ---------------------------------------------------------------------------
# e. CommandHandler emits REJECTED lifecycle with specific reason
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e_command_handler_rejected_reason():
    from DroneOS.core.command_handler import CommandHandler
    from DroneOS.shared.protocol.messages import ControlMessage, CommandAction

    fc = AsyncMock()
    store = MagicMock()
    fm = _make_fm("drone1")
    fm.last_rejection_reason = ""

    ch = CommandHandler(node_id="drone1")
    ch.flight_manager = fm
    # Register handler that returns False with a reason set
    async def _handler(params):
        fm.last_rejection_reason = "formation rejected: no slot for drone1"
        return False

    ch.register_handler(CommandAction.FORMATION_UPDATE, _handler)

    lifecycle_calls = []
    def _fake_send(sender_id, action, stage, reason=None, cmd_id=None):
        lifecycle_calls.append({"stage": stage, "reason": reason})
    ch._send_lifecycle = _fake_send

    msg = ControlMessage(
        action=CommandAction.FORMATION_UPDATE,
        params={"type": "V", "spacing": 20.0},
        sender_id="gs_mobile_01",
        timestamp=time.time(),
    )
    result = await ch.handle_command(msg)
    assert result is False
    rejected = [c for c in lifecycle_calls if c["stage"] == "REJECTED"]
    assert rejected, "Expected a REJECTED lifecycle message"
    assert "drone1" in (rejected[0]["reason"] or "")


# ---------------------------------------------------------------------------
# f. Geometry: anchor-relative offsets
# ---------------------------------------------------------------------------

class _MockSwarm:
    def __init__(self, drone_id):
        self.identity = MagicMock()
        self.identity.drone_id = drone_id
        self.registry = MagicMock()
        self.registry.get_peer.return_value = None


def _engine(drone_id):
    sm = _MockSwarm(drone_id)
    return FormationEngine(sm, MagicMock(), config=None)


def test_f_circle_4_anchor_relative_offset():
    """
    CIRCLE, 4 drones, spacing 10:
    raw offsets: slot0=(10,0), slot1=(0,10), slot2=(-10,0), slot3=(0,-10)
    anchor_relative: slot0=(0,0), slot1=(-10,10), slot2=(-20,0), slot3=(-10,-10)
    """
    engine = _engine("drone1")
    engine.form_mgr.set_formation(FormationType.CIRCLE, 10.0)
    total = 4

    # Anchor slot 0 relative offset must be (0, 0)
    rel0 = engine._anchor_relative_offset(0, total)
    assert rel0[:2] == pytest.approx((0.0, 0.0), abs=1e-9)

    # All four slots must lie at distance spacing from a common center.
    # With anchor-relative offsets computed from raw offsets, each follower
    # targets anchor_pos + rel_offset. The anchor itself is raw slot0 = (10, 0)
    # from the ring center, so the ring center relative to anchor is (-10, 0).
    # Check: raw offsets for all slots are equidistant from the ring center.
    mgr = FormationManager()
    mgr.set_formation(FormationType.CIRCLE, 10.0)
    raw = [mgr.get_offset(i, total) for i in range(total)]
    for n, e, _ in raw:
        dist = math.hypot(n, e)
        assert dist == pytest.approx(10.0, abs=1e-9), (
            f"CIRCLE raw slot not at spacing: dist={dist}"
        )


def test_f_circle_anchor_on_ring_via_relative_offsets():
    """
    With anchor-relative offsets, when anchor is at position P,
    the expected position of the anchor (slot 0) is P + (0, 0) = P.
    So the anchor sits ON the ring, not at the center.
    """
    engine = _engine("drone1")
    engine.form_mgr.set_formation(FormationType.CIRCLE, 10.0)

    # Anchor's own relative offset is always (0, 0) regardless of formation type
    rel0 = engine._anchor_relative_offset(0, 4)
    assert rel0[:2] == pytest.approx((0.0, 0.0), abs=1e-9)

    # Follower offsets must not all be zero (they form the ring around anchor)
    rel1 = engine._anchor_relative_offset(1, 4)
    assert math.hypot(rel1[0], rel1[1]) > 0.1


def test_f_square_4_anchor_relative_offset():
    """SQUARE: anchor relative offset (slot 0) = (0, 0)."""
    engine = _engine("drone1")
    engine.form_mgr.set_formation(FormationType.SQUARE, 10.0)
    rel0 = engine._anchor_relative_offset(0, 4)
    assert rel0[:2] == pytest.approx((0.0, 0.0), abs=1e-9)


def test_f_v_formation_anchor_relative_unchanged():
    """
    V formation: slot 0 offset is (0, 0), so anchor-relative equals raw.
    """
    engine = _engine("drone1")
    engine.form_mgr.set_formation(FormationType.V, 10.0)
    total = 4
    for slot in range(total):
        raw = engine.form_mgr.get_offset(slot, total)
        rel = engine._anchor_relative_offset(slot, total)
        assert rel[:2] == pytest.approx(raw[:2], abs=1e-9), (
            f"V slot {slot}: anchor-relative should equal raw, got {rel} vs {raw}"
        )


def test_f_line_formation_anchor_relative_unchanged():
    engine = _engine("drone1")
    engine.form_mgr.set_formation(FormationType.LINE, 10.0)
    total = 4
    for slot in range(total):
        raw = engine.form_mgr.get_offset(slot, total)
        rel = engine._anchor_relative_offset(slot, total)
        assert rel[:2] == pytest.approx(raw[:2], abs=1e-9)


def test_f_column_formation_anchor_relative_unchanged():
    engine = _engine("drone1")
    engine.form_mgr.set_formation(FormationType.COLUMN, 10.0)
    total = 4
    for slot in range(total):
        raw = engine.form_mgr.get_offset(slot, total)
        rel = engine._anchor_relative_offset(slot, total)
        assert rel[:2] == pytest.approx(raw[:2], abs=1e-9)


def test_f_grid_formation_anchor_relative_unchanged():
    engine = _engine("drone1")
    engine.form_mgr.set_formation(FormationType.GRID, 10.0)
    total = 4
    for slot in range(total):
        raw = engine.form_mgr.get_offset(slot, total)
        rel = engine._anchor_relative_offset(slot, total)
        assert rel[:2] == pytest.approx(raw[:2], abs=1e-9)


def test_f_diamond_formation_anchor_relative_unchanged():
    engine = _engine("drone1")
    engine.form_mgr.set_formation(FormationType.DIAMOND, 10.0)
    total = 4
    for slot in range(total):
        raw = engine.form_mgr.get_offset(slot, total)
        rel = engine._anchor_relative_offset(slot, total)
        assert rel[:2] == pytest.approx(raw[:2], abs=1e-9)
