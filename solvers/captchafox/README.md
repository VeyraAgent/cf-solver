# CaptchaFox Solver

Solves CaptchaFox slide captchas with encryption protocol support.

## What is CaptchaFox?

CaptchaFox is a slide-captcha system (similar to FunCaptcha/Arkose) that uses a client-side encryption layer to obfuscate device fingerprint and solution data before sending to the server. See [captchafox.com](https://captchafox.com/#demo) for a live demo.

## Encryption Protocol

From [notemrovsky/captchafox-encryption](https://github.com/notemrovsky/captchafox-encryption):

```
Encrypt: JSON → gzip.compress → XOR((byte, (index + 4) % 256)) → prepend [0x01, 0x04]
Decrypt: strip header → XOR((byte, (index + 4) % 256)) → gzip.decompress → JSON.parse
```

**No site-specific keys** — the XOR key is derived from byte index. The encryption is purely obfuscation, not cryptographic security.

## Boundary: Fingerprint Data

CaptchaFox includes browser fingerprint data (canvas, WebGL, user agent, etc.) in the encrypted payload. Two modes:

1. **With real fingerprint** (browser-based caller): Pass `fingerprint_data` dict from browser JS runtime. Sites with strict validation require this.

2. **Synthetic fingerprint** (headless/CLI): If `fingerprint_data` is None, generates a minimal synthetic fingerprint. Works for basic validation; sites with advanced fingerprint consistency checks may reject.

## Usage

### Slide Offset Detection (Primary Mode)

```python
from solvers.captchafox.solve import solve_captchafox
import asyncio

# With base64 images
result = asyncio.run(solve_captchafox(
    image_b64="<background_image_base64>",
    piece_b64="<puzzle_piece_base64>",
    host="example.com",
))
# result = {solved: True, type: "captchafox", token: "<encrypted_solution_b64>", offset: 142, ...}

# With URLs
result = asyncio.run(solve_captchafox(
    image_url="https://example.com/bg.jpg",
    piece_url="https://example.com/piece.png",
    proxy="http://proxy:8080",
))
```

### Encrypt/Decrypt Data

```python
import asyncio, json

# Encrypt a payload
result = asyncio.run(solve_captchafox(
    image_b64=json.dumps({"offset": 142, "ts": 1234567890}),
))
# result["token"] = base64-encoded encrypted data

# Decrypt existing data
result = asyncio.run(solve_captchafox(
    encrypted_data="<base64_or_bytes>",
))
# result["decrypted"] = original dict
```

## Solver Pipeline

1. Accept background image (b64/URL) + puzzle piece image (b64/URL)
2. Use cv2 Canny edge detection + template matching (TM_CCOEFF_NORMED) to find X offset
3. Encrypt solution payload with CaptchaFox protocol
4. Return encrypted solution as base64-encoded token

## Multi-Method Matching

Pass `advanced=True` to use multi-method offset detection with confidence scoring (TM_CCOEFF_NORMED, TM_CCORR_NORMED, TM_SQDIFF_NORMED) on both edge and color channels. Slower (~2-3x) but more robust against varying backgrounds.

## Dependencies

- `cv2` (opencv-python) — image processing
- `numpy` — array operations
- `curl_cffi` (optional) — Chrome-impersonated HTTP fetch
- stdlib: `gzip`, `json`, `base64`, `asyncio`

## Entry Point

```python
async def solve_captchafox(
    image_b64: Optional[str] = None,
    image_url: Optional[str] = None,
    piece_b64: Optional[str] = None,
    piece_url: Optional[str] = None,
    encrypted_data: Optional[bytes | str] = None,
    host: str = "unknown",
    fingerprint_data: Optional[dict] = None,
    advanced: bool = False,
    proxy: Optional[str] = None,
    timeout_s: int = 60,
) -> dict
```

Returns: `{solved, type, token, method, elapsed, error}`

On success: `token` = base64-encoded encrypted solution, `offset` = slide offset in pixels.

## Server Wiring

```python
# In server.py dispatch:
elif req.type == "captchafox":
    from solvers.captchafox.solve import solve_captchafox
    resp = await solve_captchafox(
        image_b64=req.image_b64,
        image_url=req.image,
        piece_b64=req.challenge,  # piece image in challenge field
        host=req.site_url or "unknown",
        timeout_s=req.timeout,
    )
```

## Reference

- [notemrovsky/captchafox-encryption](https://github.com/notemrovsky/captchafox-encryption) — encryption protocol
- [captchafox.com](https://captchafox.com/#demo) — live demo
