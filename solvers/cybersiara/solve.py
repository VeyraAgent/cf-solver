"""CyberSiARA solver — pure-HTTP 3-step flow ported from gzdzudp/cybersiara-solver
(MIT-style), fingerprint encoder ported from encodedata.js to pure Python.

Flow (embed.mycybersiara.com):
  1. POST /api/CyberSiara/GetCyberSiara   (fingerprint + DeviceName + MasterUrlId)
  2. POST /api/v2/verification/fp         (FPID verification)
  3. POST /api/v2/SubmitCaptcha/VerifiedSubmit  → token (JWT)

The caller passes `url` (the protected page origin) and optionally `masterurl_id`
(the site's CyberSiara MasterUrlId — default is CyberSiara's own demo id).
"""
from __future__ import annotations

import asyncio
import logging
import json
import random
import time
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

_B64CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="


def _encoded_data(data: str) -> str:
    """Port of CyberSiara's encodedData() — base64 variant over char codes."""
    chars = _B64CHARS
    enc = ""
    i = 0
    ln = len(data)
    if not data:
        return data
    while i < ln:
        o1 = ord(data[i]) if i < ln else 0
        o2 = ord(data[i + 1]) if i + 1 < ln else 0
        o3 = ord(data[i + 2]) if i + 2 < ln else 0
        i += 3
        bits = o1 << 16 | o2 << 8 | o3
        h1 = bits >> 18 & 0x3F
        h2 = bits >> 12 & 0x3F
        h3 = bits >> 6 & 0x3F
        h4 = bits & 0x3F
        enc += chars[h1] + chars[h2] + chars[h3] + chars[h4]
    r = ln % 3
    return (enc[:r - 3] if r else enc) + "==="[r or 3:]


def _fingerprint() -> str:
    """navigator/screen composite fingerprint (mirror of fingerprinter.py)."""
    import re
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36")
    composite = (str(random.randint(10, 99))
                 + re.sub(r"\D+", "", ua)
                 + str(random.randint(5, 92))
                 + "1032" + "1920" + "1080"
                 + str(random.randint(21, 98)))
    return _encoded_data(composite)


async def solve_cybersiara(url: str, masterurl_id: str =
                           "tpjOCKjjpdzv3d8Ub2E9COEWKt1vl1Mv",
                           timeout_s: int = 90) -> dict:
    """Solve CyberSiARA for `url` (protected page origin) and return the token."""
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "cybersiara", "token": "",
            "method": "pure-http", "elapsed": round(time.monotonic() - t0, 1),
            "error": error,
        }

    def _sync() -> dict:
        origin = urlparse(url)
        origin = f"{origin.scheme}://{origin.netloc}" if origin.netloc else url
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36")
        fingerprint = _fingerprint()
        visiter_id = random.randint(100000, 999999)
        request_id = random.randint(1000000, 9999999)

        s = requests.Session()
        s.headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": origin,
            "Referer": origin,
            "User-Agent": ua,
        })

        # 1. GetCyberSiara
        cap = s.post("https://embed.mycybersiara.com/api/CyberSiara/GetCyberSiara",
                     data={
                         "MasterUrlId": masterurl_id,
                         "DeviceName": ua,
                         "RequestUrl": origin,
                         "BrowserIdentity": fingerprint,
                         "PluginNo": 0,
                         "VisiterId": visiter_id,
                         "LanguageId": 1,
                         "RequestID": request_id,
                         "LangChange": 0,
                         "ClickSecond": random.randint(22, 46),
                         "Iscookie": 1,
                         "DeviceHeight": 1080,
                         "DeviceWidth": 1920,
                     }, timeout=15).json()
        status = cap.get("HttpStatusCode") or cap.get("httpstatuscode")
        if str(status) == "400":
            return _fail("rate limited by CyberSiara (HttpStatusCode 400)")

        # 2. fp verification
        s.post("https://embed.mycybersiara.com/api/v2/verification/fp",
               data={"RequestID": request_id, "FPID": fingerprint,
                     "VisiterId": visiter_id}, timeout=15)

        # 3. VerifiedSubmit
        r = s.post("https://embed.mycybersiara.com/api/v2/SubmitCaptcha/VerifiedSubmit",
                   data={
                       "MasterUrl": masterurl_id,
                       "DeviceName": ua,
                       "BrowserIdentity": fingerprint,
                       "Protocol": "https:",
                       "VisiterId": visiter_id,
                       "second": random.randint(2, 3),
                       "RequestID": request_id,
                   }, timeout=15).json()

        token = r.get("token") or r.get("Token") or (
            r.get("data", {}) or {}).get("token") if isinstance(r, dict) else None
        if token:
            return {
                "solved": True, "type": "cybersiara", "token": token,
                "method": "pure-http", "elapsed": round(time.monotonic() - t0, 1),
                "error": None,
                "warning": "CyberSiARA tokens are session-bound — replay immediately.",
            }
        return _fail(f"no token in submit response: {json.dumps(r, default=str)[:160]}")

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _fail(f"cybersiara solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("cybersiara failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
