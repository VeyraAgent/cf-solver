"""Rotate-captcha solver — CNN angle prediction (bilibili / xiaohongshu / douyin style).

The challenge shows a rotated image; the user must drag it back to the upright
position. This solver predicts the original rotation angle with the CNN from
8yteDance/RotateCaptcha (360-class classifier, 40x40 input, ~1MB ONNX —
exported from the repo's rotate_model.pth, predictions verified 1:1 against
the torch original).

This module is the ANGLE ENGINE only: it takes a PIL image (the rotated
challenge image) and returns the angle (0-359) to rotate BACK. Protocol
wrappers (fetching the challenge image from a site's captcha endpoint and
submitting the drag/answer) are site-specific — wire them like the aliyun
solver: host a minimal page or call the site's captcha API directly.

Usage:
    from solvers.rotate.solve import predict_angle
    angle = predict_angle(pil_image)          # 0-359
    correction = (360 - angle) % 360          # drag distance to upright

Dependencies: onnxruntime (already a repo dep), PIL, numpy.
Credits: 8yteDance/RotateCaptcha (model + training pipeline, MIT),
chencchen/RotateCaptchaBreak.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("rotate")

_MODEL_PATH = Path(__file__).resolve().parent / "rotate_model.onnx"
_session = None


def _get_session():
    global _session
    if _session is None:
        import onnxruntime as ort
        from solvers.common.models import model_path as _resolve_model
        _session = ort.InferenceSession(str(_resolve_model(_MODEL_PATH)))
    return _session


def predict_angle(image, session=None) -> int:
    """Predict the rotation angle (0-359) of a PIL/ndarray image.

    `image`: PIL.Image (any size — resized to 40x40 like the reference) or
    HxWx3 uint8 ndarray. Returns the class index = angle in degrees.
    """
    from PIL import Image

    if not isinstance(image, Image.Image):
        image = Image.fromarray(image)
    # reference transform: Resize((40,40)) + ToTensor (RGB, [0,1], CHW)
    img = image.convert("RGB").resize((40, 40))
    arr = np.asarray(img, dtype=np.float32) / 255.0          # HWC
    x = arr.transpose(2, 0, 1)[None]                          # 1xCxHxW

    sess = session or _get_session()
    logits = sess.run(None, {"input": np.ascontiguousarray(x)})[0]
    return int(np.argmax(logits))


def correction_angle(angle: int) -> int:
    """Drag distance that restores upright: 360 - predicted angle."""
    return (360 - int(angle)) % 360


async def solve_rotate(image=None, image_b64: Optional[str] = None,
                       url: Optional[str] = None,
                       proxy: Optional[str] = None,
                       timeout_s: int = 60) -> dict:
    """Uniform solver contract for rotate captchas.

    Supply ONE of:
      * `image`        — PIL image / ndarray of the rotated challenge image
      * `image_b64`    — base64 PNG/JPEG of it
      * `url`          — direct URL to the challenge image (fetched via httpx)

    Returns {solved, type:"rotate", token: angle_correction, method,
    angle, elapsed, error} — the caller then submits the correction per its
    site's protocol (drag distance / slider px / form field).
    """
    import base64
    import io
    import time

    t0 = time.monotonic()
    result: dict = {"type": "rotate", "solved": False, "token": "",
                    "method": "cnn-angle", "error": None}

    from PIL import Image

    img = None
    try:
        if image is not None:
            img = image if hasattr(image, "convert") else Image.fromarray(image)
        elif image_b64:
            img = Image.open(io.BytesIO(base64.b64decode(image_b64)))
        elif url:
            import httpx
            resp = await __import__("httpx").AsyncClient(
                timeout=20, proxy=proxy).get(url)
            img = Image.open(io.BytesIO(resp.content))
        if img is None:
            result["error"] = "rotate: pass image, image_b64, or url"
            return result

        angle = predict_angle(img)
        correction = correction_angle(angle)
        result["solved"] = True
        result["token"] = str(correction)
        result["angle"] = angle
        result["correction"] = correction
        result["elapsed"] = round(time.monotonic() - t0, 2)
        log.info("rotate: angle=%d correction=%d", angle, correction)
        return result
    except Exception as e:
        result["error"] = f"rotate: {type(e).__name__}: {str(e)[:120]}"
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result
