"""Dingxiang (顶象) slider captcha solver — pure-HTTP port of the v5 web protocol.

Protocol captured live from the official demo page (captcha-ui v5.1.53,
cdn.dingxiang-inc.com/ctu-group/captcha-ui/demo/, 2026-09-13) and cross-checked
against the param model documented by aidencaptcha/DingxiangCaptchaBreak
(ak / appKey / greenseer / ac / token). Flow:

  1. GET  https://cap.dingxiang-inc.com/api/a
         ?w=<box width>&h=<box height>&s=50&ak=<appId>&c=<constId>&jsv=<sdk ver>
         &aid=dx-<ms>-<rand>-<seq>&wp=1&de=0&uid=&lf=0&tpc=&cid=<8 digits>&_r=<rand>
     → {"sid": "<32hex>", "cid": ..., "y": <gap Y>, "p1": "/dx/<path>.webp",
       "p2": "/dx/<path>.webp", "type": 0, ...}
     `sid` is the challenge session id; `y` is the server-provided gap Y;
     p1 = background image path, p2 = slider piece image path (RGBA).
  2. Images: https://static4.dingxiang-inc.com/picture + p1 / p2
  3. POST https://cap.dingxiang-inc.com/api/v1  (application/x-www-form-urlencoded)
         ac=<SDK encrypted telemetry blob>&ak=<appId>&c=&uid=&jsv=&sid=<sid>
         &aid=<aid>&x=<piece final left offset in box px>&y=<gap Y native>&w=<w>&h=<h>
     x semantics (verified with controlled drags): x = final left offset of the
     slider PIECE inside the image box = piece_rest_offset (20) + drag distance.
     → {"success": bool, "token": str|null, "msg": "retry"|..., "retry": int}

The `ac` blob is the SDK's encrypted trajectory/environment payload (the
"greenseer" family of params the reference repo studies). Its encryption is
closed-source inside the obfuscated v5 SDK — this solver implements every step
around it and accepts the blob from three sources (explicit `ac`, an `ac_api`
provider URL, or a challenge-only mode that hands the caller a ready submit
payload). See README.md for the honest capability boundary.

Optional constId step (device id, raises pass rate):
  GET https://constid.dingxiang-inc.com/udid/c1?_t=<ms>
     → {"data": "<constId>", "status": 1, ...}
The v5 demo does not tie constId into /api/a; it is fetched but only the
`cid` (8-digit client id) is echoed by the challenge endpoint.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
import time
from io import BytesIO
from typing import Any, Callable
from urllib.parse import urlencode

import numpy as np
from PIL import Image

log = logging.getLogger("dingxiang")

# Official public demo appId (embedded in dingxiang's own demo page JS — public
# test target, not a secret credential).
DEMO_APP_ID = "12610a3853150e888ccd0c6d4c415626"

_JSV = "5.1.53"  # captcha-ui SDK version the wire format was captured from
_CAPTCHA_API = "https://cap.dingxiang-inc.com"
_PICTURE_CDN = "https://static4.dingxiang-inc.com/picture"
_CONSTID_API = "https://constid.dingxiang-inc.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_DEFAULT_REFERER = "https://cdn.dingxiang-inc.com/"

# Piece rest offset inside the image box (v5 web, w=300): sub-slider starts 20px
# from the box left edge. Verified live: submitted x = 20 + drag distance.
PIECE_REST_OFFSET = 20.0


# --------------------------------------------------------------------------- #
# URL builders (pure functions — unit-testable without network)                #
# --------------------------------------------------------------------------- #

def new_aid(seq: int = 1, ts: float | None = None) -> str:
    """Widget instance id: dx-<epoch ms>-<8 digits>-<seq> (format from live capture)."""
    ms = int(ts * 1000) if ts is not None else int(time.time() * 1000)
    return f"dx-{ms}-{random.randint(10_000_000, 99_999_999)}-{seq}"


def new_cid() -> str:
    """Per-page client id: 8 decimal digits (observed: cid=04167336)."""
    return f"{random.randint(0, 99_999_999):08d}"


def build_challenge_url(
    app_id: str,
    w: int = 300,
    h: int = 165,
    jsv: str = _JSV,
    aid: str | None = None,
    cid: str | None = None,
    lf: int = 0,
    s: int = 50,
    c: str = "",
) -> str:
    """Build the GET /api/a challenge URL exactly as the v5 SDK sends it.

    `c` carries the constId device token once the udid/c1 step resolved
    (observed: first request empty, later requests c=<constId>).
    """
    if not app_id:
        raise ValueError("app_id is required")
    params = {
        "w": w,
        "h": h,
        "s": s,
        "ak": app_id,
        "c": c,
        "jsv": jsv,
        "aid": aid or new_aid(),
        "wp": 1,
        "de": 0,
        "uid": "",
        "lf": lf,
        "tpc": "",
        "cid": cid or new_cid(),
        "_r": random.random(),
    }
    return f"{_CAPTCHA_API}/api/a?{urlencode(params)}"


def build_image_url(path: str) -> str:
    """p1/p2 relative paths → picture CDN URL (observed prefix: static4)."""
    if not path:
        raise ValueError("empty image path")
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{_PICTURE_CDN}{path if path.startswith('/') else '/' + path}"


def build_submit_url() -> str:
    return f"{_CAPTCHA_API}/api/v1"


def build_constid_url(ts: float | None = None) -> str:
    """v5 constId endpoint (optional device-id step)."""
    return f"{_CONSTID_API}/udid/c1?_t={int((ts or time.time()) * 1000) % 100000}"


def build_submit_fields(
    ac: str,
    app_id: str,
    sid: str,
    aid: str,
    x: float,
    y: int,
    w: int = 300,
    h: int = 165,
    jsv: str = _JSV,
    cid: str = "",
    uid: str = "",
) -> dict[str, str]:
    """Form fields for POST /api/v1 — field set/order from live capture."""
    return {
        "ac": ac,
        "ak": app_id,
        "c": "",
        "uid": uid,
        "jsv": jsv,
        "sid": sid,
        "aid": aid,
        "x": f"{x:.0f}" if float(x).is_integer() else f"{x}",
        "y": str(int(y)),
        "w": str(w),
        "h": str(h),
        "cid": cid,
    }


_APPID_RES = [
    re.compile(r"""appId\s*[:=]\s*["']([0-9a-f]{32})["']""", re.I),
    re.compile(r"""[?&]ak=([0-9a-f]{32})""", re.I),
    re.compile(r"""["']([0-9a-f]{32})["']\s*[,)\]]\s*//[^\n]*ak""", re.I),
]


