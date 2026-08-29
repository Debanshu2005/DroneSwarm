"""
Natural-language command parsing and NED/global waypoint generation.

All local geometry is expressed in NED convention:
- x / north is positive forward toward geographic north.
- y / east is positive toward east.
- altitude above launch is represented as ``down_m = -altitude_m``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from DroneOS2.shared.nlp.nav_types import NavigationMode, SensorReport


EARTH_RADIUS_M = 6378137.0
DISTANCE_UNIT_PATTERN = r"(?:m|meter|meters|metre|metres)?"
DISTANCE_UNIT_WORD_PATTERN = r"(?:meters|meter|metres|metre|m)"
TIME_UNIT_PATTERN = r"(?:seconds|second|secs|sec|s)"
DIRECTION_UNIT_PATTERN = r"(cm|m|km|meter|meters|metre|metres|kilometer|kilometers|centimeter|centimeters)?"
DIRECTION_AXES = {
    "north": ("north", 1.0),
    "forward": ("north", 1.0),
    "forwards": ("north", 1.0),
    "ahead": ("north", 1.0),
    "south": ("north", -1.0),
    "back": ("north", -1.0),
    "backward": ("north", -1.0),
    "backwards": ("north", -1.0),
    "reverse": ("north", -1.0),
    "east": ("east", 1.0),
    "right": ("east", 1.0),
    "west": ("east", -1.0),
    "left": ("east", -1.0),
    "down": ("down", 1.0),
    "descend": ("down", 1.0),
    "lower": ("down", 1.0),
    "up": ("down", -1.0),
    "ascend": ("down", -1.0),
    "climb": ("down", -1.0),
}


class TaskAction(str, Enum):
    CIRCLE = "circle"
    GOTO = "goto"
    SQUARE = "square"
    TRIANGLE = "triangle"
    SPIRAL = "spiral"
    FIGURE_8 = "figure-8"
    GRID = "grid"
    HOVER = "hover"
    SET_MODE = "set_mode"
    HOLD = "hold"
    LAND = "land"
    RTL = "rtl"
    TAKEOFF = "takeoff"
    TAKEOFF_LAND = "takeoff_land"
    MOVE_RELATIVE = "move_relative"
    ARM = "arm"
    DISARM = "disarm"
    FORWARD = "forward"
    BACKWARD = "backward"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


class TargetFrame(str, Enum):
    GLOBAL_RELATIVE_ALT = "global_relative_alt"
    LOCAL_NED = "local_ned"


@dataclass(frozen=True)
class ParsedTask:
    action: TaskAction
    params: dict[str, float]
    raw_text: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskSequence:
    tasks: list[ParsedTask]
    raw_text: str
    notes: tuple[str, ...] = ()

    @property
    def action_names(self) -> list[str]:
        return [task.action.name for task in self.tasks]


@dataclass(frozen=True)
class LocalTarget:
    name: str
    north_m: float
    east_m: float
    down_m: float
    hold_s: float = 0.0
    yaw_deg: Optional[float] = None

    @property
    def altitude_m(self) -> float:
        return -self.down_m


@dataclass(frozen=True)
class GlobalTarget:
    name: str
    lat_deg: float
    lon_deg: float
    relative_alt_m: float
    hold_s: float = 0.0
    yaw_deg: Optional[float] = None


@dataclass(frozen=True)
class VehicleOrigin:
    local_north_m: float
    local_east_m: float
    local_down_m: float
    lat_deg: Optional[float] = None
    lon_deg: Optional[float] = None
    relative_alt_m: Optional[float] = None


@dataclass(frozen=True)
class TrajectoryPlan:
    action: TaskAction
    frame: TargetFrame
    local_targets: list[LocalTarget]
    global_targets: list[GlobalTarget]
    target_altitude_m: float
    description: str

    @property
    def count(self) -> int:
        return len(self.local_targets) if self.frame == TargetFrame.LOCAL_NED else len(self.global_targets)


from typing import Optional

def _normalize_distance_m(value: float, unit: Optional[str]) -> float:
    unit = (unit or "m").lower()
    if unit.startswith('c'):
        return value * 0.01
    elif unit.startswith('k'):
        return value * 1000.0
    return value

def parse_mission(
    text: str,
    default_altitude_m: float = 3.0,
    default_hover_s: float = 2.0,
) -> list[dict]:
    normalized = _normalize_text(text)
    tasks = []
    
    kv_matches = {}
    for match in re.finditer(r'\b([a-z_]+)\s*=\s*([-+]?\d+(?:\.\d+)?)\b', normalized):
        kv_matches[match.group(1)] = float(match.group(2))

    mode_name = _mode_request(normalized)
    if mode_name:
        tasks.append({'task': 'SET_MODE', 'mode': mode_name})
    action_text = _remove_mode_request_text(normalized) if mode_name else normalized

    has_takeoff = bool(re.search(r'\btakeoff\b', normalized))
    has_hover = bool(re.search(r'\bhover\b', normalized))
    has_land = bool(re.search(r'\bland\b', action_text))
    has_rtl = bool(re.search(r'\brtl\b|\breturn\b', action_text))
    has_hold = bool(re.search(r'\bhold\b|\bloiter\b', action_text))
    has_arm = bool(re.search(r'\barm\b', action_text))
    has_disarm = bool(re.search(r'\bdisarm\b', action_text))
    has_goto = bool(re.search(r'\bgoto\b|\bgo\b|\bmove\b|\bfly\b|\bclimb\b|\bascend\b|\bdescend\b|\blower\b', action_text))
    shape_match = re.search(r'\b(circle|square|triangle|spiral|figure-8|grid)\b', normalized)

    if has_arm and not has_disarm: tasks.append({'task': 'ARM'})
    if has_disarm: tasks.append({'task': 'DISARM'})

    # Handle standalone directional commands for continuous movement
    import difflib
    def _is_match(text: str, candidates: list[str]) -> bool:
        if text in candidates: return True
        return len(difflib.get_close_matches(text, candidates, n=1, cutoff=0.75)) > 0

    if _is_match(normalized, ["forward", "move forward", "go forward"]):
        tasks.append({'task': 'FORWARD'})
        has_goto = False
    elif _is_match(normalized, ["backward", "back", "move backward", "move back", "go backward", "go back"]):
        tasks.append({'task': 'BACKWARD'})
        has_goto = False
    elif _is_match(normalized, ["left", "move left", "go left"]):
        tasks.append({'task': 'LEFT'})
        has_goto = False
    elif _is_match(normalized, ["right", "move right", "go right"]):
        tasks.append({'task': 'RIGHT'})
        has_goto = False
    elif _is_match(normalized, ["up", "move up", "go up"]):
        tasks.append({'task': 'UP'})
        has_goto = False
    elif _is_match(normalized, ["down", "move down", "go down"]):
        tasks.append({'task': 'DOWN'})
        has_goto = False

    if has_takeoff:
        alt = kv_matches.get('h', kv_matches.get('altitude', kv_matches.get('height')))
        if alt is None:
            alt_match = re.search(r'(?:takeoff|to|height of|altitude of|altitude|height)\s*(?:is\s*)?(\d+(?:\.\d+)?)\s*(cm|m|km|meter|meters|kilometer|kilometers|centimeter|centimeters)\b', normalized)
            if alt_match:
                alt = _normalize_distance_m(float(alt_match.group(1)), alt_match.group(2))
        tasks.append({'task': 'TAKEOFF', 'alt': alt if alt is not None else default_altitude_m})
        if 'hover_s' in kv_matches and not has_hover:
            tasks.append({'task': 'HOVER', 'time': kv_matches['hover_s']})

    if has_hover:
        hover_s = kv_matches.get('hover_s', kv_matches.get('seconds'))
        if hover_s is None:
            time_match = re.search(r'(?:hover|for)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b', normalized)
            if time_match:
                hover_s = float(time_match.group(1))
        tasks.append({'task': 'HOVER', 'time': hover_s if hover_s is not None else default_hover_s})

    if has_goto and not shape_match:
        x = kv_matches.get('x', kv_matches.get('north'))
        y = kv_matches.get('y', kv_matches.get('east'))
        alt = kv_matches.get('h', kv_matches.get('altitude', kv_matches.get('height', kv_matches.get('high'))))
        if alt is None:
            alt_match = re.search(
                r'(\d+(?:\.\d+)?)\s*(cm|m|km|meter|meters|kilometer|kilometers|centimeter|centimeters)\s*(?:high|altitude|height)\b',
                normalized,
            )
            if alt_match:
                alt = _normalize_distance_m(float(alt_match.group(1)), alt_match.group(2))
        direction_north, direction_east, direction_down = _direction_offsets(normalized)
        
        if x is None:
            x = direction_north
        if y is None:
            y = direction_east
        if x == 0.0 and y == 0.0 and direction_down == 0.0 and alt is None:
            raise ValueError("goto command needs a distance or target coordinate")

        if direction_down != 0.0 and alt is None:
            tasks.append({
                'task': 'MOVE_RELATIVE',
                'dn': x if x is not None else 0.0,
                'de': y if y is not None else 0.0,
                'dd': direction_down,
            })
        else:
            task_dict = {
                'task': 'GOTO',
                'x': x if x is not None else 0.0,
                'y': y if y is not None else 0.0,
            }
            if alt is not None:
                task_dict['alt'] = alt
            tasks.append(task_dict)

    if shape_match:
        shape_name = shape_match.group(1)
        scale = kv_matches.get('r', kv_matches.get('radius', kv_matches.get('size', kv_matches.get('side'))))
        if scale is None:
            scale = _named_distance_m(normalized, ("radius", "size", "side"))
        if scale is None:
            scale_match = re.search(
                r'(\d+(?:\.\d+)?)\s*(cm|m|km|meter|meters|kilometer|kilometers|centimeter|centimeters)\b',
                normalized,
            )
            if scale_match:
                scale = _normalize_distance_m(float(scale_match.group(1)), scale_match.group(2))
        
        duration = kv_matches.get('hover_s', kv_matches.get('seconds', kv_matches.get('duration')))
        if duration is None:
            duration_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b', normalized)
            if duration_match:
                duration = float(duration_match.group(1))

        alt = kv_matches.get('h', kv_matches.get('altitude', kv_matches.get('height', kv_matches.get('high'))))
        if alt is None:
            alt = _named_distance_m(normalized, ("altitude", "height", "high"))
        if alt is None:
            alt_match = re.search(
                r'\bat\s+(\d+(?:\.\d+)?)\s*(cm|m|km|meter|meters|kilometer|kilometers|centimeter|centimeters)\b',
                normalized,
            )
            if alt_match:
                alt = _normalize_distance_m(float(alt_match.group(1)), alt_match.group(2))
        
        task_dict = {
            'task': 'FORMATION', 
            'shape': shape_name, 
            'scale': scale if scale is not None else 5.0, 
            'duration': duration if duration is not None else 10.0,
        }
        if alt is not None:
            task_dict['alt'] = alt
        
        if 'n' in kv_matches: task_dict['n'] = kv_matches['n']
        if 'passes' in kv_matches: task_dict['passes'] = kv_matches['passes']
        if 'turns' in kv_matches: task_dict['turns'] = kv_matches['turns']
            
        tasks.append(task_dict)
        
    if has_hold: tasks.append({'task': 'HOLD'})
    if has_land: tasks.append({'task': 'LAND'})
    if has_rtl: tasks.append({'task': 'RTL'})
        
    return tasks


def _direction_offsets(text: str) -> tuple[float, float, float]:
    north_m = 0.0
    east_m = 0.0
    down_m = 0.0
    directions = "|".join(sorted(DIRECTION_AXES, key=len, reverse=True))
    patterns = (
        rf"\b([-+]?\d+(?:\.\d+)?)\s*{DIRECTION_UNIT_PATTERN}\s*(?:to\s+the\s+)?({directions})\b",
        rf"\b({directions})\s*([-+]?\d+(?:\.\d+)?)\s*{DIRECTION_UNIT_PATTERN}\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if match.group(1) in DIRECTION_AXES:
                direction = match.group(1)
                value = float(match.group(2))
                unit = match.group(3) if match.lastindex and match.lastindex >= 3 else None
            else:
                value = float(match.group(1))
                unit = match.group(2)
                direction = match.group(3)
            axis, sign = DIRECTION_AXES[direction]
            distance_m = sign * _normalize_distance_m(value, unit)
            if axis == "north":
                north_m += distance_m
            elif axis == "east":
                east_m += distance_m
            else:
                down_m += distance_m
    return north_m, east_m, down_m


def _named_distance_m(text: str, names: tuple[str, ...]) -> Optional[float]:
    name_pattern = "|".join(re.escape(name) for name in names)
    patterns = (
        rf"\b(?:{name_pattern})\s*(?:=|is|of|to)?\s*([-+]?\d+(?:\.\d+)?)\s*{DIRECTION_UNIT_PATTERN}\b",
        rf"\b([-+]?\d+(?:\.\d+)?)\s*{DIRECTION_UNIT_PATTERN}\s*(?:{name_pattern})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match is None:
            continue
        value = float(match.group(1))
        unit = match.group(2)
        return _normalize_distance_m(value, unit)
    return None


def parse_task_sequence(
    text: str,
    default_altitude_m: float = 3.0,
    default_hover_s: float = 2.0,
) -> TaskSequence:
    dict_tasks = parse_mission(
        text,
        default_altitude_m=default_altitude_m,
        default_hover_s=default_hover_s,
    )
    if not dict_tasks:
        raise ValueError("no executable tasks found")
        
    tasks = []
    for d in dict_tasks:
        action_str = d['task']
        params = {}
        if action_str == 'FORMATION':
            action = TaskAction(d['shape'].lower())
            if 'scale' in d:
                params['r'] = d['scale']
                params['size'] = d['scale']
            if 'duration' in d:
                params['hover_s'] = d['duration']
            if 'n' in d:
                params['n'] = d['n']
            elif action == TaskAction.CIRCLE:
                params['n'] = 36.0
            if 'passes' in d:
                params['passes'] = d['passes']
            if 'turns' in d:
                params['turns'] = d['turns']
            if 'alt' in d:
                params['h'] = d['alt']
        elif action_str == 'SET_MODE':
            action = TaskAction.SET_MODE
            tasks.append(ParsedTask(action, {}, text, notes=(f"mode={d['mode']}",)))
            continue
        elif action_str == 'MOVE_RELATIVE':
            action = TaskAction.MOVE_RELATIVE
            params['dn'] = d.get('dn', 0.0)
            params['de'] = d.get('de', 0.0)
            params['dd'] = d.get('dd', 0.0)
        elif action_str == 'GOTO':
            action = TaskAction.GOTO
            params['north'] = d.get('x', 0.0)
            params['east'] = d.get('y', 0.0)
            if 'alt' in d:
                params['h'] = d['alt']
        else:
            action = TaskAction(action_str.lower())
            if 'alt' in d:
                params['h'] = d['alt']
            if 'time' in d:
                params['hover_s'] = d['time']
        
        tasks.append(ParsedTask(action, params, text))
        
    return TaskSequence(tasks, text)


def build_trajectory(
    task: ParsedTask,
    report: SensorReport,
    origin: VehicleOrigin,
) -> TrajectoryPlan:
    if task.action in {TaskAction.HOVER, TaskAction.SET_MODE, TaskAction.HOLD, TaskAction.LAND, TaskAction.RTL, TaskAction.ARM, TaskAction.DISARM}:
        frame = _frame_for_mode(report.mode)
        altitude_m = max(0.0, float(origin.relative_alt_m or 0.0))
        if task.action == TaskAction.HOVER:
            altitude_m = max(altitude_m, float(task.params.get("h", altitude_m)))
            description = f"hover duration={task.params.get('hover_s', 0.0):.1f}s"
        elif task.action == TaskAction.SET_MODE:
            mode_name = task.notes[0].removeprefix("mode=") if task.notes else "UNKNOWN"
            description = f"set mode {mode_name}"
        else:
            description = task.action.value
        return TrajectoryPlan(task.action, frame, [], [], altitude_m, description)

    if report.mode == NavigationMode.MODE_C_DEGRADED and task.action not in {
        TaskAction.HOLD,
        TaskAction.LAND,
        TaskAction.RTL,
    }:
        raise ValueError("cannot generate navigation trajectory with degraded position estimate")

    altitude_m = _positive(task.params.get("h", origin.relative_alt_m or 3.0), "altitude")
    if task.action == TaskAction.CIRCLE:
        local_targets = _circle_targets(task, origin, altitude_m)
        description = f"circle radius={task.params['r']:.1f}m altitude={altitude_m:.1f}m"
    elif task.action == TaskAction.MOVE_RELATIVE:
        altitude_m = max(0.0, float(origin.relative_alt_m or 0.0) - task.params.get('dd', 0.0))
        north = float(origin.local_north_m or 0.0) + task.params.get('dn', 0.0)
        east = float(origin.local_east_m or 0.0) + task.params.get('de', 0.0)
        local_targets = [LocalTarget("move-relative", north, east, -altitude_m)]
        description = (
            f"move relative north={task.params.get('dn', 0.0):.1f}m "
            f"east={task.params.get('de', 0.0):.1f}m down={task.params.get('dd', 0.0):.1f}m"
        )
    elif task.action == TaskAction.GOTO:
        local_targets = [_goto_target(task, origin, altitude_m)]
        description = (
            f"goto north={local_targets[0].north_m:.1f}m "
            f"east={local_targets[0].east_m:.1f}m altitude={altitude_m:.1f}m"
        )
    elif task.action == TaskAction.SQUARE:
        local_targets = _square_search_targets(task, origin, altitude_m)
        description = f"square search size={task.params['size']:.1f}m altitude={altitude_m:.1f}m"
    elif task.action == TaskAction.TRIANGLE:
        local_targets = _triangle_targets(task, origin, altitude_m)
        description = f"triangle size={task.params['size']:.1f}m altitude={altitude_m:.1f}m"
    elif task.action == TaskAction.GRID:
        local_targets = _grid_targets(task, origin, altitude_m)
        description = f"grid size={task.params['size']:.1f}m altitude={altitude_m:.1f}m"
    elif task.action == TaskAction.SPIRAL:
        local_targets = _spiral_targets(task, origin, altitude_m)
        description = f"spiral size={task.params['size']:.1f}m altitude={altitude_m:.1f}m"
    elif task.action == TaskAction.FIGURE_8:
        local_targets = _figure_8_targets(task, origin, altitude_m)
        description = f"figure-8 size={task.params['size']:.1f}m altitude={altitude_m:.1f}m"
    elif task.action in {TaskAction.TAKEOFF, TaskAction.TAKEOFF_LAND}:
        hover_s = task.params.get("hover_s", 0.0)
        suffix = " then land" if task.action == TaskAction.TAKEOFF_LAND else ""
        description = f"takeoff altitude={altitude_m:.1f}m hover={hover_s:.1f}s{suffix}"
        frame = _frame_for_mode(report.mode)
        return TrajectoryPlan(task.action, frame, [], [], altitude_m, description)
    else:
        frame = _frame_for_mode(report.mode)
        return TrajectoryPlan(task.action, frame, [], [], altitude_m, task.action.value)

    frame = _frame_for_mode(report.mode)
    global_targets: list[GlobalTarget] = []
    if frame == TargetFrame.GLOBAL_RELATIVE_ALT:
        if origin.lat_deg is None or origin.lon_deg is None:
            raise ValueError("GPS mode selected but current latitude/longitude are unavailable")
        global_targets = [
            _local_target_to_global(target, origin)
            for target in local_targets
        ]

    return TrajectoryPlan(
        action=task.action,
        frame=frame,
        local_targets=local_targets,
        global_targets=global_targets,
        target_altitude_m=altitude_m,
        description=description,
    )


def _circle_targets(task: ParsedTask, origin: VehicleOrigin, altitude_m: float) -> list[LocalTarget]:
    radius_m = _positive(task.params["r"], "radius")
    segments = max(8, min(120, int(task.params.get("n", 36))))
    center_n = origin.local_north_m
    center_e = origin.local_east_m
    targets: list[LocalTarget] = []
    for index in range(segments + 1):
        theta = 2.0 * math.pi * index / segments
        north = center_n + radius_m * math.cos(theta)
        east = center_e + radius_m * math.sin(theta)
        yaw_deg = math.degrees(theta + math.pi / 2.0)
        targets.append(LocalTarget(f"circle-{index:02d}", north, east, -altitude_m, yaw_deg=yaw_deg))
    return targets


def _goto_target(task: ParsedTask, origin: VehicleOrigin, altitude_m: float) -> LocalTarget:
    north_offset = _first_param(task.params, ("x", "n", "north"), 0.0)
    east_offset = _first_param(task.params, ("y", "e", "east"), 0.0)
    hold_s = _first_param(task.params, ("hold", "hold_s"), 0.0)
    return LocalTarget(
        "goto",
        origin.local_north_m + north_offset,
        origin.local_east_m + east_offset,
        -altitude_m,
        hold_s=hold_s,
    )


def _square_search_targets(
    task: ParsedTask,
    origin: VehicleOrigin,
    altitude_m: float,
) -> list[LocalTarget]:
    size_m = _positive(task.params["size"], "size")
    center_n = origin.local_north_m
    center_e = origin.local_east_m

    passes = max(0, int(task.params.get("passes", 0)))
    if "search" not in task.raw_text.lower() or passes <= 0:
        corners = [
            (center_n + size_m, center_e),
            (center_n + size_m, center_e + size_m),
            (center_n, center_e + size_m),
            (center_n, center_e),
        ]
        return [
            LocalTarget(
                f"square-{index}",
                north,
                east,
                -altitude_m,
            )
            for index, (north, east) in enumerate(corners, start=1)
        ]

    return _grid_targets(task, origin, altitude_m)


def _triangle_targets(task: ParsedTask, origin: VehicleOrigin, altitude_m: float) -> list[LocalTarget]:
    size_m = _positive(task.params["size"], "size")
    height_m = size_m * math.sqrt(3.0) / 2.0
    corners = [
        (origin.local_north_m + size_m, origin.local_east_m),
        (origin.local_north_m + size_m / 2.0, origin.local_east_m + height_m),
        (origin.local_north_m, origin.local_east_m),
    ]
    return [
        LocalTarget(f"triangle-{index}", north, east, -altitude_m)
        for index, (north, east) in enumerate(corners, start=1)
    ]


def _grid_targets(task: ParsedTask, origin: VehicleOrigin, altitude_m: float) -> list[LocalTarget]:
    size_m = _positive(task.params["size"], "size")
    half = size_m / 2.0
    passes = max(1, int(task.params.get("passes", 4)))
    lane_spacing = size_m / passes
    lanes: list[LocalTarget] = []
    for index in range(passes + 1):
        east = origin.local_east_m - half + index * lane_spacing
        if index % 2 == 0:
            north_values = (origin.local_north_m - half, origin.local_north_m + half)
        else:
            north_values = (origin.local_north_m + half, origin.local_north_m - half)
        for lane_end, north in enumerate(north_values):
            lanes.append(LocalTarget(f"grid-{index}-{lane_end}", north, east, -altitude_m))
    return lanes


def _spiral_targets(task: ParsedTask, origin: VehicleOrigin, altitude_m: float) -> list[LocalTarget]:
    radius_m = _positive(task.params["size"], "size")
    turns = max(1.0, min(6.0, float(task.params.get("turns", 2.0))))
    segments = max(16, min(120, int(task.params.get("n", 48))))
    targets: list[LocalTarget] = []
    for index in range(1, segments + 1):
        fraction = index / segments
        radius = radius_m * fraction
        theta = 2.0 * math.pi * turns * fraction
        north = origin.local_north_m + radius * math.cos(theta)
        east = origin.local_east_m + radius * math.sin(theta)
        targets.append(LocalTarget(f"spiral-{index:02d}", north, east, -altitude_m))
    return targets


def _figure_8_targets(task: ParsedTask, origin: VehicleOrigin, altitude_m: float) -> list[LocalTarget]:
    radius_m = _positive(task.params["size"], "size")
    segments = max(24, min(120, int(task.params.get("n", 72))))
    targets: list[LocalTarget] = []
    for index in range(segments + 1):
        theta = 2.0 * math.pi * index / segments
        north = origin.local_north_m + radius_m * math.sin(theta)
        east = origin.local_east_m + radius_m * math.sin(theta) * math.cos(theta)
        yaw_deg = math.degrees(theta)
        targets.append(LocalTarget(f"figure-8-{index:02d}", north, east, -altitude_m, yaw_deg=yaw_deg))
    return targets


def _local_target_to_global(target: LocalTarget, origin: VehicleOrigin) -> GlobalTarget:
    if origin.lat_deg is None or origin.lon_deg is None:
        raise ValueError("Cannot convert local to global without origin GPS coordinates")
    origin_lat = float(origin.lat_deg)
    origin_lon = float(origin.lon_deg)
    delta_n = target.north_m - origin.local_north_m
    delta_e = target.east_m - origin.local_east_m
    lat, lon = offset_lat_lon(origin_lat, origin_lon, delta_n, delta_e)
    return GlobalTarget(
        target.name,
        lat,
        lon,
        target.altitude_m,
        hold_s=target.hold_s,
        yaw_deg=target.yaw_deg,
    )


def offset_lat_lon(
    origin_lat_deg: float,
    origin_lon_deg: float,
    north_m: float,
    east_m: float,
) -> tuple[float, float]:
    lat_rad = math.radians(origin_lat_deg)
    cos_lat = max(1e-6, abs(math.cos(lat_rad)))
    d_lat = north_m / EARTH_RADIUS_M
    d_lon = east_m / (EARTH_RADIUS_M * cos_lat)
    return (
        origin_lat_deg + math.degrees(d_lat),
        origin_lon_deg + math.degrees(d_lon),
    )


def local_distance_m(a: LocalTarget, north_m: float, east_m: float, down_m: float) -> float:
    return math.sqrt((a.north_m - north_m) ** 2 + (a.east_m - east_m) ** 2 + (a.down_m - down_m) ** 2)


def global_distance_m(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float:
    lat1 = math.radians(lat1_deg)
    lat2 = math.radians(lat2_deg)
    d_lat = lat2 - lat1
    d_lon = math.radians(lon2_deg - lon1_deg)
    hav = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return EARTH_RADIUS_M * 2.0 * math.atan2(math.sqrt(hav), math.sqrt(1.0 - hav))


def _frame_for_mode(mode: NavigationMode) -> TargetFrame:
    if mode == NavigationMode.MODE_A_GPS:
        return TargetFrame.GLOBAL_RELATIVE_ALT
    return TargetFrame.LOCAL_NED


def _parse_key_values(text: str) -> dict[str, float]:
    params: dict[str, float] = {}
    for match in re.finditer(r"\b([a-z_]+)\s*=\s*([-+]?\d+(?:\.\d+)?)", text):
        params[match.group(1)] = float(match.group(2))
    return params


def _strip_command_prefix(text: str) -> str:
    return re.sub(r"^\s*\[cmd\]\s*", "", text, flags=re.IGNORECASE).strip()


def _mode_request(text: str) -> Optional[str]:
    match = re.search(
        r"\b(?:switch|swich|change|set)\s+(?:flight\s+)?mode\s+(?:to\s+)?([a-z0-9_ -]+)\b",
        text,
    )
    if match is None:
        match = re.search(r"\bmode\s+([a-z0-9_ -]+)\b", text)
    if match is None:
        return None
    raw_mode = re.split(r"\s*(?:,|\bthen\b|\band\b)\s*", match.group(1), maxsplit=1)[0].strip(" .")
    return _normalize_mode_name(raw_mode)


def _remove_mode_request_text(text: str) -> str:
    return re.sub(
        r"\b(?:(?:switch|swich|change|set)\s+(?:flight\s+)?mode|mode)\s+(?:to\s+)?[a-z0-9_ -]+?(?=\s*(?:,|\bthen\b|\band\b|$))",
        "",
        text,
        count=1,
    )


def _normalize_mode_name(raw_mode: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "", raw_mode.lower())
    aliases = {
        "guided": "GUIDED",
        "guidednogps": "GUIDED_NOGPS",
        "althold": "ALT_HOLD",
        "altitudehold": "ALT_HOLD",
        "loiter": "LOITER",
        "stabilize": "STABILIZE",
        "land": "LAND",
        "rtl": "RTL",
        "returntolaunch": "RTL",
        "poshold": "POSHOLD",
        "positionhold": "POSHOLD",
        "offboard": "OFFBOARD",
        "hold": "HOLD",
        "altctl": "ALTCTL",
        "posctl": "POSCTL",
        "manual": "MANUAL",
    }
    return aliases.get(compact, raw_mode.strip().upper().replace("-", "_").replace(" ", "_"))





def command_guide() -> str:
    return (
        "Command guide:\n"
        "  Natural English examples:\n"
        "    take off to 3 meters, hover for two seconds, and land\n"
        "    fly in a 5 meter radius circle at 3 meter altitude\n"
        "    do a 10 meter square search pattern at 3 meters\n"
        "    fly a triangle with 6 meter sides at 3 meters\n"
        "    fly a figure eight size 5 at 3 meters\n"
        "    go 10 meters north and 5 meters east at 3 meters altitude\n"
        "    move 5 meters forward and 2 meters up\n"
        "    go 5 meters high\n"
        "    hold position\n"
        "    switch mode to guided\n"
        "    switch mode to althold\n"
        "    land now\n"
        "    return to launch\n"
        "  Compact command forms:\n"
        "    takeoff h=3 hover_s=2\n"
        "    circle r=5 h=3 n=36\n"
        "    square size=10 h=3 passes=4\n"
        "    triangle size=6 h=3\n"
        "    grid size=10 h=3 passes=4\n"
        "    spiral size=10 h=3 turns=3\n"
        "    figure-8 size=5 h=3\n"
        "    goto x=10 y=5 h=3\n"
        "    climb 2 | descend 1 | lower 50cm\n"
        "    mode guided | mode alt_hold\n"
        "    hold | land | rtl\n"
        "  Parameter dictionary:\n"
        "    h / altitude / height: target altitude above launch in meters\n"
        "    r / radius: circle radius in meters\n"
        "    n: number of circle waypoints\n"
        "    x / north: north offset in meters\n"
        "    y / east: east offset in meters\n"
        "    forward/back/left/right/up/down: relative movement words\n"
        "    size / side: square side length in meters\n"
        "    passes: lawnmower search passes inside square\n"
        "    passes: lawnmower search passes inside square\n"
        "    hover_s / seconds: hover duration in seconds\n"
    )


def _normalize_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = normalized.replace("take-off", "takeoff").replace("take off", "takeoff")
    normalized = re.sub(r'\btakeof\b', 'takeoff', normalized)
    normalized = normalized.replace("secobds", "seconds").replace("secondes", "seconds")
    normalized = normalized.replace("circular", "circle")
    normalized = normalized.replace("figure eight", "figure-8").replace("figure 8", "figure-8")
    for word, value in _NUMBER_WORDS.items():
        normalized = re.sub(rf"\b{word}\b", str(value), normalized)
    return normalized

_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
}


def _duration_seconds(text: str) -> Optional[float]:
    keyed = _parse_key_values(text)
    for name in ("hover_s", "seconds", "duration", "wait"):
        if name in keyed:
            return keyed[name]
    patterns = (
        rf"\b(?:hover|hold|wait|pause)\s*(?:for)?\s*([-+]?\d+(?:\.\d+)?)\s*{TIME_UNIT_PATTERN}\b",
        rf"\b([-+]?\d+(?:\.\d+)?)\s*{TIME_UNIT_PATTERN}\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def _takeoff_altitude(text: str, default_altitude_m: float) -> float:
    altitude = _named_distance(text, ("altitude", "height", "alt", "h"))
    if altitude is not None:
        return altitude
    match = re.search(
        rf"\b(?:takeoff|lift off|climb)\s*(?:to)?\s*([-+]?\d+(?:\.\d+)?)\s*{DISTANCE_UNIT_PATTERN}\b",
        text,
    )
    if match:
        return float(match.group(1))
    return default_altitude_m


def _named_distance(text: str, names: tuple[str, ...]) -> Optional[float]:
    for name in names:
        patterns = (
            rf"\b{name}\s*(?:=|is|of|to)?\s*([-+]?\d+(?:\.\d+)?)\s*{DISTANCE_UNIT_PATTERN}\b",
            rf"\b([-+]?\d+(?:\.\d+)?)\s*{DISTANCE_UNIT_PATTERN}\s+{name}\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
    return None


def _numbers(text: str) -> list[float]:
    return [
        float(match.group(1))
        for match in re.finditer(
            rf"([-+]?\d+(?:\.\d+)?)\s*{DISTANCE_UNIT_PATTERN}\b",
            text,
        )
    ]


def _direction_distance(text: str, direction: str) -> Optional[float]:
    patterns = (
        rf"\b([-+]?\d+(?:\.\d+)?)\s*{DISTANCE_UNIT_PATTERN}\s+{direction}\b",
        rf"\b{direction}\s*([-+]?\d+(?:\.\d+)?)\s*{DISTANCE_UNIT_PATTERN}\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def _first_or(default: float, values: list[float]) -> float:
    return values[0] if values else default


def _first_param(params: dict[str, float], names: tuple[str, ...], default: float) -> float:
    for name in names:
        if name in params:
            return params[name]
    return default


def _positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def parse_swarm_target(text: str, active_sysids: list[int]) -> tuple[list[int], str]:
    text = text.strip()
    target_match = re.match(r"^(all|drone(\d+)|sysid:(\d+)):\s*(.*)", text, re.IGNORECASE)
    if target_match:
        prefix = target_match.group(1).lower()
        remainder = target_match.group(4).strip()
        if prefix == "all":
            return active_sysids, remainder
        elif prefix.startswith("drone"):
            sysid = int(target_match.group(2))
            if sysid in active_sysids:
                return [sysid], remainder
            return [], remainder
        elif prefix.startswith("sysid:"):
            sysid = int(target_match.group(3))
            if sysid in active_sysids:
                return [sysid], remainder
            return [], remainder
            
    if not active_sysids:
        return [], text
    return [min(active_sysids)], text
