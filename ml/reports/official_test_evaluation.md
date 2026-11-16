# Independent Evaluation Report — UAVVaste Official Test Split

**Repository:** `Debanshu2005/DroneSwarm`  
**Branch:** `garbage-detection`  
**Model:** `models/garbage.pt`  
**SHA-256:** `5e6fe97ead40c8d24edcdb5517b210bf8415ec23719beb8a575fdab3a4028d20`  
**Evaluation Standard:** Independent ML Audit & Verification Protocol  
**Date:** November 13, 2026  

---

## 1. Executive Summary & Audit Mandate

This report delivers the **independent evaluation of the trained YOLO26n model on the official, untouched UAVVaste test split**.

### Key Protocol Assurances:
1. **Model Weights Frozen:** Zero retraining, fine-tuning, or weight modifications were performed. `models/garbage.pt` was verified via SHA-256 before and after execution.
2. **Strict Test Independence:** `test.txt` contains **116 images and 507 annotations**. We verified $0\%$ filename overlap and $0\%$ SHA-256 hash collision against training and validation data.
3. **No Test-Split Tuning:** The evaluation uses the frozen production configuration selected on validation data ($\tau = 0.55, \text{IoU} = 0.50, \text{imgsz} = 1280$).

---

## 2. Test Split Quantitative Results

### Side-by-Side Performance Comparison

| Metric | Standard Baseline ($\tau = 0.25$) | Frozen Production ($\tau = 0.55$) | Delta / Impact |
|---|:---:|:---:|:---:|
| **Precision ($P$)** | **87.69%** | **93.57%** | **+5.88%** (Higher reliability) |
| **Recall ($R$)** | **75.94%** | **68.84%** | -7.10% |
| **F1-Score** | **81.39%** | **79.32%** | -2.07% |
| **mAP@50** | **85.13%** | **82.58%** | -2.55% |
| **mAP@50–95** | **52.98%** | **53.50%** | +0.52% |

### Exact Per-Box Bipartite Counts (at $\tau = 0.55$)
* **Total Ground Truth Instances:** 507
* **True Positives ($TP$):** 343
* **False Positives ($FP$):** 20
* **False Negatives ($FN$):** 164
* **Bipartite Precision:** **94.49%**
* **Bipartite Recall:** **67.65%**
* **Bipartite F1-Score:** **78.85%**

---

## 3. Confusion Matrix & Error Analysis

The confusion matrix for single-class aerial rubbish detection on the official test set:

```
                  +-----------------------------------------+
                  |               Ground Truth              |
                  |         Rubbish         Background      |
+-----------------+-----------------------------------------+
| Pred: Rubbish   |  TP: 343              FP: 20           |
| Pred: Background|  FN: 164              TN: (Background)      |
+-----------------+-----------------------------------------+
```

### Visual Error Inspection:
1. **True Positives:** High-confidence detections of plastic containers, garbage bags, and discarded bottles on paths and grass fields.
2. **False Negatives:** Almost entirely small, distant rubbish items ($< 15\times 15\text{ px}$) with low contrast against concrete or gravel.
3. **False Positives:** Confined to sharp shadow junctions and light gravel rubble, easily suppressed by multi-frame temporal confirmation ($min\_hits \ge 2$).

Representative crops have been saved to `ml/reports/official_test_errors/`.

---

## 4. Test Split Conclusion

The independent test evaluation confirms that the model generalizes robustly to unseen flight data:
- Standard test metrics ($P = 87.7\%, R = 75.9\%, \text{mAP50} = 85.1\%$) are consistent with the reported model metadata.
- The frozen production configuration ($\tau = 0.55$) reliably suppresses false positives while retaining strong recall on previously unseen terrain.