def extract_app_id(text: str) -> str | None:
    """Best-effort appId (ak) extraction from a target page's HTML/JS."""
    for rx in _APPID_RES:
        m = rx.search(text or "")
        if m:
            return m.group(1)
    return None


# --------------------------------------------------------------------------- #
# Trajectory (deterministic, human-shaped: fast start → decelerate → overshoot #
# → correct; micro y-noise; occasional micro-pauses)                           #
# --------------------------------------------------------------------------- #

def generate_trajectory(
    distance: float,
    seed: int | None = None,
    duration_ms: int | None = None,
    points: int | None = None,
    overshoot: bool = True,
) -> list[dict[str, float]]:
    """Build a drag trajectory for `distance` px of handle travel.

    Deterministic for a given seed. Returns [{x, y, t}] with x in
    [0, distance] (handle-relative), y in ±2 px jitter, t cumulative ms.
    """
    if distance <= 0:
        return [{"x": 0.0, "y": 0.0, "t": 0}]
    rng = random.Random(seed if seed is not None else random.randint(0, 2**31 - 1))
    n = points or max(24, min(70, int(distance / 4)))
    total = float(duration_ms or rng.randint(520, 900))

    # ease-out base curve with per-step noise
    ts = [i / (n - 1) for i in range(n)]
    over = rng.uniform(0.03, 0.07) * distance if (overshoot and distance > 40) else 0.0
    traj: list[dict[str, float]] = []
    t_acc = 0.0
    y = 0.0
    for i, u in enumerate(ts):
        # ease-out cubic + tiny low-freq wobble
        base = 1 - (1 - u) ** 3
        wobble = 0.004 * distance * rng.uniform(-1, 1)
        x = base * (distance + over) + wobble
        if i > 0:
            t_acc += (total / n) * rng.uniform(0.6, 1.5)
            if rng.random() < 0.06:  # occasional human micro-pause
                t_acc += rng.uniform(15, 45)
        y = max(-2.0, min(2.0, y + rng.uniform(-0.9, 0.9)))
        traj.append({"x": round(min(x, distance + over * 1.05), 2), "y": round(y, 2), "t": round(t_acc, 1)})
    # correction phase: pull back from overshoot to exact target
    if over:
        steps = rng.randint(3, 5)
        peak = traj[-1]["x"]
        for k in range(1, steps + 1):
            t_acc += rng.uniform(18, 40)
            traj.append({
                "x": round(peak - (peak - distance) * (k / steps), 2),
                "y": round(traj[-1]["y"] + rng.uniform(-0.5, 0.5), 2),
                "t": round(t_acc, 1),
            })
    traj[-1]["x"] = round(float(distance), 2)
    traj[-1]["t"] = round(max(traj[-1]["t"], total), 1)
    return traj


