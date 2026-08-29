from enum import Enum
import math
from typing import Tuple

class FormationType(str, Enum):
    LINE = "LINE"
    COLUMN = "COLUMN"
    V = "V"
    INVERTED_V = "INVERTED_V"
    DIAMOND = "DIAMOND"
    SQUARE = "SQUARE"
    RECTANGLE = "RECTANGLE"
    CIRCLE = "CIRCLE"
    ARC = "ARC"
    GRID = "GRID"
    CUSTOM = "CUSTOM"

class FormationManager:
    """
    Computes spatial offsets for drones in a swarm to maintain a specific formation.
    The leader (index 0) is at (0, 0, 0). Offsets are given in relative (x, y, z) meters.
    X is forward, Y is right, Z is down.
    """
    def __init__(self):
        self.spacing: float = 5.0
        self.formation_type: FormationType = FormationType.V

    def set_formation(self, formation: FormationType, spacing: float = 5.0) -> None:
        self.formation_type = formation
        self.spacing = spacing

    def get_offset(self, my_index: int, total_drones: int) -> Tuple[float, float, float]:
        if my_index == 0 or total_drones <= 1:
            return (0.0, 0.0, 0.0)

        t = self.formation_type
        s = self.spacing

        if t == FormationType.LINE:
            # Side by side on Y axis
            offset_y = (my_index - (total_drones - 1) / 2.0) * s
            return (0.0, offset_y, 0.0)

        elif t == FormationType.COLUMN:
            return (-my_index * s, 0.0, 0.0)

        elif t == FormationType.V:
            sign = 1 if my_index % 2 != 0 else -1
            tier = math.ceil(my_index / 2.0)
            return (-tier * s, sign * tier * s, 0.0)

        elif t == FormationType.INVERTED_V:
            sign = 1 if my_index % 2 != 0 else -1
            tier = math.ceil(my_index / 2.0)
            return (tier * s, sign * tier * s, 0.0)

        elif t == FormationType.CIRCLE:
            angle = (2 * math.pi / total_drones) * my_index
            radius = s * total_drones / (2 * math.pi)
            if radius < s: radius = s
            return (radius * math.cos(angle) - radius, radius * math.sin(angle), 0.0)
            
        elif t == FormationType.ARC:
            angle_spread = math.pi / 2  # 90 degrees
            angle_step = angle_spread / (total_drones - 1)
            angle = -angle_spread / 2 + my_index * angle_step
            radius = s * total_drones / angle_spread
            return (radius * math.cos(angle) - radius, radius * math.sin(angle), 0.0)

        elif t == FormationType.DIAMOND:
            if my_index == 1: return (-s, s, 0.0)
            if my_index == 2: return (-s, -s, 0.0)
            if my_index == 3: return (-2.0 * s, 0.0, 0.0)
            sign = 1 if my_index % 2 != 0 else -1
            tier = math.ceil(my_index / 2.0)
            return (-tier * s * 2, sign * s, 0.0)

        elif t in [FormationType.SQUARE, FormationType.GRID, FormationType.RECTANGLE]:
            cols = math.ceil(math.sqrt(total_drones))
            if t == FormationType.RECTANGLE:
                cols = max(2, total_drones // 2)
            row = my_index // cols
            col = my_index % cols
            return (-row * s, col * s, 0.0)
            
        return (0.0, 0.0, 0.0)
