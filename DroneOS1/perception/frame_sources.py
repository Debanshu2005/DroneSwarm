"""Frame acquisition sources for perception subsystem."""

from abc import ABC, abstractmethod
import asyncio
from pathlib import Path
import time
from typing import Any, List, Optional, Union

from DroneOS1.perception.types import Frame
from DroneOS1.shared.utils.logger import setup_logger

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
    ):
        self.vehicle_name = vehicle_name
        self.host = host
        self.port = port
        self.camera_name = camera_name
        self._client = None
        self._connected = False
        self.is_synthetic = False

    def _discard_client(self) -> None:
        """Safely close and discard client reference before reconnecting or on shutdown."""
        if self._client is not None:
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
            self._client = airsim.MultirotorClient(ip=self.host, port=self.port, timeout_value=3.0)
            self._client.confirmConnection()
            self._connected = True
            logger.info("AirSimFrameSource connected to %s:%d for vehicle '%s'", self.host, self.port, self.vehicle_name)
            return True
        except Exception as e:
            logger.debug("AirSimFrameSource connection attempt failed: %s", e)
            self._discard_client()
            self._connected = False
            return False

    def _sync_capture(self) -> Optional[Frame]:
        if not self._ensure_connected():
            return None

        try:
            import airsim
            import cv2
            import numpy as np

            responses = self._client.simGetImages(
                [airsim.ImageRequest(self.camera_name, airsim.ImageType.Scene, False, True)],
                vehicle_name=self.vehicle_name,
            )
            if not responses or not responses[0].image_data_uint8:
                return None

            raw_bytes = responses[0].image_data_uint8
            arr = np.frombuffer(raw_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return None

            return Frame(image=img, timestamp=time.time())
        except Exception as e:
            logger.debug("AirSim image acquisition error: %s", e)
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

    def __init__(self, device: Union[int, str] = 0):
        self.device = device
        self._cap = None
        self.is_synthetic = False

    def _ensure_open(self) -> bool:
        if self._cap is not None and self._cap.isOpened():
            return True
        try:
            import cv2
            self._cap = cv2.VideoCapture(self.device)
            if self._cap.isOpened():
                # Set buffer size to 1 to guarantee fresh frames
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                logger.info("OpenCVFrameSource initialized on device: %s", self.device)
                return True
            logger.warning("OpenCVFrameSource failed to open device: %s", self.device)
            return False
        except Exception as e:
            logger.warning("OpenCVFrameSource error opening device %s: %s", self.device, e)
            return False

    def _sync_capture(self) -> Optional[Frame]:
        if not self._ensure_open():
            return None
        try:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                return None
            return Frame(image=frame, timestamp=time.time())
        except Exception as e:
            logger.warning("OpenCV capture error: %s", e)
            return None

    async def get_frame(self) -> Optional[Frame]:
        return await asyncio.to_thread(self._sync_capture)

    async def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None


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

