"""Douyin/TikTok puzzle captcha solver — port of onurkun/puzzle-captcha-resolver.

Two input paths, one uniform result contract:

1. Local images (primary, matches the reference webserver contract of
   POST full_image + image_partial): the caller passes `image_b64` + `piece_b64`
   (or URLs via `image_url`/`piece_url`). Solver locates the gap (gap.py), builds
   a human-shaped drag trajectory (ballistic ease-out + overshoot-and-correct,
   the profile that passes kinematic scoring), and returns x/y + trajectory.
   The actual submit is the caller's job here — the douyin client binds verify
   to its session (fp/msToken/captchaBody), so a token minted without those
   params would be rejected; the reference repo has exactly the same boundary.

2. Pure-HTTP challenge fetch (no images given): fetches a LIVE slide challenge
   from the ByteDance captcha endpoint (douyin: GET verify.zijieapi.com/captcha/get,
   verified working 2026-09-13; tiktok: verification-va.tiktok.com), downloads
   question.url1/url2, runs the same gap + trajectory pipeline, then attempts
   POST /captcha/verify. Verified live: the challenge fetch + gap solve works;
   the verify endpoint demands the client-encrypted `captchaBody` (server
   returns 504/5011 for a plain body — the encryption is SDK-version-bound and
   drifts), so the solver returns the solve artifacts (x/y/tip_y/trajectory +
   challenge id) with `verified: false` instead of lying about a token.

Result: {"solved", "type", "token", "method", "elapsed", "error", "x", "y",
"score", "trajectory", "verified", "challenge_id", "tip_y", "x_display", ...}.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import time

import httpx
import numpy as np

from .gap import detect_gap

log = logging.getLogger("douyin")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# ByteDance captcha endpoints — douyin set verified LIVE 2026-09-13 against
# verify.zijieapi.com (GET /captcha/get → 200, mode=slide, question.url1/url2).
# The fp is a client fingerprint seed; a random 19-digit value is accepted by
# /captcha/get (the challenge is IP-bound, not fp-bound at this stage).
# Params drift per SDK version — params_extra merges over them.
_ENDPOINTS = {
    "douyin": {
        "base": "https://verify.zijieapi.com",
        "get_params": {"aid": "6383", "app_name": "douyin_web", "lang": "unsup",
                       "h5_sdk_version": "2.29.0", "sdk_version": "", "iid": "0",
                       "device_id": "0", "did": "0", "web_id": "0", "ch": "",
                       "os_type": "2", "os_version": "Mac OS 10.16.0",
                       "category": "1", "subtype": "slide", "double_check": "1"},
    },
    "tiktok": {
        "base": "https://verification-va.tiktok.com",
        "get_params": {"aid": "1988", "app_name": "tiktok_web", "lang": "en",
                       "captcha_type": "slide", "subcategory": "slide"},
    },
}

# Widget display width douyin renders the 552px background at (verify x-space).
_DISPLAY_WIDTH = 340


def _rand_fp() -> str:
    """Random 19-digit fingerprint seed (accepted by /captcha/get)."""
    return str(random.randint(10**18, 10**19 - 1))


def gen_trajectory(distance: float, y: float | None = None,
                   seed: int | None = None) -> dict:
    """Human-shaped drag path: ballistic ease-out to overshoot, then correction.

    Mirrors the profile proven on aliyun (solvers/aliyun): kinematic scoring
    rejects a monotonic smoothstep even at the pixel-correct position; humans
    overshoot the target by ~6-11px then drift back. Deterministic under `seed`.
    Returns {"points": [{"x", "y", "t_ms"}, ...], "total_ms"}.
    """
    rng = random.Random(seed)
    n = max(12, min(48, int(abs(distance) / 6) + 10))
    over = rng.uniform(6.0, 11.0) * (1 if distance >= 0 else -1)
    points: list[dict] = []
    t_ms = 0.0
    # ballistic phase: fast start, slow approach, slight overshoot
    for i in range(1, n + 1):
        t = i / n
        x = (distance + over) * (1 - (1 - t) ** 3)  # ballistic ease-out to overshoot
        wy = (float(np.sin(t * 5) * 1.2) + rng.uniform(-0.6, 0.6)
              + (float(y) if y is not None else 0.0))
        t_ms += 9.0 + (i % 4) * 3.0 + rng.uniform(0, 4)
        points.append({"x": round(x, 1), "y": round(wy, 1), "t_ms": round(t_ms, 1)})
    # correction phase: drift back from the overshoot to the true target
    for j in range(1, 11):
        t = j / 10
        x = distance + over - over * t  # correct back from overshoot to target
        t_ms += 14.0 + rng.uniform(0, 8)
        points.append({"x": round(x, 1),
                       "y": round(rng.uniform(-0.4, 0.4) + (float(y) if y is not None else 0.0), 1),
                       "t_ms": round(t_ms, 1)})
    return {"points": points, "total_ms": round(t_ms, 1)}


def _result(**kw) -> dict:
    base = {"solved": False, "type": "douyin", "token": "", "method": "",
            "elapsed": 0.0, "error": None}
    base.update(kw)
    return base


def _result_shape(x: int | None, y: int | None, score, method: str,
                  traj: dict | None, verified: bool, **extra) -> dict:
    """Uniform extras shared by every exit path. `token` carries the submit
    artifacts when a gap was solved (x/y + trajectory); a verify ticket string
    replaces it only on a server-accepted verify."""
    token: str | dict = ""
    if x is not None:
        token = {"x": x, "y": y, "score": score, "trajectory": traj, **extra,
                 "note": "replay verify from a session that holds fp/msToken/captchaBody"}
    return {"token": token, "method": method, "x": x, "y": y, "score": score,
            "trajectory": traj, "verified": verified, **extra}


async def _download_image(client: httpx.AsyncClient, src: str) -> str:
    """Fetch an image (URL or already-base64 payload) and return base64."""
    if not src or "://" not in src[:8]:
        return src
    r = await client.get(src)
    r.raise_for_status()
    return base64.b64encode(r.content).decode()


async def _solve_local(image_b64: str, piece_b64: str, seed: int | None) -> dict:
    """Gap detect + trajectory on caller-supplied images (the reference path)."""
    gap = detect_gap(image_b64, piece_b64)
    traj = gen_trajectory(gap["x"], seed=seed)
    log.info("local solve: gap %s trajectory %d pts / %.0fms",
             gap, len(traj["points"]), traj["total_ms"])
    shape = _result_shape(gap["x"], gap["y"], gap["score"],
                          f"local-{gap['method']}", traj, verified=False,
                          ref_score=gap.get("ref_score"))
    return _result(solved=True, error=None, **shape)


def _challenge_images(data: dict) -> tuple[str, str]:
    """Extract (bg_url, piece_url) from a challenge payload.

    Live douyin format: data.question.url1/url2. Older captures used url_list /
    sub_agent.url_list — kept as fallbacks.
    """
    q = data.get("question") or {}
    if q.get("url1") and q.get("url2"):
        return q["url1"], q["url2"]
    urls = q.get("url_list") or (data.get("sub_agent") or {}).get("url_list") or []
    if len(urls) >= 2:
        return urls[0], urls[1]
    raise ValueError(f"challenge payload has no image pair (keys={sorted(q.keys()) or '-'})")


async def _fetch_and_solve(platform: str, web_id: str | None, url: str | None,
                           proxy: str | None, seed: int | None,
                           params_extra: dict | None) -> dict:
    """Fetch a LIVE slide challenge, solve the gap, attempt POST /captcha/verify."""
    ep = _ENDPOINTS[platform]
    params = dict(ep["get_params"])
    params["fp"] = _rand_fp()
    params["sub_sec_timestamp"] = str(int(time.time() * 1000))
    if web_id:
        params["web_id"] = web_id
        if platform == "douyin":
            params["did"] = web_id
    if params_extra:
        params.update(params_extra)
    headers = {"User-Agent": _UA, "Referer": url or f"https://www.{platform}.com/"}

    async with httpx.AsyncClient(proxy=proxy, timeout=20, headers=headers,
                                 follow_redirects=True) as cli:
        r = await cli.get(f"{ep['base']}/captcha/get", params=params)
        log.info("get %s -> %d", r.request.url, r.status_code)
        r.raise_for_status()
        payload = r.json() or {}
        if payload.get("code") not in (200, "200"):
            return _result(error=f"captcha/get rejected: {str(payload)[:200]}")
        data = payload.get("data") or {}
        challenge_id = data.get("id") or ""
        tip_y = (data.get("question") or {}).get("tip_y")
        bg_url, piece_url = _challenge_images(data)
        image_b64 = await _download_image(cli, bg_url)
        piece_b64 = await _download_image(cli, piece_url)

        gap = detect_gap(image_b64, piece_b64)
        traj = gen_trajectory(gap["x"], seed=seed)

        # scale to the widget's display coordinate space (verify x-space)
        from PIL import Image
        import io as _io
        bg_w = Image.open(_io.BytesIO(base64.b64decode(image_b64))).width
        x_display = round(gap["x"] * _DISPLAY_WIDTH / bg_w) if bg_w else None
        log.info("challenge %s: gap (%d,%d) score %.3f tip_y=%s x_display=%s",
                 challenge_id[:12], gap["x"], gap["y"], gap["score"], tip_y, x_display)
        shape = _result_shape(
            gap["x"], gap["y"], gap["score"], f"{platform}-{gap['method']}", traj,
            verified=False, challenge_id=challenge_id, tip_y=tip_y,
            x_display=x_display, display_width=_DISPLAY_WIDTH,
            ref_score=gap.get("ref_score"))

        # Verify attempt. LIVE finding 2026-09-13: POST /captcha/verify with a
        # plain JSON body returns 504 "参数错误[5011]" — the SDK encrypts the
        # slide params into `captchaBody`; without it the solve cannot be
        # server-confirmed here. Still attempted for forward compatibility.
        body = {"challenge_code": data.get("challenge_code"), "id": challenge_id,
                "mode": data.get("mode") or "slide", "subtype": "slide",
                "x": x_display, "y": tip_y, "reply": '[],[""],[""],[]',
                "fixed_ratio": 1}
        try:
            v = await cli.post(f"{ep['base']}/captcha/verify", params=params, json=body)
            vdata = v.json() if v.status_code == 200 else {}
            log.info("verify -> %d %s", v.status_code, str(vdata)[:160])
            if vdata.get("code") == 200:
                ticket = ((vdata.get("data") or {}).get("log_pb") or {}).get(
                    "captcha_verify_ticket") or vdata.get("data") or ""
                shape["verified"] = True
                out = _result(solved=True, token=ticket, **{
                    k: v2 for k, v2 in shape.items() if k != "token"})
                out.pop("error", None)
                return out
        except Exception as exc:
            log.info("verify attempt failed (returning local result): %s", exc)
        return _result(solved=True, error=None, **shape)


async def solve_douyin(url: str | None = None, web_id: str | None = None,
                       proxy: str | None = None, timeout_s: int = 90,
                       image_b64: str | None = None, piece_b64: str | None = None,
                       image_url: str | None = None, piece_url: str | None = None,
                       platform: str = "douyin", seed: int | None = None,
                       params_extra: dict | None = None) -> dict:
    """Solve a Douyin/TikTok puzzle captcha.

    Local mode: pass `image_b64` (full) + `piece_b64` (or their URL variants) —
    the reference webserver's full_image/image_partial contract.
    Network mode: no images — a challenge is fetched LIVE from the ByteDance
    captcha endpoint (aid=6383 douyin / 1988 tiktok), gap-solved, and verify is
    attempted. `url` only sets the Referer; `web_id` binds the challenge to a
    caller session when given.
    """
    t0 = time.monotonic()

    async def _run() -> dict:
        full_b64, part_b64 = image_b64, piece_b64
        if image_url or piece_url:
            async with httpx.AsyncClient(proxy=proxy, timeout=20,
                                         headers={"User-Agent": _UA}) as cli:
                full_b64 = full_b64 or await _download_image(cli, image_url or "")
                part_b64 = part_b64 or await _download_image(cli, piece_url or "")
        if full_b64 and part_b64:
            return await _solve_local(full_b64, part_b64, seed)
        if platform not in _ENDPOINTS:
            return _result(error=f"unknown platform {platform!r}; "
                                 f"expected one of {sorted(_ENDPOINTS)}")
        return await _fetch_and_solve(platform, web_id, url, proxy, seed, params_extra)

    try:
        out = await asyncio.wait_for(_run(), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _result(method="douyin", elapsed=round(time.monotonic() - t0, 1),
                       error=f"douyin solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("douyin solver failed: %s", exc)
        return _result(method="douyin", elapsed=round(time.monotonic() - t0, 1),
                       error=str(exc).splitlines()[0][:200])
    out["elapsed"] = round(time.monotonic() - t0, 1)
    return out
