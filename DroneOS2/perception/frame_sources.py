"""Frame acquisition sources for perception subsystem."""

from abc import ABC, abstractmethod
import asyncio
from pathlib import Path
import time
from typing import Any, List, Optional, Union

from DroneOS2.perception.types import Frame
from DroneOS2.shared.utils.logger import setup_logger

logger = setup_logger("FrameSource")


class FrameSource(ABC):
    """Abstract base class for camera frame acquisition."""

    is_synthetic: bool = False

    @abstractmethod
    async def get_frame(self) -> Optional[Frame]:
        """Acquire the next available frame, or None if unavailable."""
        pass

    async def close(self) -> None:
        """Release underlying camera or hardware resources."""
        pass


class AirSimFrameSource(FrameSource):
    """
    Acquires camera frames from an AirSim simulation vehicle.
    Maintains its own isolated MultirotorClient to prevent thread/RPC contention
    with flight control commands.
    """

    def __init__(
        self,
        vehicle_name: str,
        host: str = "127.0.0.1",
        port: int = 41451,
        camera_name: str = "bottom_center",
        client: Optional[Any] = None,
    ):
        self.vehicle_name = vehicle_name
        self.host = host
        self.port = port
        self.camera_name = camera_name
        self._client = client
        self._external_client = (client is not None)
        self._connected = (client is not None)
        self._warmed_up = False
        self.is_synthetic = False
        self.last_sim_get_images_ms = 0.0
        self.last_capture_ms = 0.0

    def _discard_client(self) -> None:
        """Safely close and discard client reference before reconnecting or on shutdown."""
        if not getattr(self, "_external_client", False) and self._client is not None:
            try:
                if hasattr(self._client, "close"):
                    self._client.close()
            except Exception:
                pass
            self._client = None

    def _ensure_connected(self) -> bool:
        if self._connected and self._client is not None:
            return True
        # Close / discard old client before reconnecting
        self._discard_client()
        try:
            import airsim
            self._client = airsim.MultirotorClient(ip=self.host, port=self.port, timeout_value=15.0)
            self._client.confirmConnection()
            self._connected = True
            logger.info("AirSimFrameSource connected to %s:%d for vehicle '%s'", self.host, self.port, self.vehicle_name)
            if not self._warmed_up:
                try:
                    img_req = getattr(airsim, "ImageRequest", None)
                    img_type = getattr(getattr(airsim, "ImageType", None), "Scene", 0)
                    if img_req is not None:
                        req = img_req(self.camera_name, img_type, False, False)
                        self._client.simGetImages([req], vehicle_name=self.vehicle_name)
                    self._warmed_up = True
                    logger.debug("AirSimFrameSource warmup capture completed for '%s'", self.vehicle_name)
                except Exception as we:
                    logger.debug("AirSimFrameSource warmup capture non-fatal failure: %s", we)
            return True
        except Exception as e:
            logger.debug("AirSimFrameSource connection attempt failed: %s", e)
            self._discard_client()
            self._connected = False
            return False

    def _sync_capture(self) -> Optional[Frame]:
        if not self._ensure_connected():
            return None

        t_cap_start = time.perf_counter()
        try:
            try:
                import airsim
            except (ImportError, ModuleNotFoundError):
                airsim = None
            import cv2
            import numpy as np

            img_req = getattr(airsim, "ImageRequest", None) if airsim is not None else None
            img_type = getattr(getattr(airsim, "ImageType", None), "Scene", 0) if airsim is not None else 0
            if img_req is not None:
                request = img_req(self.camera_name, img_type, False, False)
            else:
                class _GenericImageRequest:
                    def __init__(self, camera_name, image_type, pixels_as_float, compress):
                        self.camera_name = camera_name
                        self.image_type = image_type
                        self.pixels_as_float = pixels_as_float
                        self.compress = compress
                request = _GenericImageRequest(self.camera_name, img_type, False, False)

            t_rpc_start = time.perf_counter()
            responses = self._client.simGetImages(
                [request],
                vehicle_name=self.vehicle_name,
            )
            sim_get_images_ms = (time.perf_counter() - t_rpc_start) * 1000.0
            self.last_sim_get_images_ms = round(sim_get_images_ms, 2)

            if not responses or not responses[0].image_data_uint8:
                return None

            raw_bytes = responses[0].image_data_uint8
            arr = np.frombuffer(raw_bytes, dtype=np.uint8)
            img = None
            resp_h = getattr(responses[0], "height", 0)
            resp_w = getattr(responses[0], "width", 0)
            if isinstance(resp_h, int) and isinstance(resp_w, int) and resp_h > 0 and resp_w > 0:
                expected_sz = resp_h * resp_w * 3
                if len(arr) == expected_sz:
                    img = arr.reshape((resp_h, resp_w, 3))
            if img is None:
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return None

            capture_ms = (time.perf_counter() - t_cap_start) * 1000.0
            self.last_capture_ms = round(capture_ms, 2)
            meta = {
                "sim_get_images_ms": self.last_sim_get_images_ms,
                "capture_ms": self.last_capture_ms,
            }
            return Frame(image=img, timestamp=time.time(), metadata=meta)
        except Exception as e:
            logger.error("AirSim image acquisition error: %s", e)
            self._connected = False
            self._discard_client()
            return None

    async def get_frame(self) -> Optional[Frame]:
        return await asyncio.to_thread(self._sync_capture)

    async def close(self) -> None:
        self._connected = False
        self._discard_client()


