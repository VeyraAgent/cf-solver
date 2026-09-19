# Binance Slide Captcha Solver

Port of [xKiian/binance-captcha-solver](https://github.com/xKiian/binance-captcha-solver) (92 stars, MIT).

## Captcha Type

**SLIDE** — combined image (puzzle piece 60px left | background right). User drags piece to correct X position.

## Solving Method

1. **Split** combined image at 60px boundary
2. **Canny edge detection** (100/200 thresholds) on both piece and background
3. **cv2.matchTemplate** `TM_CCOEFF_NORMED` on edge-detected RGB images
4. **X offset** = `max_loc.x + piece_width//2 - 31` (empirical correction)

Core solver is pure cv2/numpy — no ML model needed.

## Encryption

Binance uses a custom XOR cipher (NOT AES) with key derivation:
- Default key: `"cdababcddcba"` (reversed + derived extra chars)
- Binance provides a per-captcha `ek` key via `getCaptcha` API
- Payload: UTF-16 → UTF-8 → XOR with derived key → custom base64

## Usage

### Local Mode (image only)
```python
from solvers.binance.solve import solve_binance

result = await solve_binance(image_url="https://bin.bnbstatic.com/...")
# result = {"solved": True, "type": "binance", "token": "169", "method": "binance_slide_local", ...}
# token = pixel X offset as string
```

### Full Mode (with Binance API)
```python
result = await solve_binance(
    biz_id="register",
    security_check_response_validate_id="<from precheck>",
    # requires curl_cffi installed
)
# token = Binance captcha validation token
```

## Dependencies

- `cv2` (opencv-python), `numpy` — slide solver core
- `httpx` — image download
- `curl_cffi` — **optional**, only for full Binance API mode

## Response Format

```json
{
  "solved": true,
  "type": "binance",
  "token": "169",
  "method": "binance_slide_local",
  "elapsed": 0.2,
  "error": null
}
```
