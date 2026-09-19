"""Vaptcha V4 wire protocol — reverse-engineered from c4.vaptcha.com/src/v4.js,
/src/core.js and /src/verify.html (build 202609041954) plus pow.js.

Flow (all request/response bodies use the "wire" envelope unless noted):

  1. POST {config_host}/api/config   fields: vid,tz,z,lang,sdkv,href,tag,dfu,ip,ua,_t
        -> {code, data:{server, knock, ad, active_probe, ...}}
     `data.server` is the per-deployment challenge origin (e.g. https://v4x..),
     `data.knock` is the initial knock id.
  2. POST {server}/api/knock         fields: KNOCK_FIELDS (44)
        -> {code, data:{knock, trajectory (image url), type, title, pow_start,
                         result, salt, dfu, knockParams, ...}}
  3. GET  {trajectory}?_t={ms}       -> challenge image (the gesture stroke photo)
  4. PoW: find n in [pow_start, pow_start+1e6) with sha256(f"{n}{salt}") == result
     (direct port of pow.js VaptchaPow.solve).
  5. POST {server}/api/validate      fields: VALIDATE_FIELDS (47)
        trajectory -> [points, encoded, totalDuration]; pow -> [nonce, order]
        -> {code, data:{result: true, token, dfu, ip}} on pass; or
           {code, data:{result: false, message: refresh_required|try_smaller|try_larger}}

Wire envelope (v4.js `i()` / verify.html `encodeWire`/`decodeWire`):
    request  = {"v": 1, "d": base64url(JSON.stringify(positional_values_by_field))}
    response = {"v": 1, "d": base64url(JSON.stringify(<plain object>))}
Special wireValue mappings: trajectory -> [points, encoded, totalDuration],
pow -> [nonce, order]; undefined -> null.

Trajectory encoding (verify.html `encodeSegment`), µs timestamps:
    per point: x(4 hex, clamp 0..420) + y(4 hex, clamp 0..280) + dt(5 hex, cap 1048575)
    concatenated and uppercased; dt = t[i] - t[i-1] (first point dt=0).
Constraints enforced by the widget: total duration >= 250_000 µs, >= 5 points after
filtering (|dx|>3 or |dy|>3 keeps a point), max 200 kept points.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import random
import time
import urllib.parse

import numpy as np
from typing import Any

log = logging.getLogger("vaptcha.protocol")

WIRE_VERSION = 1

# Public widget/config host (DNS lives under vaptcha.com; api.vaptcha.com is gone —
# the V4 stack moved to c4/v4c/v41 subdomains, verified 2026-09-13).
CONFIG_URL = "https://v4c.vaptcha.com/api/config"
SDK_SCRIPT = "https://c4.vaptcha.com/src/v4.js"
SDK_VERSION = "4.0.0"
WIDGET_BUILD = "202609041954"

# Positional field arrays verbatim from the obfuscated atob() literals in verify.html.
CONFIG_FIELDS = ["vid", "tz", "z", "lang", "sdkv", "href", "tag", "dfu", "ip", "ua", "_t"]
KNOCK_FIELDS = [
    "vid", "knock", "tag", "dfu", "pow_test", "pow_plan_ms", "attack_code",
    "fingerprint_version", "fingerprint_collect_ms", "fingerprint_wait_ms",
    "fingerprint_from_cache", "webgl_vendor", "webgl_renderer", "canvas_fingerprint",
    "dfa_components", "screen_width", "screen_height", "screen_refresh_rate",
    "device_pixel_ratio", "color_depth", "hardware_concurrency", "device_memory",
    "timezone", "language", "webgl_extensions_hash", "capabilities", "dfb_components",
    "dfc_webdriver", "dfc_is_headless", "dfc_bad_gpu", "dfc_components", "dfc_score",
    "dfc_level", "dfc_reasons", "sr", "dpr", "sw", "sh", "lang", "tz", "dfa", "dfb",
    "dfc", "ja3", "uid",
]
VALIDATE_FIELDS = [
    "knock", "vid", "tag", "dfu", "fingerprint_version", "fingerprint_collect_ms",
    "fingerprint_wait_ms", "fingerprint_from_cache", "dfa", "dfb", "dfc", "ja3",
    "webgl_vendor", "webgl_renderer", "canvas_fingerprint", "dfa_components",
    "screen_width", "screen_height", "screen_refresh_rate", "device_pixel_ratio",
    "color_depth", "hardware_concurrency", "device_memory", "timezone", "language",
    "webgl_extensions_hash", "capabilities", "dfb_components", "dfc_webdriver",
    "dfc_is_headless", "dfc_bad_gpu", "dfc_components", "dfc_score", "dfc_level",
    "dfc_reasons", "pow_test", "pow_actual_ms", "sr", "dpr", "sw", "sh", "lang", "tz",
    "trajectory", "pow", "pow_plan_ms", "attack_code",
]

# Canvas geometry of the challenge widget (verify.html CANVAS_W / CANVAS_H).
CANVAS_W = 420
CANVAS_H = 280

DT_CAP = 1_048_575          # max µs delta encodable per point (5 hex digits)
MIN_DURATION_US = 250_000   # server rejects traces shorter than 250ms
MIN_POINTS = 5              # server rejects traces with fewer kept points
MAX_POINTS = 200            # trajectory points are downsampled to 200 by the widget


# --------------------------------------------------------------------------
# Wire envelope
# --------------------------------------------------------------------------
def b64url_encode(data: str) -> str:
    raw = base64.b64encode(data.encode("utf-8")).decode("ascii")
    return raw.replace("+", "-").replace("/", "_").rstrip("=")


def b64url_decode(data: str) -> str:
    pad = "=" * ((4 - len(data) % 4) % 4)
    raw = base64.b64decode(data.replace("-", "+").replace("_", "/") + pad)
    return raw.decode("utf-8")


def _wire_value(key: str, value: Any) -> Any:
    if key == "trajectory" and value:
        return [value.get("points"), value.get("encoded"), value.get("totalDuration")]
    if key == "pow" and value:
        return [value.get("nonce"), value.get("order")]
    return None if value is None else value


def wire_encode(fields: list[str], data: dict) -> dict:
    """Encode a request body as the {"v":1,"d":...} positional envelope."""
    values = [_wire_value(f, data.get(f)) for f in fields]
    return {"v": WIRE_VERSION, "d": b64url_encode(json.dumps(values, separators=(",", ":")))}


def wire_decode(value: Any) -> Any:
    """Decode a wire response; plain JSON passes through untouched."""
    if not isinstance(value, dict) or value.get("v") != WIRE_VERSION or not isinstance(value.get("d"), str):
        return value
    return json.loads(b64url_decode(value["d"]))


# --------------------------------------------------------------------------
# PoW (port of pow.js VaptchaPow.solve — sha256 counter reversal)
# --------------------------------------------------------------------------
def solve_pow(pow_start: int, salt: str, target: str, max_rounds: int = 1_000_000) -> dict:
    """Find n >= pow_start with sha256(f"{n}{salt}") == target (hex)."""
    t0 = time.monotonic()
    n = int(pow_start)
    salt_b = salt.encode()
    for i in range(int(max_rounds)):
        if hashlib.sha256(str(n).encode() + salt_b).hexdigest() == target:
            return {"order": str(n), "nonce": n, "attempts": i + 1,
                    "elapsed_ms": int((time.monotonic() - t0) * 1000)}
        n += 1
    return {"order": "", "nonce": -1, "attempts": int(max_rounds),
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


# --------------------------------------------------------------------------
# Trajectory encoding + synthesis (ports of verify.html encodeSegment /
# filterTrajectoryPoints / limitTrajectoryPoints, plus a human-like generator)
# --------------------------------------------------------------------------
def encode_segment(points: list[dict]) -> str:
    """Hex-encode a point list: per point x:4hex + y:4hex + dt:5hex, uppercased."""
    out = []
    prev_t = 0
    for i, p in enumerate(points):
        x = max(0, min(CANVAS_W, int(round(p["x"]))))
        y = max(0, min(CANVAS_H, int(round(p["y"]))))
        dt = 0 if i == 0 else max(1, min(DT_CAP, int(round(p["t"] - prev_t))))
        prev_t = p["t"]
        out.append(f"{x:04x}{y:04x}{dt:05x}")
    return "".join(out).upper()


def filter_trajectory_points(points: list[dict]) -> list[dict]:
    """Widget filter: keep a kept point only if it moved (|dx|>3 or |dy|>3);
    always keep first/last; cap at 200 points (even resample)."""
    if not points or len(points) <= 2:
        return list(points)
    kept = [points[0]]
    last = points[0]
    for p in points[1:-1]:
        if abs(p["x"] - last["x"]) > 3 or abs(p["y"] - last["y"]) > 3 or (p["t"] - last["t"]) >= 32_000:
            kept.append(p)
            last = p
    end = points[-1]
    if kept[-1] is not end:
        kept.append(end)
    if len(kept) <= MAX_POINTS:
        return kept
    step = (len(kept) - 1) / (MAX_POINTS - 1)
    return [kept[round(i * step)] for i in range(MAX_POINTS)]


def synthesize_trajectory(curve_xy: list[tuple[float, float]], *,
                          total_ms: float = 1200.0,
                          rng: random.Random | None = None) -> list[dict]:
    """Human-like trace along the curve: ease-in-out speed profile + jitter.

    Timestamps are MICROSECONDS (the widget's `t` unit). Returns widget-format
    points [{x, y, t}, ...] ordered along the curve, guaranteed to satisfy the
    submit constraints (>= MIN_POINTS kept, duration >= MIN_DURATION_US).
    """
    rng = rng or random.Random()
    xy = np.asarray(curve_xy, dtype=float)
    if len(xy) < 2:
        return []
    seg = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total_len = float(cum[-1])
    # Sample every >=6px of arc length: the widget's filter keeps points that
    # moved (|dx|>3 or |dy|>3); a 6px chord guarantees a >=4.2px axis step.
    step_px = max(6.0, total_len / (MAX_POINTS - 4))
    n = max(MIN_POINTS + 2, min(MAX_POINTS - 4, int(total_len / step_px)))
    targets = np.linspace(0.0, total_len, n)
    # ease-in-out: slow start, quicker middle, slow finish (human trace)
    u = np.linspace(0.0, 1.0, n)
    speed = 0.35 + 0.65 * np.sin(np.pi * u) ** 0.75
    dt_us = total_ms * 1000.0 / n
    x_max, y_max = float(CANVAS_W), float(CANVAS_H)
    pts: list[dict] = []
    t = 0.0
    for i, target in enumerate(targets):
        j = min(int(np.searchsorted(cum, target)), len(xy) - 2)
        f = (target - cum[j]) / max(seg[j], 1e-9)
        x = xy[j, 0] + f * (xy[j + 1, 0] - xy[j, 0])
        y = xy[j, 1] + f * (xy[j + 1, 1] - xy[j, 1])
        if i > 0:
            t += max(1_000.0, dt_us * float(speed[i]) * (0.6 + 0.8 * rng.random()))
        pts.append({"x": min(x_max, max(0.0, float(x) + rng.uniform(-1.2, 1.2))),
                    "y": min(y_max, max(0.0, float(y) + rng.uniform(-1.2, 1.2))),
                    "t": float(t)})
    # enforce the widget's minimum trace duration
    if pts[-1]["t"] < MIN_DURATION_US * 1.15:
        scale = (MIN_DURATION_US * 1.3) / max(pts[-1]["t"], 1.0)
        for p in pts:
            p["t"] *= scale
    return filter_trajectory_points(pts)


# --------------------------------------------------------------------------
# Endpoint URL builders (pure, unit-testable)
# --------------------------------------------------------------------------
def config_url(vid: str) -> str:
    return CONFIG_URL


def knock_url(server: str) -> str:
    return f"{server.rstrip('/')}/api/knock"


def validate_url(server: str) -> str:
    return f"{server.rstrip('/')}/api/validate"


def image_url(trajectory_url: str, ts_ms: int | None = None) -> str:
    """Widget appends a cache-buster before loading the challenge image."""
    ts = int(ts_ms if ts_ms is not None else time.time() * 1000)
    sep = "&" if "?" in trajectory_url else "?"
    return f"{trajectory_url}{sep}_t={ts}"


def verify_page_url(vid: str, knock: str, tag: str = "", lang: str = "zh-CN") -> str:
    """The iframe challenge page — useful for manual inspection of a challenge."""
    q = f"vid={vid}&knock={knock}&tag={tag}&lang={lang}&v={WIDGET_BUILD}"
    return f"https://c4.vaptcha.com/src/verify.html?{q}"


# --------------------------------------------------------------------------
# HTTP client (async httpx; proxy passthrough)
# --------------------------------------------------------------------------
_DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Origin": "https://www.vaptcha.com",
    "Referer": "https://www.vaptcha.com/",
}


def _client(proxy: str | None, timeout_s: float):
    import httpx  # noqa: PLC0415

    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0)),
        headers=_DEFAULT_HEADERS,
        proxy=proxy or None,
        follow_redirects=True,
    )


async def fetch_config(vid: str, *, proxy: str | None = None,
                       timeout_s: float = 15.0, href: str = "") -> dict:
    """GET /api/config?v=1&d=<wire> (exact v4.js transport) -> {code, data:{server, knock}}.

    v4.js fetchConfig sends 11 positional wire fields [vid, tz, z, lang, sdkv, href,
    tag, dfu, ip, ua, _t] with only vid/lang/sdkv/tag/dfu/ip/_t filled (nulls for
    tz/z/href/ua); request is a GET with the wire envelope in the query string.
    """
    values = {
        "vid": vid,
        "tz": None,
        "z": None,
        "lang": "zh-CN",
        "sdkv": SDK_VERSION,
        "href": href or None,
        "tag": "",
        "dfu": "",
        "ip": "",
        "ua": None,
        "_t": str(int(time.time() * 1000)),
    }
    wire = wire_encode(CONFIG_FIELDS, values)
    url = f"{CONFIG_URL}?v={wire['v']}&d={urllib.parse.quote(wire['d'], safe='')}"
    async with _client(proxy, timeout_s) as c:
        r = await c.get(url)
        r.raise_for_status()
        return wire_decode(r.json())

async def fetch_image(trajectory_url: str, *, proxy: str | None = None,
                      timeout_s: float = 15.0) -> bytes:
    """GET the challenge image with the widget's cache-buster."""
    async with _client(proxy, timeout_s) as c:
        r = await c.get(image_url(trajectory_url))
        r.raise_for_status()
        return r.content


async def submit_validate(server: str, req: dict, *, proxy: str | None = None,
                          timeout_s: float = 15.0) -> dict:
    """POST {server}/api/validate (wire envelope) -> decoded response."""
    body = wire_encode(VALIDATE_FIELDS, req)
    async with _client(proxy, timeout_s) as c:
        r = await c.post(validate_url(server), json=body)
        r.raise_for_status()
        return wire_decode(r.json())


def curve_to_xy(curve: np.ndarray, step: int = 4) -> list[tuple[float, float]]:
    """Resample a (N, 2) curve to a dense point list for trajectory synthesis."""
    pts = [(float(curve[0, 0]), float(curve[0, 1]))]
    for i in range(step, len(curve), step):
        pts.append((float(curve[i, 0]), float(curve[i, 1])))
    if pts[-1] != (float(curve[-1, 0]), float(curve[-1, 1])):
        pts.append((float(curve[-1, 0]), float(curve[-1, 1])))
    return pts


def nan_guard(x: float) -> float:
    """Replace NaN/inf with 0 — never send non-finite floats to the wire."""
    return x if math.isfinite(x) else 0.0

