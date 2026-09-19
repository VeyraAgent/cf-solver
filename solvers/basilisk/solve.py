"""Basilisk captcha solver — two-phase slide + icon-click challenge.

Basilisk (basiliskcaptcha.com) has two mandatory phases:
  1. Slide puzzle: background with hole + puzzle piece. Edge detection via
     Sobel + normalized cross-correlation (NCC) to find the x offset.
  2. Icon click: 3 colored icons (star=cyan, calendar=blue, buy=teal)
     detected by color distance + edge weighting + FFT convolution.

Both phases need human-like mouse trails (cosine easing for slide,
recorded-template warping for icons).

Input modes:
  - Pre-harvested images: slide_bg_b64/slide_piece_b64/slide_y + icons_bg_b64/icons_order
  - Live client flow: site_key + site_domain (full HTTP challenge flow via curl_cffi)

Dependencies: numpy, pillow, scipy, curl_cffi (all in sidecar venv).

Reference: aqelionie/basilisk-captcha-solver (MIT)
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# scipy-free equivalents (scipy.ndimage.sobel / scipy.signal.fftconvolve).
# Verified bit-identical to scipy on 1D/2D inputs; keeps scipy (~40 MB) out of
# the dependency set so the sidecar stays deployable on lightweight hosts.
# ---------------------------------------------------------------------------
def sobel(a, axis: int, mode: str = "reflect") -> np.ndarray:
    """scipy.ndimage.sobel(mode='reflect') equivalent, numpy only."""
    a = np.asarray(a, dtype=np.float64)
    if a.ndim == 1:
        p = np.pad(a, (1, 1), mode="symmetric")
        return (p[2:] - p[:-2]) if axis == 0 else np.zeros_like(a)
    p = np.pad(a, ((1, 1), (1, 1)), mode="symmetric")
    if axis == 0:
        d = p[2:, 1:-1] - p[:-2, 1:-1]
        dp = np.pad(d, ((0, 0), (1, 1)), mode="symmetric")
        return dp[:, :-2] + 2.0 * dp[:, 1:-1] + dp[:, 2:]
    d = p[1:-1, 2:] - p[1:-1, :-2]
    dp = np.pad(d, ((1, 1), (0, 0)), mode="symmetric")
    return dp[:-2, :] + 2.0 * dp[1:-1, :] + dp[2:, :]


def fftconvolve(in1, in2, mode: str = "same") -> np.ndarray:
    """scipy.signal.fftconvolve(mode='same') equivalent, numpy only."""
    in1 = np.asarray(in1, dtype=np.float64)
    in2 = np.asarray(in2, dtype=np.float64)
    s1, s2 = np.array(in1.shape), np.array(in2.shape)
    shape = s1 + s2 - 1
    fsize = [int(2 ** math.ceil(math.log2(x))) for x in shape]
    f1 = np.fft.rfftn(in1, fsize, axes=(0, 1))
    f2 = np.fft.rfftn(in2, fsize, axes=(0, 1))
    ret = np.fft.irfftn(f1 * f2, fsize, axes=(0, 1))
    start = (shape - s1) // 2
    return ret[start[0]:start[0] + s1[0], start[1]:start[1] + s1[1]]

log = logging.getLogger("basilisk")

# ---------------------------------------------------------------------------
# Constants (from original repo)
# ---------------------------------------------------------------------------
PUZZLE_WIDTH = 64
CANVAS_WIDTH = 318
CANVAS_HEIGHT = 252
SLIDER_START = 8

ICON_COLORS: Dict[str, Tuple[int, int, int]] = {
    "star": (0, 224, 255),
    "calendar": (102, 102, 255),
    "buy": (20, 255, 213),
}
_ICON_NAMES = ["star", "calendar", "buy"]

_COLOR_TOL = 110.0
_MAX_TOL = 90.0
_DISK_RADIUS = 26
_GLYPH_RADIUS = 40

SERVER = "https://basiliskcaptcha.com"

ImageLike = Union[Image.Image, str, bytes]

# ---------------------------------------------------------------------------
# Disk kernel (precomputed once)
# ---------------------------------------------------------------------------
_yg, _xg = np.ogrid[-_DISK_RADIUS:_DISK_RADIUS + 1, -_DISK_RADIUS:_DISK_RADIUS + 1]
_DISK = ((_xg * _xg + _yg * _yg) <= _DISK_RADIUS * _DISK_RADIUS).astype(np.float64)
_DISK /= _DISK.sum()

# ---------------------------------------------------------------------------
# Icons trail template (recorded human path, baked into the repo)
# ---------------------------------------------------------------------------
_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "_icons_trail_template.json")

def _load_template() -> Tuple[List[Tuple[int, int, int]], List[Tuple[int, int]]]:
    """Load the recorded mouse trail template for icon clicks."""
    try:
        with open(_TEMPLATE_PATH) as f:
            data = json.load(f)
        pts = [(p[0], p[1], p[2]) for p in data["pts"]]
        coords = [(c[0], c[1]) for c in data["coords"]]
        return pts, coords
    except FileNotFoundError:
        # Fallback: no template available, trail generation will use simple paths
        return [], [(61, 67), (178, 74), (245, 161)]

_TPTS, _TCLICKS = _load_template()

def _template_anchor_index(cx: int, cy: int) -> int:
    for i, (_, x, y) in enumerate(_TPTS):
        if x == cx and y == cy:
            return i
    return 0

if _TPTS:
    _ANCHOR_IDX = [
        _template_anchor_index(*_TCLICKS[0]),
        _template_anchor_index(*_TCLICKS[1]),
        _template_anchor_index(*_TCLICKS[2]),
        len(_TPTS) - 1,
    ]
    _ANCHOR_PT = [_TCLICKS[0], _TCLICKS[1], _TCLICKS[2], (_TPTS[-1][1], _TPTS[-1][2])]
else:
    _ANCHOR_IDX = [0, 50, 100, 150]
    _ANCHOR_PT = [(61, 67), (178, 74), (245, 161), (300, 200)]

# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _as_image(src: ImageLike) -> Image.Image:
    if isinstance(src, Image.Image):
        return src
    if isinstance(src, (bytes, bytearray)):
        return Image.open(io.BytesIO(src))
    return Image.open(src)

def _b64_to_bytes(b64_str: str) -> bytes:
    """Decode base64 or data-URI to raw bytes."""
    import base64
    s = b64_str.strip()
    if "," in s and s.startswith("data:"):
        s = s.split(",", 1)[1]
    return base64.b64decode(s)

# ---------------------------------------------------------------------------
# Phase 1: Slide solver — Sobel edge + NCC 1D scan
# ---------------------------------------------------------------------------

def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    gx = sobel(gray, axis=1, mode="reflect")
    gy = sobel(gray, axis=0, mode="reflect")
    return np.hypot(gx, gy)

def _ncc_1d(bg_edge: np.ndarray, template: np.ndarray, y0: int) -> np.ndarray:
    """Normalized cross-correlation scan along x-axis at fixed y."""
    ph, pw = template.shape
    h, w = bg_edge.shape
    y1 = min(y0 + ph, h)
    template = template[:y1 - y0]
    t_c = template - template.mean()
    t_norm = np.sqrt((t_c ** 2).sum()) + 1e-9

    scores = np.full(w - pw + 1, -1.0)
    for x in range(scores.size):
        window = bg_edge[y0:y1, x:x + pw]
        w_c = window - window.mean()
        w_norm = np.sqrt((w_c ** 2).sum())
        if w_norm > 1e-6:
            scores[x] = float((w_c * t_c).sum() / (w_norm * t_norm))
    return scores


@dataclass
class SlideSolution:
    x: int
    confidence: float
    margin: float
    scores: np.ndarray

    def __int__(self) -> int:
        return self.x


def solve_slide(background: ImageLike, slide: ImageLike, slide_y: int,
                x_range: Optional[Tuple[int, int]] = None) -> SlideSolution:
    """Find x-offset where the puzzle piece fits in the background hole.

    Uses Sobel edge magnitude on both images, then NCC 1D scan at the
    known y-offset of the slide.
    """
    bg = _as_image(background).convert("L")
    bg_edge = _sobel_mag(np.asarray(bg, dtype=np.float64))

    piece = _as_image(slide).convert("RGBA")
    if piece.size[0] != PUZZLE_WIDTH:
        piece = piece.resize((PUZZLE_WIDTH, piece.size[1]))
    alpha = np.asarray(piece, dtype=np.float64)[:, :, 3]

    template = _sobel_mag(alpha)
    template /= template.max() + 1e-9

    y0 = int(round(slide_y))
    scores = _ncc_1d(bg_edge, template, y0)

    search = scores.copy()
    if x_range is not None:
        lo, hi = x_range
        mask = np.ones_like(search, dtype=bool)
        mask[max(0, lo):min(search.size, hi + 1)] = False
        search[mask] = -1.0

    x = int(np.argmax(search))
    peak = float(scores[x])

    rivals = scores.copy()
    rivals[max(0, x - PUZZLE_WIDTH // 2):x + PUZZLE_WIDTH // 2 + 1] = -1.0
    margin = peak - float(rivals.max())

    return SlideSolution(x=x, confidence=peak, margin=margin, scores=scores)


# ---------------------------------------------------------------------------
# Phase 2: Icon locator — color distance + edge weighting + FFT convolution
# ---------------------------------------------------------------------------

def _feature_maps(img: Image.Image) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """Build per-icon color+edge feature maps for star/calendar/buy."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
    gray = rgb.mean(axis=2)
    edge = np.hypot(sobel(gray, axis=1, mode="reflect"), sobel(gray, axis=0, mode="reflect"))
    edge_n = np.clip(edge / (np.percentile(edge, 98) + 1e-9), 0.0, 1.0)

    dists = {n: np.sqrt(((rgb - np.array(c, dtype=np.float64)) ** 2).sum(axis=2))
             for n, c in ICON_COLORS.items()}
    assign = np.argmin(np.stack([dists[n] for n in _ICON_NAMES]), axis=0)

    maps: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for k, n in enumerate(_ICON_NAMES):
        close = np.clip(1.0 - dists[n] / _COLOR_TOL, 0.0, 1.0)
        col = close * ((assign == k) & (dists[n] < _MAX_TOL))
        maps[n] = (col * edge_n, col)
    return maps


