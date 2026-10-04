import asyncio
import time
from typing import Dict, Any
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.core.swarm_manager import SwarmMembership
from DroneOS.core.coordination.membership import MembershipView, PeerState

logger = setup_logger("CoordinationManager")

class CoordinationManager:
    def __init__(self, swarm_manager: SwarmMembership, flight_cfg: Any, heartbeat_interval: float = 1.0):
        self.swarm = swarm_manager
        self.hb_interval = heartbeat_interval
        self.config_dict = getattr(flight_cfg, "coordination", {}) if flight_cfg else {}
        if not isinstance(self.config_dict, dict):
            try:
                self.config_dict = dict(self.config_dict)
            except:
                self.config_dict = {}
                
        self.enabled = str(self.config_dict.get("enabled", "false")).lower() == "true"
        self.mode = self.config_dict.get("mode", "advisory")
        
        self.membership = MembershipView(self.config_dict)
        self.is_running = False

    async def run(self):
        try:
            if not self.enabled:
                logger.info("[coord] Coordination module disabled via config.")
                return
    
            logger.info(f"[coord] Coordination module starting in {self.mode} mode.")
            self.is_running = True
            
            # Use explicit heartbeat_interval for poll interval
            poll_interval = max(0.25, min(0.5, self.hb_interval / 2.0))
            
            while self.is_running:
                try:
                    await asyncio.sleep(poll_interval)
                    
                    # 1. Update membership view from swarm registry
                    for peer_id in self.swarm.registry.get_all_peers():
                        peer_state = self.swarm.registry.get_peer(peer_id)
                        if peer_state:
                            self.membership.update_from_peer(
                                peer_id=peer_id,
                                last_seen=peer_state.last_seen,
                                battery=peer_state.battery_level,
                                lat=peer_state.lat,
                                lon=peer_state.lon,
                                alt=peer_state.alt
                            )
                            
                    # 2. Evaluate hysteresis / timeouts
                    self.membership.evaluate_tick(self.hb_interval)
                    
                except asyncio.CancelledError:
                    logger.info("[coord] Coordination manager task cancelled.")
                    break
        except Exception as e:
            logger.error(f"[coord] Unhandled exception in coordination loop: {e}")
            self.is_running = False
            self.enabled = False
            logger.error("[coord] Coordination module has entered FATAL FAULT state and disabled itself.")

    def stop(self):
        self.is_running = False
