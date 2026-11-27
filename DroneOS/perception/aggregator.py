"""Track aggregator merging multi-frame detections into stable ground markers."""

from dataclasses import dataclass, field
import math
import time
from typing import Dict, List, Optional, Any

from DroneOS.perception.geolocate import lat_lon_to_meters


NADIR_TOLERANCE_DEG = 10.0


def compute_optical_axis_nadir_offset_deg(
    mount_pitch_deg: float = 90.0,
    roll_deg: Optional[float] = None,
    pitch_deg: Optional[float] = None,
) -> float:
    """
    Compute angle in degrees between the camera optical axis and straight-down nadir (0, 0, +1) in NED.
    Uses the same body/mount rotation convention as geolocate.pixel_to_ray_ned (mount_pitch_deg=90 is nadir).
    """
    r_deg = float(roll_deg) if roll_deg is not None else 0.0
    p_deg = float(pitch_deg) if pitch_deg is not None else 0.0
    mp_rad = math.radians(-mount_pitch_deg)
    v_body = [math.cos(mp_rad), 0.0, -math.sin(mp_rad)]
    cr, sr = math.cos(math.radians(r_deg)), math.sin(math.radians(r_deg))
    v1 = [v_body[0], cr * v_body[1] - sr * v_body[2], sr * v_body[1] + cr * v_body[2]]
    cp, sp = math.cos(math.radians(p_deg)), math.sin(math.radians(p_deg))
    d_down = -sp * v1[0] + cp * v1[2]
    d_down_clamped = max(-1.0, min(1.0, d_down))
    return math.degrees(math.acos(d_down_clamped))


def compute_expected_shift_px(
    drone_displacement_m: float,
    altitude_agl: Optional[float],
    img_w: Optional[int],
    hfov_deg: Optional[float] = 90.0,
) -> Optional[float]:
    """
    Computes expected pixel shift for a stationary ground object under a nadir camera.
    expected_shift_px = focal_px * drone_displacement_m / altitude_agl
    where focal_px = (W / 2) / tan(hfov / 2).
    Returns None if altitude or camera intrinsics are unknown/invalid.
    """
    if altitude_agl is None or altitude_agl <= 0.0:
        return None
    if img_w is None or img_w <= 0:
        return None
    if hfov_deg is None or hfov_deg <= 0.0 or hfov_deg >= 180.0:
        return None

    focal_px = (float(img_w) / 2.0) / math.tan(math.radians(hfov_deg / 2.0))
    expected_shift_px = focal_px * drone_displacement_m / altitude_agl
    return expected_shift_px


def compute_artifact_pixel_threshold(
    expected_shift_px: Optional[float],
    vehicle_artifact_shift_ratio: float = 0.25,
    vehicle_artifact_pixel_floor_px: float = 8.0,
    fallback_pixel_tol_px: float = 50.0,
) -> float:
    """
    Computes the threshold below which an observed pixel shift is flagged as vehicle-attached.
    Uses shift_ratio * expected_shift with a small pixel floor to absorb detector jitter.
    Falls back to fallback_pixel_tol_px if expected_shift is None (unknown altitude/intrinsics).
    """
    if expected_shift_px is None:
        return fallback_pixel_tol_px
    raw_thresh = max(vehicle_artifact_pixel_floor_px, vehicle_artifact_shift_ratio * expected_shift_px)
    return min(raw_thresh, 0.5 * expected_shift_px)


