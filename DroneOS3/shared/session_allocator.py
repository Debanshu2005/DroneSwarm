"""
Re-export of PersistentSessionAllocator for DroneOS3.
"""

from gfs.session_allocator import (
    PersistentSessionAllocator,
    SessionAllocationError,
    CorruptedSessionStateError,
    ConcurrentAllocationTimeoutError,
)

__all__ = [
    "PersistentSessionAllocator",
    "SessionAllocationError",
    "CorruptedSessionStateError",
    "ConcurrentAllocationTimeoutError",
]

