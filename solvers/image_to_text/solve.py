"""Generic ImageToText OCR — exposes the local ddddocr ONNX engine (sml2h3/ddddocr)
through the sidecar's uniform contract. The caller passes the image as base64
(`image_b64`) or a direct image `url`.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time

import requests

log = logging.getLogger(__name__)

_OCR = None
_OCR_OLD = None


def _get_ocr(old: bool = False):
    global _OCR, _OCR_OLD
    import ddddocr
    if old:
        if _OCR_OLD is None:
            _OCR_OLD = ddddocr.DdddOcr(show_ad=False, old=True)
        return _OCR_OLD
    if _OCR is None:
        _OCR = ddddocr.DdddOcr(show_ad=False)
    return _OCR


def _fetch_image_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=15, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    })
    r.raise_for_status()
    return r.content


def _ocr_all(image_bytes: bytes) -> list[dict]:
    out = []
    for name, old in (("ddddocr", False), ("ddddocr-old", True)):
        try:
            text = "".join(ch for ch in str(_get_ocr(old).classification(image_bytes))
                           if ch.isalnum())
            out.append({"engine": name, "text": text})
        except Exception as exc:
            out.append({"engine": name, "error": str(exc)[:80]})
    return out


async def solve_image_to_text(image_b64: str = None, url: str = None,
                              timeout_s: int = 60) -> dict:
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "image_to_text", "token": "",
            "candidates": [], "method": "ddddocr",
            "elapsed": round(time.monotonic() - t0, 1), "error": error,
        }

    def _sync() -> dict:
        if image_b64:
            image_bytes = base64.b64decode(image_b64)
        elif url:
            image_bytes = _fetch_image_bytes(url)
        else:
            raise ValueError("image_b64 or url is required")

        candidates = _ocr_all(image_bytes)
        best = next((c["text"] for c in candidates if c.get("text")), "")
        return {
            "solved": bool(best), "type": "image_to_text", "token": best,
            "text": best, "candidates": candidates, "method": "ddddocr",
            "elapsed": round(time.monotonic() - t0, 1),
            "error": None if best else "all OCR engines returned empty",
        }

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=max(timeout_s, 15))
    except asyncio.TimeoutError:
        return _fail(f"image_to_text timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("image_to_text failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
