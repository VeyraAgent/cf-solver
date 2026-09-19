"""NetEase Yidun (网易易盾) captcha solver — pure-HTTP protocol v3 (2.28.5), slider + silent.

Protocol research credit: aidencaptcha/YidunCaptchaBreak (decodecaptcha.com) —
documents the id / token / fp / actoken / data / validate / NECaptchaValidate
parameter scheme this port follows. Working 2.28.5 protocol implementation
vendored from CodeEmpower/yidun-silder (encrypt.js / fp.js / webpack.js, run
verbatim through jsbridge.py); gap-detection and trajectory structure ported
from wenbo-chen-dev/yidun-captcha-solver. See README.md for the full flow.

Flow (slider):
  1. resolve captcha_id (caller param, else regex extraction from `url` page)
  2. node bridge:  fp(hostname) + get_cb()
  3. POST ir-sdk.dun.163.com/v4/j/up (irstoken.build_request_body) -> irToken
  4. GET  c.dun.163.com/api/v3/get  (JSONP) -> {bg[], front[], token, type}
  5. type=2 slider: download bg+front, numpy gap detect, human trajectory
     type=1 silent (无感): skip images, short idle trajectory
  6. node bridge:  get_data(trace, token, gap_x) -> encrypted `data` param
  7. GET  c.dun.163.com/api/v3/check (same JSONP callback)
  8. node bridge:  get_encryptvalidate(check_result, fp) -> final validate

Result contract: {"solved": bool, "type": "yidun", "token": str, "method": str,
"elapsed": float, "error": str|None, plus captcha_id / gap_x / raw_validate /
check_response extras}. Never raises out of the solver.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import re
import string
import time
from urllib.parse import urlparse

import numpy as np
from PIL import Image

from solvers.yidun import irstoken, jsbridge

log = logging.getLogger("yidun")

# ── Protocol constants (mirroring the proven reference flow) ─────────

_VERSION = "2.28.5"
_LOAD_VERSION = "2.5.4"
_IV = "4"
_WIDTH = 320

# Session artifacts baked into the reference run; strict deployments may bind
# these to a real page session — override via kwargs if the target requires.
_DT = "Z3f38snnC8FFQlBBEEbSyvzLO43gbqJf"
_INIT_TOKEN = "3f785c9554a742a2a33fac69f7fa5e37"
_IR_APP_ID = "YD00192283058223"

_GET_URL = "https://c.dun.163.com/api/v3/get"
_CHECK_URL = "https://c.dun.163.com/api/v3/check"
_IR_URL = "https://ir-sdk.dun.163.com/v4/j/up"

_CAPTCHA_ID_RES = [
    re.compile(r"captcha[_]?[iI]d['\"]?\s*[:=]\s*['\"]([0-9a-f]{32})['\"]"),
    re.compile(r"captchaId\s*[:=]\s*'([0-9a-f]{32})'"),
]

_CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")


def available() -> bool:
    """Solver usable only when node + vendored JS are present."""
    return jsbridge.available()


# ── Param builders (pure, unit-testable) ─────────────────────────────

def jsonp_callback(rng: random.Random | None = None) -> str:
    rng = rng or random.SystemRandom()
    tag = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(7))
    return f"__JSONP_{tag}_{rng.randint(10, 20)}"


def build_get_params(*, captcha_id: str, fp: str, cb: str, ir_token: str,
                     token: str, callback: str, referer: str = "",
                     zone_id: str = "CN31", dt: str = _DT, type_: str = "2",
                     width: int = _WIDTH, dpr: str = "1.25", dev: str = "3",
                     version: str = _VERSION, load_version: str = _LOAD_VERSION,
                     iv: str = _IV, lang: str = "zh-CN",
                     run_env: str = "10") -> dict:
    return {
        "referer": referer, "zoneId": zone_id, "dt": dt, "irToken": ir_token,
        "id": captcha_id, "fp": fp, "https": "true", "type": type_,
        "version": version, "dpr": dpr, "dev": dev, "cb": cb,
        "ipv6": "false", "runEnv": run_env, "group": "", "scene": "",
        "lang": lang, "sdkVersion": "", "loadVersion": load_version,
        "iv": iv, "user": "", "width": str(width), "audio": "false",
        "sizeType": "10", "smsVersion": "v3", "token": token,
        "callback": callback,
    }


def build_check_params(*, captcha_id: str, token: str, data: str, cb: str,
                       callback: str, referer: str = "", zone_id: str = "CN31",
                       dt: str = _DT, type_: str = "2", width: int = _WIDTH,
                       version: str = _VERSION, load_version: str = _LOAD_VERSION,
                       iv: str = _IV, run_env: str = "10") -> dict:
    return {
        "referer": referer, "zoneId": zone_id, "dt": dt, "id": captcha_id,
        "token": token, "data": data, "width": str(width), "type": type_,
        "version": version, "cb": cb, "user": "", "extraData": "", "bf": "0",
        "runEnv": run_env, "sdkVersion": "", "loadVersion": load_version,
        "iv": iv, "callback": callback,
    }


def _jsonp_parse(text: str) -> dict:
    """Parse a JSONP-wrapped (or plain JSON) response body."""
    m = re.match(r"^\s*__JSONP_\w+\(", text)
    if m:
        start = text.index("(")
        end = text.rindex(")")
        return json.loads(text[start + 1:end])
    return json.loads(text)


def extract_captcha_id(html: str) -> str | None:
    for pattern in _CAPTCHA_ID_RES:
        m = pattern.search(html)
        if m:
            return m.group(1)
    return None


# ── Gap detection (numpy port of wenbo-chen's two-stage approach) ────

def _edge_map(a: np.ndarray) -> np.ndarray:
    """Canny replacement: |dx| + |dy| gradient magnitude of the signal."""
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    if a.shape[1] > 1:
        gx[:, 1:] = np.abs(np.diff(a, axis=1))
    if a.shape[0] > 1:
        gy[1:, :] = np.abs(np.diff(a, axis=0))
    return gx + gy


def _column_scores(bg: np.ndarray) -> np.ndarray:
    """Per-column edge score over the 15%-85% vertical band (reference algorithm)."""
    h, w = bg.shape[:2]
    y1, y2 = int(h * 0.15), int(h * 0.85)
    band = bg[y1:y2]
    dx = np.abs(np.diff(band, axis=1)).max(axis=2) if band.shape[1] > 1 else np.zeros((y2 - y1, 0))
    sig = dx > 40
    counts = sig.sum(axis=0).astype(np.float64)
    sums = np.where(sig, dx, 0.0).sum(axis=0)
    means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    col_score = np.zeros(w)
    col_score[1:] = counts * means
    return np.convolve(col_score, np.ones(3) / 3, mode="same")


def find_gap(bg_img: Image.Image, piece_img: Image.Image | None = None) -> int:
    """Locate the gap's left edge x (natural pixels) in the slider background.

    Stage 1 — column edge-scan for candidate peaks. Stage 2 (when the alpha
    slider piece is available) — two-peak pairing by piece width, then
    alpha-contour NCC verification. Raises ValueError when nothing qualifies.
    """
    bg_rgb = np.asarray(bg_img.convert("RGB"), dtype=np.float64)
    h, w = bg_rgb.shape[:2]
    smoothed = _column_scores(bg_rgb)
    skip, end = max(int(w * 0.01), 1), int(w * 0.99)
    candidates = [(x, smoothed[x]) for x in range(skip + 1, end - 1)
                  if smoothed[x] > smoothed[x - 1]
                  and smoothed[x] >= smoothed[x + 1] and smoothed[x] > 0]
    candidates.sort(key=lambda c: -c[1])
    candidates = candidates[:20]
    if not candidates:
        raise ValueError("no gap edge candidates found in background")

    if piece_img is None:
        # No piece → no width info for pairing. Among near-max edge columns,
        # the LEFTMOST is the best gap-left guess (the slider travels to the
        # gap's left outline). The piece path below is the reliable one.
        top = max(c for _, c in candidates)
        strong = [x for x, c in candidates if c >= 0.8 * top]
        return min(strong)

    piece_rgba = np.asarray(piece_img.convert("RGBA"))
    piece_h, piece_w = piece_rgba.shape[:2]
    alpha = piece_rgba[:, :, 3] > 128
    if not alpha.any():
        raise ValueError("slider piece has empty alpha mask")
    # Piece content bounding box: the returned distance lands the piece
    # CONTENT left edge on the gap outline (ddddocr slide_match semantic the
    # reference flow uses), so a transparent margin shifts the NCC placement.
    x1 = int(np.where(alpha.any(axis=0))[0][0])
    min_gap, max_gap = max(25, int(piece_w * 0.5)), int(piece_w * 1.6)

    pairs: list[tuple[int, float]] = []
    for lx, lv in candidates:
        for rx, rv in candidates:
            if lx >= rx:
                continue
            gap = rx - lx
            if min_gap <= gap <= max_gap:
                gap_match = 1.0 - abs(gap - piece_w) / max(piece_w, 1)
                if gap_match >= 0.3:
                    pairs.append((lx, float(np.sqrt(lv * rv) * gap_match)))
    pairs.sort(key=lambda p: -p[1])
    pairs = pairs[:10]
    if not pairs:
        return candidates[0][0]

    bg_edge = _edge_map(np.asarray(bg_img.convert("L"), dtype=np.float32))
    piece_edge = _edge_map(alpha.astype(np.float32))
    piece_norm = float(np.sqrt((piece_edge * piece_edge).sum()))
    if piece_norm <= 0.0:
        # Degenerate (edge-free) alpha mask — fall back to the paired left peak.
        return pairs[0][0]

    best_score, best_x = -1.0, pairs[0][0]
    for lx, _score in pairs:
        for tx in range(max(skip, lx - 15), min(w - piece_w, lx + 15) + 1):
            if piece_h > h:
                break
            roi = bg_edge[0:piece_h, tx:tx + piece_w]
            if roi.shape != piece_edge.shape:
                continue
            bg_norm = float(np.sqrt((roi * roi).sum()))
            if bg_norm <= 0:
                continue
            match = float((piece_edge * roi).sum()) / (piece_norm * bg_norm)
            if match > best_score:
                best_score, best_x = match, tx
    return best_x + x1


# ── Trajectory generation (deterministic under seed) ─────────────────

def human_track(distance: float, seed: int | None = None,
                points: int = 50) -> list[list[int]]:
    """Accel → cruise → decel slider trajectory with bounded jitter.

    Rows are [x, y_offset, t_ms, isTrusted] exactly as the reference track
    format consumed by get_data. Deterministic for a given seed; jitter bounds:
    y ∈ [-2, 2], dt ∈ [10, 30] ms, x wobble ≤ 0.5 + rounding. The final point
    always lands exactly on `distance`.
    """
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    track: list[list[int]] = []
    t, x = 0, 0.0
    accel_end = max(int(points * 0.3), 1)
    decel_start = int(points * 0.7)
    max_speed = distance / (points * 0.6)

    for i in range(points):
        if i < accel_end:
            speed = max_speed * (i / accel_end) * rng.uniform(0.8, 1.2)
        elif i < decel_start:
            speed = max_speed * rng.uniform(0.9, 1.1)
        else:
            progress = (i - decel_start) / max(points - decel_start, 1)
            speed = max_speed * (1 - progress) * rng.uniform(0.8, 1.2)
        x += speed + rng.uniform(-0.5, 0.5)
        x = max(0.0, min(x, distance))
        t += rng.randint(10, 30)
        track.append([round(x), rng.randint(-2, 2), t, 1])

    track[-1][0] = round(distance)
    return track


def silent_track(seed: int | None = None, points: int = 8) -> list[list[int]]:
    """Short idle trajectory for the 无感 (silent, type=1) path — no slide.

    UNVERIFIED against live silent deployments: the reference implements the
    slider path only, silent submissions may need extra fields (see README).
    """
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    track: list[list[int]] = []
    t = 0
    for _ in range(points):
        t += rng.randint(40, 120)
        track.append([0, rng.randint(-1, 1), t, 1])
    return track


# ── Solver entry point ───────────────────────────────────────────────

def _session(proxy: str | None, timeout: float):
    from curl_cffi.requests import AsyncSession

    kwargs: dict = {"impersonate": "chrome", "timeout": timeout}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return AsyncSession(**kwargs)


def _page_headers(url: str) -> dict:
    origin = ""
    referer = ""
    if url:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        referer = url
    return {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "User-Agent": _CHROME_UA,
        "Referer": referer,
        "Origin": origin,
    }


async def solve_yidun(captcha_id: str | None = None, url: str | None = None,
                      proxy: str | None = None, timeout_s: int = 90, *,
                      mode: str = "slider", dt: str = _DT,
                      zone_id: str = "CN31", ir_app_id: str = _IR_APP_ID,
                      initial_token: str = _INIT_TOKEN, seed: int | None = None,
                      attempts: int = 3) -> dict:
    """Solve a Yidun (网易易盾) challenge over pure HTTP.

    captcha_id: the target's captchaId (32 hex). When omitted, extracted from
    the page at `url`. mode: "slider" (default) or "silent" (无感) — the actual
    path follows the challenge type the server returns. attempts: internal
    retries for risk rejections (each attempt fetches a fresh challenge —
    Yidun rejects a share of otherwise-valid submissions by design, mirroring
    the reference flow which loops until pass).
    """
    t0 = time.monotonic()

    def _fail(error: str, **extra) -> dict:
        return {
            "solved": False, "type": "yidun", "token": "",
            "method": "protocol-v3", "elapsed": round(time.monotonic() - t0, 1),
            "error": error, **extra,
        }

    if not captcha_id and not url:
        return _fail("captcha_id or url is required")
    if not available():
        return _fail("node runtime or vendored yidun JS unavailable (see solvers/yidun/README)")

    try:
        async with asyncio.timeout(max(timeout_s, 30)):
            result: dict | None = None
            for attempt in range(1, max(int(attempts), 1) + 1):
                result = await _attempt(t0, captcha_id, url, proxy, mode, dt,
                                        zone_id, ir_app_id, initial_token, seed)
                result["attempts"] = attempt
                if result.get("solved") or not result.pop("retry", False):
                    return result
                log.info("yidun: attempt %d failed (%s) — retrying", attempt,
                         result.get("error", "")[:60])
                await asyncio.sleep(random.uniform(0.4, 1.0))
            return result or _fail("no attempt completed")
    except asyncio.TimeoutError:
        return _fail(f"yidun solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("yidun failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])


async def _attempt(t0: float, captcha_id: str | None, url: str | None,
                   proxy: str | None, mode: str, dt: str, zone_id: str,
                   ir_app_id: str, initial_token: str, seed: int | None) -> dict:
    """One full get→check round. Result carries `retry: True` when a fresh
    challenge could plausibly pass (risk rejection), `retry: False` otherwise."""

    def _fail(error: str, retry: bool = False, **extra) -> dict:
        return {
            "solved": False, "type": "yidun", "token": "",
            "method": "protocol-v3", "elapsed": round(time.monotonic() - t0, 1),
            "error": error, "retry": retry, **extra,
        }

    headers = _page_headers(url or "")
    hostname = urlparse(url).hostname if url else "dun.163.com"
    request_timeout = 20.0

    async with _session(proxy, request_timeout) as s:
        # 1. Resolve captcha_id from the page when the caller didn't supply it.
        if not captcha_id:
            resp = await s.get(url, headers=headers)
            captcha_id = extract_captcha_id(resp.text)
            if not captcha_id:
                return _fail("could not extract captchaId from page source")
        log.info("yidun: captcha_id=%s mode=%s host=%s", captcha_id, mode, hostname)

        # 2. Device fingerprint + cb from the vendor JS.
        init = await jsbridge.call("init", {"hostname": hostname})
        fp, cb = init["fp"], init["cb"]
        log.info("yidun: fp+cb ready (fp=%s…)", fp[:16])

        # 3. irToken (best-effort — lax deployments accept empty).
        ir_token = ""
        try:
            body = irstoken.build_request_body(ir_app_id)
            ir_resp = await s.post(_IR_URL, json=body, headers=headers)
            ir_json = ir_resp.json()
            ir_token = (ir_json.get("data") or {}).get("tk") or ""
            log.info("yidun: irToken acquired (%s)", bool(ir_token))
        except Exception as exc:
            log.warning("yidun: irToken fetch failed (%s) — continuing empty", str(exc)[:80])

        # 4. Fetch the challenge.
        callback = jsonp_callback()
        get_params = build_get_params(
            captcha_id=captcha_id, fp=fp, cb=cb, ir_token=ir_token,
            token=initial_token, callback=callback, referer=url or "",
            zone_id=zone_id, dt=dt,
            type_="1" if mode == "silent" else "2")
        resp = await s.get(_GET_URL, params=get_params, headers=headers)
        challenge = _jsonp_parse(resp.text)
        if challenge.get("error", 0) != 0:
            return _fail(f"get error {challenge.get('error')}: {challenge.get('msg', '')}",
                         check_response=challenge)
        data = challenge.get("data") or {}
        token = data.get("token") or ""
        challenge_type = int(data.get("type") or 0)
        log.info("yidun: challenge type=%s token=%s…", challenge_type, str(token)[:12])

        # 5. Gap + trajectory (slider) or idle track (silent).
        slider = challenge_type == 2 and bool(data.get("bg"))
        gap_x = 0
        if slider:
            bg_resp = await s.get(data["bg"][0], headers=headers)
            bg_img = Image.open(io.BytesIO(bg_resp.content))
            front_img = None
            if data.get("front"):
                front_resp = await s.get(data["front"][0], headers=headers)
                front_img = Image.open(io.BytesIO(front_resp.content))
            gap_x = find_gap(bg_img, front_img)
            log.info("yidun: gap_x=%s (piece=%s)", gap_x, front_img is not None)
            trace = human_track(gap_x, seed=seed)
        else:
            trace = silent_track(seed=seed)
            log.info("yidun: silent path — idle track (%d pts)", len(trace))

        # 6. Encrypt the check payload via the vendor JS.
        data_param = (await jsbridge.call(
            "data", {"trace": trace, "token": token, "slide": gap_x}))["data"]

        # 7. Submit.
        check_params = build_check_params(
            captcha_id=captcha_id, token=token, data=data_param, cb=cb,
            callback=callback, referer=url or "", zone_id=zone_id, dt=dt,
            type_=str(challenge_type or (1 if not slider else 2)))
        resp = await s.get(_CHECK_URL, params=check_params, headers=headers)
        check_text = resp.text
        check = _jsonp_parse(check_text)
        check_error = check.get("error", -1)
        raw_validate = (check.get("data") or {}).get("validate") or ""
        log.info("yidun: check error=%s raw_validate=%s…", check_error, raw_validate[:16])

        # 8. Final encryptValidate (what the business API receives).
        final_validate = raw_validate
        if raw_validate:
            try:
                final_validate = (await jsbridge.call(
                    "encryptvalidate", {"result": check_text, "fp": fp}))["validate"]
            except Exception as exc:
                log.warning("yidun: encryptvalidate failed (%s) — returning raw validate",
                            str(exc)[:80])

        solved = check_error == 0 and bool(final_validate)
        if solved:
            error, retry = None, False
        elif check_error == 0:
            # Server answered ok but issued no validate — risk-scored rejection.
            # A fresh challenge passes on retry (the reference loops for this).
            error, retry = "check returned no validate (risk rejection)", True
        else:
            error, retry = f"check error {check_error}: {check.get('msg', '')}", False

        return {
            "solved": solved, "type": "yidun", "token": final_validate,
            "method": "protocol-v3-slider" if slider else "protocol-v3-silent",
            "elapsed": round(time.monotonic() - t0, 1),
            "error": error, "retry": retry,
            "captcha_id": captcha_id, "gap_x": gap_x,
            "raw_validate": raw_validate, "check_response": check,
            "version": _VERSION,
        }
