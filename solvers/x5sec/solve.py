"""Alibaba x5sec / baxia NC-slider solver — ported from VerzSolver (MIT), adapted
to this sidecar's CloakBrowser page-level pattern.

x5sec is Alibaba's anti-bot punish cookie: when a baxia-protected endpoint
(qoder.com, dingtalk, taobao, aliexpress, ...) flags a session it redirects to a
punish page containing an "NC" slide widget (`nc_1_n1z` handle over `nc_1_n1t`
track). Dragging the handle end-to-end with human-like motion flips the widget
to its success state and the server sets the `x5sec` cookie.

Page-level solver (like cloudflare/awswaf): the caller passes the punish `url`,
we navigate, find the slider in the main frame or any child iframe, drag it,
poll for the `x5sec` cookie. No sitekey.

Contract matches the other page-level solvers: `{solved, token, x5sec,
cookie_map, method, elapsed, error}`.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from typing import Any
from urllib.parse import urlparse

import cloakbrowser

from solvers.common.browser import browser_kwargs

log = logging.getLogger(__name__)
_solve_lock = asyncio.Lock()

_COOKIE_NAME = "x5sec"

# Slider detection: baxia / NC widget. Primary ids + class fallbacks.
_DETECT_JS = """() => {
  const z = document.querySelector('#nc_1_n1z')
          || document.querySelector('.btn_slide')
          || document.querySelector('[class*="btn_slide"]')
          || document.querySelector('.nc-lang-cnt ~ * .btn_slide');
  const s = document.querySelector('#nc_1_n1t')
          || document.querySelector('.nc_scale')
          || document.querySelector('#nc_1_wrapper')
          || document.querySelector('[class*="nc_scale"]');
  if (!z || !s) return null;
  const zr = z.getBoundingClientRect(), sr = s.getBoundingClientRect();
  if (zr.width < 5 || sr.width < 50) return null;
  return {
    x1: zr.x + zr.width / 2, y1: zr.y + zr.height / 2,
    x2: sr.x + sr.width - 6,  y2: zr.y + zr.height / 2,
    sw: sr.width,
  };
}"""

# baxia success sentinel — the widget swaps in a success class when solved.
_SUCCESS_JS = """() => {
  const ok = document.querySelector('.nc-lang-cnt .btn_ok, .btn_ok, [class*="btn_ok"]');
  if (ok) return true;
  const wrap = document.querySelector('#nc_1_wrapper, [class*="nc_wrapper"]');
  if (wrap && /succ|通过|success/i.test(wrap.className || '')) return true;
  return false;
}"""

_DIAG_JS = """() => {
  const txt = (document.body ? document.body.innerText : '') || '';
  return {
    title: document.title || '',
    url: location.href,
    frames: window.frames ? window.frames.length : 0,
    body_len: txt.length,
    snippet: txt.slice(0, 240).replace(/\\s+/g, ' ').trim(),
    has_nc: !!document.querySelector('[id^=nc_],[class*=nc_],[class*=baxia],#baxia-dialog'),
  };
}"""


def _all_frames(page: Any) -> list[Any]:
    """Playwright exposes page.frames as a property; tolerate a callable form."""
    frames_attr = getattr(page, "frames", None)
    if callable(frames_attr):
        try:
            frames = frames_attr()
        except Exception:
            frames = []
    else:
        frames = frames_attr or []
    main = getattr(page, "main_frame", None) or (frames[0] if frames else page)
    return [main] + [f for f in frames if f is not main]


async def _frame_offset(page: Any, frame: Any) -> tuple[float, float]:
    """Absolute page offset of a child frame (for page.mouse drags)."""
    if frame is page or frame is getattr(page, "main_frame", None):
        return (0.0, 0.0)
    try:
        el = await frame.frame_element()
        bb = await el.bounding_box()
        if bb:
            return (bb["x"], bb["y"])
    except Exception:
        pass
    return (0.0, 0.0)


async def _detect_slider(page: Any, tries: int = 20):
    """Find the NC slider geo across main frame + iframes."""
    for _ in range(tries):
        for frame in _all_frames(page):
            try:
                geo = await frame.evaluate(_DETECT_JS)
            except Exception:
                continue
            if geo:
                ox, oy = await _frame_offset(page, frame)
                geo["x1"] += ox
                geo["x2"] += ox
                geo["y1"] += oy
                geo["y2"] += oy
                return geo, frame
        await asyncio.sleep(0.4)
    return None


async def _human_drag(page: Any, x1: float, y1: float, dist: float, ms_total: float = 320.0) -> None:
    """Cubic ease-out ballistic overshoot, then correction drift back."""
    steps = max(14, int(ms_total / 12))
    overshoot = min(14.0, max(4.0, dist * 0.04)) * (1 if random.random() < 0.7 else -1)
    y_jitter = random.uniform(-2.0, 2.0)
    await page.mouse.move(x1, y1)
    await page.mouse.down()
    try:
        for i in range(1, steps + 1):
            t = i / steps
            eased = 1 - (1 - t) ** 3
            x = x1 + (dist + overshoot) * eased
            y = y1 + y_jitter * math.sin(t * math.pi) + random.uniform(-0.6, 0.6)
            await page.mouse.move(x, y)
            await asyncio.sleep(ms_total / 1000 / steps * random.uniform(0.7, 1.4))
        # correction drift back to the track end
        back_steps = max(4, steps // 4)
        for i in range(1, back_steps + 1):
            t = i / back_steps
            await page.mouse.move(x1 + dist + overshoot * (1 - t), y1 + y_jitter * (1 - t) * 0.4)
            await asyncio.sleep(0.012)
    finally:
        await page.mouse.up()


def _find_x5sec(cookies: list[dict[str, Any]]) -> str | None:
    for cookie in cookies:
        if cookie.get("name") == _COOKIE_NAME:
            value = cookie.get("value")
            if isinstance(value, str) and value:
                return value
    return None


async def _diagnostics(page: Any) -> str:
    try:
        return str(await page.evaluate(_DIAG_JS))
    except Exception:
        return "{}"


def _kwargs(proxy: str = None) -> dict:
    return browser_kwargs("TURNSTILE", proxy=proxy)


async def solve_x5sec(url: str, proxy: str = None, timeout_s: int = 90,
                      pre_actions: list = None, post_fetch: list = None) -> dict:
    """Navigate a baxia/x5sec punish URL, drag the NC slider, harvest the x5sec cookie."""
    t0 = time.monotonic()
    async with _solve_lock:
        async with await cloakbrowser.launch_async(**_kwargs(proxy)) as browser:
            context = await browser.new_context()
            page = await context.new_page()
            try:
                def _elapsed() -> float:
                    return round(time.monotonic() - t0, 3)

                def _fail(error: str, attempts: int = 0) -> dict:
                    return {
                        "solved": False, "type": "x5sec", "token": "", "x5sec": "",
                        "cookie_map": {}, "cookies": None, "method": "navigate",
                        "elapsed": _elapsed(), "error": error, "attempts": attempts,
                    }

                if pre_actions:
                    from solvers.common.browser import run_pre_actions
                    try:
                        await run_pre_actions(page, pre_actions)
                    except Exception:
                        pass

                # A stale x5sec must not be treated as a fresh solve.
                try:
                    await context.clear_cookies()
                except Exception:
                    pass

                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=min(max(int(timeout_s * 1000), 10000), 45000))
                except Exception as exc:
                    return _fail(f"navigation failed: {exc}")
                await asyncio.sleep(0.5)

                attempts, reloads, last_err = 0, 0, "no_slider"
                deadline = time.monotonic() + max(10, int(timeout_s))
                while time.monotonic() < deadline:
                    attempts += 1
                    found = await _detect_slider(page, tries=20)
                    if not found:
                        last_err = "no_slider"
                        diag = await _diagnostics(page)
                        # fail fast: repeated passes with NO baxia/NC element at all
                        # means this is not a punish page — hanging won't help.
                        if reloads >= 1 and '"has_nc": false' in diag.replace("'", '"'):
                            return _fail(f"no punish page: no baxia/NC widget on target "
                                         f"(attempts={attempts}) diag={diag}", attempts)
                        reloads += 1
                        if reloads <= 2:
                            try:
                                await page.reload(wait_until="domcontentloaded", timeout=30000)
                            except Exception:
                                pass
                            await asyncio.sleep(1.0)
                            continue
                        return _fail(f"no_slider: target never rendered a baxia/NC slider "
                                     f"(attempts={attempts}) diag={diag}", attempts)

                    geo, frame = found
                    dist = geo["x2"] - geo["x1"]
                    try:
                        await _human_drag(page, geo["x1"], geo["y1"], max(dist, 40.0))
                    except Exception as exc:
                        last_err = f"drag failed: {exc}"
                        await asyncio.sleep(1.0)
                        continue

                    # success sentinel, then cookie poll
                    local_deadline = min(deadline, time.monotonic() + 8.0)
                    while time.monotonic() < local_deadline:
                        try:
                            if await page.evaluate(_SUCCESS_JS):
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(0.4)

                    cookies = await context.cookies()
                    value = _find_x5sec(cookies)
                    if value:
                        ua = ""
                        try:
                            ua = await page.evaluate("() => navigator.userAgent")
                        except Exception:
                            pass
                        result = {
                            "solved": True, "type": "x5sec", "token": value,
                            "x5sec": value, "cookie_map": {_COOKIE_NAME: value},
                            "cookies": cookies, "method": "navigate",
                            "elapsed": _elapsed(), "error": None, "attempts": attempts,
                            "user_agent": ua,
                            "warning": ("x5sec is bound to IP + UA. Replay from the same IP "
                                        "with this User-Agent over a matching TLS stack."),
                        }
                        if post_fetch:
                            from solvers.common.browser import run_post_fetch
                            try:
                                result["post_fetch"] = await run_post_fetch(page, post_fetch, value)
                            except Exception:
                                pass
                        return result
                    last_err = "slider dragged but x5sec cookie not set"
                    await asyncio.sleep(1.2)

                diag = await _diagnostics(page)
                return _fail(f"{last_err} (attempts={attempts}) diag={diag}", attempts)
            finally:
                try:
                    await page.close()
                    await context.close()
                except Exception:
                    pass
