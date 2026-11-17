# Peer-Drone Exclusion & Collaborative Perception Diagnostic Summary
**Generated:** 2026-11-16T18:33:56Z
**Log File:** `D:\CityGrid\my-project\PhoneOS_Swarm\ml\reports\peer_exclusion_diagnostics\events.jsonl`

## Key Metrics
| Metric | Count | Description |
|---|---|---|
| Total Raw Detections | 14 | Total bounding boxes output by YOLO across all drones |
| Accepted Detections | 2 | Detections evaluated and passed to track aggregator |
| Rejected Detections | 11 | Detections rejected by peer exclusion or gating filters |
| Deferred Detections | 1 | Detections deferred due to ambiguous/stale peer telemetry |
| Peer Exclusion Decisions | 14 | Total geolocation-to-peer spatial evaluations performed |
| Peer Exclusion Rejections | 11 | Detections blocked because they matched an airborne peer |
| Missing Peer Positions | 0 | Registry peers without valid GPS coordinates |
| Stale Peer Positions | 0 | Peer positions exceeding freshness thresholds (>2.0s) |
| GFS Objects Created | 2 | Authoritative global target hypotheses created in GFS |
| GFS Objects Updated | 0 | Existing global targets updated with new observations |
| Events Forwarded to PhoneOS | 2 | Perception and GFS packets delivered to mobile GCS |

## Subsystem Observations
- **Peer Telemetry Packets:** 204
- **Peer Heartbeat Packets:** 204
