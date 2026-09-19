# Zhihu legacy text-captcha solver

Recognizes Zhihu's **legacy 4-character alphanumeric captcha** — the text-based
kind with distorted thin/bold font characters (150×60 grayscale GIF) that Zhihu
used before switching to Tencent slider.

## Captcha type

| Property | Value |
| --- | --- |
| **Type** | Text recognition (4-char alphanumeric) |
| **Characters** | `0-9`, `a-z`, `A-Z` (62 possible per position) |
| **Image** | 150×60 px, grayscale, GIF format |
| **Variants** | Thin font, bold font (mixed training works fine) |
| **Status** | **Deprecated** — Zhihu switched to Tencent slider captcha |

> **Honest note.** Zhihu no longer uses this captcha. This solver covers the
> legacy format only. For the current Tencent slider, use `solvers/tencent`.

## Backends

| Backend | Accuracy | Notes |
| --- | --- | --- |
| **ONNX CNN** (primary) | ~90% on real zhihu captchas | Requires trained model (`zhihu_model.onnx`). TF CNN from lonnyzhang423/zhihu-captcha — 3 conv layers + FC1024, 248 outputs (4×62). |
| **ddddocr** (fallback) | ~60-70% | Chinese OCR engine, works out-of-box on distorted chars. No training needed. |
| cv2 preprocessing | — | Adaptive threshold + morphological denoise applied before ONNX inference. |

### Training requirement

The reference repo (lonnyzhang423/zhihu-captcha) provides **training code and
sample data format** but **not pre-trained weights**. To get the ~90% accuracy:

1. Collect zhihu captcha samples (the repo has 200 base64-encoded samples as
   examples; you need ~2000+ for good accuracy)
2. Train using the TF CNN architecture from the reference repo
3. Export to ONNX: `tf2onnx.convert` or `torch.onnx.export` after porting
4. Place `zhihu_model.onnx` in this solver directory

Without the trained model, the solver uses ddddocr which gives reasonable but
lower accuracy (~60-70%).

## Usage

```python
from solvers.zhihu import solve_zhihu

result = await solve_zhihu(
    image_b64="R0lGODdhlgA8AIcA...",   # base64 captcha image
    # url="https://...",                 # or direct URL
    # challenge_blob=pil_image,          # or PIL/ndarray
    timeout_s=60,
)
# result["token"]    = "aB3x"    ← recognized text
# result["solved"]   = True/False
# result["backend"]  = "onnx" | "ddddocr"
# result["needs_training"] = True/False
```

Result contract: `{"solved": bool, "type": "zhihu", "token": str,
"method": str, "elapsed": float, "error": str|None, "backend": str,
"confidence": str, "needs_training": bool}` — never raises.

### Parameters

| Param | Default | Notes |
| --- | --- | --- |
| `image_b64` | — | base64-encoded captcha image (PNG/GIF/JPEG) |
| `url` | — | direct URL to fetch the captcha image |
| `challenge_blob` | — | raw bytes, PIL Image, or ndarray |
| `captcha_type` | — | accepted for API compat, unused |
| `proxy` | — | proxy for URL fetch (curl_cffi) |
| `timeout_s` | `60` | overall budget |

## Self-test

```bash
python3 -m solvers.zhihu.selftest
```

Covers: config constants, image preprocessing (decode/resize/normalize/enhance),
uniform contract error paths (no input, bad base64), backend detection, synthetic
image end-to-end (contract + no crash), and predict_text direct call.

## ONNX model architecture

If training your own model, the reference architecture from
lonnyzhang423/zhihu-captcha is:

```
Input: (batch, 9000)  →  reshape to (batch, 60, 150, 1)

Conv2D(3×3, 32, relu, same) → MaxPool(2×2) → Dropout(0.5)
Conv2D(3×3, 64, relu, same) → MaxPool(2×2) → Dropout(0.5)
Conv2D(3×3, 64, relu, same) → MaxPool(2×2) → Dropout(0.5)

Flatten → Dense(1024, relu) → Dropout(0.5) → Dense(248)

Output: (batch, 248)  →  reshape to (batch, 4, 62)  →  argmax per position
```

## Files

```text
solvers/zhihu/
├── solve.py       entry point (solve_zhihu), preprocessing, ONNX + ddddocr backends
├── selftest.py    offline unit tests (preprocessing + contract + synthetic end-to-end)
└── README.md      this file
```

## Credits

| Source | What was taken |
| --- | --- |
| [lonnyzhang423/zhihu-captcha](https://github.com/lonnyzhang423/zhihu-captcha) | CNN architecture, config constants (CHAR_SET, IMG dimensions), sample format, preprocessing approach |
| [sml2h3/ddddocr](https://github.com/sml2h3/ddddocr) | Fallback OCR engine for Chinese/distorted text recognition |
