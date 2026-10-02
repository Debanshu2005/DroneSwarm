import pytest
import asyncio
import math
from unittest.mock import MagicMock, AsyncMock
from DroneOS.core.intents import IntentSource, IntentAction

packages = ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]

VALID_4_PARAMS = {
    "type": "V",
    "spacing": 20.0,
    "members": ["drone1", "drone2", "drone3", "drone4"],
    "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3},
}


def _make_fm(pkg_name, drone_id="drone1", min_sep=8.0, min_ca=2.0):
    FlightManager = __import__(f"{pkg_name}.core.flight_manager", fromlist=["FlightManager"]).FlightManager
    fc = AsyncMock()
    store = MagicMock()
    fm = FlightManager(fc, store)
    sm = MagicMock()
    sm.identity.drone_id = drone_id
    fm.set_swarm_manager(sm)
    cfg = MagicMock()
    cfg.formation.min_formation_separation_m = min_sep
    cfg.collision_avoidance.min_horizontal_distance = min_ca
    fm._flight_config = cfg
    return fm


# ---------------------------------------------------------------------------
# Original tests (preserved)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", packages)
async def test_parity_formation_update_no_slot(pkg_name):
    fm = _make_fm(pkg_name)
    fm.formation_params = {"old": "data"}
    params = {"type": "V"}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.formation_params == {"old": "data"}


@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", packages)
async def test_parity_formation_update_missing_my_slot(pkg_name):
    fm = _make_fm(pkg_name)
    fm.formation_params = {"old": "data"}
    params = {"type": "V", "spacing": 20.0, "slot_assignments": {"drone2": 0}}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.formation_params == {"old": "data"}


@pytest.mark.parametrize("pkg_name", packages)
def test_parity_formation_engine_no_slot(pkg_name):
    FormationEngine = __import__(f"{pkg_name}.core.formation_engine", fromlist=["FormationEngine"]).FormationEngine

    sm = MagicMock()
    sm.identity.drone_id = "drone4"
    store = MagicMock()
    engine = FormationEngine(sm, store)

    telemetry = MagicMock()
    telemetry.gps_valid = True
    telemetry.latitude = 10.0
    telemetry.longitude = 10.0

    params = {"type": "V", "slot_assignments": {"drone1": 0}}
    intent = engine.compute_intent(telemetry, {}, params)

    assert intent.source == IntentSource.IDLE
    assert intent.action == IntentAction.IDLE


# ---------------------------------------------------------------------------
# b. type=None -> False, reason set, no exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", packages)
async def test_parity_type_none_rejected(pkg_name):
    fm = _make_fm(pkg_name)
    params = {"type": None, "spacing": 20.0, "slot_assignments": {"drone1": 0}}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.last_rejection_reason != ""


# ---------------------------------------------------------------------------
# c. Spacing below minimum -> False, reason contains the minimum
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", packages)
async def test_parity_spacing_below_minimum(pkg_name):
    fm = _make_fm(pkg_name, min_sep=8.0, min_ca=2.0)  # min_viable = 12.0
    params = {"type": "V", "spacing": 5.0, "slot_assignments": {"drone1": 0}}
    res = await fm.formation_update(params)
    assert res is False
    assert fm.last_rejection_reason != ""
    reason = fm.last_rejection_reason.lower()
    assert "minimum" in reason or "12.0" in fm.last_rejection_reason


# ---------------------------------------------------------------------------
# d. Valid V for drone1..drone4 -> True, params stored
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", packages)
async def test_parity_valid_v_formation(pkg_name):
    fm = _make_fm(pkg_name)
    res = await fm.formation_update(dict(VALID_4_PARAMS))
    assert res is True
    assert fm.formation_params is not None
    assert fm.formation_params["type"] == "V"


# ---------------------------------------------------------------------------
# f. Geometry: anchor-relative offset is (0,0) for slot 0 in all packages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pkg_name", packages)
def test_parity_anchor_relative_slot0_is_zero(pkg_name):
    FormationEngine = __import__(f"{pkg_name}.core.formation_engine", fromlist=["FormationEngine"]).FormationEngine
    FormationType = __import__(f"{pkg_name}.core.formation_manager", fromlist=["FormationType"]).FormationType

    sm = MagicMock()
    sm.identity.drone_id = "drone1"
    engine = FormationEngine(sm, MagicMock(), config=None)

    for ft in [FormationType.CIRCLE, FormationType.SQUARE, FormationType.V,
               FormationType.LINE, FormationType.COLUMN]:
        engine.form_mgr.set_formation(ft, 10.0)
        rel0 = engine._anchor_relative_offset(0, 4)
        assert rel0[:2] == pytest.approx((0.0, 0.0), abs=1e-9), (
            f"{pkg_name} {ft}: anchor-relative slot 0 must be (0,0), got {rel0}"
        )


@pytest.mark.parametrize("pkg_name", packages)
def test_parity_squadron_shapes_anchor_relative_equals_raw(pkg_name):
    """For V, LINE, COLUMN, GRID, DIAMOND: anchor-relative == raw (slot0 raw is (0,0))."""
    FormationEngine = __import__(f"{pkg_name}.core.formation_engine", fromlist=["FormationEngine"]).FormationEngine
    FormationType = __import__(f"{pkg_name}.core.formation_manager", fromlist=["FormationType"]).FormationType

    sm = MagicMock()
    sm.identity.drone_id = "drone1"
    engine = FormationEngine(sm, MagicMock(), config=None)

    squadron = [FormationType.V, FormationType.LINE, FormationType.COLUMN,
                FormationType.GRID, FormationType.DIAMOND]
    for ft in squadron:
        engine.form_mgr.set_formation(ft, 10.0)
        total = 4
        for slot in range(total):
            raw = engine.form_mgr.get_offset(slot, total)
            rel = engine._anchor_relative_offset(slot, total)
            assert rel[:2] == pytest.approx(raw[:2], abs=1e-9), (
                f"{pkg_name} {ft} slot {slot}: anchor-relative should equal raw"
            )


@pytest.mark.parametrize("file_path", [
    "core/collision_avoidance.py",
    "core/flight_pipeline.py",
    "core/decision_engine.py"
])
def test_parity_file_contents(file_path):
    import os
    import re
    import difflib
    
    with open(f"DroneOS/{file_path}", "r", encoding="utf-8") as f:
        src_content = f.read()
    src_lines = src_content.splitlines()

    for target in ["DroneOS1", "DroneOS2", "DroneOS3"]:
        with open(f"{target}/{file_path}", "r", encoding="utf-8") as f:
            tgt_content = f.read()
            
        tgt_content = re.sub(f'{target}\\.', 'DroneOS.', tgt_content)
        tgt_content = re.sub(f'from {target}\\b', 'from DroneOS', tgt_content)
        tgt_content = re.sub(f'import {target}\\b', 'import DroneOS', tgt_content)
        
        tgt_lines = tgt_content.splitlines()
        
        diff = list(difflib.unified_diff(src_lines, tgt_lines, fromfile=f"DroneOS/{file_path}", tofile=f"{target}/{file_path}"))
        
        assert not diff, f"File {file_path} differs between DroneOS and {target} after normalization:\n" + "\n".join(diff)
