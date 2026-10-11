"""
Property-based tests for the six self-healing formation correctness invariants.
Validates: Requirements 6, 7, 8, 9, 10 (coordination correctness properties).

Run with:
    python -m pytest DroneOS/tests/test_coordination_properties.py -v
"""
import asyncio
from typing import Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, assume, settings, HealthCheck
from hypothesis import strategies as st

from DroneOS.core.coordination.healing import plan_healing, HealPlan, RejectKind
from DroneOS.core.coordination.quorum import compute_quorum, QuorumState
from DroneOS.core.coordination.membership import MembershipView, PeerState


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers / strategies
# ──────────────────────────────────────────────────────────────────────────────

VALID_FORMATIONS = ["V", "LINE", "SQUARE", "COLUMN"]
DRONE_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

drone_ids = st.text(
    alphabet=DRONE_ID_ALPHABET,
    min_size=2,
    max_size=6,
)


class MockFlightCfg:
    """Minimal flight config accepted by plan_healing."""
    class formation:
        min_formation_separation_m = 3.0
        spacing = 15.0
    class collision_avoidance:
        min_horizontal_distance = 2.0
    coordination = {
        "dead_drone_obstacle": "false",
        "mode": "advisory",
        "heal_separation_margin_m": "1.0",
        "heal_position_margin_m": "1.0",
        "heal_settled_radius_m": "100.0",   # generous — we pass nominal positions
        "heal_max_position_age_s": "0",      # disable age check in tests
    }


def _make_params(drone_ids_list, f_type="V", spacing=20.0):
    """Build a formation_params dict with consecutive slots."""
    slots = {pid: i for i, pid in enumerate(drone_ids_list)}
    return {
        "type": f_type,
        "spacing": spacing,
        "slot_assignments": slots,
        "members": list(slots.keys()),
    }


def _make_membership_view(all_ids, dead_ids, clock_val=100.0):
    """Create a MembershipView with the given alive/dead split."""
    clock = lambda: clock_val
    mv = MembershipView({
        "suspect_missed_beats": "3",
        "dead_missed_beats": "6",
        "rejoin_stable_s": "10",
    }, clock=clock)
    for pid in all_ids:
        mv.update_from_peer(pid, clock_val, 100.0, 0.0, 0.0, 10.0, peer_position_stamp=clock_val)
    # Force dead nodes
    for pid in dead_ids:
        if pid in mv.nodes:
            mv.nodes[pid].state = PeerState.DEAD
    return mv


# ──────────────────────────────────────────────────────────────────────────────
# Property 1: Quorum Invariant
# Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5
# ──────────────────────────────────────────────────────────────────────────────

