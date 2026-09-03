import time
import math
from DroneOS1.shared.utils.logger import setup_logger
from DroneOS1.core.formation_manager import FormationManager, FormationType, convert_local_offset_to_global, global_offset_local_m
from DroneOS1.core.repulsion_field import compute_repulsion
from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction

logger = setup_logger("FormationEngine")

class FormationEngine:
    def __init__(self, swarm_manager, state_store, config=None):
        self.swarm_manager = swarm_manager
        self.state_store = state_store
        self.config = config
        self.form_mgr = FormationManager()
        self.last_total_drones = 0

    def compute_intent(self, current_telemetry, peer_telemetry, params) -> FlightIntent:
        f_type_str = params.get('type', 'V').upper()
        try:
            f_type = FormationType(f_type_str)
        except ValueError:
            logger.error(f"Invalid formation type: {f_type_str}")
            return None
            
        spacing = float(params.get('spacing', 2.0))
        speed = float(params.get('speed', 0.5))
        repulsion_radius_m = float(params.get('repulsion_radius_m', 2.5))
        self.form_mgr.set_formation(f_type, spacing)
        
        my_node_id = self.swarm_manager.identity.drone_id
        
        now = time.time()
        active_peers = [pid for pid, peer in self.swarm_manager.registry.peers.items() if (now - peer.last_seen) < 3.0]
        if my_node_id not in active_peers:
            active_peers.append(my_node_id)
        active_peers.sort() 
        
        my_index = active_peers.index(my_node_id)
        total_drones = len(active_peers)
        
        # Separation safety check
        if total_drones != self.last_total_drones:
            self.last_total_drones = total_drones
            points = [self.form_mgr.get_offset(i, total_drones) for i in range(total_drones)]
            min_dist = float('inf')
            for i in range(total_drones):
                for j in range(i+1, total_drones):
                    dist = math.sqrt((points[i][0]-points[j][0])**2 + (points[i][1]-points[j][1])**2)
                    if dist < min_dist:
                        min_dist = dist
            if min_dist < spacing / 2.0 and total_drones > 1:
                logger.warning(f"Formation safety check: minimum separation {min_dist:.1f}m is less than half spacing {spacing/2.0:.1f}m")
        
        anchor_id = active_peers[0]
        
        if not current_telemetry.gps_valid:
            logger.warning("Formation engine waiting: GPS invalid.")
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)

        if my_node_id == anchor_id:
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)
        else:
            anchor_peer = self.swarm_manager.registry.get_peer(anchor_id)
            anchor_pos_valid = (
                anchor_peer and 
                anchor_peer.last_position_time is not None and 
                (now - anchor_peer.last_position_time) < 3.0 and
                anchor_peer.lat is not None and anchor_peer.lon is not None and anchor_peer.alt is not None
            )
            
            if not anchor_pos_valid:
                logger.warning(f"Anchor {anchor_id} position stale or missing. Hovering.")
                return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)
            else:
                dx_north, dy_east, dz_down = self.form_mgr.get_offset(my_index, total_drones)
                
                # --- REPULSION FIELD LOGIC ---
                if current_telemetry.latitude is not None and current_telemetry.longitude is not None:
                    neighbor_offsets = []
                    for p_id in active_peers:
                        if p_id == my_node_id:
                            continue
                        peer = self.swarm_manager.registry.get_peer(p_id)
                        if (peer and peer.last_position_time is not None and 
                            (now - peer.last_position_time) < 3.0 and
                            peer.lat is not None and peer.lon is not None):
                            
                            p_north, p_east = global_offset_local_m(
                                current_telemetry.latitude, current_telemetry.longitude, peer.lat, peer.lon
                            )
                            neighbor_offsets.append((p_north, p_east))
                    
                    rep_n, rep_e = compute_repulsion(
                        neighbor_offsets, 
                        radius=repulsion_radius_m, 
                        gain=1.0, 
                        max_displacement=2.0
                    )
                    dx_north += rep_n
                    dy_east += rep_e

                target_lat, target_lon, target_alt = convert_local_offset_to_global(
                    anchor_peer.lat, anchor_peer.lon, anchor_peer.alt, dx_north, dy_east
                )
                
                if current_telemetry.latitude is None or current_telemetry.longitude is None:
                    return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)
                    
                error_north, error_east = global_offset_local_m(
                    current_telemetry.latitude, current_telemetry.longitude, target_lat, target_lon
                )
                
                # Proportional velocity control
                kp = 1.0
                if hasattr(self, 'config') and self.config and getattr(self.config, 'formation', None):
                    kp = float(self.config.formation.velocity_gain)
                    
                vx = error_north * kp
                vy = error_east * kp
                
                return FlightIntent(
                    IntentSource.FORMATION, 
                    IntentAction.MOVE_VELOCITY, 
                    ttl_seconds=1.0, 
                    params={"vx": vx, "vy": vy, "vz": 0.0, "yaw_rate": 0.0}
                )

