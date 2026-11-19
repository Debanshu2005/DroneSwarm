"""
Validated configuration models for the Global Fusion Service (GFS).
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any, Union, Tuple
import yaml
from pydantic import BaseModel, Field, field_validator


class GFSConfig(BaseModel):
    # Storage and Locking
    db_path: str = Field(default="data/gfs.db", description="Path to SQLite database file")
    lock_path: str = Field(default="data/gfs.lock", description="Path to process lock file")
    
    # Network Ports
    inbox_udp_port: int = Field(default=41458, description="UDP port for incoming drone observations")
    outbox_udp_port: int = Field(default=41455, description="UDP port for broadcasting fused global tracks to GCS")
    ack_udp_port: int = Field(default=41459, description="UDP port for receiving GCS application ACKs")
    
    # Fusion Execution
    batch_interval_ms: int = Field(default=100, ge=10, le=5000, description="OCC batch processing interval in ms")
    ref_origin_lat: float = Field(default=22.3149, ge=-90.0, le=90.0, description="Topocentric ENU origin latitude")
    ref_origin_lon: float = Field(default=87.3105, ge=-180.0, le=180.0, description="Topocentric ENU origin longitude")
    ref_origin_alt: float = Field(default=0.0, description="Topocentric ENU origin altitude")

    # Spatial and Covariance Association
    spatial_mahalanobis_thresh: float = Field(default=9.21, gt=0.0, description="Chi-square gating threshold (2D 99%)")
    spatial_physical_r_max_m: float = Field(default=3.5, gt=0.0, description="Physical distance outer bound in metres")
    spatial_noise_floor_m2: float = Field(default=0.25, gt=0.0, description="Minimum covariance floor in m^2 (0.5m std)")
    unassigned_dummy_cost: int = Field(default=9210000, gt=0, description="Dummy unassigned column cost in integer micro-units")

    # Temporal Gating
    temporal_max_diff_ms: int = Field(default=5400, gt=0, description="Maximum pairwise observation time delta in ms")
    clock_skew_sigma_ms: int = Field(default=200, ge=0, description="Estimated clock skew uncertainty in ms")

    # Bounded Bron-Kerbosch Limits
    clique_max_observations: int = Field(default=100, ge=10, description="Maximum observations evaluated per batch")
    clique_max_cliques: int = Field(default=500, ge=10, description="Maximum maximal cliques enumerated before truncation")
    clique_time_limit_ms: float = Field(default=25.0, gt=0.0, description="CPU time budget for Bron-Kerbosch in ms")

    # Outbox and ACK Protocol
    outbox_ack_timeout_ms: int = Field(default=15000, gt=0, description="Timeout before retrying unacknowledged outbox events")
    outbox_max_retries: int = Field(default=5, ge=1, description="Maximum retry count before terminal FAILED state")
    worker_lease_duration_ms: int = Field(default=10000, gt=0, description="Worker lease duration in ms")
    hmac_secret_key: str = Field(default="super_secret_gfs_gcs_hmac_key_2026", min_length=16, description="Shared HMAC secret")
    hmac_key_id: str = Field(default="gcs-key-1", description="Default HMAC Key ID")
    hmac_keys: Dict[str, str] = Field(
        default_factory=lambda: {"gcs-key-1": "super_secret_gfs_gcs_hmac_key_2026"},
        description="Map of key identifiers (kid) to secrets"
    )
    authorized_gcs_clients: list[str] = Field(
        default_factory=lambda: ["gcs-primary", "gcs-backup", "gcs-pilot-1", "gcs-client-1"],
        description="List of authorized GCS client identifiers"
    )
    deferred_observation_ttl_ms: int = Field(default=60000, gt=0, description="Deferred observation TTL in ms")
    provisional_object_ttl_ms: int = Field(default=60000, gt=0, description="Provisional candidate TTL in ms")
    provisional_max_lifetime_ms: int = Field(default=300000, gt=0, description="Maximum total lifetime for unconfirmed provisional object in ms")
    enable_temporal_parallax_confirmation: bool = Field(default=False, description="Experimental temporal parallax confirmation flag")
    temporal_parallax_min_baseline_m: float = Field(default=4.0, gt=0.0, description="Minimum drone translation baseline in metres for parallax")
    temporal_parallax_min_duration_s: float = Field(default=5.0, gt=0.0, description="Minimum tracking duration in seconds for parallax")

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "GFSConfig":
        """
        Loads configuration from YAML file if provided, overlaid with environment variables.
        """
        data: Dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    data.update(loaded)

        # Environment variable overrides
        env_mappings: Dict[str, Union[str, Tuple[str, Any]]] = {
            "GFS_DB_PATH": "db_path",
            "GFS_LOCK_PATH": "lock_path",
            "GFS_INBOX_UDP_PORT": ("inbox_udp_port", int),
            "GFS_OUTBOX_UDP_PORT": ("outbox_udp_port", int),
            "GFS_ACK_UDP_PORT": ("ack_udp_port", int),
            "GFS_BATCH_INTERVAL_MS": ("batch_interval_ms", int),
            "GFS_HMAC_SECRET": "hmac_secret_key",
            "GFS_R_MAX_M": ("spatial_physical_r_max_m", float),
            "GFS_PROVISIONAL_TTL_MS": ("provisional_object_ttl_ms", int),
            "GFS_ENABLE_TEMPORAL_PARALLAX": ("enable_temporal_parallax_confirmation", lambda v: str(v).lower() in ("true", "1", "yes")),
        }
        for env_var, field_spec in env_mappings.items():
            val = os.environ.get(env_var)
            if val is not None:
                if isinstance(field_spec, tuple):
                    field_name, field_type = field_spec
                    data[str(field_name)] = field_type(val)
                else:
                    data[str(field_spec)] = val

        return cls(**data)