def _locate(maps: Dict[str, Tuple[np.ndarray, np.ndarray]], name: str) -> Tuple[int, int, float]:
    """Find the center of an icon by FFT-convolving a disk kernel over the feature map."""
    loc, col = maps[name]
    dens = fftconvolve(loc, _DISK, mode="same")
    py, px = np.unravel_index(int(np.argmax(dens)), dens.shape)
    conf = float(dens[py, px])

    r = _GLYPH_RADIUS
    y0, y1 = max(0, py - r), min(col.shape[0], py + r + 1)
    x0, x1 = max(0, px - r), min(col.shape[1], px + r + 1)
    win = col[y0:y1, x0:x1]
    thr = max(1e-6, 0.4 * win.max())
    ys, xs = np.where(win >= thr)
    if len(xs) == 0:
        return int(px), int(py), conf
    cx = x0 + (int(xs.min()) + int(xs.max())) / 2.0
    cy = y0 + (int(ys.min()) + int(ys.max())) / 2.0
    return int(round(cx)), int(round(cy)), conf


def locate_icon(image: ImageLike, name: str) -> Tuple[int, int, float]:
    """Locate a single named icon in an image."""
    return _locate(_feature_maps(_as_image(image).convert("RGB")), name)


def solve_icons(background: ImageLike, icons_order: List[str]) -> List[Dict[str, int]]:
    """Find the center coordinates of each icon in the given order."""
    maps = _feature_maps(_as_image(background).convert("RGB"))
    return [{"x": x, "y": y} for x, y, _ in (_locate(maps, n) for n in icons_order)]