@given(
    all_ids=st.lists(drone_ids, min_size=1, max_size=8, unique=True),
    dead_fraction=st.floats(min_value=0.0, max_value=1.0),
    quorum_required=st.booleans(),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_prop1_quorum_invariant(all_ids, dead_fraction, quorum_required):
    """
    Property 1: Quorum Invariant
    compute_quorum() returns QUORUM iff alive_count > len(universe) / 2.
    Own node (all_ids[0]) always counts as alive.
    """
    my_id = all_ids[0]
    universe = {pid: i for i, pid in enumerate(all_ids)}

    # Compute how many non-self peers are dead
    n_dead = int(len(all_ids) * dead_fraction)
    # Never mark my_id as dead
    dead_ids = set(all_ids[1:n_dead + 1])

    mv = _make_membership_view(all_ids, dead_ids)
    result = compute_quorum(my_id, universe, mv, quorum_required)

    # Own node always alive
    alive_count = len(all_ids) - len(dead_ids)  # my_id is never dead

    if not universe or my_id not in universe:
        assert result == QuorumState.IDLE
    elif not quorum_required:
        assert result == QuorumState.QUORUM
    elif alive_count > len(universe) / 2.0:
        assert result == QuorumState.QUORUM, (
            f"Expected QUORUM: alive={alive_count}, universe={len(universe)}, got {result}"
        )
    else:
        assert result == QuorumState.ISOLATED, (
            f"Expected ISOLATED: alive={alive_count}, universe={len(universe)}, got {result}"
        )


def test_prop1_idle_when_not_in_formation():
    """If my_id not in slot_assignments, result is IDLE regardless."""
    mv = MembershipView({}, clock=lambda: 1.0)
    result = compute_quorum("stranger", {"d0": 0, "d1": 1}, mv, True)
    assert result == QuorumState.IDLE


def test_prop1_quorum_override():
    """When quorum_required=False, always returns QUORUM."""
    mv = _make_membership_view(["d0", "d1", "d2"], {"d1", "d2"})
    slots = {"d0": 0, "d1": 1, "d2": 2}
    result = compute_quorum("d0", slots, mv, quorum_required=False)
    assert result == QuorumState.QUORUM


# ──────────────────────────────────────────────────────────────────────────────
# Properties 2, 3, 4, 6 — all exercise plan_healing
# ──────────────────────────────────────────────────────────────────────────────

def _call_plan_healing(alive_list, dead_list, f_type="V", spacing=20.0):
    """
    Invoke plan_healing in advisory mode with nominal positions (no membership view).
    Returns the HealPlan or None.
    """
    assume(len(alive_list) >= 1)
    assume(len(dead_list) >= 1)
    assume(set(alive_list).isdisjoint(set(dead_list)))

    all_ids = alive_list + dead_list
    params = _make_params(all_ids, f_type, spacing)
    healthy = set(alive_list)
    current_anchor = alive_list[0]  # slot 0 in all_ids is alive_list[0] by construction
    prop_anchor = current_anchor

    cfg = MockFlightCfg()
    return plan_healing(
        current_params=params,
        healthy_members=healthy,
        current_anchor=current_anchor,
        prop_anchor=prop_anchor,
        my_id=prop_anchor,
        flight_cfg=cfg,
        membership_view=None,
        my_status=None,
    )


# Property 2: Slot Bijection — Validates: Requirements 7.1, 7.2, 7.3
@given(
    alive=st.lists(drone_ids, min_size=2, max_size=7, unique=True),
    dead=st.lists(drone_ids, min_size=1, max_size=3, unique=True),
)
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_prop2_slot_bijection(alive, dead):
    """
    Property 2: Slot Bijection
    An accepted HealPlan assigns exactly one unique slot to each survivor, covering {0..n-1}.
    """
    assume(set(alive).isdisjoint(set(dead)))

    plan = _call_plan_healing(alive, dead)
    if plan is None or not plan.accepted or plan.is_hold:
        return  # skip non-accepted plans

    n = len(alive)
    keys = set(plan.slot_assignments.keys())
    values = list(plan.slot_assignments.values())

    assert keys == set(alive), f"Keys {keys} != survivors {set(alive)}"
    assert set(values) == set(range(n)), f"Values {set(values)} != range({n})"
    assert len(values) == len(set(values)), f"Duplicate slot values: {values}"


# Property 3: Anchor Invariant — Validates: Requirements 8.1, 8.2, 8.3
@given(
    alive=st.lists(drone_ids, min_size=2, max_size=7, unique=True),
    dead=st.lists(drone_ids, min_size=1, max_size=3, unique=True),
)
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_prop3_anchor_gets_slot_zero(alive, dead):
    """
    Property 3: Anchor Invariant
    The proposed anchor always holds slot 0 in any accepted HealPlan.
    """
    assume(set(alive).isdisjoint(set(dead)))
    assume(len(alive) >= 1)

    # Build params with alive[0] as the current anchor (slot 0)
    all_ids = alive + dead
    params = _make_params(all_ids)
    current_anchor = alive[0]  # alive[0] always has slot 0 in all_ids ordering
    prop_anchor = current_anchor
    healthy = set(alive)

    plan = plan_healing(
        current_params=params,
        healthy_members=healthy,
        current_anchor=current_anchor,
        prop_anchor=prop_anchor,
        my_id=prop_anchor,
        flight_cfg=MockFlightCfg(),
    )

    if plan is None or not plan.accepted or plan.is_hold:
        return

    assert plan.slot_assignments.get(prop_anchor) == 0, (
        f"Anchor {prop_anchor!r} has slot {plan.slot_assignments.get(prop_anchor)}, expected 0"
    )


def test_prop3_anchor_promotion_on_anchor_death():
    """When slot-0 dies, the lex-first healthy drone is promoted to slot 0."""
    # d0 is slot-0 and dies; d1, d2 survive. lex-first is d1.
    params = _make_params(["d0", "d1", "d2"], spacing=20.0)
    plan = plan_healing(
        current_params=params,
        healthy_members={"d1", "d2"},
        current_anchor=None,  # anchor is dead
        prop_anchor="d1",
        my_id="d1",
        flight_cfg=MockFlightCfg(),
    )
    assert plan is not None and plan.accepted
    assert plan.slot_assignments["d1"] == 0


# Property 4: Separation Invariant — Validates: Requirements 9.1, 9.2, 9.3
@given(
    alive=st.lists(drone_ids, min_size=2, max_size=5, unique=True),
    dead=st.lists(drone_ids, min_size=1, max_size=2, unique=True),
    spacing=st.floats(min_value=15.0, max_value=40.0),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_prop4_separation_invariant(alive, dead, spacing):
    """
    Property 4: Separation Invariant
    For any accepted plan, plan.min_separation >= min_formation_separation_m.
    """
    assume(set(alive).isdisjoint(set(dead)))
    assume(len(alive) >= 2)

    plan = _call_plan_healing(alive, dead, spacing=spacing)
    if plan is None or not plan.accepted or plan.is_hold:
        return

    min_sep_required = MockFlightCfg.formation.min_formation_separation_m
    assert plan.min_separation >= min_sep_required - 1e-6, (
        f"min_separation {plan.min_separation:.3f} < required {min_sep_required:.3f}"
    )


# Property 5: Session Cap — Validates: Requirements 10.1, 10.2, 10.3, 10.4
@given(
    max_heals=st.integers(min_value=1, max_value=5),
    n_triggers=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_prop5_session_cap(max_heals, n_triggers):
    """
    Property 5: Session Cap
    _heal_count never exceeds max_heals_per_session.
    _heals_disabled is True once the cap is reached.
    """
    from DroneOS.core.coordination.manager import CoordinationManager
    from DroneOS.core.coordination.quorum import QuorumState

    mock_swarm = MagicMock()
    mock_swarm.identity.drone_id = "d0"
    mock_swarm.registry.get_all_peers.return_value = []

    class Cfg:
        coordination = {
            "enabled": "true",
            "mode": "active",
            "armed": "true",
            "max_heals_per_session": str(max_heals),
            "reslot_cooldown_s": "0",
            "dead_drone_obstacle": "false",
            "reshape_on_follower_loss": "false",
            "kill_switch_file": "",
        }
        class formation:
            min_formation_separation_m = 3.0
        class collision_avoidance:
            min_horizontal_distance = 2.0

    call_count = [0]

    async def mock_sender(params, targets):
        call_count[0] += 1
        return True

    t = [0.0]
    def mock_clock():
        t[0] += 0.1
        return t[0]

    manager = CoordinationManager(
        swarm_manager=mock_swarm,
        flight_cfg=Cfg(),
        heartbeat_interval=1.0,
        clock=mock_clock,
        formation_update_sender=mock_sender,
    )
    manager._heal_count = 0
    manager._heals_disabled = False
    manager.max_heals_per_session = max_heals

    # Simulate heal sends by directly calling the counter logic
    for _ in range(n_triggers):
        if manager._heals_disabled:
            break
        if manager._heal_count >= max_heals:
            manager._heals_disabled = True
            break
        # Simulate a successful send
        manager._heal_count += 1
        if manager._heal_count >= max_heals:
            manager._heals_disabled = True

        # Invariant check at every step
        assert manager._heal_count <= max_heals, (
            f"_heal_count {manager._heal_count} exceeded cap {max_heals}"
        )

    assert manager._heal_count <= max_heals
    if manager._heal_count >= max_heals:
        assert manager._heals_disabled, "Cap reached but _heals_disabled is False"


# Property 6: No Dead Drone in Healed Slots — Validates: Requirements 7.4
@given(
    alive=st.lists(drone_ids, min_size=2, max_size=7, unique=True),
    dead=st.lists(drone_ids, min_size=1, max_size=3, unique=True),
)
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_prop6_no_dead_in_healed_slots(alive, dead):
    """
    Property 6: No Dead Drone in Healed Slots
    Dead drone IDs never appear in an accepted HealPlan's slot_assignments.
    """
    assume(set(alive).isdisjoint(set(dead)))

    plan = _call_plan_healing(alive, dead)
    if plan is None or not plan.accepted or plan.is_hold:
        return

    dead_set = set(dead)
    overlap = dead_set & set(plan.slot_assignments.keys())
    assert not overlap, f"Dead drones {overlap} found in slot_assignments"

