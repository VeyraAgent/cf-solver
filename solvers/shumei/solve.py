"""Shumei (数美) captcha solver — pure-HTTP protocol for the spatial_select and
icon_select models.

Protocol (reverse-verified field-by-field by AhCheng1027/shumei-captcha-
protocol-demo against live traffic; icon_select matching from
taisuii/OpenCV_IconSelect):

  1. GET {base}/ca/v1/conf       — session config (non-fatal, logged only)
  2. GET {base}/ca/v1/register   — JSONP {detail:{rid, order, bg, [fg],
                                    domains, bg_width, bg_height}}
  3. GET https://{domain}{bg}    — challenge image (bg; + fg order bar for
                                    icon_select), first CDN that answers
  4. local detection:
       spatial_select -> HSV shape detection, click instruction target
       icon_select    -> NCC template match of fg slots over bg, ordered
  5. GET {base}/ca/v2/fverify    — DES-ECB-encrypted fields:
       sp = [[fx, fy, ts], ...]   normalized click points + timestamps
       ox = [[fx, fy, ts], ...]   normalized mouse trail incl. click ends
       gt = elapsed ms
     per-field ASCII keys sp=735c85df ox=b06aad3b gt=ed4576ba, zero padding
     (verified 6/6 against the vendor captcha-sdk.min.js DES implementation)

Everything is one fverify GET; riskLevel PASS in the JSONP reply is the only
success signal. `org_code` defaults to Shumei's public trial organization.
`url` is the protected page URL (sent as Referer); if it points at the captcha
API host itself it is used as the API base instead.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import string
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

from solvers.shumei.imaging import (
    detect_objects,
    load_rgb,
    match_pieces,
    select_target,
    split_fg_templates,
)

log = logging.getLogger("shumei")

DEFAULT_API_BASE = "https://captcha1.fengkongcloud.cn"
DEFAULT_ORG = "d6tpAY1oV0Kv5jRSgxQr"  # Shumei public trial org (ishumei.com/trial)
DEFAULT_REFERER = "https://www.ishumei.com/trial/captcha.html"

SDKVER = "1.1.3"
RVERSION = "1.0.4"

# Field-level DES keys captured from the SDK (fixed ASCII, one per field).
FIELD_KEYS = {"sp": "735c85df", "ox": "b06aad3b", "gt": "ed4576ba"}

# Encrypted device-fingerprint fields, verbatim from a real browser capture
# (static values accepted by the backend for this protocol version).
STATIC_PARAMS = {
    "lo": "3BTH6e3gu50=",
    "eg": "uJjTNmFVWw0=",
    "xz": "z37FiXPo/cI=",
    "te": "KBQBpE43AY0=",
    "fr": "T+Mnqb3n7bQ=",
    "fq": "24ZLCZkj4M4=",
    "gr": "uHeqtyFzn+Y=",
    "sn": "KNpO2YyA97Q=",
    "xb": "P/Co/oqSMug=",
}

_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": DEFAULT_REFERER,
    "Origin": "https://www.ishumei.com",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"),
}


def _gen_uuid() -> str:
    return f"{int(time.time() * 1000)}{''.join(random.choices(string.ascii_letters + string.digits, k=20))}"


def _gen_callback() -> str:
    return f"sm_{int(time.time() * 1000)}"


def _parse_jsonp(text: str) -> Dict:
    return json.loads(text[text.index("(") + 1:text.rindex(")")])


def _norm_proxy(proxy: Optional[str]) -> Optional[str]:
    if proxy and "://" not in proxy:
        return f"http://{proxy}"
    return proxy


def encrypt_field(key: str, plaintext: str) -> str:
    """Field-level DES-ECB with zero padding + base64 (vendor SDK semantics:
    DES(key, data, mode=1, pad=0)). Verified byte-identical against the real
    captcha-sdk.min.js on 6 plaintexts incl. edge sizes 1/8/9 bytes."""
    try:
        from Crypto.Cipher import DES
    except ImportError as exc:  # documented dep in requirements.txt
        raise RuntimeError("pycryptodome is required for the shumei solver") from exc
    data = plaintext.encode()
    data += b"\x00" * (-len(data) % 8)
    cipher = DES.new(key.encode()[:8], DES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(data)).decode()


def build_submit_payload(points_px: List[Tuple[int, int]], bg_w: int, bg_h: int,
                         now_ms: Optional[int] = None) -> Dict[str, str]:
    """sp/ox/gt plaintexts for the click sequence, then per-field encryption.

    Timing model from the reference: register was 3-8s ago, first click after
    0.3-1.5s, 0.5-1.6s between clicks; the mouse trail eases from a random
    start point through each click with 18-60ms per step."""
    now = now_ms or int(time.time() * 1000)
    start = now - random.randint(3000, 8000)

    spatial: List[List[float]] = []
    ts = start + random.randint(300, 1200)
    for x, y in points_px:
        ts += random.randint(500, 1600)
        spatial.append([round(x / bg_w, 6), round(y / bg_h, 6), ts])
    click_json = json.dumps(spatial, separators=(",", ":"))

    mouse: List[List[float]] = []
    t = start
    cx, cy = random.random() * 0.2, random.random() * 0.35 + 0.15
    for fx, fy, cts in spatial:
        steps = random.randint(6, 12)
        for i in range(1, steps + 1):
            p = i / steps
            mx = cx + (fx - cx) * p
            my = cy + (fy - cy) * p
            t += random.randint(18, 60)
            mouse.append([round(mx, 6), round(my, 6), t])
        t = cts  # click lands exactly on the click timestamp
        cx, cy = fx, fy
    mouse_json = json.dumps(mouse, separators=(",", ":"))
    gt = str(max(ts, t) - start)
    return {
        "sp": encrypt_field(FIELD_KEYS["sp"], click_json),
        "ox": encrypt_field(FIELD_KEYS["ox"], mouse_json),
        "gt": encrypt_field(FIELD_KEYS["gt"], gt),
    }


def _base_and_referer(url: Optional[str]) -> Tuple[str, str]:
    """Split caller `url` into (api base, referer). A URL on the captcha API
    host (or an /ca/... path) is the API base; anything else is the page."""
    if not url:
        return DEFAULT_API_BASE, DEFAULT_REFERER
    p = urlparse(url if "://" in url else f"https://{url}")
    host = (p.netloc or "").lower()
    if "fengkongcloud" in host or "shumei" in host or p.path.startswith("/ca/"):
        return f"{p.scheme or 'https'}://{p.netloc}", DEFAULT_REFERER
    return DEFAULT_API_BASE, url




async def solve_shumei(org_code: Optional[str] = None, url: Optional[str] = None,
                       proxy: Optional[str] = None, timeout_s: int = 90,
                       mode: str = "spatial_select") -> dict:
    """Solve a Shumei spatial_select / icon_select captcha.

    Args:
        org_code:  organization id (defaults to the public trial org).
        url:       protected page URL (-> Referer), or a captcha API base URL.
        proxy:     "scheme://user:pass@host:port" (scheme optional, http
                   implied) forwarded to every request.
        timeout_s: overall budget.
        mode:      "spatial_select" | "icon_select" | "auto"
                   ("auto" registers spatial_select and switches to icon
                   matching when the reply carries an fg order bar).
    """
    t0 = time.monotonic()
    deadline = t0 + max(10, timeout_s)
    org = org_code or DEFAULT_ORG
    base, referer = _base_and_referer(url)
    if mode not in ("spatial_select", "icon_select", "auto"):
        mode = "spatial_select"
    model = "icon_select" if mode == "icon_select" else "spatial_select"
    uuid = _gen_uuid()
    headers = {**_HEADERS, "Referer": referer}
    if referer != DEFAULT_REFERER:
        p = urlparse(referer)
        if p.scheme:
            headers["Origin"] = f"{p.scheme}://{p.netloc}"

    common = {"organization": org, "appId": "default", "channel": "default",
              "model": model, "sdkver": SDKVER, "rversion": RVERSION,
              "lang": "zh-cn", "captchaUuid": uuid}

    def remaining() -> float:
        return max(0.5, deadline - time.monotonic())

    def _fail(error: str, method: str = "protocol", **extra) -> Dict:
        """Uniform failure result; carries everything learned so far."""
        return {"solved": False, "type": "shumei", "token": "", "method": method,
                "elapsed": round(time.monotonic() - t0, 2), "error": error,
                "model": model, "org": org, **extra}

    rid = ""
    session = AsyncSession(impersonate="chrome", proxy=_norm_proxy(proxy),
                           headers=headers, timeout=timeout_s)
    try:
        # 1. conf — config handshake, best-effort
        try:
            r = await session.get(f"{base}/ca/v1/conf",
                                  params={**common, "callback": _gen_callback()},
                                  timeout=remaining())
            conf = _parse_jsonp(r.text)
            log.info("shumei: conf code=%s", conf.get("code"))
        except Exception as exc:
            log.info("shumei: conf skipped (%s)", str(exc)[:80])

        # 2. register
        r = await session.get(f"{base}/ca/v1/register",
                              params={**common, "data": "{}", "callback": _gen_callback()},
                              timeout=remaining())
        reg = _parse_jsonp(r.text)
        detail = reg.get("detail") or {}
        rid = detail.get("rid") or ""
        if reg.get("code") != 1100 or not rid:
            return _fail(f"register failed: code={reg.get('code')} "
                         f"{str(reg.get('message'))[:120]}")
        order: List = detail.get("order") or []
        bg_w, bg_h = int(detail.get("bg_width", 600)), int(detail.get("bg_height", 300))
        log.info("shumei: register rid=%s model=%s order=%s", rid, model, order[:1])

        # auto: an fg order bar means icon_select semantics
        icon_mode = bool(detail.get("fg")) and (mode == "icon_select"
                                                or (mode == "auto" and not order))
        if mode in ("icon_select", "auto") and not detail.get("fg") and not order:
            return _fail("register returned neither order nor fg bar", rid=rid)

        # 3. download images from the first working CDN
        domains = detail.get("domains") or ["castatic.fengkongcloud.cn"]

        async def _fetch(path: str) -> bytes:
            last = "no domains"
            for d in domains:
                try:
                    resp = await session.get(f"https://{d}{path}", timeout=remaining())
                    if resp.status_code == 200 and resp.content:
                        return resp.content
                    last = f"HTTP {resp.status_code}"
                except Exception as exc:
                    last = str(exc)[:80]
            raise RuntimeError(f"image download failed: {last}")

        bg_bytes = await _fetch(detail["bg"])
        bg_rgb = load_rgb(bg_bytes)
        log.info("shumei: bg ...%s (%dx%d)", detail["bg"][-28:],
                 bg_rgb.shape[1], bg_rgb.shape[0])

        # 4. detection -> ordered click points
        hits: List[Dict] = []
        if icon_mode:
            fg_bytes = await _fetch(detail["fg"])
            templates = split_fg_templates(load_rgb(fg_bytes))
            hits = match_pieces(bg_rgb, templates)
            if any(h["x"] < 0 for h in hits):
                return _fail("icon matching failed for at least one slot",
                             rid=rid, matches=hits)
            points = [(h["x"], h["y"]) for h in hits]
            method = "icon-ncc"
        else:
            instruction = str(order[0]) if order else ""
            if not instruction:
                return _fail("no instruction in register reply", rid=rid)
            import cv2

            objs = detect_objects(cv2.cvtColor(bg_rgb, cv2.COLOR_RGB2BGR))
            log.info("shumei: %d objects: %s", len(objs),
                     [(o["color"], o["type"], o["center"]) for o in objs])
            if not objs:
                return _fail("no objects detected in bg", rid=rid,
                             order=instruction)
            try:
                target = select_target(objs, instruction)
            except ValueError as exc:
                return _fail(str(exc), rid=rid, order=instruction)
            points = [tuple(target["center"])]
            method = "spatial-hsv"
        log.info("shumei: click points %s", points)

        # 5. fverify
        payload = build_submit_payload(points, bg_w, bg_h)
        r = await session.get(
            f"{base}/ca/v2/fverify",
            params={**STATIC_PARAMS, "organization": org, "sdkver": SDKVER,
                    "rversion": RVERSION, "protocol": "207", "ostype": "web",
                    "act.os": "web_pc", "rid": rid, "captchaUuid": uuid,
                    "callback": _gen_callback(), **payload},
            timeout=remaining())
        res = _parse_jsonp(r.text)
        risk = res.get("riskLevel")
        elapsed = round(time.monotonic() - t0, 2)
        log.info("shumei: fverify riskLevel=%s message=%s", risk, res.get("message"))

        out = {
            "solved": risk == "PASS",
            "type": "shumei",
            "token": str(res.get("requestId", "")) if risk == "PASS" else "",
            "method": method,
            "elapsed": elapsed,
            "error": None if risk == "PASS" else
                     f"riskLevel={risk} {str(res.get('message', ''))[:120]}",
            "rid": rid,
            "org": org,
            "model": "icon_select" if icon_mode else "spatial_select",
            "points": [list(p) for p in points],
            "risk_level": risk,
            "request_id": res.get("requestId"),
            "code": res.get("code"),
            "message": res.get("message"),
        }
        if icon_mode:
            out["matches"] = hits
        return out
    except asyncio.TimeoutError:
        return _fail("timeout", rid=rid)
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {str(exc)[:180]}".replace("\n", " "),
                     rid=rid)
    finally:
        await session.close()
