"""Vaptcha (手势验证 / gesture captcha) solver — pure-HTTP V4 protocol + CV port.

Protocol layer is reverse-engineered from the live V4 widget stack
(c4.vaptcha.com/src/{v4,core}.js, /src/verify.html, build 202609041954):

    POST v4c.vaptcha.com/api/config   -> {server, knock}
    POST {server}/api/knock           -> {trajectory (img url), pow_start, result, salt, ...}
    GET  {trajectory}?_t=...          -> challenge image (480x270-class photo with a
                                         translucent bright stroke to trace)
    PoW sha256 counter reversal       -> order (port of pow.js VaptchaPow.solve)
    POST {server}/api/validate        -> {result: true, token, dfu, ip} on pass
                                         (bodies use the {"v":1,"d":b64url(...)} wire
                                         envelope; see protocol.py for the full map)

Recognition layer ports wobuxiangtong/vaptcha_recoginse (Mask R-CNN gesture-line
segmentation) to a deterministic classical detector — top-hat lift + DP max-brightness
path (see recognize.py). If solvers/vaptcha/line_seg.onnx exists it is preferred.

Entry: solve_vaptcha(vid, url, proxy, timeout_s) — `vid` is the site's Vaptcha VID;
`url` (the protected page) is used only as the config `href` field. On pass the
uniform contract is fulfilled with `token` = validate response token, which the
caller replays to the site exactly like the widget's vaptcha_pass message does.

Known limits (honest): the V4 server scores device fingerprints (dfc/dfa/dfb, webgl,
canvas, ja3 — all wire fields are sent, zeros/empties by default) and trace quality.
Pure-HTTP attempts may fail risk scoring on stricter deployments; failures surface as
solved:false with the server message (refresh_required / try_smaller / try_larger /
retry). The challenge refresh is free, so the flow retries across detection
candidates within timeout.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import time

import numpy as np

from . import protocol as vp
from .recognize import N_ANCHORS, detect_gesture_curves, detect_line_curve, sample_anchors

log = logging.getLogger("vaptcha")

_MAX_ATTEMPTS = 5  # validate/knock refresh cycles within timeout (refresh is free)


def _result(**kw) -> dict:
    base = {"solved": False, "type": "vaptcha", "token": "", "method": "pure-http",
            "elapsed": 0.0, "error": None}
    base.update(kw)
    return base


def _decode_image(data: bytes) -> np.ndarray | None:
    import cv2  # noqa: PLC0415

    arr = np.frombuffer(data, np.uint8)
    im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if im is None:
        return None
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def _build_validate_request(vid: str, tag: str, dfu: str, lang: str, knock: str,
                            points: list[dict], pow_info: dict, knock_params: dict,
                            pow_actual_ms: int) -> dict:
    encoded = vp.encode_segment(points)
    total_duration = points[-1]["t"] if points else 0
    req = dict(knock_params or {})
    req.update({
        "knock": knock, "vid": vid, "tag": tag, "dfu": dfu, "lang": lang,
        "sr": 60, "sw": 1920, "sh": 1080, "dpr": 1,
        "dfc_components": "{}",
        "pow_actual_ms": max(0, int(pow_actual_ms)),
        "trajectory": {"points": points, "encoded": encoded, "totalDuration": total_duration},
        "pow": {"nonce": pow_info.get("nonce", -1), "order": pow_info.get("order", "")},
        "pow_test": 0,
        "fingerprint_version": 0, "fingerprint_collect_ms": 0, "fingerprint_wait_ms": 0,
        "fingerprint_from_cache": True,
        "dfc_webdriver": False, "dfc_is_headless": False, "dfc_bad_gpu": False,
        "dfc_score": 0, "dfc_level": "", "dfc_reasons": [],
        "webgl_vendor": "", "webgl_renderer": "", "canvas_fingerprint": "",
        "dfa_components": "{}", "webgl_extensions_hash": "", "capabilities": "{}",
        "dfb_components": "{}", "dfa": "", "dfb": "", "dfc": "", "ja3": "",
        "attack_code": "", "pow_plan_ms": 0,
    })
    return req


async def solve_vaptcha(vid: str | None = None, url: str | None = None,
                        proxy: str | None = None, timeout_s: int = 90,
                        *, lang: str = "zh-CN", image_b64: str | None = None,
                        server: str | None = None, knock: str | None = None,
                        knock_params: dict | None = None) -> dict:
    """Solve a Vaptcha gesture challenge.

    vid          : the site's Vaptcha VID (24-hex). Required unless `server`+`knock`
                   (and optionally image_b64) are supplied directly.
    url          : protected page origin — only used as config `href`.
    proxy        : optional proxy URL passed to httpx.
    image_b64    : caller-provided challenge image (base64) — skips config/knock
                   fetch for prediction-only mode (no token without a live knock).
    server/knock : caller-supplied challenge coordinates (advanced; replay mode).
    """
    t0 = time.monotonic()

    async def _fail(error: str, **extra) -> dict:
        log.info("vaptcha solve failed: %s", error)
        return _result(error=error, elapsed=round(time.monotonic() - t0, 2), **extra)

    if not vid and not (server and knock):
        return await _fail("vid is required (or server+knock for direct mode)")

    try:
        # ---- 1. config -> server + initial knock id ------------------------
        if not (server and knock):
            cfg = await vp.fetch_config(vid or "", proxy=proxy, timeout_s=min(timeout_s, 15),
                                        href=url or "")
            if not isinstance(cfg, dict) or cfg.get("code") not in (0, None):
                return await _fail(f"config rejected: code={cfg.get('code')} msg={cfg.get('msg')}")
            data = cfg.get("data") or {}
            server = data.get("server") or ""
            knock = data.get("knock") or ""
            if not server or not knock:
                return await _fail("config did not return server/knock",
                                   config_keys=sorted(k for k in data if isinstance(data, dict)))
            log.info("config ok: server=%s knock=%s", server, str(knock)[:12])

        # ---- 2. knock -> challenge image + PoW params ----------------------
        kn = await vp.fetch_knock(server, knock, vid or "", tag="", dfu="", lang=lang,
                                  knock_params=knock_params, proxy=proxy,
                                  timeout_s=min(timeout_s, 15))
        if not isinstance(kn, dict) or kn.get("code") not in (0, None):
            msg = kn.get("msg") or kn.get("message") if isinstance(kn, dict) else str(kn)
            return await _fail(f"knock rejected: {msg}")
        kd = kn.get("data") or {}
        img_url = kd.get("trajectory") or kd.get("imgUrl") or ""
        pow_start = kd.get("pow_start")
        pow_target = kd.get("result") or ""
        pow_salt = kd.get("salt") or ""
        knock_params = kd.get("knockParams") or knock_params or {}
        log.info("knock ok: type=%s title=%s pow=%s", kd.get("type"), kd.get("title"),
                 bool(pow_start and pow_target))

        # ---- 3. challenge image --------------------------------------------
        if image_b64:
            raw = base64.b64decode(image_b64)
        elif img_url:
            raw = await vp.fetch_image(img_url, proxy=proxy, timeout_s=min(timeout_s, 15))
        else:
            raw = b""
        im = _decode_image(raw) if raw else None
        if im is None:
            return await _fail("challenge image unavailable/undecodable",
                               image_url=img_url or None)

        # ---- 4. recognition -------------------------------------------------
        curve, method = detect_line_curve(im)
        if len(curve) < 8:
            return await _fail("gesture stroke not found in challenge image",
                               recognition_method=method)
        candidates = [curve] + detect_gesture_curves(im, k=2)
        # The widget records pointer positions on a fixed 420x280 canvas that
        # stretches the challenge image — map image pixels to canvas space.
        ih, iw = im.shape[:2]
        sx, sy = vp.CANVAS_W / max(iw, 1), vp.CANVAS_H / max(ih, 1)
        candidates = [c * np.array([sx, sy]) for c in candidates]
        log.info("stroke detected via %s (%d candidates, img %dx%d -> canvas %.2f/%.2f)",
                 method, len(candidates), iw, ih, sx, sy)
        if pow_start is not None and pow_target and pow_salt:
            pow_info = await asyncio.to_thread(
                vp.solve_pow, int(pow_start), str(pow_salt), str(pow_target))
            if not pow_info["order"]:
                return await _fail("PoW not solved within 1e6 rounds")

        candidates = [curve] + detect_gesture_curves(im, k=2)
        rng = random.Random()
        last_message = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            curve = candidates[min(attempt - 1, len(candidates) - 1)]
            anchors = sample_anchors(curve, n=N_ANCHORS)
            xy = vp.curve_to_xy(curve)
            points = vp.synthesize_trajectory(
                [(vp.nan_guard(x), vp.nan_guard(y)) for x, y in xy],
                total_ms=rng.uniform(900.0, 1600.0), rng=rng)
            if len(points) < vp.MIN_POINTS:
                return await _fail("trajectory too short after filtering",
                                   points=len(points))
            req = _build_validate_request(vid or "", "", "", lang, str(knock), points,
                                          pow_info, knock_params, pow_info["elapsed_ms"])
            try:
                resp = await vp.submit_validate(server, req, proxy=proxy,
                                                timeout_s=min(timeout_s, 15))
            except Exception as exc:
                return await _fail(f"validate request failed: {exc}",
                                   attempt=attempt, recognition_method=method)
            payload = resp.get("data") if isinstance(resp, dict) else None
            if isinstance(payload, dict) and "result" in payload:
                pass_payload = payload
            else:
                pass_payload = resp if isinstance(resp, dict) else {}
            if pass_payload.get("result") is True:
                elapsed = round(time.monotonic() - t0, 2)
                token = str(pass_payload.get("token") or "")
                log.info("vaptcha PASS in %.1fs (attempt %d, method=%s)",
                         elapsed, attempt, method)
                return _result(solved=True, token=token, method=f"pure-http/{method}",
                               elapsed=elapsed, error=None, attempt=attempt,
                               knock=str(knock), server=server, dfu=pass_payload.get("dfu"),
                               ip=pass_payload.get("ip"), anchors=anchors,
                               recognition_method=method, points=len(points))
            message = str(pass_payload.get("message") or pass_payload.get("msg") or "retry")
            code = resp.get("code") if isinstance(resp, dict) else None
            log.info("attempt %d rejected: code=%s message=%s", attempt, code, message)
            last_message = message
            if message in ("try_smaller", "try_larger", "retry"):
                continue  # same knock, next candidate curve / new jitter
            if message == "refresh_required" and img_url and attempt < _MAX_ATTEMPTS:
                # fresh image + fresh knock, re-run recognition
                try:
                    kn2 = await vp.fetch_knock(server, str(knock), vid or "", tag="",
                                               dfu="", lang=lang,
                                               knock_params=knock_params, proxy=proxy,
                                               timeout_s=min(timeout_s, 15))
                    kd2 = (kn2.get("data") or {}) if isinstance(kn2, dict) else {}
                    if kd2.get("knock"):
                        knock = kd2["knock"]
                    if kd2.get("trajectory"):
                        img_url = kd2["trajectory"]
                        raw = await vp.fetch_image(img_url, proxy=proxy,
                                                   timeout_s=min(timeout_s, 15))
                        im2 = _decode_image(raw)
                        if im2 is not None:
                            im = im2
                            candidates = detect_gesture_curves(im, k=3)
                    if kd2.get("pow_start") is not None and kd2.get("result") and kd2.get("salt"):
                        pow_info = await asyncio.to_thread(
                            vp.solve_pow, int(kd2["pow_start"]), str(kd2["salt"]),
                            str(kd2["result"]))
                except Exception as exc:
                    log.warning("refresh cycle failed: %s", exc)
                continue
        return await _fail(f"rejected after {_MAX_ATTEMPTS} attempts: {last_message}",
                           recognition_method=method)
    except Exception as exc:
        return await _fail(f"{type(exc).__name__}: {exc}")


def predict_points(image_bytes: bytes) -> dict:
    """Standalone recognition helper: challenge image bytes -> anchors + curve.

    Useful for callers that fetch the image themselves (or for tests); no network.
    """
    import cv2  # noqa: PLC0415

    arr = np.frombuffer(image_bytes, np.uint8)
    im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if im is None:
        return {"ok": False, "error": "undecodable image"}
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    curve, method = detect_line_curve(im)
    if len(curve) < 8:
        return {"ok": False, "error": "stroke not found", "method": method}
    return {"ok": True, "method": method,
            "anchors": sample_anchors(curve, n=N_ANCHORS),
            "curve": [[float(x), float(y)] for x, y in curve]}