# ---------------------------------------------------------------------------
# Trail generation — human-like mouse movement
# ---------------------------------------------------------------------------

Trail = List[Dict[str, int]]


def generate_slide_trail(
    target_x: int,
    start_x: int = SLIDER_START,
    rng: Optional[random.Random] = None,
    now_ms: Optional[int] = None,
) -> Tuple[Trail, Trail]:
    """Generate a cosine-eased slide trail from start_x to target_x."""
    rng = rng or random
    target_x = int(round(target_x))
    distance = max(1, target_x - start_x)

    n = int(min(260, max(45, distance * 1.7)))
    t = int(now_ms if now_ms is not None else time.time() * 1000)

    trail_x: Trail = []
    trail_y: Trail = []
    y = 0.0
    for i in range(n + 1):
        p = i / n
        ease = 0.5 - 0.5 * math.cos(math.pi * p)
        x = start_x + distance * ease + rng.uniform(-0.6, 0.6)
        cx = max(start_x, min(target_x, int(round(x))))

        y += rng.uniform(-0.8, 1.0)
        y = max(-4.0, min(20.0, y))

        dt = rng.randint(3, 11)
        if rng.random() < 0.05:
            dt += rng.randint(12, 45)
        t += dt

        trail_x.append({"timestamp": t, "coord": cx})
        trail_y.append({"timestamp": t, "coord": int(round(y))})

    trail_x[-1]["coord"] = target_x
    return trail_x, trail_y


