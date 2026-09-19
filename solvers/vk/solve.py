"""VK Captcha solver — CTC-based OCR with ONNX models.

Ported from DedInc/vk_captchasolver (41⭐). Ships its own trained models
(captcha_model.onnx + ctc_model.onnx). Pure onnxruntime + numpy + PIL.

VK captcha = distorted alphanumeric text (charset: 24578acdehkmnpqsuvxyz).
Input: image_b64 or image_url (or sid+s for VK API fetch).
Output: {solved, type:"vk", token: captcha_text, method: "ctc-ocr"}.

Credit: DedInc/vk_captchasolver (MIT License).
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
from onnxruntime import InferenceSession
from PIL import Image

log = logging.getLogger("vk")

_MODEL_DIR = Path(__file__).parent
_CHARSET = " 24578acdehkmnpqsuvxyz"  # VK captcha charset (index 0 = blank/CTC)
_sess_captcha = None
_sess_ctc = None


def _get_sessions():
    global _sess_captcha, _sess_ctc
    if _sess_captcha is None:
        _sess_captcha = InferenceSession(str(_MODEL_DIR / "captcha_model.onnx"))
        _sess_ctc = InferenceSession(str(_MODEL_DIR / "ctc_model.onnx"))
    return _sess_captcha, _sess_ctc


def _decode(image_bytes: bytes) -> str:
    """Run VK captcha OCR on raw image bytes. Returns decoded text."""
    img = Image.open(io.BytesIO(image_bytes)).resize((128, 64)).convert("RGB")
    x = np.array(img).reshape(1, -1).astype(np.float32) / 255.0
    x = np.expand_dims(x, axis=0)

    sess_cap, sess_ctc = _get_sessions()
    out = sess_cap.run(None, {sess_cap.get_inputs()[0].name: x[0]})
    out = sess_ctc.run(None, {sess_ctc.get_inputs()[i].name: np.float32(out[i])
                               for i in range(len(sess_ctc.get_inputs()))})

    # CTC decode: argmax > 0 → map to charset
    logits = out[0]  # shape (1, T, classes)
    indices = np.uint8(out[-1][logits > 0])  # indices where prob > 0
    text = "".join(_CHARSET[c] if c < len(_CHARSET) else "" for c in indices)
    return text.strip()


async def solve_vk(
    image_b64: Optional[str] = None,
    image_url: Optional[str] = None,
    sid: Optional[str] = None,
    s: Optional[str] = None,
    proxy: Optional[str] = None,
    timeout_s: int = 30,
) -> dict:
    """Solve a VK captcha.

    Args:
      image_b64: base64-encoded captcha image
      image_url: URL to download captcha image from
      sid: VK captcha SID (fetches from api.vk.com/captcha.php)
      s: VK captcha 's' parameter (optional, with sid)
    """
    t0 = time.monotonic()
    result = {"type": "vk", "solved": False, "token": "", "method": "ctc-ocr",
              "elapsed": 0, "error": None}

    image_bytes = None

    if image_b64:
        image_bytes = base64.b64decode(image_b64)
    elif sid:
        # Fetch from VK API
        url = f"https://api.vk.com/captcha.php?sid={sid}"
        if s:
            url += f"&s={s}"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15, verify=False) as c:
                resp = await c.get(url)
                image_bytes = resp.content
        except Exception as e:
            result["error"] = f"vk: fetch failed: {e}"
            return result
    elif image_url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15, verify=False) as c:
                resp = await c.get(image_url)
                image_bytes = resp.content
        except Exception as e:
            result["error"] = f"vk: download failed: {e}"
            return result

    if not image_bytes:
        result["error"] = "vk: pass image_b64, image_url, or sid"
        return result

    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(_decode, image_bytes),
            timeout=max(timeout_s - 2, 10),
        )
    except Exception as e:
        result["error"] = f"vk: decode error: {e}"
        return result

    result["solved"] = bool(text)
    result["token"] = text
    result["elapsed"] = round(time.monotonic() - t0, 2)
    if not text:
        result["error"] = "vk: empty decode result"
    log.info("vk: text=%r elapsed=%.2fs", text, result["elapsed"])
    return result
