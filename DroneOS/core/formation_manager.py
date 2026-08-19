from enum import Enum
import math
from typing import Tuple

class FormationType(str, Enum):
    LINE = "LINE"
    V = "V"
    DIAMOND = "DIAMOND"
    SQUARE = "SQUARE"
    CIRCLE = "CIRCLE"

class FormationManager:
    """
    Computes spatial offsets for drones in a swarm to maintain a specific formation.
    The leader is at (0, 0, 0). Offsets are given in relative (x, y, z) meters.
    """
    def __init__(self):
        self.spacing: float = 5.0
        self.formation_type: FormationType = FormationType.V

    def set_formation(self, formation: FormationType, spacing: float = 5.0) -> None:
        self.formation_type = formation
        self.spacing = spacing

    def get_offset(self, my_index: int, total_drones: int) -> Tuple[float, float, float]:
        """
        Calculate relative offset for a drone based on its index (0 is leader).
        X is forward, Y is right, Z is down.
        """
        if my_index == 0:
            return (0.0, 0.0, 0.0)

        if self.formation_type == FormationType.LINE:
            # Line abreast (side by side)
            # 1: right, 2: left, 3: right further, 4: left further
            sign = 1 if my_index % 2 != 0 else -1
            y_offset = sign * math.ceil(my_index / 2.0) * self.spacing
            return (0.0, y_offset, 0.0)

        elif self.formation_type == FormationType.V:
            # V formation trailing behind leader
            sign = 1 if my_index % 2 != 0 else -1
            tier = math.ceil(my_index / 2.0)
            x_offset = -tier * self.spacing
            y_offset = sign * tier * self.spacing
            return (x_offset, y_offset, 0.0)

        elif self.formation_type == FormationType.DIAMOND:
            # Simple diamond for 4 drones
            if my_index == 1: return (-self.spacing, self.spacing, 0.0)
            if my_index == 2: return (-self.spacing, -self.spacing, 0.0)
            if my_index == 3: return (-2.0 * self.spacing, 0.0, 0.0)
            return (0.0, 0.0, 0.0) # Fallback

        elif self.formation_type == FormationType.SQUARE:
            # Simple square for 4 drones
            if my_index == 1: return (0.0, self.spacing, 0.0)
            if my_index == 2: return (-self.spacing, 0.0, 0.0)
            if my_index == 3: return (-self.spacing, self.spacing, 0.0)
            return (0.0, 0.0, 0.0) # Fallback

        elif self.formation_type == FormationType.CIRCLE:
            # Circle around leader
            if total_drones > 1:
                angle_step = (2.0 * math.pi) / (total_drones - 1)
                angle = (my_index - 1) * angle_step
                x_offset = self.spacing * math.cos(angle)
                y_offset = self.spacing * math.sin(angle)
                return (x_offset, y_offset, 0.0)
            
        return (0.0, 0.0, 0.0)
