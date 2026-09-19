"""Zhihu text-captcha solver — CNN-based 4-char alphanumeric recognition.

Zhihu's legacy captcha (before they switched to Tencent slider) was a
150×60 grayscale image containing 4 distorted alphanumeric characters
(digits 0-9, lowercase a-z, uppercase A-Z = 62 possible chars per position).
Two visual variants: thin font and bold font.

The reference repo (lonnyzhang423/zhihu-captcha) trains a TF CNN:
  conv(3,32) → pool → conv(3,64) → pool → conv(3,64) → pool → FC1024 → out(248)

This solver provides:
  1. ONNX inference path (if trained .onnx model is provided)
  2. ddddocr fallback (Chinese OCR engine that handles distorted chars)
  3. numpy/cv2 preprocessing pipeline (grayscale, threshold, resize)

Without a trained ONNX model, the solver uses ddddocr which gives
~60-70% on this style; with a trained model, ~90% per the reference.

NOTE: Zhihu has since upgraded to Tencent slider captcha (see tencent solver).
This solver handles the LEGACY text-captcha only.

Dependencies: PIL, numpy, cv2, onnxruntime (optional), ddddocr (optional).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("zhihu")

# ── Constants (from zhihu-captcha/config.py) ──────────────────────────
CAPTCHA_LEN = 4
_CHAR_SET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CHAR_SET_LEN = len(_CHAR_SET)  # 62
IMG_WIDTH = 150
IMG_HEIGHT = 60

_MODEL_PATH = Path(__file__).resolve().parent / "zhihu_model.onnx"

# ── ONNX session (lazy) ──────────────────────────────────────────────
_onnx_session = None


def _get_onnx_session():
    """Load ONNX model if available. Returns None if not found."""
    global _onnx_session
    if _onnx_session is not None:
        return _onnx_session
    if not _MODEL_PATH.exists():
        log.info("zhihu: no ONNX model at %s — will use ddddocr fallback", _MODEL_PATH)
        return None
    try:
        import onnxruntime as ort
        _onnx_session = ort.InferenceSession(
            str(_MODEL_PATH), providers=["CPUExecutionProvider"])
        log.info("zhihu: loaded ONNX model from %s", _MODEL_PATH)
        return _onnx_session
    except Exception as e:
        log.warning("zhihu: ONNX load failed: %s", e)
        return None


# ── Image preprocessing ──────────────────────────────────────────────

def _decode_image(source) -> "np.ndarray":
    """Decode image from base64 string, URL bytes, or PIL Image → grayscale ndarray.

    Returns: float32 array shape (IMG_HEIGHT, IMG_WIDTH), values 0..1.
    """
    from PIL import Image

    if isinstance(source, str):
        # base64 encoded
        img_bytes = base64.b64decode(source)
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
    elif isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(source)).convert("L")
    elif hasattr(source, "convert"):
        img = source.convert("L")
    elif hasattr(source, "shape"):
        # numpy array
        arr = np.asarray(source)
        if arr.ndim == 3:
            arr = np.mean(arr[:, :, :3], axis=2)
        img = Image.fromarray(arr.astype(np.uint8), mode="L")
    else:
        raise ValueError(f"Unsupported image source type: {type(source)}")

    img = img.resize((IMG_WIDTH, IMG_HEIGHT), Image.BILINEAR)
    arr = np.array(img, dtype=np.float32) / 255.0
    return arr


def preprocess_for_onnx(arr: np.ndarray) -> np.ndarray:
    """Reshape (H,W) grayscale → (1, H*W) flat for TF-style input."""
    return arr.flatten().reshape(1, -1)


# ── ONNX inference ───────────────────────────────────────────────────

def _predict_onnx(arr: np.ndarray, session=None) -> str:
    """Run ONNX CNN prediction on preprocessed image array."""
    session = session or _get_onnx_session()
    if session is None:
        return ""

    x = preprocess_for_onnx(arr)
    input_name = session.get_inputs()[0].name
    logits = session.run(None, {input_name: x})[0]

    # logits shape: (1, 248) = 4 positions × 62 classes
    logits_2d = logits.reshape(CAPTCHA_LEN, _CHAR_SET_LEN)
    indices = np.argmax(logits_2d, axis=1)

    text = ""
    for idx in indices:
        if 0 <= idx < _CHAR_SET_LEN:
            text += _CHAR_SET[idx]
        else:
            text += "?"
    return text


# ── ddddocr fallback ─────────────────────────────────────────────────

_ocr_instance = None


def _get_ocr():
    """Lazy-load ddddocr instance."""
    global _ocr_instance
    if _ocr_instance is not None:
        return _ocr_instance
    try:
        import ddddocr
        _ocr_instance = ddddocr.DdddOcr(show_ad=False)
        log.info("zhihu: ddddocr loaded as fallback")
        return _ocr_instance
    except ImportError:
        log.warning("zhihu: ddddocr not available — install with: pip install ddddocr")
        return None
    except Exception as e:
        log.warning("zhihu: ddddocr init failed: %s", e)
        return None


def _predict_ddddocr(image_b64: str = None, image_bytes: bytes = None) -> str:
    """Run ddddocr recognition. Accepts base64 or raw bytes."""
    ocr = _get_ocr()
    if ocr is None:
        return ""

    try:
        if image_b64:
            raw = base64.b64decode(image_b64)
        elif image_bytes:
            raw = image_bytes
        else:
            return ""
        text = ocr.classification(raw)
        # ddddocr returns arbitrary-length text; take first 4 chars
        # and filter to valid charset
        cleaned = "".join(c for c in text[:CAPTCHA_LEN] if c in _CHAR_SET)
        return cleaned[:CAPTCHA_LEN]
    except Exception as e:
        log.warning("zhihu: ddddocr error: %s", e)
        return ""


# ── cv2 preprocessing (for noisy captchas) ────────────────────────────

def _enhance_image(arr: np.ndarray) -> np.ndarray:
    """Apply cv2-based enhancement: adaptive threshold + denoise.

    Input: float32 (H,W) 0..1.
    Output: float32 (H,W) 0..1, enhanced.
    """
    try:
        import cv2

        # Convert to uint8 for cv2 ops
        img_u8 = (arr * 255).astype(np.uint8)

        # Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(img_u8, (3, 3), 0)

        # Adaptive threshold for clean binarization
        binary = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2)

        # Morphological open to remove small noise
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        return cleaned.astype(np.float32) / 255.0
    except ImportError:
        return arr


# ── Public API ────────────────────────────────────────────────────────

def available() -> bool:
    """True when at least one backend (ONNX model or ddddocr) can work."""
    return _MODEL_PATH.exists() or _get_ocr() is not None


def predict_text(image_b64: str = None, image=None, url: str = None,
                 enhance: bool = False) -> str:
    """Predict 4-char text from a zhihu captcha image.

    Tries ONNX model first, falls back to ddddocr.
    Returns empty string if no backend available.
    """
    # Get raw bytes for ddddocr fallback
    raw_b64 = image_b64

    # Try ONNX first
    session = _get_onnx_session()
    if session is not None:
        try:
            if image is not None:
                arr = _decode_image(image)
            elif image_b64:
                arr = _decode_image(image_b64)
            elif url:
                arr = _decode_image(_fetch_image(url))
            else:
                return ""

            if enhance:
                arr = _enhance_image(arr)

            text = _predict_onnx(arr, session)
            if text and len(text) == CAPTCHA_LEN:
                return text
        except Exception as e:
            log.warning("zhihu: ONNX predict failed: %s", e)

    # Fallback: ddddocr (needs base64 or bytes)
    if raw_b64:
        return _predict_ddddocr(image_b64=raw_b64)
    elif image is not None:
        try:
            buf = io.BytesIO()
            if hasattr(image, "save"):
                image.save(buf, format="PNG")
            else:
                from PIL import Image
                Image.fromarray(np.asarray(image)).save(buf, format="PNG")
            return _predict_ddddocr(image_bytes=buf.getvalue())
        except Exception as e:
            log.warning("zhihu: ddddocr fallback failed: %s", e)
    return ""


def _fetch_image(url: str, proxy: str = None) -> bytes:
    """Fetch image bytes from URL with Chrome impersonation."""
    try:
        import curl_cffi.requests as cffi_requests
        resp = cffi_requests.get(url, impersonate="chrome", timeout=15,
                                 proxies={"https": proxy, "http": proxy} if proxy else None)
        resp.raise_for_status()
        return resp.content
    except ImportError:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()


# ── Entry point (uniform contract) ───────────────────────────────────

async def solve_zhihu(image_b64: str = None, url: str = None,
                      challenge_blob=None, captcha_type: str = None,
                      proxy: str = None, timeout_s: int = 60) -> dict:
    """Solve zhihu legacy text captcha — uniform contract.

    Supply ONE of:
      * image_b64      — base64-encoded captcha image (PNG/GIF/JPEG)
      * url             — direct URL to the captcha image
      * challenge_blob  — raw image bytes or PIL Image

    captcha_type is accepted for API compat but unused (zhihu = 4-char text).

    Returns:
      {"solved": bool, "type": "zhihu", "token": str, "method": str,
       "elapsed": float, "error": str|None, "confidence": str,
       "backend": str, "needs_training": bool}

    Never raises.
    """
    t0 = time.monotonic()
    result: dict = {
        "type": "zhihu",
        "solved": False,
        "token": "",
        "method": "",
        "elapsed": 0.0,
        "error": None,
        "confidence": "unknown",
        "backend": "",
        "needs_training": not _MODEL_PATH.exists(),
    }

    try:
        # Determine backend
        session = _get_onnx_session()
        if session is not None:
            result["backend"] = "onnx"
            result["method"] = "cnn-ocr"
        elif _get_ocr() is not None:
            result["backend"] = "ddddocr"
            result["method"] = "ddddocr-fallback"
        else:
            result["error"] = ("zhihu: no backend available — "
                               "place zhihu_model.onnx in solver dir or "
                               "install ddddocr (pip install ddddocr)")
            result["elapsed"] = round(time.monotonic() - t0, 2)
            return result

        # Resolve image source
        image_data = None
        if image_b64:
            image_data = image_b64
        elif challenge_blob is not None:
            image_data = challenge_blob
        elif url:
            try:
                image_bytes = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _fetch_image(url, proxy))
                image_data = base64.b64encode(image_bytes).decode()
            except Exception as fe:
                result["error"] = f"zhihu: fetch failed: {type(fe).__name__}: {str(fe)[:100]}"
                result["elapsed"] = round(time.monotonic() - t0, 2)
                return result
        else:
            result["error"] = "zhihu: pass image_b64, url, or challenge_blob"
            result["elapsed"] = round(time.monotonic() - t0, 2)
            return result

        # Run prediction
        text = predict_text(
            image_b64=image_data if isinstance(image_data, str) else None,
            image=image_data if not isinstance(image_data, str) else None,
            enhance=True,
        )

        if text and len(text) == CAPTCHA_LEN:
            result["solved"] = True
            result["token"] = text
            result["confidence"] = "high" if result["backend"] == "onnx" else "medium"
            log.info("zhihu: solved '%s' via %s", text, result["backend"])
        elif text:
            result["token"] = text
            result["confidence"] = "low"
            result["error"] = f"zhihu: partial result ({len(text)}/{CAPTCHA_LEN} chars)"
            log.warning("zhihu: partial '%s' via %s", text, result["backend"])
        else:
            result["error"] = "zhihu: no text recognized"

    except Exception as e:
        result["error"] = f"zhihu: {type(e).__name__}: {str(e)[:200]}"

    result["elapsed"] = round(time.monotonic() - t0, 2)
    return result
