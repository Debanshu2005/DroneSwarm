from enum import Enum
import math
from typing import Tuple

class FormationType(str, Enum):
    LINE = "LINE"
    COLUMN = "COLUMN"
    V = "V"
    WEDGE = "WEDGE"
    ECHELON_LEFT = "ECHELON_LEFT"
    ECHELON_RIGHT = "ECHELON_RIGHT"
    DIAMOND = "DIAMOND"
    CIRCLE = "CIRCLE"
    SQUARE = "SQUARE"
    GRID = "GRID"

def convert_local_offset_to_global(anchor_lat: float, anchor_lon: float, anchor_alt: float, dx_north: float, dy_east: float) -> Tuple[float, float, float]:
    """
    Converts a local NED offset (dx_north, dy_east) in meters to global (lat, lon, alt)
    relative to an anchor position, using a flat-earth approximation.
    
    dx_north maps to X (forward/North).
    dy_east maps to Y (right/East).
    """
    EARTH_RADIUS_M = 6371000.0
    dlat = dx_north / EARTH_RADIUS_M * (180.0 / math.pi)
    dlon = dy_east / (EARTH_RADIUS_M * math.cos(math.radians(anchor_lat))) * (180.0 / math.pi)
    target_lat = anchor_lat + dlat
    target_lon = anchor_lon + dlon
    target_alt = anchor_alt # Maintain anchor's altitude offset
    return (target_lat, target_lon, target_alt)

class FormationManager:
    """
    Computes spatial offsets for drones in a swarm to maintain a specific formation.
    The anchor (usually leader, index 0) is at (0, 0, 0). Offsets are given in relative (x, y, z) meters.
    X is forward (North), Y is right (East), Z is down.
    """
    def __init__(self):
        self.spacing: float = 5.0
        self.formation_type: FormationType = FormationType.V

    def set_formation(self, formation: FormationType, spacing: float = 5.0) -> None:
        self.formation_type = formation
        self.spacing = spacing

    def get_offset(self, my_index: int, total_drones: int) -> Tuple[float, float, float]:
        if total_drones <= 0:
            return (0.0, 0.0, 0.0)

        t = self.formation_type
        s = self.spacing

        # Assembly formations: everyone is a point on the shape (including index 0)
        if t == FormationType.CIRCLE:
            # N points evenly spaced by angle around a circle of radius self.spacing
            # Start angle is 0 (North, X-axis)
            angle = (2.0 * math.pi / total_drones) * my_index
            return (s * math.cos(angle), s * math.sin(angle), 0.0)
            
        elif t == FormationType.SQUARE:
            # N points evenly spaced by walked perimeter distance around a square of side self.spacing
            perimeter = 4.0 * s
            dist = my_index * (perimeter / total_drones)
            # Start at NE corner (+s/2, +s/2) and walk clockwise
            # Side 1 (East side): (s/2, s/2) to (-s/2, s/2)
            if dist < s:
                return (s/2.0 - dist, s/2.0, 0.0)
            # Side 2 (South side): (-s/2, s/2) to (-s/2, -s/2)
            elif dist < 2.0 * s:
                return (-s/2.0, s/2.0 - (dist - s), 0.0)
            # Side 3 (West side): (-s/2, -s/2) to (s/2, -s/2)
            elif dist < 3.0 * s:
                return (-s/2.0 + (dist - 2.0 * s), -s/2.0, 0.0)
            # Side 4 (North side): (s/2, -s/2) to (s/2, s/2)
            else:
                return (s/2.0, -s/2.0 + (dist - 3.0 * s), 0.0)

        # Squadron formations: index 0 is at (0,0,0), others offset behind/beside
        if my_index == 0 or total_drones <= 1:
            return (0.0, 0.0, 0.0)

        if t == FormationType.LINE:
            sign = 1 if my_index % 2 != 0 else -1
            y_offset = sign * math.ceil(my_index / 2.0) * s
            return (0.0, y_offset, 0.0)

        elif t == FormationType.COLUMN:
            return (-my_index * s, 0.0, 0.0)

        elif t in (FormationType.V, FormationType.WEDGE):
            sign = 1 if my_index % 2 != 0 else -1
            tier = math.ceil(my_index / 2.0)
            return (-tier * s, sign * tier * s, 0.0)

        elif t == FormationType.ECHELON_LEFT:
            return (-my_index * s, -my_index * s, 0.0)

        elif t == FormationType.ECHELON_RIGHT:
            return (-my_index * s, my_index * s, 0.0)

        elif t == FormationType.DIAMOND:
            if my_index == 1: return (-s, s, 0.0)
            if my_index == 2: return (-s, -s, 0.0)
            if my_index == 3: return (-2.0 * s, 0.0, 0.0)
            sign = 1 if my_index % 2 != 0 else -1
            tier = math.ceil((my_index - 1) / 2.0) # Adjust tier for > 3
            return (-tier * s * 2, sign * s, 0.0)

        elif t == FormationType.GRID:
            cols = math.ceil(math.sqrt(total_drones))
            row = my_index // cols
            col = my_index % cols
            return (-row * s, col * s, 0.0)
            
        return (0.0, 0.0, 0.0)
