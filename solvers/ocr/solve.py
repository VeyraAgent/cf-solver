"""Offline image-captcha OCR — local CTC model, no network, no vision API.

Two backends, picked at call time:

  * `ddddocr` (project dependency) — the standard ddddocr/common CTC model.
    ~5–20 ms per image on CPU, no GPU, no API key.
  * vendored ONNX engine (`_onnx_engine.py`, needs `models/common.onnx`) —
    same architecture/decoder as the ddddocr "common" model, used when ddddocr
    is not installed on the host (or when you want a pinned model file).

This is the cheap first pass for any fixed-font captcha (mtcaptcha text mode,
steam/vk numeric, simple login codes). For tile/grid/rotate work use the
dedicated solvers (`grid`, `rotate`, `recognition`) — those need vision.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_MODEL_DIR = _HERE / "models"

_engine = None
_engine_lock = threading.Lock()


def _decode_image(image_b64: str | None) -> bytes:
    if not image_b64:
        raise ValueError("image_b64 (or image_url) is required")
    raw = image_b64.strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[1] if "," in raw else ""
    raw = raw.replace("\n", "").replace("\r", "").replace(" ", "")
    # accept urlsafe base64 too — some callers pass it straight from a JSON payload
    pad = (-len(raw)) % 4
    raw += "=" * pad
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            data = decoder(raw)
        except (binascii.Error, ValueError):
            continue
        if data:
            return data
    raise ValueError("image_b64 is not valid base64")


def _get_engine():
    """Return a cached engine: ddddocr first, vendored ONNX engine second."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            import ddddocr  # noqa: WPS433 (lazy: keeps import cost off /health)

            ocr = ddddocr.DdddOcr(show_ad=False)
            _engine = ("ddddocr", ocr)
            log.info("ocr: using ddddocr backend")
            return _engine
        except Exception as e:  # pragma: no cover - depends on host
            log.warning("ocr: ddddocr unavailable (%s) — trying ONNX engine", e)
        model = _MODEL_DIR / "common.onnx"
        charset = _MODEL_DIR / "common.json"
        if model.is_file() and charset.is_file():
            from solvers.ocr._onnx_engine import OCREngine  # type: ignore

            _engine = ("onnx", OCREngine(str(model), str(charset)))
            log.info("ocr: using vendored ONNX engine")
            return _engine
        raise RuntimeError(
            "no OCR backend available: pip install ddddocr (preferred) or drop "
            "models/common.onnx + models/common.json into solvers/ocr/models/")


def _run(engine, data: bytes) -> str:
    kind, inst = engine
    if kind == "ddddocr":
        return inst.classification(data) or ""
    return inst.predict_bytes(data) or ""


async def solve_ocr(image_b64: str | None = None, image_url: str | None = None,
                    proxy: str | None = None, timeout_s: int = 60) -> dict:
    """OCR a captcha image → `{solved, text, token}`. `text` is the answer."""
    t0 = time.monotonic()
    try:
        if not image_b64 and image_url:
            import httpx

            async with httpx.AsyncClient(timeout=min(30, timeout_s or 30),
                                         proxy=proxy or None,
                                         follow_redirects=True) as cli:
                r = await cli.get(image_url)
                r.raise_for_status()
                data = r.content
        else:
            data = _decode_image(image_b64)
        engine = _get_engine()
        text = await asyncio.to_thread(_run, engine, data)
        text = (text or "").strip()
        return {"solved": bool(text), "text": text, "token": text,
                "method": engine[0], "elapsed": round(time.monotonic() - t0, 2),
                **({"error": "empty OCR result"} if not text else {})}
    except Exception as e:
        return {"solved": False, "text": "", "token": "",
                "method": "ocr", "error": f"{type(e).__name__}: {e}"[:200],
                "elapsed": round(time.monotonic() - t0, 2)}


async def solve(*args, **kwargs) -> dict:  # pragma: no cover - dispatch alias
    return await solve_ocr(*args, **kwargs)


if __name__ == "__main__":  # python -m solvers.ocr.solve <file-or-b64>
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    p = Path(arg)
    payload = base64.b64encode(p.read_bytes()).decode() if p.is_file() else arg
    print(asyncio.run(solve_ocr(payload)))