class OpenCVFrameSource(FrameSource):
    """Acquires live frames via OpenCV VideoCapture (V4L2, USB webcam, or RTSP)."""

    def __init__(
        self,
        device: Union[int, str] = 0,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[float] = None,
        prefer_mjpg: bool = True,
        reconnect_backoff_s: float = 1.0,
        max_backoff_s: float = 10.0,
        stall_timeout_s: float = 3.0,
    ):
        self.device = device
        self.requested_width = width
        self.requested_height = height
        self.requested_fps = fps
        self.prefer_mjpg = prefer_mjpg
        self.reconnect_backoff_s = float(reconnect_backoff_s)
        self.max_backoff_s = float(max_backoff_s)
        self.stall_timeout_s = float(stall_timeout_s)

        self._cap = None
        self.is_synthetic = False
        self.backend_name: str = "unopened"
        self.negotiated_info: dict = {}
        self.last_error: Optional[str] = None
        self.last_capture_ms: float = 0.0
        self.last_frame_monotonic: Optional[float] = None
        self._opened_at_monotonic: Optional[float] = None
        self._next_reconnect_at: float = 0.0
        self._current_backoff_s: float = self.reconnect_backoff_s
        self._consecutive_failures: int = 0

    @property
    def is_stalled(self) -> bool:
        """True if camera is failing or has produced no valid frame within stall_timeout_s."""
        ref = self.last_frame_monotonic if self.last_frame_monotonic is not None else self._opened_at_monotonic
        if ref is None:
            return self.last_error is not None
        return (time.monotonic() - ref) > self.stall_timeout_s

    def _release_cap(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def _schedule_reconnect(self, reason: str) -> None:
        self.last_error = reason
        self._consecutive_failures += 1
        self._release_cap()
        self._next_reconnect_at = time.monotonic() + self._current_backoff_s
        self._current_backoff_s = min(self.max_backoff_s, self._current_backoff_s * 2.0)

    def _ensure_open(self) -> bool:
        if self._cap is not None and self._cap.isOpened():
            return True
        now_m = time.monotonic()
        if now_m < self._next_reconnect_at:
            return False
        try:
            import sys
            import cv2

            candidates = []
            if sys.platform.startswith("win") and isinstance(self.device, int) and hasattr(cv2, "CAP_DSHOW"):
                candidates.append(("DSHOW", cv2.CAP_DSHOW))
            candidates.append(("DEFAULT", None))

            cap = None
            opened_backend = "DEFAULT"
            for b_name, b_flag in candidates:
                try:
                    c = cv2.VideoCapture(self.device) if b_flag is None else cv2.VideoCapture(self.device, b_flag)
                except TypeError:
                    c = cv2.VideoCapture(self.device)
                if c is not None and c.isOpened():
                    cap = c
                    opened_backend = b_name
                    break
                if c is not None:
                    try:
                        c.release()
                    except Exception:
                        pass

            if cap is None or not cap.isOpened():
                self._schedule_reconnect(f"camera busy or not found: {self.device}")
                logger.warning("OpenCVFrameSource failed to open device=%s (%s)", self.device, self.last_error)
                return False

            self._cap = cap
            self.backend_name = opened_backend
            self._opened_at_monotonic = now_m
            self._current_backoff_s = self.reconnect_backoff_s
            self.last_error = None

            # Buffer size 1 so we always get the freshest frame; MJPG when available
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if self.prefer_mjpg and hasattr(cv2, "VideoWriter_fourcc"):
                try:
                    self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                except Exception:
                    pass
            if self.requested_width is not None:
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.requested_width))
            if self.requested_height is not None:
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.requested_height))
            if self.requested_fps is not None:
                self._cap.set(cv2.CAP_PROP_FPS, float(self.requested_fps))

            neg_w = self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            neg_h = self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            neg_fps = self._cap.get(cv2.CAP_PROP_FPS)
            self.negotiated_info = {
                "backend": opened_backend,
                "width": int(neg_w) if isinstance(neg_w, (int, float)) and neg_w > 0 else None,
                "height": int(neg_h) if isinstance(neg_h, (int, float)) and neg_h > 0 else None,
                "fps": round(float(neg_fps), 2) if isinstance(neg_fps, (int, float)) and neg_fps > 0 else None,
            }
            logger.info(
                "OpenCVFrameSource opened device=%s backend=%s negotiated=%sx%s@%sfps",
                self.device,
                opened_backend,
                self.negotiated_info["width"],
                self.negotiated_info["height"],
                self.negotiated_info["fps"],
            )
            return True
        except Exception as e:
            self._schedule_reconnect(f"error opening device {self.device}: {e}")
            logger.warning("OpenCVFrameSource error opening device %s: %s", self.device, e)
            return False

    def _sync_capture(self) -> Optional[Frame]:
        if not self._ensure_open():
            return None
        t_start = time.perf_counter()
        try:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._schedule_reconnect(f"empty frame from device {self.device}")
                logger.warning("OpenCVFrameSource received empty/None frame from device %s", self.device)
                return None

            capture_ms = (time.perf_counter() - t_start) * 1000.0
            self.last_capture_ms = round(capture_ms, 2)
            now_mono = time.monotonic()
            self.last_frame_monotonic = now_mono
            self._consecutive_failures = 0
            self._current_backoff_s = self.reconnect_backoff_s
            self.last_error = None
            h, w = frame.shape[:2]
            return Frame(
                image=frame,
                timestamp=time.time(),
                metadata={
                    "capture_ms": self.last_capture_ms,
                    "monotonic_ts": now_mono,
                    "backend": self.backend_name,
                    "width": w,
                    "height": h,
                },
            )
        except Exception as e:
            self._schedule_reconnect(f"capture exception on device {self.device}: {e}")
            logger.warning("OpenCV capture error: %s", e)
            return None

    async def get_frame(self) -> Optional[Frame]:
        return await asyncio.to_thread(self._sync_capture)

    async def close(self) -> None:
        self._release_cap()
        self._next_reconnect_at = 0.0
        self.last_error = None


