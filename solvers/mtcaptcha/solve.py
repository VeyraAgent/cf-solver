"""MTCaptcha solver — pure-HTTP flow ported from yaziradevteam/mtcaptcha-solver,
OCR swapped from external DeepInfra API to local ddddocr (100% offline).

Flow: getchallenge.json → solve_fold (pure-Python fold algorithm) → getimage.json
→ local OCR (dual-model candidates) → solvechallenge.json → verifiedToken.vt.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

SERVICE_DOMAIN = "service.mtcaptcha.com"
SECRET = "mtcap@mtcaptcha.com"
DEMO_SITEKEY = "MTPublic-KzqLY1cKH"
DEMO_BD = "2captcha.com"

BASE64_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
_BASE64_MAP = {c: i for i, c in enumerate(BASE64_CHARS)}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

_WIDGET_COOKIES = {
    "mtv1ConfSum": "{v:01|wdsz:std|thm:basic|lan:en|chlg:std|clan:1|cstyl:1|afv:0|afot:0|}",
    "jsV": "2026-05-04.21.34.59",
    "mtv1Pulse": "0001R1YCTf-lT8Y85AlfY3YSVt",
}


def _b64_char_to_int(ch: str) -> int:
    return _BASE64_MAP[ch]


def _b64_int_to_char(i: int) -> str:
    return BASE64_CHARS[i]


def _to_signed32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x > 0x7FFFFFFF else x


def _fold_base64_array(a1: list, fold_count: int) -> list:
    a2 = a1[::-1]
    a3 = a1[:]
    y = z = 0
    for i in range(fold_count):
        offset = i + 1
        for x in range(len(a1)):
            val = ((a3[x] + a2[(x + offset) % len(a2)]) * 73) // 8
            a3[x] = (val + y + z) % 64
            z = y // 2
            y = a3[x] // 2
    return a3


def _hash_int_array(arr: list) -> int:
    h = 0
    for v in arr:
        h = _to_signed32((h << 5) - h + v)
    return -h if h < 0 else h


def solve_fold(fseed: str, fslots: int, fdepth: int) -> str:
    ints = [_b64_char_to_int(c) for c in fseed]
    pairs = []
    for _ in range(fslots):
        ints = _fold_base64_array(ints, 31)
        folded_again = _fold_base64_array(ints, fdepth)
        h = _hash_int_array(folded_again)
        val = h % 4096
        pairs.append(_b64_int_to_char(val >> 6) + _b64_int_to_char(val & 63))
    return "".join(pairs)


def _kt() -> str:
    return "".join(secrets.choice(BASE64_CHARS) for _ in range(64))


def _tsh(sitekey: str) -> str:
    return "TH[" + hashlib.md5((SECRET + sitekey).encode()).hexdigest() + "]"


def _headers(sitekey: str) -> dict:
    return {
        "User-Agent": _UA,
        "Accept": "*/*",
        "Referer": (f"https://{SERVICE_DOMAIN}/mtcv1/client/iframe.html"
                    f"?sitekey={sitekey}&iframeId=mtcaptcha-iframe-1&widgetSize=standard"),
    }


async def solve_mtcaptcha(sitekey: str = DEMO_SITEKEY, bd: str = DEMO_BD,
                           timeout_s: int = 90, max_retries: int = 3) -> dict:
    """Solve an MTCaptcha challenge for `sitekey` and return the verified token (vt)."""
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "mtcaptcha", "token": "", "vt": "",
            "method": "pure-http-ocr", "elapsed": round(time.monotonic() - t0, 1),
            "error": error,
        }

    def _sync() -> dict:
        import ddddocr
        ocr = ddddocr.DdddOcr(show_ad=False)
        ocr_old = ddddocr.DdddOcr(show_ad=False, old=True)

        session_id = "S0" + str(uuid.uuid4())
        now = int(time.time() * 1000)
        last_err = "unknown"

        for attempt in range(1, max_retries + 1):
            chal = requests.get(
                f"https://{SERVICE_DOMAIN}/mtcv1/api/getchallenge.json",
                params={"sk": sitekey, "bd": bd, "rt": now, "tsh": _tsh(sitekey),
                        "act": "$", "ss": session_id, "lf": "0", "tl": "$", "lg": "en", "tp": "s"},
                headers=_headers(sitekey), cookies=_WIDGET_COOKIES, timeout=15).json()
            if chal.get("code") != 1200:
                last_err = f"getchallenge code={chal.get('code')}"
                continue
            challenge = chal["result"]["challenge"]
            ct = challenge["ct"]
            fold = challenge.get("foldChlg") or {}
            fseed = fold.get("fseed", "")
            fa = solve_fold(fseed, fold.get("fslots", 0), fold.get("fdepth", 0)) \
                if fseed and fold.get("preRes") else "$"

            img = requests.get(
                f"https://{SERVICE_DOMAIN}/mtcv1/api/getimage.json",
                params={"sk": sitekey, "ct": ct, "fa": fa, "ss": session_id},
                headers=_headers(sitekey), cookies=_WIDGET_COOKIES, timeout=15).json()
            if img.get("code") != 1200:
                last_err = f"getimage code={img.get('code')}"
                continue

            image_bytes = base64.b64decode(img["result"]["img"]["image64"])
            answer_len = challenge["textChlg"]["textlen"]

            candidates = []
            for engine in (ocr, ocr_old):
                try:
                    text = "".join(ch for ch in str(engine.classification(image_bytes)) if ch.isalnum())
                except Exception:
                    continue
                if text and text not in candidates:
                    candidates.append(text[:answer_len] if len(text) > answer_len else text)

            # vision fallback (same Mistral pool the recaptcha image path uses)
            try:
                from solvers.common.mistral import KeyPool
                pool = KeyPool(str(Path(__file__).parent.parent / "common" / "apikey.txt"))
                img_b64 = base64.b64encode(image_bytes).decode()
                vis = pool.classify_custom(
                    img_b64,
                    f"The image contains a CAPTCHA text of exactly {answer_len} characters "
                    "(upper/lowercase letters and digits). Respond with ONLY those "
                    f"{answer_len} characters, nothing else.")
                vis = "".join(ch for ch in str(vis) if ch.isalnum())
                if vis and vis.lower() not in ('false', 'true', 'none') and vis not in candidates:
                    candidates.append(vis[:answer_len] if len(vis) > answer_len else vis)
            except Exception as e:
                log.debug("mistral fallback unavailable: %s", e)

            for answer in candidates:
                solved = requests.get(
                    f"https://{SERVICE_DOMAIN}/mtcv1/api/solvechallenge.json",
                    params={"ct": ct, "sk": sitekey, "st": answer, "lf": "0", "bd": bd,
                            "rt": int(time.time() * 1000), "tsh": _tsh(sitekey), "fa": fa,
                            "qh": "$", "act": "$", "ss": session_id, "tl": "$", "lg": "en",
                            "tp": "s", "kt": _kt(), "fs": fseed or ""},
                    headers=_headers(sitekey), cookies=_WIDGET_COOKIES, timeout=15).json()
                if solved.get("code") != 1200:
                    last_err = f"solvechallenge code={solved.get('code')}"
                    continue
                vr = solved.get("result", {}).get("verifyResult", {})
                if vr.get("isVerified"):
                    vt = vr["verifiedToken"]["vt"]
                    return {
                        "solved": True, "type": "mtcaptcha", "token": vt, "vt": vt,
                        "method": "pure-http-ocr", "elapsed": round(time.monotonic() - t0, 1),
                        "error": None, "attempts": attempt, "ocr": answer,
                    }
                last_err = f"not verified (ocr={answer})"
            session_id = "S0" + str(uuid.uuid4())

        return _fail(f"failed after {max_retries} attempts: {last_err}")

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _fail(f"mtcaptcha solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("mtcaptcha failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
