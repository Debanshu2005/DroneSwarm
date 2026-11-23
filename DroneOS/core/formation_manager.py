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

def normalize_drone_id(drone_id: str) -> str:
    """Canonical normalization for drone IDs (lowercase, stripped)."""
    return str(drone_id).strip().lower()

def compute_min_formation_spacing(flight_cfg: any = None) -> float:
    """
    Computes minimum safe formation spacing: 1.5 * max(min_formation_separation_m, min_horizontal_distance).
    Shared between FlightManager, Healing, and TerminalController.
    """
    min_sep = 8.0
    min_ca = 2.0
    if flight_cfg is not None:
        try:
            formation = getattr(flight_cfg, "formation", None)
            if formation is not None and hasattr(formation, "min_formation_separation_m"):
                min_sep = float(formation.min_formation_separation_m)
            ca = getattr(flight_cfg, "collision_avoidance", None)
            if ca is not None and hasattr(ca, "min_horizontal_distance"):
                min_ca = float(ca.min_horizontal_distance)
        except (TypeError, ValueError):
            pass
    return 1.5 * max(min_sep, min_ca)

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

def global_offset_local_m(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> Tuple[float, float]:
    """
    Computes the local NED offset (dx_north, dy_east) in meters from one global 
    position to another, using a flat-earth approximation. This is the inverse 
    of convert_local_offset_to_global.
    """
    EARTH_RADIUS_M = 6371000.0
    dlat = to_lat - from_lat
    dlon = to_lon - from_lon
    
    dx_north = dlat * (math.pi / 180.0) * EARTH_RADIUS_M
    dy_east = dlon * (math.pi / 180.0) * EARTH_RADIUS_M * math.cos(math.radians(from_lat))
    
    return (dx_north, dy_east)

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

def build_slot_assignments(drone_ids: list[str]) -> dict[str, int]:
    """
    Builds a slot assignment mapping for a list of drone IDs.
    Returns a dict mapping drone_id -> slot index.
    Slot 0 is the anchor.

    MUST stay consistent with mobile/src/utils/formation.js buildFormationParams().
    Both sort IDs and assign slots by ascending sorted index.
    Normalizes drone IDs to lowercase so casing differences ('Drone1' vs 'drone1')
    do not cause duplicate entries or slot assignment mismatches.
    """
    members = sorted(set(normalize_drone_id(d) for d in drone_ids))
    return {drone_id: index for index, drone_id in enumerate(members)}
