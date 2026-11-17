"""
Diagnostic instrumentation module for peer-drone exclusion and collaborative perception.
Logs structured events to ml/reports/peer_exclusion_diagnostics/events.jsonl
and generates summary reports.
"""

from __future__ import annotations
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PerceptionDiagnostics")

# Find repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "ml" / "reports" / "peer_exclusion_diagnostics"
DEFAULT_JSONL_PATH = DEFAULT_OUTPUT_DIR / "events.jsonl"


class DiagnosticRecorder:
    _instance: Optional[DiagnosticRecorder] = None
    _lock = threading.Lock()

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
        self.jsonl_path = self.output_dir / "events.jsonl"
        self._file_lock = threading.Lock()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # In-memory summary counters
        self.counts = {
            "total_raw_detections": 0,
            "accepted_detections": 0,
            "rejected_detections": 0,
            "deferred_detections": 0,
            "peer_exclusion_decisions": 0,
            "peer_exclusion_rejections": 0,
            "missing_peer_positions": 0,
            "stale_peer_positions": 0,
            "gfs_objects_created": 0,
            "gfs_objects_updated": 0,
            "events_forwarded_to_phoneos": 0,
            "peer_telemetry_received": 0,
            "peer_heartbeats_received": 0,
        }
        self.events_cache: List[Dict[str, Any]] = []

    @classmethod
    def get_instance(cls, output_dir: Optional[Path] = None) -> DiagnosticRecorder:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(output_dir=output_dir)
            return cls._instance

    @classmethod
    def reset(cls, output_dir: Optional[Path] = None) -> DiagnosticRecorder:
        with cls._lock:
            cls._instance = cls(output_dir=output_dir)
            # Recreate empty events.jsonl
            if cls._instance.jsonl_path.exists():
                try:
                    cls._instance.jsonl_path.unlink()
                except Exception:
                    pass
            return cls._instance

    def record_event(self, event_type: str, drone_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Record a single structured diagnostic event."""
        now_ts = time.time()
        event = {
            "timestamp": now_ts,
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now_ts)) + f".{int((now_ts % 1) * 1000):03d}Z",
            "event_type": event_type,
            "drone_id": str(drone_id),
            **data
        }

        # Update in-memory metrics
        self._update_counts(event_type, data)

        # Write to JSONL
        try:
            line = json.dumps(event) + "\n"
            with self._file_lock:
                self.events_cache.append(event)
                with open(self.jsonl_path, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as e:
            logger.warning(f"Failed to append diagnostic event: {e}")

        return event

    def _update_counts(self, event_type: str, data: Dict[str, Any]):
        if event_type == "RAW_DETECTION":
            self.counts["total_raw_detections"] += 1
        elif event_type == "PEER_EXCLUSION_EVAL":
            self.counts["peer_exclusion_decisions"] += 1
            if data.get("decision") == "REJECTED":
                self.counts["rejected_detections"] += 1
                self.counts["peer_exclusion_rejections"] += 1
            elif data.get("decision") == "DEFERRED":
                self.counts["deferred_detections"] += 1
            elif data.get("decision") == "ACCEPTED":
                self.counts["accepted_detections"] += 1
        elif event_type == "PEER_TELEMETRY_RX":
            self.counts["peer_telemetry_received"] += 1
            if not data.get("has_lat_lon") or not data.get("gps_valid"):
                self.counts["missing_peer_positions"] += 1
        elif event_type == "PEER_POSITION_STATUS":
            if data.get("status") == "MISSING":
                self.counts["missing_peer_positions"] += 1
            elif data.get("status") == "STALE":
                self.counts["stale_peer_positions"] += 1
        elif event_type == "PEER_HEARTBEAT_RX":
            self.counts["peer_heartbeats_received"] += 1
        elif event_type == "GFS_FUSION_RESULT":
            action = data.get("action")
            if action == "CREATED_NEW":
                self.counts["gfs_objects_created"] += 1
            elif action == "FUSED_EXISTING":
                self.counts["gfs_objects_updated"] += 1
        elif event_type in ("RELAY_DETECTION_FWD", "RELAY_GFS_EVENT_FWD", "RELAY_GFS_SNAPSHOT_FWD"):
            self.counts["events_forwarded_to_phoneos"] += 1

    def generate_summary(self) -> Dict[str, Any]:
        """Generate summary statistics dictionary and write summary.md and summary.json."""
        summary_data = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "output_directory": str(self.output_dir),
            "events_log": str(self.jsonl_path),
            "metrics": {
                "total_raw_detections": self.counts["total_raw_detections"],
                "accepted_detections": self.counts["accepted_detections"],
                "rejected_detections": self.counts["rejected_detections"],
                "deferred_detections": self.counts["deferred_detections"],
                "peer_exclusion_decisions": self.counts["peer_exclusion_decisions"],
                "peer_exclusion_rejections": self.counts["peer_exclusion_rejections"],
                "missing_peer_positions": self.counts["missing_peer_positions"],
                "stale_peer_positions": self.counts["stale_peer_positions"],
                "gfs_objects_created": self.counts["gfs_objects_created"],
                "gfs_objects_updated": self.counts["gfs_objects_updated"],
                "events_forwarded_to_phoneos": self.counts["events_forwarded_to_phoneos"],
                "peer_telemetry_received": self.counts["peer_telemetry_received"],
                "peer_heartbeats_received": self.counts["peer_heartbeats_received"],
            }
        }

        # Write summary.json
        summary_json_path = self.output_dir / "summary.json"
        try:
            with open(summary_json_path, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write summary.json: {e}")

        # Write summary.md
        summary_md_path = self.output_dir / "summary.md"
        try:
            md_content = self._render_summary_markdown(summary_data)
            with open(summary_md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            logger.warning(f"Failed to write summary.md: {e}")

        return summary_data

    def _render_summary_markdown(self, data: Dict[str, Any]) -> str:
        m = data["metrics"]
        lines = [
            "# Peer-Drone Exclusion & Collaborative Perception Diagnostic Summary",
            f"**Generated:** {data['generated_at']}",
            f"**Log File:** `{data['events_log']}`",
            "",
            "## Key Metrics",
            "| Metric | Count | Description |",
            "|---|---|---|",
            f"| Total Raw Detections | {m['total_raw_detections']} | Total bounding boxes output by YOLO across all drones |",
            f"| Accepted Detections | {m['accepted_detections']} | Detections evaluated and passed to track aggregator |",
            f"| Rejected Detections | {m['rejected_detections']} | Detections rejected by peer exclusion or gating filters |",
            f"| Deferred Detections | {m.get('deferred_detections', 0)} | Detections deferred due to ambiguous/stale peer telemetry |",
            f"| Peer Exclusion Decisions | {m['peer_exclusion_decisions']} | Total geolocation-to-peer spatial evaluations performed |",
            f"| Peer Exclusion Rejections | {m['peer_exclusion_rejections']} | Detections blocked because they matched an airborne peer |",
            f"| Missing Peer Positions | {m['missing_peer_positions']} | Registry peers without valid GPS coordinates |",
            f"| Stale Peer Positions | {m['stale_peer_positions']} | Peer positions exceeding freshness thresholds (>2.0s) |",
            f"| GFS Objects Created | {m['gfs_objects_created']} | Authoritative global target hypotheses created in GFS |",
            f"| GFS Objects Updated | {m['gfs_objects_updated']} | Existing global targets updated with new observations |",
            f"| Events Forwarded to PhoneOS | {m['events_forwarded_to_phoneos']} | Perception and GFS packets delivered to mobile GCS |",
            "",
            "## Subsystem Observations",
            f"- **Peer Telemetry Packets:** {m['peer_telemetry_received']}",
            f"- **Peer Heartbeat Packets:** {m['peer_heartbeats_received']}",
            "",
        ]
        return "\n".join(lines)


# Global helper functions for convenient access across modules
def get_diagnostics() -> DiagnosticRecorder:
    return DiagnosticRecorder.get_instance()


def log_raw_detection(drone_id: str, frame_id: int, det_idx: int, trace_id: str,
                      bbox_xyxy: List[float], confidence: float, label: str,
                      drone_gps: Dict[str, Any]) -> Dict[str, Any]:
    return get_diagnostics().record_event("RAW_DETECTION", drone_id, {
        "frame_id": frame_id,
        "det_idx": det_idx,
        "trace_id": trace_id,
        "bbox_xyxy": bbox_xyxy,
        "confidence": confidence,
        "label": label,
        "drone_gps": drone_gps
    })


def log_peer_exclusion_eval(drone_id: str, frame_id: int, det_idx: int, trace_id: str,
                            projected_ground: Dict[str, Any],
                            peer_registry_contents: List[str],
                            peers: List[Dict[str, Any]],
                            decision: str, reason: str,
                            matched_peer_id: Optional[str] = None,
                            matched_distance_m: Optional[float] = None) -> Dict[str, Any]:
    return get_diagnostics().record_event("PEER_EXCLUSION_EVAL", drone_id, {
        "frame_id": frame_id,
        "det_idx": det_idx,
        "trace_id": trace_id,
        "projected_ground": projected_ground,
        "peer_registry_contents": peer_registry_contents,
        "peers": peers,
        "decision": decision,
        "reason": reason,
        "matched_peer_id": matched_peer_id,
        "matched_distance_m": matched_distance_m
    })


def log_peer_telemetry_rx(drone_id: str, sender_id: str, in_registry: bool,
                          gps_valid: bool, has_lat_lon: bool,
                          lat: Optional[float], lon: Optional[float], alt: Optional[float],
                          position_updated: bool, position_timestamp: Optional[float]) -> Dict[str, Any]:
    return get_diagnostics().record_event("PEER_TELEMETRY_RX", drone_id, {
        "sender_id": sender_id,
        "in_registry": in_registry,
        "gps_valid": gps_valid,
        "has_lat_lon": has_lat_lon,
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "position_updated": position_updated,
        "position_timestamp": position_timestamp
    })


def log_peer_heartbeat_rx(drone_id: str, sender_id: str, in_registry: bool,
                          status: str, is_active: bool,
                          lat: Optional[float], lon: Optional[float], alt: Optional[float]) -> Dict[str, Any]:
    return get_diagnostics().record_event("PEER_HEARTBEAT_RX", drone_id, {
        "sender_id": sender_id,
        "in_registry": in_registry,
        "status": status,
        "is_active": is_active,
        "lat": lat,
        "lon": lon,
        "alt": alt
    })


def log_peer_lifecycle(drone_id: str, peer_id: str, action: str, reason: str) -> Dict[str, Any]:
    return get_diagnostics().record_event("PEER_LIFECYCLE", drone_id, {
        "peer_id": peer_id,
        "action": action,
        "reason": reason
    })


def log_peer_position_status(drone_id: str, peer_id: str, status: str,
                             age_s: Optional[float], lat: Optional[float], lon: Optional[float],
                             alt: Optional[float]) -> Dict[str, Any]:
    return get_diagnostics().record_event("PEER_POSITION_STATUS", drone_id, {
        "peer_id": peer_id,
        "status": status,
        "age_s": age_s,
        "lat": lat,
        "lon": lon,
        "alt": alt
    })


def log_gfs_observation_rx(drone_id: str, obs_id: str, local_track_id: str,
                           trace_id: Optional[str], lat: float, lon: float,
                           alt: Optional[float], confidence: float) -> Dict[str, Any]:
    return get_diagnostics().record_event("GFS_OBSERVATION_RX", "GFS", {
        "drone_id": drone_id,
        "obs_id": obs_id,
        "local_track_id": local_track_id,
        "trace_id": trace_id,
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "confidence": confidence
    })


def log_gfs_fusion_result(global_id: str, action: str, label: str,
                          lat: float, lon: float, alt: Optional[float],
                          confidence: float, observation_count: int,
                          source_drones: List[str],
                          contributing_local_tracks: List[str]) -> Dict[str, Any]:
    return get_diagnostics().record_event("GFS_FUSION_RESULT", "GFS", {
        "global_id": global_id,
        "action": action,
        "label": label,
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "confidence": confidence,
        "observation_count": observation_count,
        "source_drones": source_drones,
        "contributing_local_tracks": contributing_local_tracks
    })


def log_relay_fwd(event_type: str, relay_port: int, details: Dict[str, Any]) -> Dict[str, Any]:
    return get_diagnostics().record_event(event_type, "RELAY", {
        "relay_port": relay_port,
        **details
    })