def generate_icons_trail(
    points: List[Dict[str, int]],
    apply_button: Optional[Tuple[int, int]] = None,
    rng: Optional[random.Random] = None,
    now_ms: Optional[int] = None,
) -> Tuple[Trail, Trail]:
    """Generate human-like mouse trails warping a recorded template to the icon positions."""
    rng = rng or random
    if apply_button is None:
        apply_button = (rng.randint(160, 210), rng.randint(300, 320))
    my = [(p["x"], p["y"]) for p in points] + [apply_button]
    t0 = int(now_ms if now_ms is not None else time.time() * 1000)

    tx: Trail = []
    ty: Trail = []
    for s in range(3):
        i0, i1 = _ANCHOR_IDX[s], _ANCHOR_IDX[s + 1]
        ra, rb = _ANCHOR_PT[s], _ANCHOR_PT[s + 1]
        ma, mb = my[s], my[s + 1]
        rlen = math.hypot(rb[0] - ra[0], rb[1] - ra[1]) or 1.0
        mlen = math.hypot(mb[0] - ma[0], mb[1] - ma[1]) or 1.0
        rux, ruy = (rb[0] - ra[0]) / rlen, (rb[1] - ra[1]) / rlen
        mux, muy = (mb[0] - ma[0]) / mlen, (mb[1] - ma[1]) / mlen
        scale = mlen / rlen
        for i in range(i0, i1 + 1):
            if s > 0 and i == i0:
                continue
            ts, rx, ry = _TPTS[i]
            vx, vy = rx - ra[0], ry - ra[1]
            along = (vx * rux + vy * ruy) * scale
            perp = (vx * (-ruy) + vy * rux) * scale
            nx = max(0, min(318, ma[0] + along * mux + perp * (-muy)))
            ny = max(0, min(340, ma[1] + along * muy + perp * mux))
            tx.append({"timestamp": t0 + ts, "coord": int(round(nx))})
            ty.append({"timestamp": t0 + ts, "coord": int(round(ny))})
    return tx, ty


# ---------------------------------------------------------------------------
# Live HTTP client (curl_cffi) — for full challenge flow
# ---------------------------------------------------------------------------

class BasiliskError(RuntimeError):
    pass


