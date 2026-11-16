"""
Canonical observation identification and payload hashing (CNOR v1).
"""

import math
import re
import json
import hashlib
import unicodedata
from typing import Dict, Any

ID_REGEX = re.compile(r'^[a-zA-Z0-9_\-]+$')


def construct_canonical_obs_id(
    drone_id: str,
    session_id: str,
    generation: int,
    frame_id: int,
    local_track_id: str
) -> str:
    """
    Constructs a deterministic canonical observation ID from the 5-tuple:
    (drone_id, drone_session_id, session_generation, frame_id, local_track_id).
    """
    d_clean = unicodedata.normalize('NFKC', str(drone_id)).strip()
    s_clean = str(session_id).lower().strip()
    t_clean = unicodedata.normalize('NFKC', str(local_track_id)).strip()

    if not ID_REGEX.match(d_clean):
        raise ValueError(f"Invalid characters in drone_id: {d_clean}")
    if not ID_REGEX.match(t_clean):
        raise ValueError(f"Invalid characters in local_track_id: {t_clean}")
    if generation < 0 or frame_id < 0:
        raise ValueError(f"Negative values forbidden: generation={generation}, frame_id={frame_id}")

    return f"{d_clean}:{s_clean}:{generation}:{frame_id}:{t_clean}"


def validate_observation_dict(obs: Dict[str, Any]) -> None:
    """
    Validates observation dictionary fields before canonical hashing and ingestion.
    Rejects NaN, Inf, missing fields, and out-of-range values.
    """
    required_keys = [
        "drone_id", "drone_session_id", "session_generation", "frame_id",
        "local_track_id", "capture_timestamp_ms", "lat", "lon", "alt",
        "label", "confidence", "cov_xx", "cov_yy", "cov_xy"
    ]
    for k in required_keys:
        if k not in obs:
            raise ValueError(f"Missing required observation field: {k}")

    # Check numeric floats for NaN / Inf
    numeric_floats = ["lat", "lon", "alt", "confidence", "cov_xx", "cov_yy", "cov_xy"]
    for k in numeric_floats:
        val = obs[k]
        if val is None or not isinstance(val, (int, float)):
            raise ValueError(f"Field {k} must be numeric, got {type(val)}")
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"Non-finite float forbidden in field {k}: {val}")

    # Latitude / longitude range
    lat = float(obs["lat"])
    lon = float(obs["lon"])
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Latitude out of bounds [-90, 90]: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Longitude out of bounds [-180, 180]: {lon}")

    # Confidence range
    conf = float(obs["confidence"])
    if not (0.0 <= conf <= 1.0):
        raise ValueError(f"Confidence out of bounds [0.0, 1.0]: {conf}")

    # Covariance positive semi-definite check for 2D horizontal
    cov_xx = float(obs["cov_xx"])
    cov_yy = float(obs["cov_yy"])
    cov_xy = float(obs["cov_xy"])
    if cov_xx < 0.0 or cov_yy < 0.0:
        raise ValueError(f"Diagonal covariance must be non-negative: cov_xx={cov_xx}, cov_yy={cov_yy}")
    det = cov_xx * cov_yy - cov_xy * cov_xy
    if det < -1e-6:
        raise ValueError(f"Covariance matrix is not positive semi-definite: det={det}")


def compute_cnor_v1_hash(obs: Dict[str, Any]) -> str:
    """
    Computes Canonical Normalized Observation Representation v1 (CNOR v1) SHA-256 hash.
    Ensures deterministic serialization across platforms and architectures.
    """
    validate_observation_dict(obs)

    canonical_dict = {
        "alt": f"{float(obs['alt']):.3f}",
        "conf": f"{float(obs['confidence']):.4f}",
        "cov": [
            f"{float(obs['cov_xx']):.6f}",
            f"{float(obs['cov_yy']):.6f}",
            f"{float(obs['cov_xy']):.6f}"
        ],
        "drone": unicodedata.normalize("NFKC", str(obs["drone_id"])).strip(),
        "frame": int(obs["frame_id"]),
        "gen": int(obs["session_generation"]),
        "label": unicodedata.normalize("NFKC", str(obs["label"])).strip().lower(),
        "lat": f"{float(obs['lat']):.7f}",
        "lon": f"{float(obs['lon']):.7f}",
        "sess": str(obs["drone_session_id"]).lower().strip(),
        "time": int(obs["capture_timestamp_ms"]),
        "track": unicodedata.normalize("NFKC", str(obs["local_track_id"])).strip(),
        "v": "cnor_v1"
    }

    canonical_bytes = json.dumps(
        canonical_dict,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True
    ).encode('utf-8')

    return hashlib.sha256(canonical_bytes).hexdigest()

