import pytest
import time
from DroneOS.core.coordination.quorum import compute_quorum, QuorumState
from DroneOS.core.coordination.anchor import propose_anchor, is_healthy
from DroneOS.core.coordination.membership import MembershipView, PeerState
from DroneOS.core.swarm_manager import SwarmMembership, DroneIdentityMessage

class MockSwarmRegistry:
    def __init__(self):
        self.peers = {}
    def get_peer(self, pid):
        return self.peers.get(pid)

class MockSwarm:
    def __init__(self):
        self.registry = MockSwarmRegistry()

class MockPeerState:
    def __init__(self, battery, last_pos):
        self.battery_level = battery
        self.last_position_time = last_pos

def _make_mv(alive_ids):
    mv = MembershipView({})
    for pid in alive_ids:
        mv.update_from_peer(pid, 100.0, 100.0, 0, 0, 0)
        mv.nodes[pid].state = PeerState.ALIVE
    return mv

def test_compute_quorum():
    mv = _make_mv(["d1", "d2"])
    
    # 4 members, 3 alive (d0, d1, d2) -> QUORUM
    slots_4 = {"d0": 0, "d1": 1, "d2": 2, "d3": 3}
    assert compute_quorum("d0", slots_4, mv, True) == QuorumState.QUORUM
    
    # 4 members, 2 alive (d0, d1) -> ISOLATED
    mv_2 = _make_mv(["d1"])
    assert compute_quorum("d0", slots_4, mv_2, True) == QuorumState.ISOLATED
    
    # 3 members, 2 alive -> QUORUM
    slots_3 = {"d0": 0, "d1": 1, "d2": 2}
    assert compute_quorum("d0", slots_3, mv_2, True) == QuorumState.QUORUM
    
    # 2 members, 1 alive (d0) -> ISOLATED
    slots_2 = {"d0": 0, "d1": 1}
    mv_none = _make_mv([])
    assert compute_quorum("d0", slots_2, mv_none, True) == QuorumState.ISOLATED
    
    # No formation active -> IDLE
    assert compute_quorum("d0", {}, mv, True) == QuorumState.IDLE
    
    # Not in formation -> IDLE
    assert compute_quorum("dx", slots_4, mv, True) == QuorumState.IDLE

def test_is_healthy():
    mv = _make_mv(["d1"])
    swarm = MockSwarm()
    swarm.registry.peers["d1"] = MockPeerState(battery=50.0, last_pos=100.0)
    
    assert is_healthy("d1", "d0", mv, swarm, anchor_min_battery=30.0, now=101.0) == True
    
    # Unhealthy: low battery
    swarm.registry.peers["d1"].battery_level = 20.0
    assert is_healthy("d1", "d0", mv, swarm, anchor_min_battery=30.0, now=101.0) == False
    
    # Unhealthy: stale position
    swarm.registry.peers["d1"].battery_level = 50.0
    assert is_healthy("d1", "d0", mv, swarm, anchor_min_battery=30.0, now=105.0) == False
    
    # Unhealthy: DEAD in membership
    mv.nodes["d1"].state = PeerState.DEAD
    assert is_healthy("d1", "d0", mv, swarm, anchor_min_battery=30.0, now=101.0) == False

def test_propose_anchor():
    slots = {"d1": 0, "d2": 1, "d3": 2}
    now = 100.0
    
    # Anchor healthy -> kept
    healthy = {"d1", "d2", "d3"}
    ans, t = propose_anchor(slots, healthy, None, None, 10.0, now)
    assert ans == "d1"
    assert t == now
    
    # Anchor lost -> lowest healthy ID proposed (d2)
    healthy = {"d2", "d3"}
    ans, t = propose_anchor(slots, healthy, None, None, 10.0, now)
    assert ans == "d2"
    assert t == now
    
    # Dwell prevents flapping: we previously proposed d3 at t=95 (not enough dwell)
    ans, t = propose_anchor(slots, healthy, "d3", 95.0, 10.0, now)
    assert ans == "d2"
    assert t == now # Resets timer for d2
    
    # Dwell prevents flapping: we previously proposed d2 at t=95
    ans, t = propose_anchor(slots, healthy, "d2", 95.0, 10.0, now)
    assert ans == "d2"
    assert t == 95.0 # Maintains timer