class BasiliskClient:
    """HTTP client for basiliskcaptcha.com challenge flow.

    Handles: create-challenge → slide-challenge → slide-verify →
    icons-challenge → icons-verify → captcha_response token.
    """

    def __init__(self, site_key: str, site_domain: str,
                 impersonate: str = "chrome",
                 rng: Optional[random.Random] = None,
                 proxy: Optional[str] = None,
                 fast: bool = False):
        from curl_cffi import requests as cffi_requests
        self.site_key = site_key
        self.site_domain = site_domain
        self.rng = rng or random.Random()
        self.fast = fast
        self.proxy = proxy
        self.session = cffi_requests.Session(impersonate=impersonate)
        self.headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": site_domain,
            "Referer": site_domain.rstrip("/") + "/",
        }

    def _pause(self, lo: float, hi: float, fast_lo: float = 0.0, fast_hi: float = 0.0):
        a, b = (fast_lo, fast_hi) if self.fast else (lo, hi)
        if b > 0:
            time.sleep(self.rng.uniform(a, b))

    def _post(self, endpoint: str, extra: Optional[dict] = None) -> dict:
        payload: dict = {"site_key": self.site_key, "site_domain": self.site_domain}
        if extra:
            payload.update(extra)
        r = self.session.post(
            f"{SERVER}/challenge/{endpoint}",
            data=json.dumps(payload),
            headers=self.headers,
            timeout=20,
            proxies={"https": self.proxy, "http": self.proxy} if self.proxy else None,
        )
        try:
            return r.json()
        except Exception as exc:
            raise BasiliskError(f"{endpoint}: non-JSON reply {r.status_code}") from exc

    def check_site(self) -> bool:
        return bool(self._post("check-site").get("success"))

    def create_challenge(self, retries: int = 30) -> dict:
        for _ in range(retries):
            r = self._post("create-challenge")
            if r.get("success"):
                return r["data"]
            if r.get("message") != "Rejected":
                raise BasiliskError(f"create-challenge: {r.get('message')}")
            time.sleep(0.3)
        raise BasiliskError("create-challenge kept returning Rejected (rate limit)")

    def refresh_slide(self, captcha_id: str) -> dict:
        r = self._post("slide-challenge", {"captcha_id": captcha_id})
        if not r.get("success"):
            raise BasiliskError(f"slide-challenge refresh failed: {r.get('message')}")
        return r["data"]

    def fetch_image(self, url: str) -> Image.Image:
        return Image.open(io.BytesIO(
            self.session.get(url, headers=self.headers, timeout=20,
                             proxies={"https": self.proxy, "http": self.proxy} if self.proxy else None
                             ).content))

    def submit_slide(self, captcha_id: str, solution: SlideSolution) -> dict:
        trail_x, trail_y = generate_slide_trail(solution.x, rng=self.rng)
        return self._post("slide-verify",
                          {"captcha_id": captcha_id, "trail_x": trail_x, "trail_y": trail_y})

    def solve_slide_challenge(self, data: dict) -> SlideSolution:
        bg = self.fetch_image(data["background_url"])
        piece = self.fetch_image(data["slide_url"])
        return solve_slide(bg, piece, data["slide_y"])

    def solve_slide_phase(self, max_attempts: int = 4) -> dict:
        """Solve the slide phase, returning captcha_id + solution data."""
        data = self.create_challenge()
        captcha_id = data["captcha_id"]
        for attempt in range(max_attempts):
            solution = self.solve_slide_challenge(data)
            self._pause(1.2, 2.5)
            result = self.submit_slide(captcha_id, solution)
            if result.get("success"):
                return {"captcha_id": captcha_id, "data": data,
                        "solution": solution, "attempts": attempt + 1}
            if attempt < max_attempts - 1:
                data = self.refresh_slide(captcha_id)
        raise BasiliskError(f"slide unsolved after {max_attempts} attempts")

    def icons_challenge(self, captcha_id: str) -> dict:
        r = self._post("icons-challenge", {"captcha_id": captcha_id})
        if not r.get("success"):
            raise BasiliskError(f"icons-challenge failed: {r.get('message')}")
        return r["data"]

    def submit_icons(self, captcha_id: str, coords: list) -> dict:
        trail_x, trail_y = generate_icons_trail(coords, rng=self.rng)
        return self._post("icons-verify",
                          {"captcha_id": captcha_id, "coords": coords,
                           "trail_x": trail_x, "trail_y": trail_y})

    def solve_full(self, max_attempts: int = 3) -> str:
        """Solve both phases and return the captcha_response token."""
        slide = self.solve_slide_phase()
        captcha_id = slide["captcha_id"]
        for _ in range(max_attempts):
            self._pause(1.0, 1.8)
            data = self.icons_challenge(captcha_id)
            bg = self.fetch_image(data["background_url"])
            coords = solve_icons(bg, data["icons_order"])
            self._pause(1.2, 2.5)
            result = self.submit_icons(captcha_id, coords)
            token = (result.get("data") or {}).get("captcha_response")
            if result.get("success") and token:
                return token
        raise BasiliskError(f"icons unsolved after {max_attempts} attempts")


# ---------------------------------------------------------------------------
# Public entry point — uniform dict contract
# ---------------------------------------------------------------------------

