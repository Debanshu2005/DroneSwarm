from enum import Enum
from DroneOS.core.coordination.membership import MembershipView, PeerState

class QuorumState(Enum):
    IDLE = "IDLE"
    QUORUM = "QUORUM"
    ISOLATED = "ISOLATED"

def compute_quorum(
    my_id: str,
    slot_assignments: dict,
    membership_view: MembershipView,
    quorum_required: bool
) -> QuorumState:
    """
    membership universe = the member IDs in the CURRENT live formation's slot_assignments
    """
    if not slot_assignments or my_id not in slot_assignments:
        return QuorumState.IDLE
        
    if not quorum_required:
        return QuorumState.QUORUM
        
    universe_size = len(slot_assignments)
    alive_count = 0
    
    for member_id in slot_assignments:
        if member_id == my_id:
            alive_count += 1
        else:
            node = membership_view.nodes.get(member_id)
            if node and node.state == PeerState.ALIVE:
                alive_count += 1
                
    if alive_count > (universe_size / 2.0):
        return QuorumState.QUORUM
    else:
        return QuorumState.ISOLATED
