"""Webcam ground test for DroneOS PerceptionPipeline (SOFTWARE GATE ONLY).

Runs the real PerceptionPipeline against a USB/laptop webcam via OpenCVFrameSource
with fake/fixed (or simulated-walk) telemetry. Refuses to start if a real flight
controller adapter is configured.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.frame_sources import ImageFolderSource, OpenCVFrameSource
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.preflight import _get_runtime_versions, run_perception_preflight
from DroneOS.shared.config.models import CameraConfig, PerceptionConfig
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.shared.utils.logger import setup_logger

logger = setup_logger("WebcamGroundTest")

FORBIDDEN_FC_BACKENDS = {"px4", "mavsdk", "ardupilot", "real", "hw", "airsim"}


class FakeGroundTelemetryAdapter:
    """Test-only telemetry source supplying fake altitude and GPS (never connects to an FC)."""

    def __init__(
        self,
        altitude_m: float = 6.0,
        base_lat: float = 28.5450,
        base_lon: float = 77.1926,
        walk_mps: float = 0.0,
    ):
        self.altitude_m = float(altitude_m)
        self.base_lat = float(base_lat)
        self.base_lon = float(base_lon)
        self.walk_mps = float(walk_mps)
        self._t0_mono = time.monotonic()

    async def get_telemetry(self) -> TelemetryData:
        now_mono = time.monotonic()
        elapsed = now_mono - self._t0_mono
        lat = self.base_lat + (self.walk_mps * elapsed) / 111_111.0
        return TelemetryData(
            drone_id="webcam_ground_test",
            timestamp=time.time(),
            latitude=lat,
            longitude=self.base_lon,
            altitude=self.altitude_m,
            roll=0.0,
            pitch=0.0,
            yaw=0.0,
            heading=0.0,
            vx=self.walk_mps,
            vy=0.0,
            vz=0.0,
            flight_mode="OFFBOARD",
            armed_state="ARMED",
            gps_valid=True,
            satellites_visible=12,
        )


class RecordingNetworkAdapter:
    """Captures confirmed DetectionMessage alerts and optionally relays them to the app."""

    def __init__(self, send_alerts: bool = False, alert_url: str = "http://127.0.0.1:8000/api/detections"):
        self.send_alerts = send_alerts
        self.alert_url = alert_url
        self.confirmed_alerts: List[Dict[str, Any]] = []

    async def broadcast_message(self, msg: Any) -> None:
        detections = getattr(msg, "detections", None) or []
        for det in detections:
            record = {
                "timestamp": getattr(msg, "timestamp", time.time()),
                "frame_id": getattr(msg, "frame_id", None),
                "label": getattr(det, "label", "rubbish"),
                "confidence": float(getattr(det, "confidence", 0.0)),
                "latitude": float(getattr(det, "lat", 0.0)),
                "longitude": float(getattr(det, "lon", 0.0)),
                "hits": int(getattr(det, "hits", 1)),
                "status": getattr(det, "status", "CONFIRMED"),
            }
            self.confirmed_alerts.append(record)
            if self.send_alerts:
                await asyncio.to_thread(self._relay_http, record)

    def _relay_http(self, record: Dict[str, Any]) -> None:
        import urllib.request

        try:
            data = json.dumps(record).encode("utf-8")
            req = urllib.request.Request(
                self.alert_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=1.0):
                pass
        except Exception as exc:
            logger.debug("Alert relay skipped/failed (%s): %s", self.alert_url, exc)


def _percentiles(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "count": int(len(arr)),
        "mean": round(float(np.mean(arr)), 2),
        "p50": round(float(np.percentile(arr, 50)), 2),
        "p95": round(float(np.percentile(arr, 95)), 2),
    }


def _draw_dashed_rect(
    canvas: np.ndarray,
    pt1: tuple,
    pt2: tuple,
    color: tuple,
    thickness: int = 2,
    dash_len: int = 8,
) -> None:
    x1, y1 = pt1
    x2, y2 = pt2
    for x in range(x1, x2, dash_len * 2):
        cv2.line(canvas, (x, y1), (min(x + dash_len, x2), y1), color, thickness)
        cv2.line(canvas, (x, y2), (min(x + dash_len, x2), y2), color, thickness)
    for y in range(y1, y2, dash_len * 2):
        cv2.line(canvas, (x1, y), (x1, min(y + dash_len, y2)), color, thickness)
        cv2.line(canvas, (x2, y), (x2, min(y + dash_len, y2)), color, thickness)


def supports_imshow() -> bool:
    """Return True if the installed OpenCV build supports GUI windows (cv2.imshow)."""
    if not hasattr(cv2, "imshow") or not hasattr(cv2, "namedWindow"):
        return False
    probe = "__droneos_imshow_probe__"
    try:
        cv2.namedWindow(probe, cv2.WINDOW_NORMAL)
        cv2.destroyWindow(probe)
        return True
    except Exception:
        return False


def draw_preview_overlay(
    image: np.ndarray,
    strong_boxes: List[Any],
    weak_boxes: List[Any],
    *,
    capture_fps: float = 0.0,
    inference_ms: float = 0.0,
    frame_age_ms: float = 0.0,
    gate_status: str = "OK",
    altitude_m: float = 6.0,
    tilt_deg: float = 0.0,
    num_tracks: int = 0,
    num_alerts: int = 0,
    det_age_s: float = 0.0,
    stale_after_s: float = 1.5,
    show_weak: bool = True,
    paused: bool = False,
) -> np.ndarray:
    """Draw diagnostic detections, weak tier, stale markers, and status banner onto a frame copy."""
    canvas = image.copy()
    is_stale = det_age_s > stale_after_s
    stale_tag = f" [STALE {det_age_s:.1f}s]" if is_stale else ""

    strong_color = (120, 180, 120) if is_stale else (0, 255, 0)
    weak_color = (110, 150, 190) if is_stale else (0, 165, 255)

    for b in strong_boxes:
        x1, y1, x2, y2 = [int(round(v)) for v in b.bbox_xyxy]
        if is_stale:
            _draw_dashed_rect(canvas, (x1, y1), (x2, y2), strong_color, thickness=2)
        else:
            cv2.rectangle(canvas, (x1, y1), (x2, y2), strong_color, 2)
        cv2.putText(
            canvas,
            f"{b.label} {b.confidence:.2f}{stale_tag}",
            (x1, max(16, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            strong_color,
            2,
        )

    if show_weak:
        for b in weak_boxes:
            x1, y1, x2, y2 = [int(round(v)) for v in b.bbox_xyxy]
            _draw_dashed_rect(canvas, (x1, y1), (x2, y2), weak_color, thickness=2)
            cv2.putText(
                canvas,
                f"weak {b.label} {b.confidence:.2f}{stale_tag}",
                (x1, max(16, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                weak_color,
                1,
            )

    h, w = canvas.shape[:2]
    bar_h = min(98, h)
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (w, bar_h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.65, canvas, 0.35, 0.0, canvas)

    lines = [
        f"FAKE TELEMETRY - LAPTOP CAMERA{' [PAUSED]' if paused else ''}",
        f"fps={capture_fps:.1f} | inf={inference_ms:.1f}ms | age={frame_age_ms:.1f}ms | gate={gate_status}",
        f"alt={altitude_m:.1f}m tilt={tilt_deg:.1f}deg | tracks={num_tracks} | alerts={num_alerts}",
        f"dets_age={det_age_s:.1f}s{' (STALE)' if is_stale else ''} | weak={'ON' if show_weak else 'OFF'} | keys: q s p w",
    ]
    for idx, text in enumerate(lines):
        color = (0, 215, 255) if idx == 0 else ((0, 165, 255) if (idx == 3 and is_stale) else (240, 240, 240))
        cv2.putText(canvas, text, (8, 20 + idx * 22), cv2.FONT_HERSHEY_SIMPLEX, 0.50, color, 1, cv2.LINE_AA)
    return canvas


def _save_annotated_frame(
    save_dir: Path,
    frame_id: int,
    image: np.ndarray,
    boxes: List[Any],
    tag: str,
) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    canvas = image.copy()
    for b in boxes:
        x1, y1, x2, y2 = [int(round(v)) for v in b.bbox_xyxy]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            canvas,
            f"{b.label} {b.confidence:.2f}",
            (x1, max(15, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            2,
        )
    out_img = save_dir / f"frame_{frame_id:05d}_{tag}.jpg"
    cv2.imwrite(str(out_img), canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return out_img


def _validate_no_real_fc() -> None:
    env_backend = os.environ.get("DRONEOS_BACKEND", "").strip().lower()
    if env_backend in FORBIDDEN_FC_BACKENDS:
        raise RuntimeError(
            f"Refusing to run webcam_ground_test with DRONEOS_BACKEND='{env_backend}'. "
            "This test must never run against a real flight controller."
        )


async def run_ground_test(args: argparse.Namespace) -> int:
    _validate_no_real_fc()

    cam_device: Any = int(args.camera) if str(args.camera).isdigit() else str(args.camera)
    weights_path = Path(args.weights)
    if not weights_path.is_absolute():
        weights_path = REPO_ROOT / weights_path

    cfg = PerceptionConfig(
        enabled=True,
        source="opencv",
        weights=str(weights_path),
        confidence=args.conf,
        device=args.device,
        max_fps=args.max_fps,
        camera=CameraConfig(device=cam_device),
    )
    if args.no_gates:
        logger.warning("=" * 68)
        logger.warning("! DIAGNOSTIC --no-gates ACTIVE: altitude/tilt/age/skew gates relaxed!")
        logger.warning("=" * 68)
        cfg.min_altitude_m = 0.0
        cfg.max_tilt_deg = 180.0
        cfg.max_range_m = 1e6
        cfg.max_frame_age_s = 1e6
        cfg.max_telemetry_age_s = 1e6
        cfg.max_sync_skew_s = 1e6

    logger.warning("=" * 68)
    logger.warning("! WEBCAM GROUND TEST (SOFTWARE GATE ONLY)")
    logger.warning("! ALTITUDE (%.1fm) AND GPS (walk=%.2fm/s) ARE FAKE. NO FLIGHT CONTROLLER.", args.altitude, args.walk_mps)
    logger.warning("=" * 68)

    preflight_status, preflight_reason = run_perception_preflight(cfg, repo_root=REPO_ROOT)
    print(f"[PREFLIGHT] status={preflight_status} reason={preflight_reason or 'OK'}", flush=True)

    if args.source == "folder":
        folder_path = Path(args.folder) if args.folder else (REPO_ROOT / "assets" / "perception" / "golden")
        frame_src = ImageFolderSource(folder_path=str(folder_path))
        if not frame_src.image_files:
            print(f"ERROR: No images found in folder '{folder_path}'.", file=sys.stderr)
            await frame_src.close()
            return 2
    else:
        frame_src = OpenCVFrameSource(
            device=cam_device,
            width=args.width,
            height=args.height,
            fps=args.cam_fps,
        )
        if not await asyncio.to_thread(frame_src._ensure_open):
            print(f"ERROR: Could not open webcam device '{cam_device}' ({frame_src.last_error}).", file=sys.stderr)
            await frame_src.close()
            return 2

    det_conf = min(float(cfg.confidence), float(args.preview_min_conf)) if args.preview else float(cfg.confidence)
    detector = GarbageDetector(
        weights_path=str(weights_path),
        imgsz=cfg.imgsz,
        confidence=det_conf,
        iou=cfg.iou,
        device=cfg.device,
    )
    if args.pure_ort:
        sys.modules["ultralytics"] = None

    t_warm_start = time.perf_counter()
    if not detector.load():
        print(f"ERROR: Failed to load detector weights: {weights_path}", file=sys.stderr)
        await frame_src.close()
        return 2
    detector.warmup(cfg.warmup_frames)
    warmup_s = round(time.perf_counter() - t_warm_start, 3)
    print(f"[WARMUP] detector ready in {warmup_s:.3f}s (device={detector.device}, pure_ort={args.pure_ort})", flush=True)

    fc = FakeGroundTelemetryAdapter(altitude_m=args.altitude, walk_mps=args.walk_mps)
    net = RecordingNetworkAdapter(send_alerts=args.send_alerts)

    # Intercept detector.detect lightly to capture per-frame boxes/scores and separate weak tier in --preview
    orig_detect = detector.detect
    last_raw_boxes: List[Any] = []
    preview_state: Dict[str, Any] = {
        "strong_boxes": [],
        "weak_boxes": [],
        "det_mono": None,
        "inference_ms": 0.0,
        "frame_age_ms": 0.0,
        "gate_status": "OK",
    }

    async def _recording_detect(img):
        nonlocal last_raw_boxes
        all_boxes = list(await orig_detect(img) or [])
        if args.preview:
            strong = [b for b in all_boxes if float(b.confidence) >= float(args.conf)]
            weak = [b for b in all_boxes if float(args.preview_min_conf) <= float(b.confidence) < float(args.conf)]
        else:
            strong = all_boxes
            weak = []
        last_raw_boxes = strong
        preview_state["strong_boxes"] = strong
        preview_state["weak_boxes"] = weak
        preview_state["det_mono"] = time.monotonic()
        preview_state["inference_ms"] = float(getattr(detector, "last_timing", {}).get("inference_ms", 0.0) or 0.0)
        return strong

    detector.detect = _recording_detect

    pipeline = PerceptionPipeline(
        drone_id="webcam_ground_test",
        frame_source=frame_src,
        detector=detector,
        flight_controller=fc,
        network_adapter=net,
        config=cfg,
    )

    cap_age_ms: List[float] = []
    pre_ms: List[float] = []
    inf_ms: List[float] = []
    post_ms: List[float] = []
    total_ms: List[float] = []
    detections_per_frame: List[Dict[str, Any]] = []
    gate_rejections: Dict[str, int] = {}
    frames_processed = 0
    total_detections = 0

    save_dir = Path(args.save_frames) if args.save_frames else None
    use_gui_preview = False
    headless_fallback = False
    if args.preview:
        if supports_imshow():
            use_gui_preview = True
        else:
            headless_fallback = True
            if save_dir is None:
                save_dir = REPO_ROOT / "scratch" / "webcam_preview_fallback"
            print(
                f"WARNING: OpenCV does not support cv2.imshow (headless build). "
                f"Falling back to saving annotated frames in '{save_dir}'.",
                flush=True,
            )

    pipeline.start_capture()

    t_run_start = time.monotonic()
    last_status_t = t_run_start
    last_fallback_save_t = t_run_start
    period_s = 1.0 / max(0.1, float(args.max_fps))
    stop_requested = False

    async def _step_once() -> None:
        nonlocal frames_processed, total_detections, last_raw_boxes, last_status_t, last_fallback_save_t
        last_raw_boxes = []
        await pipeline._step()
        stats = dict(pipeline.last_step_stats or {})
        step_info = dict(getattr(pipeline, "_current_step_info", {}) or {})
        rej = stats.get("rejection_reason")
        if "frame_age_s" in step_info:
            preview_state["frame_age_ms"] = float(step_info["frame_age_s"]) * 1000.0
        if rej and not stats.get("reached_inference", False):
            key = str(rej).split(" ")[0]
            gate_rejections[key] = gate_rejections.get(key, 0) + 1
            preview_state["gate_status"] = key
        elif stats.get("reached_inference", False):
            preview_state["gate_status"] = "OK"
            frames_processed += 1
            det_timing = dict(getattr(detector, "last_timing", {}) or {})
            if "frame_age_s" in step_info:
                cap_age_ms.append(float(step_info["frame_age_s"]) * 1000.0)
            pre_val = step_info.get("preprocess_ms", det_timing.get("preprocess_ms"))
            if pre_val is not None:
                pre_ms.append(float(pre_val))
            inf_val = step_info.get("inference_ms", det_timing.get("inference_ms", stats.get("inference_latency_ms")))
            if inf_val is not None:
                inf_ms.append(float(inf_val))
            post_val = step_info.get("postprocess_ms", det_timing.get("postprocess_filter_ms"))
            if post_val is not None:
                post_ms.append(float(post_val))
            e2e_val = step_info.get("e2e_ms")
            if e2e_val is not None:
                total_ms.append(float(e2e_val))
            elif all(v is not None for v in (pre_val, inf_val, post_val)):
                total_ms.append(float(pre_val) + float(inf_val) + float(post_val))

            scores = [round(float(b.confidence), 4) for b in last_raw_boxes]
            total_detections += len(last_raw_boxes)
            detections_per_frame.append(
                {
                    "frame_id": pipeline._frame_id,
                    "num_detections": len(last_raw_boxes),
                    "scores": scores,
                }
            )
            if save_dir is not None and pipeline._latest_frame is not None:
                has_any = bool(last_raw_boxes or preview_state["weak_boxes"])
                periodic = (
                    (time.monotonic() - last_fallback_save_t) >= max(1, args.save_every)
                    if headless_fallback
                    else (pipeline._frame_id % max(1, args.save_every) == 0)
                )
                if has_any or periodic:
                    if headless_fallback:
                        annotated = draw_preview_overlay(
                            pipeline._latest_frame.image,
                            preview_state["strong_boxes"],
                            preview_state["weak_boxes"],
                            capture_fps=frames_processed / max(1e-6, time.monotonic() - t_run_start),
                            inference_ms=preview_state["inference_ms"],
                            frame_age_ms=preview_state["frame_age_ms"],
                            gate_status=preview_state["gate_status"],
                            altitude_m=args.altitude,
                            num_tracks=len(getattr(pipeline.aggregator, "tracks", {})),
                            num_alerts=len(net.confirmed_alerts),
                            det_age_s=0.0,
                        )
                        save_dir.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(
                            str(save_dir / f"frame_{pipeline._frame_id:05d}_{'det' if has_any else 'sample'}.jpg"),
                            annotated,
                            [int(cv2.IMWRITE_JPEG_QUALITY), 90],
                        )
                        last_fallback_save_t = time.monotonic()
                    else:
                        _save_annotated_frame(
                            save_dir,
                            pipeline._frame_id,
                            pipeline._latest_frame.image,
                            last_raw_boxes,
                            "det" if last_raw_boxes else "sample",
                        )

        now_m = time.monotonic()
        if (now_m - last_status_t) >= 5.0:
            print(
                f"[t={now_m - t_run_start:5.1f}s] captured={pipeline._latest_frame_id} "
                f"processed={frames_processed} rejected={gate_rejections} "
                f"detections={total_detections} confirmed_alerts={len(net.confirmed_alerts)}",
                flush=True,
            )
            last_status_t = now_m

    async def _pipeline_loop() -> None:
        while not stop_requested and (time.monotonic() - t_run_start) < args.duration:
            t_step_start = time.monotonic()
            await _step_once()
            elapsed_step = time.monotonic() - t_step_start
            if elapsed_step < period_s:
                await asyncio.sleep(period_s - elapsed_step)

    video_writer = None
    win_name = "DroneOS Webcam Ground Test (Preview)"
    try:
        if use_gui_preview:
            cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
            pipe_task = asyncio.create_task(_pipeline_loop())
            show_weak = True
            paused = False
            last_annotated = None
            cap_fps_est = 0.0
            prev_cap_id = 0
            prev_cap_t = time.monotonic()
            record_path = Path(args.preview_record) if args.preview_record else None

            try:
                while not pipe_task.done() and (time.monotonic() - t_run_start) < args.duration:
                    now_m = time.monotonic()
                    if (now_m - prev_cap_t) >= 0.5:
                        cur_id = pipeline._latest_frame_id
                        cap_fps_est = (cur_id - prev_cap_id) / max(1e-6, now_m - prev_cap_t)
                        prev_cap_id = cur_id
                        prev_cap_t = now_m

                    latest = pipeline._latest_frame
                    if not paused and latest is not None and getattr(latest, "image", None) is not None:
                        det_mono = preview_state["det_mono"]
                        det_age_s = (now_m - det_mono) if det_mono is not None else 0.0
                        last_annotated = draw_preview_overlay(
                            latest.image,
                            preview_state["strong_boxes"],
                            preview_state["weak_boxes"],
                            capture_fps=cap_fps_est,
                            inference_ms=preview_state["inference_ms"],
                            frame_age_ms=preview_state["frame_age_ms"],
                            gate_status=preview_state["gate_status"],
                            altitude_m=args.altitude,
                            num_tracks=len(getattr(pipeline.aggregator, "tracks", {})),
                            num_alerts=len(net.confirmed_alerts),
                            det_age_s=det_age_s,
                            show_weak=show_weak,
                            paused=paused,
                        )
                    elif paused and last_annotated is not None:
                        pass

                    if last_annotated is not None:
                        cv2.imshow(win_name, last_annotated)
                        if record_path is not None:
                            if video_writer is None:
                                record_path.parent.mkdir(parents=True, exist_ok=True)
                                rh, rw = last_annotated.shape[:2]
                                video_writer = cv2.VideoWriter(
                                    str(record_path),
                                    cv2.VideoWriter_fourcc(*"mp4v"),
                                    15.0,
                                    (rw, rh),
                                )
                            if video_writer is not None and video_writer.isOpened():
                                video_writer.write(last_annotated)

                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        stop_requested = True
                        break
                    if key == ord("p"):
                        paused = not paused
                    elif key == ord("w"):
                        show_weak = not show_weak
                    elif key == ord("s") and last_annotated is not None:
                        out_dir = save_dir or (REPO_ROOT / "scratch" / "webcam_preview_saved")
                        out_dir.mkdir(parents=True, exist_ok=True)
                        snap = out_dir / f"snapshot_{int(time.time())}.jpg"
                        cv2.imwrite(str(snap), last_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                        print(f"[PREVIEW] Saved snapshot to {snap}", flush=True)

                    if cv2.getWindowProperty(win_name, cv2.WND_PROP_VISIBLE) < 1:
                        stop_requested = True
                        break

                    await asyncio.sleep(0.03)
            finally:
                stop_requested = True
                await pipe_task
                if video_writer is not None:
                    video_writer.release()
                try:
                    cv2.destroyWindow(win_name)
                except Exception:
                    pass
        else:
            await _pipeline_loop()
    finally:
        await pipeline.stop()
        await frame_src.close()
        detector.close()

    total_run_s = max(1e-6, time.monotonic() - t_run_start)
    report = {
        "metadata": {
            "timestamp": time.time(),
            "software_gate_only": True,
            "fake_telemetry": {"altitude_m": args.altitude, "walk_mps": args.walk_mps},
            "camera_device": cam_device,
            "negotiated_camera": getattr(frame_src, "negotiated_info", None),
            "weights": str(weights_path),
            "device": str(detector.device),
            "pure_ort": bool(args.pure_ort),
            "duration_s": round(total_run_s, 2),
            "max_fps": args.max_fps,
            "conf": args.conf,
            "no_gates": bool(args.no_gates),
            "preflight": {"status": preflight_status, "reason": preflight_reason},
            "warmup_s": warmup_s,
            "versions": _get_runtime_versions(),
        },
        "frames_captured": pipeline._latest_frame_id,
        "frames_processed": frames_processed,
        "effective_fps": round(frames_processed / total_run_s, 3),
        "gate_rejections_by_reason": gate_rejections,
        "latency_ms": {
            "capture_age": _percentiles(cap_age_ms),
            "preprocess": _percentiles(pre_ms),
            "inference": _percentiles(inf_ms),
            "postprocess": _percentiles(post_ms),
            "total": _percentiles(total_ms),
        },
        "detections_per_frame": detections_per_frame,
        "confirmed_alerts": net.confirmed_alerts,
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[DONE] Report written to {report_path} (alerts={len(net.confirmed_alerts)}, processed={frames_processed})", flush=True)
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Webcam ground test for DroneOS PerceptionPipeline.")
    p.add_argument("--camera", default="0", help="Camera index or path (default: 0)")
    p.add_argument("--source", default="webcam", choices=["webcam", "folder"], help="Frame source (default: webcam)")
    p.add_argument("--folder", default=None, help="Folder path when --source folder is used")
    p.add_argument("--weights", default="models/garbage.pt", help="Weights path (.pt or .onnx)")
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help="Inference device")
    p.add_argument("--altitude", type=float, default=6.0, help="Fake altitude AGL in meters (default: 6.0)")
    p.add_argument("--walk-mps", type=float, default=0.0, help="Simulated horizontal walk speed m/s")
    p.add_argument("--duration", type=float, default=120.0, help="Timed run duration in seconds")
    p.add_argument("--max-fps", type=float, default=1.0, help="Max pipeline step rate")
    p.add_argument("--conf", type=float, default=0.55, help="Confidence threshold")
    p.add_argument("--save-frames", default=None, help="Directory to save detection and sampled frames")
    p.add_argument("--save-every", type=int, default=10, help="Save every N-th non-detection frame when --save-frames is set")
    p.add_argument("--report", default="scratch/webcam_ground_test_report.json", help="Output JSON report path")
    p.add_argument("--send-alerts", action="store_true", help="Relay confirmed alerts to local app endpoint")
    p.add_argument("--no-gates", action="store_true", help="Diagnostic only: relax altitude/tilt/age/skew gates")
    p.add_argument("--pure-ort", action="store_true", help="Force pure ONNX Runtime path (block ultralytics)")
    p.add_argument("--width", type=int, default=None, help="Requested camera width")
    p.add_argument("--height", type=int, default=None, help="Requested camera height")
    p.add_argument("--cam-fps", type=float, default=None, help="Requested camera FPS")
    p.add_argument("--preview", action="store_true", help="Open live OpenCV diagnostic preview window")
    p.add_argument("--preview-min-conf", type=float, default=0.15, help="Weak-tier min confidence for --preview display only")
    p.add_argument("--preview-record", default=None, help="Optional MP4 path to record annotated --preview stream")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return asyncio.run(run_ground_test(args))


if __name__ == "__main__":
    raise SystemExit(main())