# --------------------------------------------------------------------------- #
# Gap detection (numpy/PIL; the server gives gap Y, we find gap X)              #
# --------------------------------------------------------------------------- #

def _to_np(img: Any) -> np.ndarray:
    """PIL Image / bytes / ndarray → uint8 ndarray."""
    if isinstance(img, np.ndarray):
        return img
    if isinstance(img, (bytes, bytearray)):
        return np.asarray(Image.open(BytesIO(bytes(img))))
    arr = np.asarray(img)
    return arr




def _erode(mask: np.ndarray, iters: int) -> np.ndarray:
    """Binary erosion via shift-AND (no scipy dependency)."""
    for _ in range(iters):
        mask = (mask & np.roll(mask, 1, 0) & np.roll(mask, -1, 0)
                & np.roll(mask, 1, 1) & np.roll(mask, -1, 1))
        mask[0, :] = mask[-1, :] = mask[:, 0] = mask[:, -1] = False
    return mask


def detect_gap(
    bg: Any,
    piece: Any,
    y_hint: int | None = None,
    y_slack: int = 6,
    exclude_rest_px: int = 80,
    erode_px: int = 3,
) -> dict:
    """Locate the slider-piece placement (canvas top-left) in the background.

    Dingxiang v5 holes are the original image content shown darkened through
    the piece silhouette. Matched filter: masked Pearson correlation between
    the piece's RGB content and each background window — invariant to the
    brightness scale/offset of the darkening. The piece's white outline +
    antialiased rim (baked into p2) are excluded by eroding the alpha mask,
    which is what makes the correlation converge on live challenges.

    `y_hint` (server-provided gap Y, native px = canvas-anchor row) pins the
    search to a tight row band (window row = y_hint + piece's y margin);
    wrong-row searching is deliberately NOT used as a fallback.
    `exclude_rest_px` suppresses the piece's own rendered start position on
    the left of the background.

    Returns {"x": int, "y": int, "score": float} — x/y = piece canvas
    top-left in background (native) coordinates.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    bg_arr = _to_np(bg)
    piece_arr = _to_np(piece)
    if bg_arr.ndim == 3:
        bg_gray = bg_arr[:, :, :3].mean(axis=2)
    else:
        bg_gray = bg_arr.astype(np.float64)
    if piece_arr.ndim != 3 or piece_arr.shape[2] != 4:
        raise ValueError("piece must be RGBA")
    if bg_gray.shape[0] < piece_arr.shape[0] or bg_gray.shape[1] < piece_arr.shape[1]:
        raise ValueError(f"bg {bg_gray.shape} smaller than piece {piece_arr.shape}")

    alpha = piece_arr[:, :, 3] > 24
    ys, xs = np.nonzero(alpha)
    if len(ys) == 0:
        raise ValueError("piece has no opaque pixels")
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    mask = _erode(alpha[y0:y1, x0:x1].copy(), erode_px).astype(np.float64)
    content = piece_arr[y0:y1, x0:x1, :3].mean(axis=2) * mask
    n = mask.sum()
    pc = (content - (content * mask).sum() / n) * mask
    sp = np.sqrt((pc ** 2).sum()) + 1e-9

    ph, pw = mask.shape
    H, W = bg_gray.shape

    def _search(y_lo: int, y_hi: int) -> dict:
        best = {"score": -1e18, "x": 0, "y": y_lo}
        for yy in range(max(0, y_lo), min(H - ph, y_hi) + 1):
            row = sliding_window_view(bg_gray[yy:yy + ph, :], (ph, pw))[0]  # (nx, ph, pw)
            wm = row * mask
            wmean = wm.sum(axis=(-1, -2)) / n
            wc = (row - wmean[..., None, None]) * mask
            score = (wc * pc).sum(axis=(-1, -2)) / (
                np.sqrt((wc ** 2).sum(axis=(-1, -2))) * sp + 1e-9)
            if exclude_rest_px > 0:
                score = np.where(np.arange(score.shape[0]) < exclude_rest_px, -2.0, score)
            i = int(score.argmax())
            if score[i] > best["score"]:
                best = {"score": float(score[i]), "x": i, "y": yy}
        return best

    if y_hint is not None:
        # Server y = hole canvas-anchor top (native px); the correlation window
        # sits y0 lower (transparent piece margin). Trust the server row: a
        # wide fallback search only invites wrong-row photo-texture false
        # positives (observed live: score 0.45 at a wrong row vs 0.31 at the
        # true hole).
        best = _search(y_hint + y0 - y_slack, y_hint + y0 + y_slack)
    else:
        best = _search(0, H - ph)
    return {"x": best["x"] - x0, "y": best["y"] - y0, "score": round(best["score"], 4)}


# --------------------------------------------------------------------------- #
# HTTP session (httpx for http(s); curl_cffi for socks proxies — httpx lacks   #
# socks support without socksio)                                               #
# --------------------------------------------------------------------------- #

class _Session:
    """Minimal uniform async HTTP session over httpx or curl_cffi."""

    def __init__(self, proxy: str | None, timeout: float, referer: str):
        self._proxy = proxy
        self._timeout = timeout
        self._headers = {
            "User-Agent": _UA,
            "Referer": referer or _DEFAULT_REFERER,
            "Origin": "https://cdn.dingxiang-inc.com",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self._impl: Any = None
        self._kind = ""

    async def __aenter__(self) -> "_Session":
        if self._proxy and self._proxy.lower().startswith(("socks4", "socks5")):
            from curl_cffi.requests import AsyncSession
            self._impl = AsyncSession(impersonate="chrome", proxy=self._proxy, timeout=self._timeout)
            self._kind = "curl_cffi"
        else:
            import httpx
            kwargs: dict[str, Any] = {"timeout": self._timeout, "headers": self._headers,
                                      "follow_redirects": True}
            if self._proxy:
                try:
                    self._impl = httpx.AsyncClient(proxy=self._proxy, **kwargs)
                except TypeError:  # older httpx
                    self._impl = httpx.AsyncClient(proxies=self._proxy, **kwargs)
            else:
                self._impl = httpx.AsyncClient(**kwargs)
            self._kind = "httpx"
        log.debug("dingxiang session via %s (proxy=%s)", self._kind, bool(self._proxy))
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            await self._impl.aclose()
        except Exception:
            pass

    async def get(self, url: str) -> Any:
        return await self._impl.get(url, headers=self._headers, timeout=self._timeout)

    async def post_form(self, url: str, fields: dict[str, str]) -> Any:
        return await self._impl.post(url, data=fields, headers={**self._headers,
                                     "Content-Type": "application/x-www-form-urlencoded"},
                                     timeout=self._timeout)

    async def post_json(self, url: str, payload: dict) -> Any:
        return await self._impl.post(url, json=payload, headers=self._headers,
                                     timeout=self._timeout)


async def _fetch_json(sess: _Session, url: str) -> dict:
    r = await sess.get(url)
    r.raise_for_status()
    return r.json()


async def _fetch_bytes(sess: _Session, url: str) -> bytes:
    r = await sess.get(url)
    r.raise_for_status()
    return r.content


# --------------------------------------------------------------------------- #
# ac (greenseer-family telemetry blob) resolution                              #
# --------------------------------------------------------------------------- #

async def _resolve_ac(ac: str | Callable[[dict], str] | None, ac_api: str | None,
                      ctx: dict, sess: _Session) -> str | None:
    """Three sources for the SDK-generated ac blob, in priority order:
    explicit value/callable → external ac_api provider → None (challenge-only)."""
    if ac is not None:
        if callable(ac):
            out = ac(dict(ctx))
            if inspect.isawaitable(out):
                out = await out
            return str(out)
        return str(ac)
    if ac_api:
        r = await sess.post_json(ac_api, ctx)
        data = r.json() if hasattr(r, "json") else r
        blob = data.get("ac") or data.get("data") or (data if isinstance(data, str) else None)
        if blob:
            return str(blob)
        log.warning("ac_api returned no ac: %s", data)
    return None


# --------------------------------------------------------------------------- #
# Solver entry point                                                           #
# --------------------------------------------------------------------------- #

def _base_result(t0: float) -> dict:
    return {
        "solved": False,
        "type": "dingxiang",
        "token": "",
        "method": "pure-http",
        "elapsed": round(time.monotonic() - t0, 2),
        "error": None,
    }


def _error(result: dict, msg: str, **extra: Any) -> dict:
    result["error"] = msg
    result.update(extra)
    return result


async def solve_dingxiang(
    app_id: str | None = None,
    url: str | None = None,
    proxy: str | None = None,
    timeout_s: int = 90,
    *,
    app_key: str | None = None,
    ac: str | Callable[[dict], str] | None = None,
    ac_api: str | None = None,
    w: int = 300,
    h: int = 165,
    max_attempts: int = 3,
    seed: int | None = None,
    piece_rest_offset: float = PIECE_REST_OFFSET,
    constid: bool = False,
) -> dict:
    """Solve a Dingxiang (顶象) slider challenge over pure HTTP.

    app_id  — the 32-hex captcha appId ("ak"). Optional: falls back to the
              official public demo appId, or is extracted from `url`'s HTML.
    url     — target page URL (used as Referer; scanned for an appId when
              app_id is not given).
    ac      — pre-generated ac blob (or callable) if the caller has one.
    ac_api  — URL of an ac/greenseer provider service; receives
              {sid, ak, app_key, x, y, w, h, distance, trajectory, referer,
              aid, cid, jsv} and must return {"ac": "<blob>"}.
    app_key — the scenario secret key ("appKey"); only meaningful to ac
              providers (forwarded in the ac ctx) — never sent to the wire.
    Without either, the solver runs challenge-only: fetch → images → gap
    detect → trajectory → prepared submit payload (honest solved:false —
    the v5 server rejects submissions without a valid ac blob).
    """
    t0 = time.monotonic()
    result = _base_result(t0)
    deadline = t0 + max(timeout_s, 15)

    if not app_id and url:
        try:
            async with _Session(proxy, 10, url) as s:
                page = await _fetch_bytes(s, url)
            app_id = extract_app_id(page.decode("utf-8", "ignore"))
            if app_id:
                log.info("extracted appId %s from page", app_id)
        except Exception as exc:
            log.warning("appId extraction from %s failed: %s", url, exc)
    if not app_id:
        app_id = DEMO_APP_ID
        result["app_id_source"] = "demo-default"
    result["app_id"] = app_id

    aid = new_aid()
    cid = new_cid()
    referer = url or _DEFAULT_REFERER
    last_msg = ""
    attempts = 0

    try:
        async with _Session(proxy, 15, referer) as sess:
            if constid:
                try:
                    cdata = await _fetch_json(sess, build_constid_url())
                    result["constid"] = cdata.get("data")
                    log.info("constid: %s", result["constid"])
                except Exception as exc:
                    log.warning("constid step failed (non-fatal): %s", exc)

            while attempts < max_attempts and time.monotonic() < deadline:
                attempts += 1
                result["attempts"] = attempts

                # 1. challenge ------------------------------------------------
                ch_url = build_challenge_url(app_id, w=w, h=h, aid=aid, cid=cid,
                                             c=result.get("constid") or "")
                log.info("[%d] GET %s", attempts, ch_url.split("?")[0])
                ch = await _fetch_json(sess, ch_url)
                if not ch.get("sid") or ch.get("p1") is None:
                    return _error(result,
                                  f"challenge fetch failed: type={ch.get('type')} msg={ch.get('msg')}",
                                  challenge=ch)
                if ch.get("type") not in (0, None):
                    return _error(result,
                                  f"unsupported challenge type={ch.get('type')} (only slider/0 implemented)")
                sid, y = ch["sid"], int(ch["y"])
                result.update({"sid": sid, "gap_y_server": y})

                # 2. images ---------------------------------------------------
                bg_url = build_image_url(ch["p1"])
                pc_url = build_image_url(ch["p2"])
                log.info("[%d] images: %s", attempts, bg_url.rsplit('/', 1)[-1])
                bg_bytes, pc_bytes = await asyncio.gather(
                    _fetch_bytes(sess, bg_url), _fetch_bytes(sess, pc_url))

                # 3. gap detect (blocking CV → thread) ------------------------
                try:
                    det = await asyncio.to_thread(detect_gap, bg_bytes, pc_bytes, y)
                except Exception as exc:
                    return _error(result, f"gap detection failed: {exc}")
                # images are served larger than the display box (e.g. 400x200
                # native for a w=300 box): convert to the box coordinates the
                # wire protocol's `x` speaks (verified: sdk sent x=140 after a
                # 120px drag with rest offset 20; y stays native).
                bg_w, bg_h = Image.open(BytesIO(bg_bytes)).size
                x_scale = w / bg_w
                gap_x_display = det["x"] * x_scale
                log.info("[%d] gap: native=(%s,%s) score=%s display_x=%.1f (server y=%d)",
                         attempts, det["x"], det["y"], det["score"], gap_x_display, y)
                result.update({
                    "gap_x": det["x"], "gap_y_detected": det["y"],
                    "gap_score": det["score"], "gap_x_display": round(gap_x_display, 1),
                    "bg_native_size": [bg_w, bg_h],
                })

                # 4. trajectory + submit geometry ------------------------------
                distance = round(gap_x_display - piece_rest_offset, 2)
                if distance <= 0:
                    last_msg = f"gap at rest offset (x={det['x']}); refreshing"
                    log.warning("[%d] %s", attempts, last_msg)
                    continue
                traj = generate_trajectory(distance, seed=None if seed is None else seed + attempts)
                result.update({"distance": distance, "trajectory_points": len(traj)})
                submit_fields = build_submit_fields(
                    ac="", app_id=app_id, sid=sid, aid=aid, x=gap_x_display, y=y, w=w, h=h, cid=cid)
                submit_fields.pop("ac")
                result["submit_payload"] = submit_fields

                # 5. ac + submit ----------------------------------------------
                blob = await _resolve_ac(ac, ac_api, {
                    "sid": sid, "ak": app_id, "app_key": app_key,
                    "x": gap_x_display, "y": y, "w": w, "h": h,
                    "distance": distance, "trajectory": traj, "referer": referer,
                    "aid": aid, "cid": cid, "jsv": _JSV,
                }, sess)
                if not blob:
                    result["mode"] = "challenge-only"
                    return _error(
                        result,
                        "no ac provider configured: the v5 /api/v1 endpoint requires the "
                        "SDK-generated encrypted 'ac' blob (greenseer family — see README); "
                        "pass ac= or ac_api=. Challenge data + submit payload returned.",
                    )
                fields = build_submit_fields(
                    ac=blob, app_id=app_id, sid=sid, aid=aid, x=gap_x_display, y=y, w=w, h=h, cid=cid)
                log.info("[%d] POST %s (x=%s y=%s ac_len=%d)",
                         attempts, build_submit_url(), fields["x"], fields["y"], len(blob))
                resp = await sess.post_form(build_submit_url(), fields)
                resp.raise_for_status()
                verdict = resp.json()
                last_msg = str(verdict.get("msg"))
                log.info("[%d] verdict: success=%s msg=%s", attempts,
                         verdict.get("success"), last_msg)
                result["submit_msg"] = last_msg
                if verdict.get("success") and verdict.get("token"):
                    result.update({
                        "solved": True,
                        "token": verdict["token"],
                        "dx_token": verdict["token"],
                        "method": "pure-http-slider",
                    })
                    return result
                if str(verdict.get("msg")).lower() == "retry":
                    continue  # fresh challenge, next attempt
                return _error(result, f"submit rejected: msg={last_msg}", verdict=verdict)

        return _error(result, f"exhausted {attempts} attempts (last msg={last_msg or 'n/a'})")
    except asyncio.TimeoutError:
        return _error(result, f"dingxiang solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("dingxiang solver failed: %s", exc)
        return _error(result, str(exc).splitlines()[0][:200])
