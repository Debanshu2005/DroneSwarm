import math
from typing import Dict, List, Tuple

class FormationEngine:
    """
    Decentralized generic formation engine.
    Calculates target offsets (x, y, z) for a given drone index within a swarm.
    Offsets are relative to the swarm center or the 'leader' (index 0).
    """

    def __init__(self, spacing: float = 3.0):
        self.spacing = spacing

    def calculate_offset(self, pattern: str, drone_index: int, total_drones: int) -> Tuple[float, float, float]:
        """
        Returns (x_offset, y_offset, z_offset) in meters (NED).
        x is Forward/North, y is Right/East, z is Down.
        """
        pattern = pattern.upper()
        if total_drones <= 1:
            return (0.0, 0.0, 0.0)
            
        if pattern == "LINE":
            # Side by side on Y axis
            offset_y = (drone_index - (total_drones - 1) / 2.0) * self.spacing
            return (0.0, offset_y, 0.0)
            
        elif pattern == "COLUMN":
            # Follow the leader on X axis
            offset_x = -drone_index * self.spacing
            return (offset_x, 0.0, 0.0)
            
        elif pattern == "V":
            if drone_index == 0:
                return (0.0, 0.0, 0.0)
            # Alternate left/right, move backward
            side = 1 if drone_index % 2 != 0 else -1
            row = (drone_index + 1) // 2
            return (-row * self.spacing, side * row * self.spacing, 0.0)
            
        elif pattern == "INVERTED_V":
            if drone_index == 0:
                return (0.0, 0.0, 0.0)
            side = 1 if drone_index % 2 != 0 else -1
            row = (drone_index + 1) // 2
            return (row * self.spacing, side * row * self.spacing, 0.0)
            
        elif pattern == "DIAMOND":
            if drone_index == 0: return (0.0, 0.0, 0.0)
            if drone_index == 1: return (-self.spacing, -self.spacing, 0.0)
            if drone_index == 2: return (-self.spacing, self.spacing, 0.0)
            if drone_index == 3: return (-2.0 * self.spacing, 0.0, 0.0)
            # Fallback for > 4
            return self.calculate_offset("V", drone_index, total_drones)
            
        elif pattern in ["SQUARE", "RECTANGLE", "GRID"]:
            cols = math.ceil(math.sqrt(total_drones))
            if pattern == "RECTANGLE":
                cols = max(2, total_drones // 2)
            row = drone_index // cols
            col = drone_index % cols
            return (-row * self.spacing, col * self.spacing, 0.0)
            
        elif pattern == "CIRCLE":
            angle = (2 * math.pi / total_drones) * drone_index
            radius = self.spacing * total_drones / (2 * math.pi)
            if radius < self.spacing: radius = self.spacing
            return (radius * math.cos(angle) - radius, radius * math.sin(angle), 0.0)
            
        elif pattern == "ARC":
            if total_drones == 1: return (0.0, 0.0, 0.0)
            angle_spread = math.pi / 2  # 90 degrees
            angle_step = angle_spread / (total_drones - 1)
            angle = -angle_spread / 2 + drone_index * angle_step
            radius = self.spacing * total_drones / angle_spread
            return (radius * math.cos(angle) - radius, radius * math.sin(angle), 0.0)
            
        else:
            # Custom or Default
            return (0.0, 0.0, 0.0)
