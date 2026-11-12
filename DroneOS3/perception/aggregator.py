"""Track aggregator merging multi-frame detections into stable ground markers."""

from dataclasses import dataclass, field
import math
import time
from typing import Dict, List, Optional, Tuple

from DroneOS3.perception.geolocate import lat_lon_to_meters


@dataclass
class AggregatedTrack:
    """Internal state of a persistent ground track."""
    id: str
    label: str
    lat: float
    lon: float
    hits: int = 1
    max_confidence: float = 0.0
    sum_conf: float = 0.0
    sum_conf_lat: float = 0.0
    sum_conf_lon: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_emitted_time: float = 0.0
    last_emitted_hits: int = 0
    confirmed: bool = False
    altitude_agl: Optional[float] = None


@dataclass
class GeoreferencedDetection:
    """Detection with ground coordinates."""
    label: str
    confidence: float
    lat: float
    lon: float
    altitude_agl: Optional[float] = None


@dataclass
class AggregatorItem:
    """Item emitted by the aggregator for broadcast."""
    id: str
    label: str
    confidence: float
    lat: float
    lon: float
    hits: int
    status: str  # "new" | "update"
    altitude_agl: Optional[float] = None


class TrackAggregator:
    """
    Fuses per-frame detections of static ground objects into persistent markers.
    Uses greedy matching by confidence within merge_radius_m and running
    confidence-weighted position averaging.
    """

    def __init__(
        self,
        drone_id: str,
        merge_radius_m: float = 2.5,
        min_hits: int = 2,
        update_interval_s: float = 10.0,
        max_tracks: int = 2000,
        unconfirmed_ttl_s: float = 30.0,
    ):
        self.drone_id = drone_id
        self.merge_radius_m = merge_radius_m
        self.min_hits = min_hits
        self.update_interval_s = update_interval_s
        self.max_tracks = max_tracks
        self.unconfirmed_ttl_s = unconfirmed_ttl_s

        self._seq = 0
        self.tracks: Dict[str, AggregatedTrack] = {}

    def update(
        self,
        detections: List[GeoreferencedDetection],
        now: Optional[float] = None,
    ) -> List[AggregatorItem]:
        """
        Process a list of georeferenced detections from a single frame.
        Returns list of AggregatorItem ("new" or "update") to be broadcast.
        """
        if now is None:
            now = time.time()

        # Sort detections by confidence descending for greedy association
        sorted_dets = sorted(detections, key=lambda d: d.confidence, reverse=True)
        matched_track_ids = set()
        emitted_items: List[AggregatorItem] = []

        for det in sorted_dets:
            best_track_id: Optional[str] = None
            best_dist = float("inf")

            for tid, track in self.tracks.items():
                if tid in matched_track_ids:
                    continue
                # Calculate distance between track and detection in meters
                dn, de = lat_lon_to_meters(det.lat, det.lon, track.lat, track.lon)
                dist = math.hypot(dn, de)
                if dist <= self.merge_radius_m and dist < best_dist:
                    best_dist = dist
                    best_track_id = tid

            if best_track_id is not None:
                # Match to existing track
                matched_track_ids.add(best_track_id)
                track = self.tracks[best_track_id]
                track.hits += 1
                track.max_confidence = max(track.max_confidence, det.confidence)
                track.sum_conf += det.confidence
                track.sum_conf_lat += det.confidence * det.lat
                track.sum_conf_lon += det.confidence * det.lon
                track.lat = track.sum_conf_lat / track.sum_conf
                track.lon = track.sum_conf_lon / track.sum_conf
                track.last_seen = now
                if det.altitude_agl is not None:
                    track.altitude_agl = det.altitude_agl
            else:
                # Create a new candidate track
                self._seq += 1
                new_id = f"{self.drone_id}-g{self._seq:04d}"
                track = AggregatedTrack(
                    id=new_id,
                    label=det.label,
                    lat=det.lat,
                    lon=det.lon,
                    hits=1,
                    max_confidence=det.confidence,
                    sum_conf=det.confidence,
                    sum_conf_lat=det.confidence * det.lat,
                    sum_conf_lon=det.confidence * det.lon,
                    first_seen=now,
                    last_seen=now,
                    last_emitted_time=0.0,
                    last_emitted_hits=0,
                    confirmed=False,
                    altitude_agl=det.altitude_agl,
                )
                self.tracks[new_id] = track
                matched_track_ids.add(new_id)

        # Check emission criteria
        for track in self.tracks.values():
            if not track.confirmed and track.hits >= self.min_hits:
                track.confirmed = True
                track.last_emitted_time = now
                track.last_emitted_hits = track.hits
                emitted_items.append(
                    AggregatorItem(
                        id=track.id,
                        label=track.label,
                        confidence=track.max_confidence,
                        lat=track.lat,
                        lon=track.lon,
                        hits=track.hits,
                        status="new",
                        altitude_agl=track.altitude_agl,
                    )
                )
            elif track.confirmed:
                if (
                    track.hits > track.last_emitted_hits
                    and (now - track.last_emitted_time) >= self.update_interval_s
                ):
                    track.last_emitted_time = now
                    track.last_emitted_hits = track.hits
                    emitted_items.append(
                        AggregatorItem(
                            id=track.id,
                            label=track.label,
                            confidence=track.max_confidence,
                            lat=track.lat,
                            lon=track.lon,
                            hits=track.hits,
                            status="update",
                            altitude_agl=track.altitude_agl,
                        )
                    )

        # Prune unconfirmed tracks older than TTL (30 s)
        to_prune = [
            tid
            for tid, t in self.tracks.items()
            if not t.confirmed and (now - t.first_seen) > self.unconfirmed_ttl_s
        ]
        for tid in to_prune:
            del self.tracks[tid]

        # Bound session track count: evict oldest by last_seen
        if len(self.tracks) > self.max_tracks:
            sorted_tracks = sorted(self.tracks.items(), key=lambda kv: kv[1].last_seen)
            excess = len(self.tracks) - self.max_tracks
            for tid, _ in sorted_tracks[:excess]:
                del self.tracks[tid]

        return emitted_items

