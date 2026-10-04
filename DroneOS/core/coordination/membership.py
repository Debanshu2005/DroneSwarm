import time
from enum import Enum
from typing import Dict, Optional, List
from DroneOS.shared.utils.logger import setup_logger

logger = setup_logger("CoordinationMembership")

class PeerState(Enum):
    ALIVE = "ALIVE"
    SUSPECT = "SUSPECT"
    DEAD = "DEAD"

class MemberNode:
    def __init__(self, drone_id: str, clock=time.monotonic):
        self.drone_id = drone_id
        self.state: PeerState = PeerState.ALIVE
        self.last_heartbeat_time: float = clock()
        self.missed_beats: int = 0
        self.rejoin_stable_start: Optional[float] = None
        
        # Telemetry extracted from heartbeats
        self.battery: Optional[float] = None
        self.lat: Optional[float] = None
        self.lon: Optional[float] = None
        self.alt: Optional[float] = None
        
class MembershipView:
    def __init__(self, config: dict, clock=time.monotonic):
        self.clock = clock
        self.config = config
        self.nodes: Dict[str, MemberNode] = {}
        
        self.suspect_missed = int(config.get("suspect_missed_beats", 3))
        self.dead_missed = int(config.get("dead_missed_beats", 6))
        self.rejoin_stable_s = float(config.get("rejoin_stable_s", 10.0))
        
        self.last_eval_time = self.clock()
        self._last_seen_tracker = {}

    def update_from_peer(self, peer_id: str, last_seen: float, battery: Optional[float], lat: Optional[float], lon: Optional[float], alt: Optional[float]):
        if peer_id not in self.nodes:
            self.nodes[peer_id] = MemberNode(peer_id, clock=self.clock)
            self._last_seen_tracker[peer_id] = last_seen
            logger.info(f"[coord] New member discovered: {peer_id}")
            
        node = self.nodes[peer_id]
        
        if peer_id not in self._last_seen_tracker or last_seen != self._last_seen_tracker[peer_id]:
            # We received a new heartbeat
            self._last_seen_tracker[peer_id] = last_seen
            node.last_heartbeat_time = self.clock()
            node.missed_beats = 0
            
            if battery is not None: node.battery = battery
            if lat is not None: node.lat = lat
            if lon is not None: node.lon = lon
            if alt is not None: node.alt = alt
            
            if node.state == PeerState.DEAD:
                if node.rejoin_stable_start is None:
                    node.rejoin_stable_start = self.clock()
                    logger.info(f"[coord] {peer_id} started rejoin stabilization")
                elif self.clock() - node.rejoin_stable_start >= self.rejoin_stable_s:
                    node.state = PeerState.ALIVE
                    node.rejoin_stable_start = None
                    logger.info(f"[coord] {peer_id} fully rejoined as ALIVE")
            elif node.state == PeerState.SUSPECT:
                node.state = PeerState.ALIVE
                logger.info(f"[coord] {peer_id} recovered from SUSPECT to ALIVE")

    def evaluate_tick(self, hb_interval: float = 1.0):
        now = self.clock()
        dt = now - self.last_eval_time
        if dt < min(0.25, hb_interval / 2.0):
            return # evaluate roughly every second
        
        self.last_eval_time = now
        
        for peer_id, node in self.nodes.items():
            # If we haven't seen a heartbeat in over 1.5 seconds, consider it a missed beat (assuming ~1Hz heartbeats)
            if now - node.last_heartbeat_time > hb_interval * 1.5:
                # Increment missed beats based on elapsed time to be robust
                node.missed_beats = int((now - node.last_heartbeat_time) / hb_interval)
                
                if node.state == PeerState.ALIVE and node.missed_beats >= self.suspect_missed:
                    node.state = PeerState.SUSPECT
                    logger.warning(f"[coord] {peer_id} marked SUSPECT ({node.missed_beats} missed beats)")
                    
                if node.state in (PeerState.ALIVE, PeerState.SUSPECT) and node.missed_beats >= self.dead_missed:
                    node.state = PeerState.DEAD
                    node.rejoin_stable_start = None
                    logger.error(f"[coord] {peer_id} marked DEAD ({node.missed_beats} missed beats)")

    def get_alive_peers(self) -> List[str]:
        return [pid for pid, n in self.nodes.items() if n.state == PeerState.ALIVE]