class ImageFolderSource(FrameSource):
    """
    Replays static images from a local folder for demonstrations and offline tests.
    All detections generated from this source are explicitly flagged as synthetic.
    """

    is_synthetic: bool = True

    def __init__(self, folder_path: str):
        self.folder_path = Path(folder_path)
        self.image_files: List[Path] = []
        self._index = 0

        logger.warning("=" * 68)
        logger.warning("! LOUD NOTICE: ImageFolderSource replay mode is ACTIVE.")
        logger.warning("! All detections will be flagged 'synthetic=True'.")
        logger.warning("! Default AirSim scenery has no litter; replay mode simulates detections.")
        logger.warning("=" * 68)

        if self.folder_path.exists() and self.folder_path.is_dir():
            valid_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
            self.image_files = sorted(
                p for p in self.folder_path.iterdir() if p.suffix.lower() in valid_extensions
            )
            logger.info("ImageFolderSource found %d images in %s", len(self.image_files), self.folder_path)
        else:
            logger.warning("ImageFolderSource folder does not exist: %s", self.folder_path)

    def _sync_read_next(self) -> Optional[Frame]:
        if not self.image_files:
            return None

        try:
            import cv2
            img_path = self.image_files[self._index]
            self._index = (self._index + 1) % len(self.image_files)

            img = cv2.imread(str(img_path))
            if img is None:
                return None
            return Frame(image=img, timestamp=time.time())
        except Exception as e:
            logger.warning("Error reading replay image: %s", e)
            return None

    async def get_frame(self) -> Optional[Frame]:
        return await asyncio.to_thread(self._sync_read_next)

