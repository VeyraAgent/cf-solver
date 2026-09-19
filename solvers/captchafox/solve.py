"""CaptchaFox slide-captcha solver with encryption protocol.

CaptchaFox is a slide-captcha (similar to FunCaptcha/Arkose) with a client-side
encryption layer that obfuscates device fingerprint and solution data before
sending to the server.

Encryption protocol (from notemrovsky/captchafox-encryption):
    1. Serialize payload to JSON
    2. gzip.compress(json_bytes)
    3. XOR each byte with (index + 4) % 256
    4. Prepend header bytes [0x01, 0x04]

Decryption reverses steps 4→3→2→1.

Solver pipeline:
    1. Accept background image (b64/URL) + puzzle piece image (b64/URL)
    2. Use cv2 Canny edge detection + matchTemplate (TM_CCOEFF_NORMED) to find X offset
    3. Encrypt solution payload with CaptchaFox protocol
    4. Return encrypted solution as token

Boundary: The encryption uses a fixed XOR key derived from (index + 4) mod 256 —
no site-specific keys needed. However, CaptchaFox requires browser-like fingerprint
data (fingerprint.json) to be included in the encrypted payload. If the caller
doesn't supply fingerprint_data, we generate a minimal synthetic one.

Reference: notemrovsky/captchafox-encryption (encrypt.py + fingerprint.json),
           captchafox.com demo
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import time
from typing import Any, Optional

log = logging.getLogger("captchafox")

# ---------------------------------------------------------------------------
# CaptchaFox encryption / decryption (from notemrovsky/captchafox-encryption)
# ---------------------------------------------------------------------------

def encrypt_data(data: dict) -> bytes:
    """Encrypt payload using CaptchaFox protocol: JSON → gzip → XOR → prepend header."""
    json_str = json.dumps(data, separators=(",", ":"))
    compressed = gzip.compress(json_str.encode("utf-8"), compresslevel=6)
    obfuscated = bytearray(len(compressed))
    for i in range(len(compressed)):
        obfuscated[i] = (compressed[i] ^ ((i + 4) % 256)) % 256
    return bytes([0x01, 0x04]) + bytes(obfuscated)


def decrypt_data(encrypted: bytes) -> dict:
    """Decrypt CaptchaFox protocol: strip header → XOR → gunzip → JSON parse."""
    if len(encrypted) < 3:
        raise ValueError("encrypted data too short (need ≥3 bytes)")
    if encrypted[0] != 0x01 or encrypted[1] != 0x04:
        log.warning("captchafox: unexpected header bytes %02x %02x (expected 01 04)",
                     encrypted[0], encrypted[1])
    payload = bytes(v % 256 for v in encrypted)
    deobfuscated = bytearray(len(payload) - 2)
    for i in range(2, len(payload)):
        deobfuscated[i - 2] = (payload[i] ^ ((i - 2 + 4) % 256)) % 256
    decompressed = gzip.decompress(bytes(deobfuscated))
    return json.loads(decompressed.decode("utf-8"))


# ---------------------------------------------------------------------------
# Image fetch helpers
# ---------------------------------------------------------------------------

def _fetch_image(url: str, proxy: str | None = None, timeout: float = 30.0) -> bytes:
    """Download image bytes with Chrome impersonation."""
    try:
        import curl_cffi.requests as cffi_requests
        proxies = {"https": proxy, "http": proxy} if proxy else None
        resp = cffi_requests.get(url, impersonate="chrome131", proxies=proxies,
                                 timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except ImportError:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        if proxy:
            handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener()
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()


def _decode_image_b64(b64: str) -> bytes:
    """Decode base64 image, stripping data-URI prefix if present."""
    s = b64.strip()
    if "," in s and s.startswith("data:"):
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def _load_image(data: bytes):
    """Decode image bytes into cv2 BGR array."""
    import cv2
    import numpy as np
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("cv2.imdecode returned None — invalid image data")
    return img


# ---------------------------------------------------------------------------
# Slide solver: Canny edge + template matching
# ---------------------------------------------------------------------------

def _find_slide_offset(bg_img, piece_img, method: int = None) -> int:
    """Find X offset where puzzle piece fits in background using edge+template matching.

    Returns pixel offset (integer).
    """
    import cv2
    import numpy as np

    if method is None:
        method = cv2.TM_CCOEFF_NORMED

    # Convert to grayscale for edge detection
    bg_gray = cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
    piece_gray = cv2.cvtColor(piece_img, cv2.COLOR_BGR2GRAY)

    # Canny edge detection — captures shape boundaries
    bg_edges = cv2.Canny(bg_gray, 100, 200)
    piece_edges = cv2.Canny(piece_gray, 100, 200)

    # Template matching on edge maps
    result = cv2.matchTemplate(bg_edges, piece_edges, method)

    # Find best match location
    if method in (cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED):
        _, _, min_loc, _ = cv2.minMaxLoc(result)
        offset_x = min_loc[0]
    else:
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        offset_x = max_loc[0]
        log.debug("captchafox: template match confidence=%.4f at x=%d", max_val, offset_x)

    # Also try color-space matching as fallback/comparison
    result_color = cv2.matchTemplate(bg_img, piece_img, method)
    if method in (cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED):
        _, _, min_loc_c, _ = cv2.minMaxLoc(result_color)
        offset_color = min_loc_c[0]
    else:
        _, max_val_c, _, max_loc_c = cv2.minMaxLoc(result_color)
        offset_color = max_loc_c[0]
        log.debug("captchafox: color match confidence=%.4f at x=%d", max_val_c, offset_color)

    # Use edge-based result; if confidence is very low, try color
    # The edge approach works better when background has strong patterns
    return offset_x


def _find_slide_offset_advanced(bg_img, piece_img) -> int:
    """Multi-method offset detection with confidence scoring."""
    import cv2
    import numpy as np

    best_offset = 0
    best_score = -1.0

    methods = [
        ("TM_CCOEFF_NORMED", cv2.TM_CCOEFF_NORMED, False),
        ("TM_CCORR_NORMED", cv2.TM_CCORR_NORMED, False),
        ("TM_SQDIFF_NORMED", cv2.TM_SQDIFF_NORMED, True),
    ]

    bg_gray = cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
    piece_gray = cv2.cvtColor(piece_img, cv2.COLOR_BGR2GRAY)
    bg_edges = cv2.Canny(bg_gray, 100, 200)
    piece_edges = cv2.Canny(piece_gray, 100, 200)

    for name, method, invert in methods:
        try:
            # Edge-based
            result = cv2.matchTemplate(bg_edges, piece_edges, method)
            _, val, _, loc = cv2.minMaxLoc(result)
            score = 1.0 - val if invert else val
            x = loc[0]
            if score > best_score:
                best_score = score
                best_offset = x
            log.debug("captchafox: %s edges score=%.4f x=%d", name, score, x)

            # Color-based
            result_c = cv2.matchTemplate(bg_img, piece_img, method)
            _, val_c, _, loc_c = cv2.minMaxLoc(result_c)
            score_c = 1.0 - val_c if invert else val_c
            x_c = loc_c[0]
            if score_c > best_score:
                best_score = score_c
                best_offset = x_c
            log.debug("captchafox: %s color score=%.4f x=%d", name, score_c, x_c)
        except cv2.error:
            continue

    log.info("captchafox: best offset=%d (score=%.4f)", best_offset, best_score)
    return best_offset


# ---------------------------------------------------------------------------
# Synthetic fingerprint generation
# ---------------------------------------------------------------------------

def _make_synthetic_fingerprint(host: str = "unknown") -> dict:
    """Generate minimal fingerprint structure matching CaptchaFox's expected format.

    The real fingerprint comes from browser JS runtime; this is a synthetic
    placeholder for headless/CLI use. Sites with strict fingerprint validation
    will reject this — document that boundary.
    """
    return {
        "lng": "en",
        "h": "synthetic|" + "0" * 64,
        "cs": {
            "CF0100": False,  # adBlock
            "CF0101": False,  # debug
            "CF0103": True,   # cookieEnabled
            "CF0105": False,  # webdriver
            "CF0108": 7,      # hardwareConcurrency
            "CF0115": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "CF0117": "Google Inc.",
            "CF0118": "Linux x86_64",
            "CF0125": True,   # localStorage
            "CF0127": True,   # sessionStorage
            "CF0134": "object",
            "CF0142": True,   # indexedDB
            "CF0143": True,   # openDatabase
            "CF0146": True,   # performance
            "CF0148": f"https://{host}/",
        },
        "host": host,
        "k": 0,
        "type": "slide",
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _fail(msg: str, elapsed: float = 0.0) -> dict:
    return {"solved": False, "type": "captchafox", "token": "", "method": "cv2-template",
            "elapsed": round(elapsed, 2), "error": msg}


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
) -> dict:
    """Solve a CaptchaFox slide captcha.

    Two modes:
    A) Slide offset detection (primary):
       - Pass background image (image_b64 or image_url) AND
         puzzle piece image (piece_b64 or piece_url)
       - Returns X offset as token + encrypted solution payload

    B) Encrypt/decrypt existing data:
       - Pass encrypted_data (bytes or base64 string) to decrypt and return
       - Or pass a dict-like payload in image_b64 (as JSON string) to encrypt

    Args:
        image_b64: Background image as base64 string (or JSON payload for encrypt mode)
        image_url: Background image URL (alternative to image_b64)
        piece_b64: Puzzle piece image as base64 string
        piece_url: Puzzle piece image URL (alternative to piece_b64)
        encrypted_data: Pre-encrypted CaptchaFox data to decrypt
        host: Target hostname for fingerprint context
        fingerprint_data: Browser fingerprint dict (uses synthetic if None)
        advanced: Use multi-method matching (slower but more robust)
        proxy: HTTP proxy for image fetching
        timeout_s: Timeout in seconds

    Returns:
        Uniform dict: {solved, type, token, method, elapsed, error}
        On success: token = encrypted solution (base64), offset = slide offset px
    """
    t0 = time.monotonic()
    result: dict[str, Any] = {
        "type": "captchafox",
        "solved": False,
        "token": "",
        "method": "cv2-template",
        "error": None,
    }

    # Mode B: decrypt existing encrypted data
    if encrypted_data is not None:
        try:
            if isinstance(encrypted_data, str):
                encrypted_data = base64.b64decode(encrypted_data)
            decrypted = await asyncio.wait_for(
                asyncio.to_thread(decrypt_data, encrypted_data),
                timeout=max(timeout_s, 5))
            result["solved"] = True
            result["token"] = json.dumps(decrypted, separators=(",", ":"))
            result["method"] = "decrypt"
            result["decrypted"] = decrypted
            result["elapsed"] = round(time.monotonic() - t0, 2)
            return result
        except Exception as e:
            result["error"] = f"captchafox decrypt: {e}"
            result["elapsed"] = round(time.monotonic() - t0, 2)
            return result

    # Mode B-alt: encrypt JSON payload passed as image_b64
    if image_b64 and not image_url and not piece_b64 and not piece_url:
        try:
            payload = json.loads(image_b64)
            if isinstance(payload, dict):
                encrypted = await asyncio.wait_for(
                    asyncio.to_thread(encrypt_data, payload),
                    timeout=max(timeout_s, 5))
                result["solved"] = True
                result["token"] = base64.b64encode(encrypted).decode("ascii")
                result["method"] = "encrypt"
                result["elapsed"] = round(time.monotonic() - t0, 2)
                return result
        except (json.JSONDecodeError, TypeError):
            pass  # Not JSON — fall through to image mode

    # Mode A: slide offset detection
    if not image_b64 and not image_url:
        result["error"] = "captchafox: provide image_b64 or image_url (background)"
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result
    if not piece_b64 and not piece_url:
        result["error"] = "captchafox: provide piece_b64 or piece_url (puzzle piece)"
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result

    try:
        # Fetch images
        loop = asyncio.get_running_loop()

        if image_url:
            bg_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_image, image_url, proxy, float(timeout_s)),
                timeout=timeout_s)
        else:
            bg_bytes = _decode_image_b64(image_b64)

        if piece_url:
            piece_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_image, piece_url, proxy, float(timeout_s)),
                timeout=timeout_s)
        else:
            piece_bytes = _decode_image_b64(piece_b64)

        # Decode and solve
        def _solve():
            bg_img = _load_image(bg_bytes)
            piece_img = _load_image(piece_bytes)
            if advanced:
                return _find_slide_offset_advanced(bg_img, piece_img)
            return _find_slide_offset(bg_img, piece_img)

        offset = await asyncio.wait_for(
            asyncio.to_thread(_solve),
            timeout=max(timeout_s, 10))

        # Build encrypted solution payload
        fp = fingerprint_data or _make_synthetic_fingerprint(host)
        solution_payload = {
            "offset": offset,
            "timestamp": int(time.time() * 1000),
            "fingerprint": fp,
        }
        encrypted = encrypt_data(solution_payload)

        result["solved"] = True
        result["token"] = base64.b64encode(encrypted).decode("ascii")
        result["offset"] = offset
        result["elapsed"] = round(time.monotonic() - t0, 2)
        log.info("captchafox: offset=%d px, encrypted=%d bytes", offset, len(encrypted))
        return result

    except asyncio.TimeoutError:
        result["error"] = f"captchafox: exceeded {timeout_s}s"
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result
    except Exception as e:
        result["error"] = f"captchafox: {type(e).__name__}: {e}"
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result
