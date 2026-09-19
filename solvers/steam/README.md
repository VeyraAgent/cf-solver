# Steam — character sprite segmentation + SVM classify

`solvers/steam/` — port of [scholtzm/opencv-steam-captcha](https://github.com/scholtzm/opencv-steam-captcha) (C++) to Python cv2/numpy.

Steam community captcha (`steamcommunity.com/tradeoffer/new/captcha`) shows 6 distorted character sprites that must be identified. The character set is A-Z + 0-9 minus {0,1,5,6,O,I,S} + special chars {@, %, &} — **32 classes**.

## How it works

Algorithm faithfully ported from the C++ original:

1. **Preprocessing** — resize 2×, adaptive threshold (mean C, block 3), histogram-based optimal threshold, binary threshold, morphological close (3×3 ellipse dilate+erode).
2. **Segmentation** — column-projection (per-column white pixel count) → contiguous horizontal bands; row-projection → vertical span (largest band = text row). Cross-product → rectangles. Shrink to tightest content bounding box. Take top-6 by area, sort by x-position.
3. **Classification** — each 32×48 character crop → simple descriptor (flattened [0,1] float) or HOG descriptor → SVM predict → character label.

## Model status

**No pre-trained model ships with this solver.** The original C++ project trained only on 100 labeled images (~19 samples/char) and could only reliably distinguish G and Y. The segmentation engine works well (95% character extraction rate per the original paper).

To make classification work:

1. **Collect data** — 100+ labeled captcha images per character (3200+ total). Each image filename must be the captcha text (e.g., `2GCZ4A.png`).
2. **Segment** — run `segment_characters()` to extract 32×48 character crops.
3. **Train** — run `train_model(data_dir)` which auto-saves to `steam_svm.pkl`. Uses sklearn SVC (linear kernel) if available, falls back to OpenCV SVM.
4. **Deploy** — place `steam_svm.pkl` alongside `solve.py`. The solver auto-loads it.

```python
from solvers.steam.solve import train_model
model = train_model("/path/to/labeled/crops/")
# → saves steam_svm.pkl
```

## Usage

```python
import asyncio
from solvers.steam.solve import solve_steam

# From base64 image
res = asyncio.run(solve_steam(image_b64="<base64 png>"))

# From URL
res = asyncio.run(solve_steam(url="https://steamcommunity.com/tradeoffer/new/captcha?..."))

# From cv2 ndarray
import cv2
img = cv2.imread("captcha.png", cv2.IMREAD_GRAYSCALE)
res = asyncio.run(solve_steam(image=img))
```

Result (uniform contract, never raises):

```python
{
    "solved": True,            # False if no model or segmentation fails
    "type": "steam",
    "token": "ABCDEF",         # the 6-char captcha text
    "method": "segment+svm",
    "char_count": 6,
    "elapsed": 0.15,
    "error": None,
}
```

Without a trained model:

```python
{
    "solved": False,
    "type": "steam",
    "token": "",
    "method": "segment+no-model",
    "char_count": 4,           # segmentation still works
    "elapsed": 0.08,
    "error": "no trained model — segmentation succeeded (4 chars) but ...",
}
```

## Segmentation only

Use `segment_characters()` directly for character extraction without classification:

```python
from solvers.steam.solve import segment_characters
import cv2

gray = cv2.imread("captcha.png", cv2.IMREAD_GRAYSCALE)
crops = segment_characters(gray)  # list of 32×48 uint8 arrays
```

## Test on reference data

Self-test runs on the 100 reference images from `scholtzm/opencv-steam-captcha`:

```bash
python3 solvers/steam/selftest.py
```

No model needed — tests validate segmentation pipeline, descriptor shapes, contract shape, and alias handling.

## Character set

```
234789ABCDEFGHJKLMNPQRTUVWXYZ@&%
```

Characters 0, 1, 5, 6, O, I, S are never used in Steam captchas.

Special characters are aliased in filenames: `@` → `at`, `%` → `pct`, `&` → `and`.

## References

- scholtzm/opencv-steam-captcha — C++ original (MIT), 100 labeled images + algorithm.
- steamcommunity.com captcha endpoint: `/tradeoffer/new/captcha?v={timestamp}&sessionid={sid}&partner={pid}`
