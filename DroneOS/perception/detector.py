"""Object detector wrapper around Ultralytics YOLO and ONNX Runtime with lazy loading and retry backoff."""

import asyncio
import concurrent.futures
import math
from pathlib import Path
import time
from typing import List, Optional, Tuple, Union

import numpy as np

from DroneOS.perception.types import Detection
from DroneOS.shared.utils.logger import setup_logger

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
        self._cached_onnx_input_shape = None
        self._class_names = None
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
                    attempt = self._retry_count
                    self._available = True
                    self._current_backoff_s = 0.0
                    self._retry_count = 0
                    self._last_error = ""
                    logger.info(
                        "ONNX detector loaded via Ultralytics from %s (attempt #%d).",
                        self.weights_path,
                        attempt,
                    )
                    return True
                except ImportError:
                    # Fallback to pure ONNX Runtime
                    import onnxruntime as ort

                    providers = ["CPUExecutionProvider"]
                    if self.device and "cuda" in str(self.device).lower():
                        providers.insert(0, "CUDAExecutionProvider")
                    self._ort_session = ort.InferenceSession(str(self.weights_path), providers=providers)
                    self._load_ort_metadata()
                    attempt = self._retry_count
                    self._available = True
                    self._current_backoff_s = 0.0
                    self._retry_count = 0
                    self._last_error = ""
                    logger.info(
                        "ONNX detector loaded via ONNX Runtime from %s (attempt #%d).",
                        self.weights_path,
                        attempt,
                    )
                    return True
            else:
                from ultralytics import YOLO

                self._model = YOLO(str(self.weights_path))
                attempt = self._retry_count
                self._available = True
                self._current_backoff_s = 0.0
                self._retry_count = 0
                self._last_error = ""
                logger.info(
                    "PyTorch YOLO detector loaded from %s (attempt #%d).",
                    self.weights_path,
                    attempt,
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
            return False

    def _load_ort_metadata(self) -> None:
        """Read class names and model properties from ONNX model metadata."""
        if self._ort_session is None:
            return
        try:
            custom_meta = self._ort_session.get_modelmeta().custom_metadata_map
            if "names" in custom_meta:
                import ast

                raw_names = ast.literal_eval(custom_meta["names"])
                if isinstance(raw_names, dict):
                    self._class_names = {int(k): str(v) for k, v in raw_names.items()}
                elif isinstance(raw_names, list):
                    self._class_names = {i: str(v) for i, v in enumerate(raw_names)}
        except Exception as e:
            logger.debug("Could not parse ONNX model metadata: %s", e)

    def _get_class_name(self, cls_id: int) -> str:
        """Resolve class name from parsed metadata or fallback to standard label."""
        if self._class_names and cls_id in self._class_names:
            return self._class_names[cls_id]
        return "rubbish" if cls_id == 0 else str(cls_id)

    def _enforce_onnx_input_shape(self) -> None:
        """Inspect ONNX model input shape and enforce static dimensions, caching session input shape."""
        if self._cached_onnx_input_shape is not None:
            static_h, static_w = self._cached_onnx_input_shape
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
            return

        try:
            inputs = None
            if self._ort_session is not None:
                inputs = self._ort_session.get_inputs()
            else:
                import onnxruntime as ort

                sess = ort.InferenceSession(str(self.weights_path), providers=["CPUExecutionProvider"])
                inputs = sess.get_inputs()

            if inputs:
                shape = inputs[0].shape
                # shape format: [batch, channels, height, width]
                if len(shape) >= 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
                    static_h, static_w = int(shape[2]), int(shape[3])
                    self._cached_onnx_input_shape = (static_h, static_w)
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

    def filter_candidate_box(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        confidence: float,
        label: str,
        cls_id: Optional[int] = None,
        img_w: Optional[int] = None,
        img_h: Optional[int] = None,
    ) -> Optional[Detection]:
        """
        Validate and defensively filter a single candidate bounding box.
        Applies class selection, finite checks, image boundary clipping,
        zero-area rejection, and area/aspect ratio gating.
        Retains float coordinates without integer truncation.
        """
        if self.classes and label not in self.classes and str(cls_id) not in self.classes:
            return None

        try:
            x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
            confidence = float(confidence)
        except (ValueError, TypeError):
            return None

        # Validate finite numerical values
        if not (
            math.isfinite(x1)
            and math.isfinite(y1)
            and math.isfinite(x2)
            and math.isfinite(y2)
            and math.isfinite(confidence)
        ):
            return None

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

        # Reject degenerate zero or negative area boxes
        if bw <= 0.0 or bh <= 0.0:
            return None

        # Defensive geometric filtering (area and aspect ratio)
        if img_w is not None and img_h is not None and (img_w * img_h) > 0:
            area_ratio = (bw * bh) / float(img_w * img_h)
            if self.min_area_ratio > 0.0 and area_ratio < self.min_area_ratio:
                return None
            if self.max_area_ratio < 1.0 and area_ratio > self.max_area_ratio:
                return None

        if self.max_aspect_ratio > 0.0 and bw > 0.0 and bh > 0.0:
            aspect_ratio = max(bw / bh, bh / bw)
            if aspect_ratio > self.max_aspect_ratio:
                return None

        return Detection(bbox_xyxy=(x1, y1, x2, y2), confidence=confidence, label=label)

    @staticmethod
    def _letterbox_image(
        image: np.ndarray, target_shape: Tuple[int, int], color=(114, 114, 114)
    ) -> Tuple[np.ndarray, float, Tuple[float, float]]:
        """
        Resize and pad image while meeting target shape constraints (aspect ratio preserved).
        Returns: (padded_image, scale_ratio, (pad_left, pad_top))
        """
        import cv2

        target_h, target_w = target_shape
        img_h, img_w = image.shape[:2]

        r = min(target_w / float(img_w), target_h / float(img_h))
        new_unpad_w, new_unpad_h = int(round(img_w * r)), int(round(img_h * r))

        dw = target_w - new_unpad_w
        dh = target_h - new_unpad_h
        pad_left = dw / 2.0
        pad_top = dh / 2.0

        if (img_w, img_h) != (new_unpad_w, new_unpad_h):
            resized = cv2.resize(image, (new_unpad_w, new_unpad_h), interpolation=cv2.INTER_LINEAR)
        else:
            resized = image

        top = int(round(pad_top - 0.1))
        bottom = int(round(dh - top))
        left = int(round(pad_left - 0.1))
        right = int(round(dw - left))

        padded = cv2.copyMakeBorder(
            resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color
        )
        return padded, r, (pad_left, pad_top)

    def _sync_predict_onnxruntime(self, image) -> List[Detection]:
        """
        Fallback prediction path when using pure ONNX Runtime without Ultralytics.
        Applies letterbox preprocessing, reads labels from model metadata,
        supports both end-to-end [1, N, 6] and anchor [1, 5, 8400] formats,
        and applies shared candidate filtering without integer truncation.
        """
        import cv2

        img_h, img_w = image.shape[:2]
        target_h, target_w = (self.imgsz, self.imgsz) if isinstance(self.imgsz, int) else self.imgsz

        padded_img, r, (pad_left, pad_top) = self._letterbox_image(image, (target_h, target_w))
        rgb = cv2.cvtColor(padded_img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]

        input_name = self._ort_session.get_inputs()[0].name
        raw_outputs = self._ort_session.run(None, {input_name: blob})
        if not raw_outputs:
            return []

        out = raw_outputs[0]

        # Check for end-to-end NMS output format [1, N, 6] or [N, 6]
        if (out.ndim == 3 and out.shape[2] == 6) or (out.ndim == 2 and out.shape[1] == 6):
            rows = out[0] if out.ndim == 3 else out
            detections: List[Detection] = []
            for row in rows:
                score = float(row[4])
                if score < self.confidence:
                    continue
                cls_id = int(row[5])
                label = self._get_class_name(cls_id)
                # Unletterbox coordinates back to original image space
                x1 = (float(row[0]) - pad_left) / r
                y1 = (float(row[1]) - pad_top) / r
                x2 = (float(row[2]) - pad_left) / r
                y2 = (float(row[3]) - pad_top) / r
                det = self.filter_candidate_box(
                    x1, y1, x2, y2, score, label, cls_id=cls_id, img_w=img_w, img_h=img_h
                )
                if det is not None:
                    detections.append(det)
            return detections

        # Shape is (1, 4 + num_classes, num_anchors) e.g. (1, 5, 8400)
        # Transpose so rows are anchors and columns are [cx, cy, w, h, score...]
        if out.ndim == 3:
            if out.shape[1] in (5, 6, 84, 85) and out.shape[2] != out.shape[1]:
                out = np.transpose(out[0], (1, 0))
            elif out.shape[2] in (5, 6, 84, 85) and out.shape[1] != out.shape[2]:
                out = out[0]
            elif out.shape[1] < out.shape[2]:
                out = np.transpose(out[0], (1, 0))
            else:
                out = out[0]

        boxes_ltrb = []
        confidences = []
        class_ids = []

        for row in out:
            classes_scores = row[4:]
            if len(classes_scores) == 0:
                continue
            cls_id = int(np.argmax(classes_scores))
            score = float(classes_scores[cls_id])
            if score >= self.confidence:
                cx, cy, bw, bh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                # Unletterbox candidate center and bounds to original image space
                x1_lb = cx - (bw / 2.0)
                y1_lb = cy - (bh / 2.0)
                x2_lb = cx + (bw / 2.0)
                y2_lb = cy + (bh / 2.0)

                x1 = (x1_lb - pad_left) / r
                y1 = (y1_lb - pad_top) / r
                x2 = (x2_lb - pad_left) / r
                y2 = (y2_lb - pad_top) / r

                boxes_ltrb.append((x1, y1, x2, y2))
                confidences.append(score)
                class_ids.append(cls_id)

        detections: List[Detection] = []
        if not boxes_ltrb:
            return detections

        nms_boxes = [[int(x1), int(y1), int(x2 - x1), int(y2 - y1)] for x1, y1, x2, y2 in boxes_ltrb]
        indices = cv2.dnn.NMSBoxes(nms_boxes, confidences, self.confidence, self.iou)
        if len(indices) == 0:
            return detections

        for idx in indices.flatten():
            x1, y1, x2, y2 = boxes_ltrb[idx]
            conf = confidences[idx]
            cls_id = class_ids[idx]
            label = self._get_class_name(cls_id)

            det = self.filter_candidate_box(
                x1, y1, x2, y2, conf, label, cls_id=cls_id, img_w=img_w, img_h=img_h
            )
            if det is not None:
                detections.append(det)

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

            xyxy = box.xyxy[0]
            if hasattr(xyxy, "tolist"):
                xyxy = xyxy.tolist()

            try:
                x1, y1, x2, y2 = float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])
            except (ValueError, IndexError, TypeError):
                continue

            det = self.filter_candidate_box(
                x1, y1, x2, y2, conf, label, cls_id=cls_id, img_w=img_w, img_h=img_h
            )
            if det is not None:
                detections.append(det)

        return detections

        return detections

    async def detect(self, image) -> List[Detection]:
        """Asynchronously run prediction on an image in the dedicated worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._sync_predict, image)

    def close(self):
        """Shutdown the inference worker thread."""
        self._executor.shutdown(wait=False)
