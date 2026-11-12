"""Object detector wrapper around Ultralytics YOLO with lazy loading."""

import asyncio
import concurrent.futures
from pathlib import Path
from typing import List, Optional

from DroneOS1.perception.types import Detection
from DroneOS1.shared.utils.logger import setup_logger

logger = setup_logger("GarbageDetector")


class GarbageDetector:
    """
    Executes garbage detection inferences using Ultralytics YOLO in a single-thread executor.
    Imports and model weights are loaded lazily upon first inference.
    """

    def __init__(
        self,
        weights_path: str = "models/garbage.pt",
        imgsz: int = 1280,
        confidence: float = 0.35,
        iou: float = 0.5,
        device: Optional[str] = None,
        classes: Optional[List[str]] = None,
    ):
        self.weights_path = Path(weights_path)
        self.imgsz = imgsz
        self.confidence = confidence
        self.iou = iou
        self.device = device
        self.classes = set(classes) if classes else None

        self._model = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._load_attempted = False
        self._available = False

    def load(self) -> bool:
        """Attempt to load Ultralytics and weights."""
        if self._load_attempted:
            return self._available

        self._load_attempted = True
        try:
            from ultralytics import YOLO
        except ImportError:
            logger.warning("Ultralytics library not installed. Perception detector disabled.")
            self._available = False
            return False

        if not self.weights_path.exists():
            logger.warning("Perception model weights file not found: %s", self.weights_path)
            self._available = False
            return False

        try:
            logger.info("Loading YOLO model weights from %s", self.weights_path)
            self._model = YOLO(str(self.weights_path))
            self._available = True
            logger.info("YOLO detector successfully initialized.")
            return True
        except Exception as e:
            logger.exception("Failed to initialize YOLO model: %s", e)
            self._available = False
            return False

    def _sync_predict(self, image) -> List[Detection]:
        if not self.load() or self._model is None:
            return []

        predict_kwargs = {
            "source": image,
            "imgsz": self.imgsz,
            "conf": self.confidence,
            "iou": self.iou,
            "verbose": False,
        }
        if self.device is not None:
            predict_kwargs["device"] = self.device

        results = self._model.predict(**predict_kwargs)
        detections: List[Detection] = []

        if not results:
            return detections

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return detections

        names = getattr(result, "names", {})

        for box in boxes:
            conf = float(box.conf[0])
            cls_id = int(box.cls[0])
            label = names.get(cls_id, str(cls_id))

            if self.classes and label not in self.classes:
                continue

            xyxy = box.xyxy[0].tolist()
            bbox = (float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3]))
            detections.append(Detection(bbox_xyxy=bbox, confidence=conf, label=label))

        return detections

    async def detect(self, image) -> List[Detection]:
        """Asynchronously run prediction on an image in the dedicated worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._sync_predict, image)

    def close(self):
        """Shutdown the inference worker thread."""
        self._executor.shutdown(wait=False)

