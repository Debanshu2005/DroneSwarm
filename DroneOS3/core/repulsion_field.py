import math
from typing import List, Tuple

def compute_repulsion(
    neighbor_offsets: List[Tuple[float, float]], 
    radius: float, 
    gain: float, 
    max_displacement: float
) -> Tuple[float, float]:
    """
    Computes a positional displacement vector to avoid nearby neighbors.
    
    Args:
        neighbor_offsets: List of (dx_north, dy_east) offsets from this drone to each neighbor in meters.
        radius: The repulsion radius in meters. Neighbors at or beyond this distance exert zero force.
        gain: The gain multiplier for the repulsive force.
        max_displacement: The maximum allowed displacement magnitude to clamp the result.
        
    Returns:
        (repulsion_n, repulsion_e) displacement to add to the target position.
    """
    total_n = 0.0
    total_e = 0.0
    epsilon = 1e-6
    
    for dx_north, dy_east in neighbor_offsets:
        dist = math.sqrt(dx_north**2 + dy_east**2)
        if dist >= radius:
            continue
            
        dist = max(dist, epsilon)
        
        # Repulsion magnitude: (1/d - 1/r)
        magnitude = gain * (1.0 / dist - 1.0 / radius)
        
        # Direction vector pointing away from the neighbor
        dir_n = -dx_north / dist
        dir_e = -dy_east / dist
        
        total_n += magnitude * dir_n
        total_e += magnitude * dir_e
        
    # Clamp total displacement
    total_dist = math.sqrt(total_n**2 + total_e**2)
    if total_dist > max_displacement:
        scale = max_displacement / total_dist
        total_n *= scale
        total_e *= scale
        
    return total_n, total_e
