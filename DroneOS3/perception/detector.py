"""Object detector wrapper around Ultralytics YOLO and ONNX Runtime with lazy loading and retry backoff."""

import asyncio
import concurrent.futures
import math
from pathlib import Path
import time
from typing import List, Optional, Tuple, Union

import numpy as np

from DroneOS3.perception.types import Detection
from DroneOS3.shared.utils.logger import setup_logger

logger = setup_logger("GarbageDetector")


class GarbageDetector:
    """
    Executes garbage detection inferences using Ultralytics YOLO or ONNX Runtime
    in a dedicated single-thread executor.
    Supports .pt and .onnx models with static input shape enforcement,
    resilient exponential backoff retry on load failures, and bounded retry status.
    """

    def __init__(
        self,
        weights_path: str = "models/garbage.pt",
        imgsz: Union[int, Tuple[int, int]] = 640,
        confidence: float = 0.55,
        iou: float = 0.5,
        device: Optional[str] = None,
        classes: Optional[List[str]] = None,
        min_area_ratio: float = 0.0,
        max_area_ratio: float = 1.0,
        max_aspect_ratio: float = 0.0,
    ):
        self.weights_path = Path(weights_path)
        self.imgsz = imgsz
        self.confidence = confidence
        self.iou = iou
        self.device = device
        self.classes = set(str(c) for c in classes) if classes else None
        self.min_area_ratio = min_area_ratio
        self.max_area_ratio = max_area_ratio
        self.max_aspect_ratio = max_aspect_ratio

        self.is_onnx = self.weights_path.suffix.lower() == ".onnx"
        self._model = None
        self._ort_session = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # Resilient load backoff state (removes permanent shutdown latch)
        self._available = False
        self._retry_count = 0
        self._max_retries = 1000
        self._backoff_min_s = 5.0
        self._backoff_max_s = 60.0
        self._current_backoff_s = 0.0
        self._last_attempt_time = 0.0
        self._last_error = ""

    def load(self) -> bool:
        """
        Attempt to load model weights. On failure, applies exponential backoff
        (5s to 60s) without permanently disabling subsequent retries.
        """
        if self._available and (self._model is not None or self._ort_session is not None):
            return True

        now = time.monotonic()
        if self._last_attempt_time > 0.0 and (now - self._last_attempt_time) < self._current_backoff_s:
            return False

        self._last_attempt_time = now
        self._retry_count = min(self._max_retries, self._retry_count + 1)
        self._current_backoff_s = min(self._backoff_max_s, self._backoff_min_s * (2 ** (self._retry_count - 1)))

        if not self.weights_path.exists():
            self._last_error = f"Weights not found: {self.weights_path}"
            logger.warning(
                "Perception model weights file not found (retry #%d, next in %.1fs): %s",
                self._retry_count,
                self._current_backoff_s,
                self.weights_path,
            )
            self._available = False
            return False

        # If ONNX, read exported static imgsz from model input shape and enforce it
        if self.is_onnx:
            self._enforce_onnx_input_shape()

        # Attempt loading through Ultralytics or ONNX Runtime
        try:
            if self.is_onnx:
                # Try loading via Ultralytics (handles ONNX with exact YOLO postprocessing)
                try:
                    from ultralytics import YOLO

                    self._model = YOLO(str(self.weights_path), task="detect")
                    self._available = True
                    self._current_backoff_s = 0.0
                    self._retry_count = 0
                    self._last_error = ""
                    logger.info(
                        "ONNX detector loaded via Ultralytics from %s (attempt #%d).",
                        self.weights_path,
                        self._retry_count,
                    )
                    return True
                except ImportError:
                    # Fallback to pure ONNX Runtime
                    import onnxruntime as ort

                    providers = ["CPUExecutionProvider"]
                    if self.device and "cuda" in str(self.device).lower():
                        providers.insert(0, "CUDAExecutionProvider")
                    self._ort_session = ort.InferenceSession(str(self.weights_path), providers=providers)
                    self._available = True
                    self._current_backoff_s = 0.0
                    self._retry_count = 0
                    self._last_error = ""
                    logger.info(
                        "ONNX detector loaded via ONNX Runtime from %s (attempt #%d).",
                        self.weights_path,
                        self._retry_count,
                    )
                    return True
            else:
                from ultralytics import YOLO

                self._model = YOLO(str(self.weights_path))
                self._available = True
                self._current_backoff_s = 0.0
                self._retry_count = 0
                self._last_error = ""
                logger.info(
                    "PyTorch YOLO detector loaded from %s (attempt #%d).",
                    self.weights_path,
                    self._retry_count,
                )
                return True
        except ImportError as e:
            self._last_error = f"Missing dependency: {e}"
            logger.warning(
                "Dependency missing for detector (retry #%d, next in %.1fs): %s",
                self._retry_count,
                self._current_backoff_s,
                e,
            )
            self._available = False
            self._current_backoff_s = min(self._backoff_max_s, self._current_backoff_s * 2.0)
            return False
        except Exception as e:
            self._last_error = f"Load error: {e}"
            logger.exception(
                "Failed to load detector model (retry #%d, next in %.1fs): %s",
                self._retry_count,
                self._current_backoff_s,
                e,
            )
            self._available = False
            self._current_backoff_s = min(self._backoff_max_s, self._current_backoff_s * 2.0)
            return False

    def _enforce_onnx_input_shape(self) -> None:
        """Inspect ONNX model input shape and enforce static dimensions if present."""
        try:
            import onnxruntime as ort

            sess = ort.InferenceSession(str(self.weights_path), providers=["CPUExecutionProvider"])
            inputs = sess.get_inputs()
            if inputs:
                shape = inputs[0].shape
                # shape format: [batch, channels, height, width]
                if len(shape) >= 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
                    static_h, static_w = int(shape[2]), int(shape[3])
                    static_imgsz = static_h if static_h == static_w else (static_h, static_w)
                    if self.imgsz != static_imgsz:
                        logger.warning(
                            "Configured imgsz (%s) does not match ONNX exported static input shape %s; "
                            "overriding imgsz to %s",
                            self.imgsz,
                            (static_h, static_w),
                            static_imgsz,
                        )
                        self.imgsz = static_imgsz
        except Exception as e:
            logger.debug("Could not inspect ONNX input shape via onnxruntime: %s", e)

    def get_status(self) -> Tuple[str, str]:
        """Return (status, reason) reflecting detector readiness and retry state."""
        if self._available:
            return ("OK", "")
        if self._last_error:
            return ("DEGRADED", f"Retry #{self._retry_count}: {self._last_error}")
        return ("DEGRADED", f"Retry #{self._retry_count}: model not loaded")

    async def ensure_loaded(self) -> bool:
        """Asynchronously ensure weights/model are loaded in the dedicated worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.load)

    def _sync_predict_onnxruntime(self, image) -> List[Detection]:
        """Fallback prediction path when using pure ONNX Runtime without Ultralytics."""
        import cv2

        img_h, img_w = image.shape[:2]
        target_h, target_w = (self.imgsz, self.imgsz) if isinstance(self.imgsz, int) else self.imgsz

        # Resize image
        resized = cv2.resize(image, (target_w, target_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]

        input_name = self._ort_session.get_inputs()[0].name
        raw_outputs = self._ort_session.run(None, {input_name: blob})
        if not raw_outputs:
            return []

        out = raw_outputs[0]
        # Shape is (1, 4 + num_classes, num_anchors) e.g. (1, 5, 8400)
        if out.ndim == 3 and out.shape[1] < out.shape[2]:
            out = np.transpose(out[0], (1, 0))  # (8400, 5)
        elif out.ndim == 3:
            out = out[0]

        boxes = []
        confidences = []
        class_ids = []

        scale_x = img_w / float(target_w)
        scale_y = img_h / float(target_h)

        for row in out:
            classes_scores = row[4:]
            if len(classes_scores) == 0:
                continue
            cls_id = int(np.argmax(classes_scores))
            score = float(classes_scores[cls_id])
            if score >= self.confidence:
                cx, cy, bw, bh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                x1 = (cx - bw / 2.0) * scale_x
                y1 = (cy - bh / 2.0) * scale_y
                w_box = bw * scale_x
                h_box = bh * scale_y
                boxes.append([int(x1), int(y1), int(w_box), int(h_box)])
                confidences.append(score)
                class_ids.append(cls_id)

        detections: List[Detection] = []
        if not boxes:
            return detections

        indices = cv2.dnn.NMSBoxes(boxes, confidences, self.confidence, self.iou)
        if len(indices) == 0:
            return detections

        for idx in indices.flatten():
            bx, by, bw, bh = boxes[idx]
            x1 = max(0.0, min(float(img_w), float(bx)))
            y1 = max(0.0, min(float(img_h), float(by)))
            x2 = max(0.0, min(float(img_w), float(bx + bw)))
            y2 = max(0.0, min(float(img_h), float(by + bh)))

            conf = confidences[idx]
            label = "rubbish"
            detections.append(Detection(bbox_xyxy=(x1, y1, x2, y2), confidence=conf, label=label))

        return detections

    def _sync_predict(self, image) -> List[Detection]:
        if not self.load():
            return []

        # If loaded via pure ONNX Runtime
        if self._ort_session is not None and self._model is None:
            return self._sync_predict_onnxruntime(image)

        if self._model is None:
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

        try:
            results = self._model.predict(**predict_kwargs)
        except Exception as e:
            # Fallback to CPU if CUDA initialization or memory fails
            if self.device is not None and "cuda" in str(self.device).lower():
                logger.warning(
                    "YOLO inference failed on device '%s' (%s); falling back to CPU",
                    self.device,
                    e,
                )
                try:
                    predict_kwargs["device"] = "cpu"
                    results = self._model.predict(**predict_kwargs)
                except Exception as inner_e:
                    logger.exception("YOLO inference failed on CPU fallback: %s", inner_e)
                    return []
            else:
                logger.exception("YOLO inference failed: %s", e)
                return []

        detections: List[Detection] = []
        if not results:
            return detections

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return detections

        names = getattr(result, "names", {})
        img_h, img_w = getattr(image, "shape", (None, None))[:2]

        for box in boxes:
            conf = float(box.conf[0])
            cls_id = int(box.cls[0])
            label = names.get(cls_id, str(cls_id))

            if self.classes and label not in self.classes and str(cls_id) not in self.classes:
                continue

            xyxy = box.xyxy[0]
            if hasattr(xyxy, "tolist"):
                xyxy = xyxy.tolist()

            try:
                x1, y1, x2, y2 = float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])
            except (ValueError, IndexError, TypeError):
                continue

            # Validate finite numerical values
            if not (math.isfinite(x1) and math.isfinite(y1) and math.isfinite(x2) and math.isfinite(y2)):
                continue

            # Ensure proper ordering x1 <= x2 and y1 <= y2
            x1, x2 = min(x1, x2), max(x1, x2)
            y1, y2 = min(y1, y2), max(y1, y2)

            # Boundary clipping to reference image dimensions
            if img_w is not None and img_h is not None:
                x1 = max(0.0, min(float(img_w), x1))
                x2 = max(0.0, min(float(img_w), x2))
                y1 = max(0.0, min(float(img_h), y1))
                y2 = max(0.0, min(float(img_h), y2))

            bw = x2 - x1
            bh = y2 - y1

            # Reject degenerate zero-area boxes
            if bw <= 0.0 or bh <= 0.0:
                continue

            # Defensive geometric filtering (area and aspect ratio)
            if img_w is not None and img_h is not None and (img_w * img_h) > 0:
                area_ratio = (bw * bh) / float(img_w * img_h)
                if self.min_area_ratio > 0.0 and area_ratio < self.min_area_ratio:
                    continue
                if self.max_area_ratio < 1.0 and area_ratio > self.max_area_ratio:
                    continue

            if self.max_aspect_ratio > 0.0 and bw > 0.0 and bh > 0.0:
                aspect_ratio = max(bw / bh, bh / bw)
                if aspect_ratio > self.max_aspect_ratio:
                    continue

            bbox = (x1, y1, x2, y2)
            detections.append(Detection(bbox_xyxy=bbox, confidence=conf, label=label))

        return detections

    async def detect(self, image) -> List[Detection]:
        """Asynchronously run prediction on an image in the dedicated worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._sync_predict, image)

    def close(self):
        """Shutdown the inference worker thread."""
        self._executor.shutdown(wait=False)
