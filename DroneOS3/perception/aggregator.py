"""Track aggregator merging multi-frame detections into stable ground markers."""

from dataclasses import dataclass
import math
import time
from typing import Dict, List, Optional, Any

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
    last_hit_time: float = 0.0
    last_drone_lat: Optional[float] = None
    last_drone_lon: Optional[float] = None
    distinct_hits: int = 1
    last_distinct_hit_time: float = 0.0
    last_distinct_drone_lat: Optional[float] = None
    last_distinct_drone_lon: Optional[float] = None
    last_distinct_bearing_deg: Optional[float] = None
    first_frame_id: int = 0
    frames_to_confirm: int = 0
    seconds_to_confirm: float = 0.0
    last_pixel_x: Optional[float] = None
    last_pixel_y: Optional[float] = None
    last_distinct_pixel_x: Optional[float] = None
    last_distinct_pixel_y: Optional[float] = None
    first_pixel_x: Optional[float] = None
    first_pixel_y: Optional[float] = None
    first_drone_lat: Optional[float] = None
    first_drone_lon: Optional[float] = None
    is_vehicle_artifact: bool = False


@dataclass
class GeoreferencedDetection:
    """Detection with ground coordinates and optional drone position."""
    label: str
    confidence: float
    lat: float
    lon: float
    altitude_agl: Optional[float] = None
    drone_lat: Optional[float] = None
    drone_lon: Optional[float] = None
    pixel_x: Optional[float] = None
    pixel_y: Optional[float] = None
    box_w: Optional[float] = None
    box_h: Optional[float] = None


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
    frames_to_confirm: int = 0
    seconds_to_confirm: float = 0.0


