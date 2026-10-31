"""Generic ImageToText — two engines behind one contract:

  1. local ddddocr ONNX OCR (sml2h3/ddddocr + its `old` model)
  2. **Jev** (TypeSafe System One, `jev-preview`) via `jev-captcha.service :8917`
     — see `~/work/jev_captcha/` and `jev_client.py`

Routing (`jev_mode`, per request or `I2T_JEV_MODE`):

  * `fallback` (default) — ddddocr first; Jev only rescues an empty result.
    Costs nothing on the common path and keeps working when the service is down.
  * `utama` — Jev first, ddddocr when Jev returns nothing.
  * `off` — ddddocr only (the pre-Jev behaviour).

Jev is the model that actually *understands* a captcha: pass `prompt`
("select all airplanes"), `jev_task` (grid|single|locate|rotate|slider|text|rank|
puzzle|classify) and, for classify tasks, `jev_options` + `ensemble_votes`.
Plain text OCR needs none of that — leave them unset.

The service is remote-model backed (`https://api.typesafe.ai/v1/systemone`,
100-key pool) and lives at `JEV_URL` (default `http://127.0.0.1:8917`). On a
remote deployment point `JEV_URL` at a tunnel, or leave `jev_mode=off`.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
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
        except Exception as exc:  # noqa: BLE001
            out.append({"engine": name, "error": str(exc)[:80]})
    return out


async def solve_image_to_text(image_b64: str = None, url: str = None,
                              timeout_s: int = 60, jev_mode: str = None,
                              prompt: str = "", task: str = None,
                              options: list = None, ensemble_votes: int = 5) -> dict:
    t0 = time.monotonic()
    jev_mode = (jev_mode or os.environ.get("I2T_JEV_MODE", "fallback")).lower()
    if jev_mode not in ("utama", "fallback", "off"):
        jev_mode = "fallback"

    def _fail(error: str, method: str = "ddddocr") -> dict:
        return {
            "solved": False, "type": "image_to_text", "token": "",
            "candidates": [], "method": method,
            "elapsed": round(time.monotonic() - t0, 1), "error": error,
        }

    image_bytes: bytes | None = None

    def _image64() -> str:
        if image_b64:
            return image_b64
        if not image_bytes:
            raise ValueError("image_b64 or url is required")
        return base64.b64encode(image_bytes).decode()

    def _sync() -> dict:
        nonlocal image_bytes
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

    async def _jev_attempt() -> dict:
        from solvers.image_to_text.jev_client import jev_solve
        if url and not image_b64:
            # let the local Jev service take the URL? it only accepts base64 —
            # fetch once here so both engines share the same bytes.
            nonlocal image_bytes
            image_bytes = await asyncio.to_thread(_fetch_image_bytes, url)
        return await jev_solve(_image64(), prompt=prompt,
                               task=task or None,  # let Jev autodetect from the prompt
                               options=options, ensemble_votes=ensemble_votes,
                               timeout_s=timeout_s)

    try:
        if jev_mode == "utama":
            j = await _jev_attempt()
            if j.get("ok") and j.get("token"):
                j.update({"type": "image_to_text", "solved": True,
                          "candidates": [], "error": None})
                return j
            log.warning("jev-first failed (%s) — ddddocr fallback", j.get("error"))
            r = await asyncio.wait_for(asyncio.to_thread(_sync), timeout=max(timeout_s, 15))
            r["method"] = f"jev-fallback+{r.get('method') or 'ddddocr'}"
            r["jev_error"] = j.get("error")
            return r

        r = await asyncio.wait_for(asyncio.to_thread(_sync), timeout=max(timeout_s, 15))
        if r.get("solved") or jev_mode == "off":
            return r

        # fallback mode: ddddocr came back empty → one Jev rescue, but ONLY for a
        # vision question. Jev's own text_captcha warns it is "not optimised for
        # OCR"; a bare rescue on a text captcha can answer confidently wrong
        # (measured: 'K7M4Q' -> 'aaa4a'), so a plain text image stays ddddocr.
        wants_vision = bool(prompt.strip()) or (
            bool(task) and task.strip().lower() not in ("text", "ocr", "text_captcha"))
        if not wants_vision:
            r["error"] = (r.get("error") or "") + " (jev rescue skipped: text OCR has no vision task)"
            return r
        j = await _jev_attempt()
        if j.get("ok") and j.get("token"):
            j.update({"type": "image_to_text", "solved": True,
                      "candidates": r.get("candidates", []),
                      "method": "ddddocr+jev-fallback", "error": None})
            return j
        r["error"] = (r.get("error") or "") + f" (jev rescue: {j.get('error')})"
        return r
    except asyncio.TimeoutError:
        return _fail(f"image_to_text timed out after {timeout_s}s")
    except Exception as exc:  # noqa: BLE001
        log.warning("image_to_text failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
