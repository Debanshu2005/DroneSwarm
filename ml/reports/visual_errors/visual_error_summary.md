# Visual Error Analysis Report

This report documents visual inspection of representative detections and error failure modes on the UAVVaste validation split.

**Color Conventions:**
- **Green Box**: Ground Truth Rubbish Annotation
- **Blue Box**: True Positive Prediction (Matches Ground Truth)
- **Red Box**: False Positive Prediction (No Ground Truth match)
- **Orange Dashed Box**: False Negative (Missed Ground Truth Object)

## True Positives
Identified 5 representative instances:

- **Sample 1** (`BATCH_d07_img_2600.jpg`):
  - Crop saved: `true_positives_1_BATCH_d07_img_2600.jpg`
  - Confidence: `0.861`
  - Matching IoU: `0.860`

- **Sample 2** (`BATCH_d07_img_2600.jpg`):
  - Crop saved: `true_positives_2_BATCH_d07_img_2600.jpg`
  - Confidence: `0.822`
  - Matching IoU: `0.874`

- **Sample 3** (`BATCH_d07_img_2600.jpg`):
  - Crop saved: `true_positives_3_BATCH_d07_img_2600.jpg`
  - Confidence: `0.767`
  - Matching IoU: `0.845`

- **Sample 4** (`BATCH_d07_img_2600.jpg`):
  - Crop saved: `true_positives_4_BATCH_d07_img_2600.jpg`
  - Confidence: `0.752`
  - Matching IoU: `0.774`

- **Sample 5** (`batch_s02_img_121.jpg`):
  - Crop saved: `true_positives_5_batch_s02_img_121.jpg`
  - Confidence: `0.808`
  - Matching IoU: `0.798`

## False Positives
Identified 5 representative instances:

- **Sample 1** (`BATCH_d07_img_2600.jpg`):
  - Crop saved: `false_positives_1_BATCH_d07_img_2600.jpg`
  - Confidence: `0.759`

- **Sample 2** (`BATCH_d07_img_2600.jpg`):
  - Crop saved: `false_positives_2_BATCH_d07_img_2600.jpg`
  - Confidence: `0.502`

- **Sample 3** (`GOPR0028.JPG`):
  - Crop saved: `false_positives_3_GOPR0028.jpg`
  - Confidence: `0.405`

- **Sample 4** (`BATCH_d08_img_350.jpg`):
  - Crop saved: `false_positives_4_BATCH_d08_img_350.jpg`
  - Confidence: `0.584`

- **Sample 5** (`BATCH_d08_img_350.jpg`):
  - Crop saved: `false_positives_5_BATCH_d08_img_350.jpg`
  - Confidence: `0.504`

## False Negatives
Identified 5 representative instances:

- **Sample 1** (`GOPR0028.JPG`):
  - Crop saved: `false_negatives_1_GOPR0028.jpg`

- **Sample 2** (`GOPR0028.JPG`):
  - Crop saved: `false_negatives_2_GOPR0028.jpg`

- **Sample 3** (`GOPR0028.JPG`):
  - Crop saved: `false_negatives_3_GOPR0028.jpg`

- **Sample 4** (`BATCH_s03_img_2220.jpg`):
  - Crop saved: `false_negatives_4_BATCH_s03_img_2220.jpg`

- **Sample 5** (`BATCH_s03_img_2220.jpg`):
  - Crop saved: `false_negatives_5_BATCH_s03_img_2220.jpg`

## Clusters
Identified 5 representative instances:

- **Sample 1** (`batch_03_img_820.jpg`):
  - Crop saved: `clusters_1_batch_03_img_820.jpg`
  - Matching IoU: `0.177`

- **Sample 2** (`batch_s02_img_86.jpg`):
  - Crop saved: `clusters_2_batch_s02_img_86.jpg`
  - Matching IoU: `0.160`

- **Sample 3** (`batch_s02_img_86.jpg`):
  - Crop saved: `clusters_3_batch_s02_img_86.jpg`
  - Matching IoU: `0.115`

- **Sample 4** (`BATCH_s05_img_9110.jpg`):
  - Crop saved: `clusters_4_BATCH_s05_img_9110.jpg`
  - Matching IoU: `0.493`

- **Sample 5** (`photo_2020-10-23_14-02-05.jpg`):
  - Crop saved: `clusters_5_photo_2020-10-23_14-02-05.jpg`
  - Matching IoU: `0.103`