class TrackAggregator:
    """
    Fuses per-frame detections of static ground objects into persistent markers.
    Uses greedy matching by confidence within merge_radius_m and running
    confidence-weighted position averaging.
    Enforces strengthened temporal confirmation requiring observations separated by
    >= min_temporal_separation_s OR >= min_drone_displacement_m.
    """

    def __init__(
        self,
        drone_id: str,
        merge_radius_m: float = 2.5,
        min_hits: int = 2,
        update_interval_s: float = 10.0,
        max_tracks: int = 2000,
        unconfirmed_ttl_s: float = 30.0,
        min_temporal_separation_s: float = 2.0,
        min_drone_displacement_m: float = 1.5,
        gps_sigma_m: float = 0.5,
        min_viewpoint_angle_deg: float = 5.0,
        vehicle_artifact_pixel_tol_px: float = 35.0,
    ):
        self.drone_id = drone_id
        self.merge_radius_m = merge_radius_m
        self.min_hits = min_hits
        self.update_interval_s = update_interval_s
        self.max_tracks = max_tracks
        self.unconfirmed_ttl_s = unconfirmed_ttl_s
        self.min_temporal_separation_s = min_temporal_separation_s
        self.min_drone_displacement_m = min_drone_displacement_m
        self.gps_sigma_m = gps_sigma_m
        self.min_viewpoint_angle_deg = min_viewpoint_angle_deg
        self.vehicle_artifact_pixel_tol_px = vehicle_artifact_pixel_tol_px

        self._seq = 0
        self._current_frame_id = 0
        self.tracks: Dict[str, AggregatedTrack] = {}
        self.last_confirmed_events: List[Dict[str, Any]] = []

    def update(
        self,
        detections: List[GeoreferencedDetection],
        now: Optional[float] = None,
        frame_id: Optional[int] = None,
    ) -> List[AggregatorItem]:
        """
        Process a list of georeferenced detections from a single frame.
        Returns list of AggregatorItem ("new" or "update") to be broadcast.
        """
        if now is None:
            now = time.time()
        if frame_id is not None:
            self._current_frame_id = frame_id

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

                # Check observation distinctness for temporal confirmation:
                # Requires drone displacement >= max(min_drone_displacement_m, 3 * sigma_diff)
                # OR viewpoint angle change >= min_viewpoint_angle_deg.
                # Stationary hovering drone with GPS jitter must NOT confirm on time separation alone.
                if not track.confirmed:
                    has_drone_coords = (
                        det.drone_lat is not None
                        and det.drone_lon is not None
                        and track.last_distinct_drone_lat is not None
                        and track.last_distinct_drone_lon is not None
                    )

                    disp_m = 0.0
                    delta_bearing_deg = 0.0
                    if has_drone_coords:
                        d_dn, d_de = lat_lon_to_meters(
                            det.drone_lat, det.drone_lon,
                            track.last_distinct_drone_lat, track.last_distinct_drone_lon
                        )
                        disp_m = math.hypot(d_dn, d_de)

                        t_dn, t_de = lat_lon_to_meters(track.lat, track.lon, det.drone_lat, det.drone_lon)
                        current_bearing = math.degrees(math.atan2(t_de, t_dn)) % 360.0

                        if track.last_distinct_bearing_deg is not None:
                            diff = abs(current_bearing - track.last_distinct_bearing_deg) % 360.0
                            delta_bearing_deg = 360.0 - diff if diff > 180.0 else diff
                        else:
                            delta_bearing_deg = 0.0

                        sigma_diff = math.sqrt(2.0) * self.gps_sigma_m
                        required_disp = max(self.min_drone_displacement_m, 3.0 * sigma_diff)

                        time_advanced = (now > track.last_seen)
                        t_dn, t_de = lat_lon_to_meters(track.lat, track.lon, det.drone_lat, det.drone_lon)
                        range_to_target = max(1.0, math.hypot(t_dn, t_de))
                        # 3-sigma angular noise cone from GPS jitter at current range
                        angle_threshold_deg = max(
                            self.min_viewpoint_angle_deg,
                            math.degrees(3.0 * sigma_diff / range_to_target)
                        )
                        viewpoint_or_disp_ok = (disp_m >= required_disp) or (delta_bearing_deg >= angle_threshold_deg)
                        is_distinct = time_advanced and viewpoint_or_disp_ok

                        # Vehicle artifact rejection: check whether pixel centroid stays nearly fixed
                        # across hits while drone displaces >= min_drone_displacement_m / required_disp
                        has_pixel_coords = (
                            det.pixel_x is not None
                            and det.pixel_y is not None
                            and track.last_distinct_pixel_x is not None
                            and track.last_distinct_pixel_y is not None
                        )
                        if has_pixel_coords:
                            p_disp = math.hypot(
                                det.pixel_x - track.last_distinct_pixel_x,
                                det.pixel_y - track.last_distinct_pixel_y,
                            )
                            tot_drone_disp = 0.0
                            tot_p_disp = 0.0
                            if track.first_drone_lat is not None and track.first_drone_lon is not None:
                                f_dn, f_de = lat_lon_to_meters(
                                    det.drone_lat, det.drone_lon,
                                    track.first_drone_lat, track.first_drone_lon
                                )
                                tot_drone_disp = math.hypot(f_dn, f_de)
                            if track.first_pixel_x is not None and track.first_pixel_y is not None:
                                tot_p_disp = math.hypot(
                                    det.pixel_x - track.first_pixel_x,
                                    det.pixel_y - track.first_pixel_y,
                                )

                            is_stationary_pixel = False
                            if disp_m >= required_disp and p_disp <= self.vehicle_artifact_pixel_tol_px:
                                is_stationary_pixel = True
                            if tot_drone_disp >= self.min_drone_displacement_m and tot_p_disp <= self.vehicle_artifact_pixel_tol_px:
                                is_stationary_pixel = True

                            if is_stationary_pixel:
                                track.is_vehicle_artifact = True
                                is_distinct = False
                    else:
                        # Detections without drone coordinates cannot verify spatial displacement
                        # or viewpoint angle change; they must NOT reach confirmed status on time alone.
                        is_distinct = False

                    if is_distinct:
                        track.hits += 1
                        track.distinct_hits += 1
                        track.last_distinct_hit_time = now
                        if det.drone_lat is not None and det.drone_lon is not None:
                            track.last_distinct_drone_lat = det.drone_lat
                            track.last_distinct_drone_lon = det.drone_lon
                            t_dn, t_de = lat_lon_to_meters(track.lat, track.lon, det.drone_lat, det.drone_lon)
                            track.last_distinct_bearing_deg = math.degrees(math.atan2(t_de, t_dn)) % 360.0
                        if det.pixel_x is not None and det.pixel_y is not None:
                            track.last_distinct_pixel_x = det.pixel_x
                            track.last_distinct_pixel_y = det.pixel_y
                else:
                    if now > track.last_seen:
                        track.hits += 1

                track.last_hit_time = now
                if det.drone_lat is not None and det.drone_lon is not None:
                    track.last_drone_lat = det.drone_lat
                    track.last_drone_lon = det.drone_lon
                if det.pixel_x is not None and det.pixel_y is not None:
                    track.last_pixel_x = det.pixel_x
                    track.last_pixel_y = det.pixel_y

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
                init_bearing = None
                if det.drone_lat is not None and det.drone_lon is not None:
                    t_dn, t_de = lat_lon_to_meters(det.lat, det.lon, det.drone_lat, det.drone_lon)
                    init_bearing = math.degrees(math.atan2(t_de, t_dn)) % 360.0

                track = AggregatedTrack(
                    id=new_id,
                    label=det.label,
                    lat=det.lat,
                    lon=det.lon,
                    hits=1,
                    distinct_hits=1,
                    max_confidence=det.confidence,
                    sum_conf=det.confidence,
                    sum_conf_lat=det.confidence * det.lat,
                    sum_conf_lon=det.confidence * det.lon,
                    first_seen=now,
                    last_seen=now,
                    last_hit_time=now,
                    last_drone_lat=det.drone_lat,
                    last_drone_lon=det.drone_lon,
                    last_distinct_hit_time=now,
                    last_distinct_drone_lat=det.drone_lat,
                    last_distinct_drone_lon=det.drone_lon,
                    last_distinct_bearing_deg=init_bearing,
                    first_frame_id=self._current_frame_id,
                    last_emitted_time=0.0,
                    last_emitted_hits=0,
                    confirmed=False,
                    altitude_agl=det.altitude_agl,
                    last_pixel_x=det.pixel_x,
                    last_pixel_y=det.pixel_y,
                    last_distinct_pixel_x=det.pixel_x,
                    last_distinct_pixel_y=det.pixel_y,
                    first_pixel_x=det.pixel_x,
                    first_pixel_y=det.pixel_y,
                    first_drone_lat=det.drone_lat,
                    first_drone_lon=det.drone_lon,
                    is_vehicle_artifact=False,
                )
                self.tracks[new_id] = track
                matched_track_ids.add(new_id)

        # Check emission criteria
        for track in self.tracks.values():
            if not track.confirmed and not track.is_vehicle_artifact and track.distinct_hits >= self.min_hits:
                track.confirmed = True
                track.frames_to_confirm = max(1, self._current_frame_id - track.first_frame_id + 1)
                track.seconds_to_confirm = max(0.0, now - track.first_seen)
                track.last_emitted_time = now
                track.last_emitted_hits = track.hits
                item = AggregatorItem(
                    id=track.id,
                    label=track.label,
                    confidence=track.max_confidence,
                    lat=track.lat,
                    lon=track.lon,
                    hits=track.hits,
                    status="new",
                    altitude_agl=track.altitude_agl,
                    frames_to_confirm=track.frames_to_confirm,
                    seconds_to_confirm=track.seconds_to_confirm,
                )
                emitted_items.append(item)
                self.last_confirmed_events.append({
                    "id": track.id,
                    "label": track.label,
                    "confidence": track.max_confidence,
                    "lat": track.lat,
                    "lon": track.lon,
                    "frames": track.frames_to_confirm,
                    "seconds": round(track.seconds_to_confirm, 3),
                })
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
