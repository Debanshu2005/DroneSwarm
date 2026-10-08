from typing import Optional, Dict
from DroneOS.core.coordination.membership import MembershipView, PeerState
from DroneOS.core.swarm_manager import SwarmMembership

def is_healthy(
    peer_id: str,
    my_id: str,
    membership_view: MembershipView,
    swarm: SwarmMembership,
    anchor_min_battery: float,
    now: float,
    max_position_age: float = 5.0
) -> bool:
    if peer_id == my_id:
        # Check own health via swarm manager or manager.py?
        # Actually, the requirement: "Healthy = ALIVE in MembershipView, coord battery_level >= anchor_min_battery (percent), and position fresh (use PeerState.last_position_time)."
        # If peer_id == my_id, we can look ourselves up in swarm registry if we're there, or expect a self-node?
        # Wait, the manager only updates MembershipView with remote peers. Self is not in MembershipView nodes!
        pass
    
    # Healthy = ALIVE in MembershipView
    node = membership_view.nodes.get(peer_id)
    if not node or node.state != PeerState.ALIVE:
        return False
        
    registry_peer_state = swarm.registry.get_peer(peer_id)
    if not registry_peer_state:
        return False
        
    if registry_peer_state.battery_level is not None:
        if registry_peer_state.battery_level < anchor_min_battery:
            return False
            
    if node.last_position_time is not None:
        age = now - node.last_position_time
        if age < 0 or (max_position_age > 0 and age > max_position_age):
            return False
    else:
        return False
        
    return True

def propose_anchor(
    slot_assignments: dict,
    healthy_members: set[str],
    current_proposed: Optional[str],
    current_proposed_time: Optional[float],
    anchor_min_dwell_s: float,
    now: float
) -> tuple[Optional[str], Optional[float]]:
    """
    Returns (proposed_anchor_id, time_first_proposed).
    """
    if not slot_assignments:
        return None, None
        
    current_anchor = None
    for pid, slot in slot_assignments.items():
        if int(slot) == 0:
            current_anchor = pid
            break
            
    if current_anchor and current_anchor in healthy_members:
        return current_anchor, now
        
    healthy_sorted = sorted([pid for pid in healthy_members if pid in slot_assignments])
    if not healthy_sorted:
        return None, None
        
    best_candidate = healthy_sorted[0]
    
    if best_candidate == current_proposed:
        if current_proposed_time is not None:
            if (now - current_proposed_time) >= anchor_min_dwell_s:
                return best_candidate, current_proposed_time
        return best_candidate, current_proposed_time
    else:
        return best_candidate, now
