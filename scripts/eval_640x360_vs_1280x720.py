"""
Evaluate recall effect of 640x360 input vs 1280x720 input on the real UAVVaste test split.
Keeps imgsz=640 unchanged. Evaluates at conf=0.55 and conf=0.35.
"""

from pathlib import Path
import sys
import cv2

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.types import Detection
from scripts.eval_cuda_fp16_vs_fp32 import load_test_data, evaluate_detections

def main():
    samples = load_test_data()
    print(f"Loaded {len(samples)} test samples with GT boxes.")

    for conf in [0.55, 0.35]:
        det = GarbageDetector(weights_path="models/garbage.pt", imgsz=640, confidence=conf, iou=0.5, device="cuda")
        det.load()

        all_gt = []
        all_dt_1280 = []
        all_dt_640 = []

        for img_p, gt_boxes in samples:
            img = cv2.imread(str(img_p))
            if img is None:
                continue
            h_orig, w_orig = img.shape[:2]

            # 1280x720 capture baseline
            img_1280 = cv2.resize(img, (1280, 720))
            dets_1280 = det._sync_predict(img_1280)
            scaled_1280 = []
            for d in dets_1280:
                sx1 = d.bbox_xyxy[0] * (w_orig / 1280.0)
                sy1 = d.bbox_xyxy[1] * (h_orig / 720.0)
                sx2 = d.bbox_xyxy[2] * (w_orig / 1280.0)
                sy2 = d.bbox_xyxy[3] * (h_orig / 720.0)
                scaled_1280.append(Detection(
                    bbox_xyxy=(sx1, sy1, sx2, sy2),
                    confidence=d.confidence,
                    label=d.label,
                ))

            # 640x360 capture mitigation
            img_640 = cv2.resize(img, (640, 360))
            dets_640 = det._sync_predict(img_640)
            scaled_640 = []
            for d in dets_640:
                sx1 = d.bbox_xyxy[0] * (w_orig / 640.0)
                sy1 = d.bbox_xyxy[1] * (h_orig / 360.0)
                sx2 = d.bbox_xyxy[2] * (w_orig / 640.0)
                sy2 = d.bbox_xyxy[3] * (h_orig / 360.0)
                scaled_640.append(Detection(
                    bbox_xyxy=(sx1, sy1, sx2, sy2),
                    confidence=d.confidence,
                    label=d.label,
                ))

            all_gt.append(gt_boxes)
            all_dt_1280.append(scaled_1280)
            all_dt_640.append(scaled_640)

        res_1280 = evaluate_detections(all_dt_1280, all_gt, iou_thresh=0.5)
        res_640 = evaluate_detections(all_dt_640, all_gt, iou_thresh=0.5)

        print(f"\n================ CONF {conf:.2f} ================")
        print(f"1280x720 capture: Precision={res_1280['precision']:.4f}, Recall={res_1280['recall']:.4f}, F1={res_1280['f1']:.4f} (TP={res_1280['tp']}, FP={res_1280['fp']}, FN={res_1280['fn']})")
        print(f"640x360 capture:  Precision={res_640['precision']:.4f}, Recall={res_640['recall']:.4f}, F1={res_640['f1']:.4f} (TP={res_640['tp']}, FP={res_640['fp']}, FN={res_640['fn']})")
        delta_r = res_640['recall'] - res_1280['recall']
        delta_f1 = res_640['f1'] - res_1280['f1']
        print(f"Delta Recall:     {delta_r:+.4f} ({delta_r*100:+.2f}%)")
        print(f"Delta F1:         {delta_f1:+.4f}")
        det.close()

if __name__ == "__main__":
    main()