@dataclass
class TrackHit:
    """Historical observation associated with an aggregated track."""
    timestamp: float
    drone_lat: Optional[float]
    drone_lon: Optional[float]
    pixel_x: Optional[float]
    pixel_y: Optional[float]
    lat: float
    lon: float
    confidence: float
    altitude_agl: Optional[float] = None
    img_w: Optional[int] = None
    img_h: Optional[int] = None
    roll_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    is_vehicle_artifact: bool = False


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
    hits_history: List[TrackHit] = field(default_factory=list)


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
    img_w: Optional[int] = None
    img_h: Optional[int] = None
    roll_deg: Optional[float] = None
    pitch_deg: Optional[float] = None


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
        vehicle_artifact_pixel_tol_px: float = 50.0,
        vehicle_artifact_shift_ratio: float = 0.25,
        vehicle_artifact_pixel_floor_px: float = 8.0,
        hfov_deg: float = 90.0,
        mount_pitch_deg: float = 90.0,
        vehicle_artifact_mode: str = "auto",
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
        self.vehicle_artifact_shift_ratio = vehicle_artifact_shift_ratio
        self.vehicle_artifact_pixel_floor_px = vehicle_artifact_pixel_floor_px
        self.hfov_deg = hfov_deg
        self.mount_pitch_deg = mount_pitch_deg
        mode_norm = str(vehicle_artifact_mode).strip().lower()
        if mode_norm not in ("auto", "off", "fixed"):
            raise ValueError(f"Invalid vehicle_artifact_mode: {vehicle_artifact_mode!r}. Expected 'auto', 'off', or 'fixed'.")
        self.vehicle_artifact_mode = mode_norm

        self._seq = 0
        self._current_frame_id = 0
        self.tracks: Dict[str, AggregatedTrack] = {}
        self.last_confirmed_events: List[Dict[str, Any]] = []

    def is_pair_nadir_geometry(self, h1: TrackHit, h2: TrackHit) -> bool:
        """Return True iff both observations are within NADIR_TOLERANCE_DEG of straight-down nadir."""
        off1 = compute_optical_axis_nadir_offset_deg(self.mount_pitch_deg, h1.roll_deg, h1.pitch_deg)
        off2 = compute_optical_axis_nadir_offset_deg(self.mount_pitch_deg, h2.roll_deg, h2.pitch_deg)
        return off1 <= NADIR_TOLERANCE_DEG and off2 <= NADIR_TOLERANCE_DEG

    def _compute_pair_artifact_threshold(
        self,
        disp_m: float,
        h1: TrackHit,
        h2: TrackHit,
    ) -> Optional[float]:
        """
        Compute the pixel-shift threshold below which (h1, h2) is considered vehicle-attached.
        Returns None when vehicle_artifact_mode == 'off'.
        In 'fixed' mode, returns vehicle_artifact_pixel_tol_px.
        In 'auto' mode, uses nadir expected-shift model only when camera geometry is within
        NADIR_TOLERANCE_DEG of nadir and altitude/intrinsics are known; otherwise falls back to
        vehicle_artifact_pixel_tol_px.
        """
        if self.vehicle_artifact_mode == "off":
            return None
        if self.vehicle_artifact_mode == "fixed":
            return self.vehicle_artifact_pixel_tol_px
        if not self.is_pair_nadir_geometry(h1, h2):
            return self.vehicle_artifact_pixel_tol_px
        alt = h2.altitude_agl if h2.altitude_agl is not None else h1.altitude_agl
        w = h2.img_w if h2.img_w is not None else h1.img_w
        exp_shift = compute_expected_shift_px(disp_m, alt, w, self.hfov_deg)
        return compute_artifact_pixel_threshold(
            exp_shift,
            self.vehicle_artifact_shift_ratio,
            self.vehicle_artifact_pixel_floor_px,
            self.vehicle_artifact_pixel_tol_px,
        )

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
                if track.is_vehicle_artifact:
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

                new_hit = TrackHit(
                    timestamp=now,
                    drone_lat=det.drone_lat,
                    drone_lon=det.drone_lon,
                    pixel_x=det.pixel_x,
                    pixel_y=det.pixel_y,
                    lat=det.lat,
                    lon=det.lon,
                    confidence=det.confidence,
                    altitude_agl=det.altitude_agl,
                    img_w=det.img_w,
                    img_h=det.img_h,
                    roll_deg=det.roll_deg,
                    pitch_deg=det.pitch_deg,
                )
                track.hits_history.append(new_hit)
                if len(track.hits_history) > 30:
                    track.hits_history = track.hits_history[-30:]

                # Check observation distinctness for temporal confirmation:
                # Requires drone displacement >= max(min_drone_displacement_m, 3 * sigma_diff)
                # OR viewpoint angle change >= min_viewpoint_angle_deg.
                # Stationary hovering drone with GPS jitter must NOT confirm on time separation alone.
                # Classify vehicle-attached hits individually and exclude them when counting distinct hits.
                if not track.confirmed:
                    sigma_diff = math.sqrt(2.0) * self.gps_sigma_m
                    required_disp = max(self.min_drone_displacement_m, 3.0 * sigma_diff)

                    # 1. Per-hit artifact classification against prior hits on this track
                    if self.vehicle_artifact_mode != "off" and new_hit.drone_lat is not None and new_hit.drone_lon is not None and new_hit.pixel_x is not None and new_hit.pixel_y is not None:
                        for prev_hit in track.hits_history[:-1]:
                            if prev_hit.drone_lat is None or prev_hit.drone_lon is None:
                                continue
                            if prev_hit.pixel_x is None or prev_hit.pixel_y is None:
                                continue
                            d_dn, d_de = lat_lon_to_meters(
                                new_hit.drone_lat, new_hit.drone_lon,
                                prev_hit.drone_lat, prev_hit.drone_lon
                            )
                            disp_m = math.hypot(d_dn, d_de)
                            if disp_m >= self.min_drone_displacement_m:
                                thresh_px = self._compute_pair_artifact_threshold(disp_m, prev_hit, new_hit)
                                if thresh_px is not None:
                                    p_disp = math.hypot(
                                        new_hit.pixel_x - prev_hit.pixel_x,
                                        new_hit.pixel_y - prev_hit.pixel_y,
                                    )
                                    if p_disp < thresh_px:
                                        prev_hit.is_vehicle_artifact = True
                                        new_hit.is_vehicle_artifact = True

                    # 2. Exclude vehicle-attached hits when counting distinct hits
                    non_artifact_hits = [h for h in track.hits_history if not h.is_vehicle_artifact]

                    if not non_artifact_hits:
                        track.is_vehicle_artifact = True
                        track.distinct_hits = 1
                    else:
                        track.is_vehicle_artifact = False
                        prev_distinct = track.distinct_hits
                        distinct_count = 1
                        ref_hit = non_artifact_hits[0]
                        if ref_hit.drone_lat is not None and ref_hit.drone_lon is not None:
                            t_dn, t_de = lat_lon_to_meters(track.lat, track.lon, ref_hit.drone_lat, ref_hit.drone_lon)
                            ref_bearing: Optional[float] = math.degrees(math.atan2(t_de, t_dn)) % 360.0
                        else:
                            ref_bearing = None

                        for cand in non_artifact_hits[1:]:
                            if ref_hit.drone_lat is None or ref_hit.drone_lon is None:
                                if cand.drone_lat is not None and cand.drone_lon is not None:
                                    ref_hit = cand
                                    t_dn, t_de = lat_lon_to_meters(track.lat, track.lon, ref_hit.drone_lat, ref_hit.drone_lon)
                                    ref_bearing = math.degrees(math.atan2(t_de, t_dn)) % 360.0
                                continue
                            if cand.drone_lat is None or cand.drone_lon is None:
                                continue

                            time_advanced = (cand.timestamp > ref_hit.timestamp)
                            d_dn, d_de = lat_lon_to_meters(
                                cand.drone_lat, cand.drone_lon,
                                ref_hit.drone_lat, ref_hit.drone_lon
                            )
                            disp_m = math.hypot(d_dn, d_de)

                            t_dn, t_de = lat_lon_to_meters(track.lat, track.lon, cand.drone_lat, cand.drone_lon)
                            cand_bearing = math.degrees(math.atan2(t_de, t_dn)) % 360.0
                            delta_bearing_deg = 0.0
                            if ref_bearing is not None:
                                diff = abs(cand_bearing - ref_bearing) % 360.0
                                delta_bearing_deg = 360.0 - diff if diff > 180.0 else diff

                            range_to_target = max(1.0, math.hypot(t_dn, t_de))
                            angle_threshold_deg = max(
                                self.min_viewpoint_angle_deg,
                                math.degrees(3.0 * sigma_diff / range_to_target)
                            )
                            viewpoint_or_disp_ok = (disp_m >= required_disp) or (delta_bearing_deg >= angle_threshold_deg)

                            is_pair_stationary = False
                            if (
                                self.vehicle_artifact_mode != "off"
                                and cand.pixel_x is not None and cand.pixel_y is not None
                                and ref_hit.pixel_x is not None and ref_hit.pixel_y is not None
                                and (disp_m >= self.min_drone_displacement_m or viewpoint_or_disp_ok)
                            ):
                                thresh_px = self._compute_pair_artifact_threshold(disp_m, ref_hit, cand)
                                if thresh_px is not None:
                                    p_disp = math.hypot(
                                        cand.pixel_x - ref_hit.pixel_x,
                                        cand.pixel_y - ref_hit.pixel_y,
                                    )
                                    if p_disp < thresh_px:
                                        is_pair_stationary = True

                            if time_advanced and viewpoint_or_disp_ok and not is_pair_stationary:
                                distinct_count += 1
                                ref_hit = cand
                                ref_bearing = cand_bearing

                        track.distinct_hits = distinct_count
                        if distinct_count > prev_distinct:
                            track.hits = max(track.hits + 1, distinct_count)
                        if ref_hit.drone_lat is not None and ref_hit.drone_lon is not None:
                            track.last_distinct_drone_lat = ref_hit.drone_lat
                            track.last_distinct_drone_lon = ref_hit.drone_lon
                            track.last_distinct_bearing_deg = ref_bearing
                            track.last_distinct_pixel_x = ref_hit.pixel_x
                            track.last_distinct_pixel_y = ref_hit.pixel_y
                            track.last_distinct_hit_time = ref_hit.timestamp
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

                hit0 = TrackHit(
                    timestamp=now,
                    drone_lat=det.drone_lat,
                    drone_lon=det.drone_lon,
                    pixel_x=det.pixel_x,
                    pixel_y=det.pixel_y,
                    lat=det.lat,
                    lon=det.lon,
                    confidence=det.confidence,
                    altitude_agl=det.altitude_agl,
                    img_w=det.img_w,
                    img_h=det.img_h,
                    roll_deg=det.roll_deg,
                    pitch_deg=det.pitch_deg,
                )
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
                    hits_history=[hit0],
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