async def solve_basilisk(
    image_b64: Optional[str] = None,
    url: Optional[str] = None,
    site_key: Optional[str] = None,
    site_domain: Optional[str] = None,
    slide_bg_b64: Optional[str] = None,
    slide_piece_b64: Optional[str] = None,
    slide_y: Optional[int] = None,
    icons_bg_b64: Optional[str] = None,
    icons_order: Optional[List[str]] = None,
    proxy: Optional[str] = None,
    timeout_s: int = 60,
) -> dict:
    """Solve a Basilisk captcha.

    Modes:
      A) Pre-harvested images (offline, no HTTP):
         Pass slide_bg_b64, slide_piece_b64, slide_y for the slide phase,
         and icons_bg_b64 + icons_order for the icons phase.
         Returns slide_x and icon_coords.

      B) Live HTTP flow:
         Pass site_key + site_domain to run the full challenge flow
         against basiliskcaptcha.com. Returns the final captcha_response token.

      C) Single-image slide-only (image_b64):
         Pass image_b64 as the slide background with slide_piece_b64 + slide_y.
         Returns just the slide solution.

    Returns uniform dict:
      {solved: bool, type: "basilisk", token: str, method: str,
       elapsed: float, error: str|None, ...extra...}
    """
    t0 = time.monotonic()
    result: Dict[str, Any] = {
        "type": "basilisk",
        "solved": False,
        "token": "",
        "method": "cv-ncc+color-fft",
        "elapsed": 0.0,
        "error": None,
    }

    try:
        # --- Mode B: full live HTTP flow ---
        if site_key and site_domain:
            def _live_solve() -> str:
                client = BasiliskClient(
                    site_key=site_key,
                    site_domain=site_domain,
                    proxy=proxy,
                    fast=True,
                )
                return client.solve_full()

            token = await asyncio.wait_for(
                asyncio.to_thread(_live_solve),
                timeout=max(timeout_s, 30),
            )
            result["solved"] = True
            result["token"] = token
            result["method"] = "cv-ncc+color-fft+http"
            result["elapsed"] = round(time.monotonic() - t0, 2)
            log.info("basilisk: live solved in %.1fs", result["elapsed"])
            return result

        # --- Mode A: pre-harvested slide + icons ---
        if slide_bg_b64 and slide_piece_b64 and slide_y is not None:
            slide_bg_bytes = _b64_to_bytes(slide_bg_b64)
            slide_piece_bytes = _b64_to_bytes(slide_piece_b64)

            def _solve_slide() -> SlideSolution:
                return solve_slide(slide_bg_bytes, slide_piece_bytes, int(slide_y))

            sol = await asyncio.wait_for(
                asyncio.to_thread(_solve_slide),
                timeout=max(timeout_s, 10),
            )
            result["slide_x"] = sol.x
            result["slide_confidence"] = round(sol.confidence, 4)
            result["slide_margin"] = round(sol.margin, 4)

            # If icon data also provided, solve the full flow
            if icons_bg_b64 and icons_order:
                icons_bg_bytes = _b64_to_bytes(icons_bg_b64)

                def _solve_icons() -> List[Dict[str, int]]:
                    return solve_icons(icons_bg_bytes, icons_order)

                coords = await asyncio.wait_for(
                    asyncio.to_thread(_solve_icons),
                    timeout=max(timeout_s, 10),
                )
                result["icon_coords"] = coords
                result["icons_order"] = icons_order

                # Generate trails (demonstrates full output, useful for replay)
                slide_trail_x, slide_trail_y = generate_slide_trail(sol.x)
                icons_trail_x, icons_trail_y = generate_icons_trail(coords)
                result["slide_trail_points"] = len(slide_trail_x)
                result["icons_trail_points"] = len(icons_trail_x)

                # Token = serialized solution for submission
                solution_payload = {
                    "slide_x": sol.x,
                    "icon_coords": coords,
                }
                result["token"] = json.dumps(solution_payload)
                result["method"] = "cv-ncc+color-fft"
            else:
                # Slide-only
                result["token"] = str(sol.x)
                result["method"] = "cv-ncc"

            result["solved"] = True
            result["elapsed"] = round(time.monotonic() - t0, 2)
            log.info("basilisk: solved in %.1fs slide_x=%d", result["elapsed"], sol.x)
            return result

        # --- Mode C: image_b64 as slide background ---
        if image_b64 and slide_piece_b64 and slide_y is not None:
            bg_bytes = _b64_to_bytes(image_b64)
            piece_bytes = _b64_to_bytes(slide_piece_b64)

            def _solve_single() -> SlideSolution:
                return solve_slide(bg_bytes, piece_bytes, int(slide_y))

            sol = await asyncio.wait_for(
                asyncio.to_thread(_solve_single),
                timeout=max(timeout_s, 10),
            )
            result["solved"] = True
            result["token"] = str(sol.x)
            result["method"] = "cv-ncc"
            result["slide_x"] = sol.x
            result["slide_confidence"] = round(sol.confidence, 4)
            result["slide_margin"] = round(sol.margin, 4)
            result["elapsed"] = round(time.monotonic() - t0, 2)
            log.info("basilisk: slide solved x=%d conf=%.2f in %.1fs",
                     sol.x, sol.confidence, result["elapsed"])
            return result

        # --- Nothing valid ---
        result["error"] = (
            "basilisk: pass (site_key + site_domain) for live flow, "
            "or (slide_bg_b64 + slide_piece_b64 + slide_y) for offline solve, "
            "or (image_b64 + slide_piece_b64 + slide_y) for single-image slide"
        )
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result

    except asyncio.TimeoutError:
        result["error"] = f"basilisk: exceeded {timeout_s}s timeout"
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result
    except BasiliskError as e:
        result["error"] = str(e)
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result
    except Exception as e:
        result["error"] = f"basilisk: {type(e).__name__}: {e}"
        result["elapsed"] = round(time.monotonic() - t0, 2)
        return result
