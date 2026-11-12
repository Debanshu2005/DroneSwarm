"""Perception data types and structure definitions."""

from dataclasses import dataclass
from typing import Any, Optional, Tuple


@dataclass
class Frame:
    """Captured camera frame with timestamp."""
    image: Any  # BGR numpy.ndarray
    timestamp: float


@dataclass
class Detection:
    """Bounding box detection in image pixel coordinates."""
    bbox_xyxy: Tuple[float, float, float, float]
    confidence: float
    label: str


@dataclass
class PoseSnapshot:
    """Snapshot of drone flight telemetry at time of frame capture."""
    latitude: float
    longitude: float
    altitude: float  # Relative altitude above home (m)
    heading: float   # Yaw angle in degrees [0, 360)
    roll: float      # Degrees
    pitch: float     # Degrees
    yaw: float       # Degrees
    timestamp: float
    gps_valid: bool
    armed_state: str
