"""Human-like slider drag.

Measured details that matter:
  * the background element's CSS width vs the logical challenge width gives the
    logical->screen scale (observed 0.505952 on a 1280x900 viewport),
  * the handle must be grabbed by its centre,
  * easing: ease-in-out cubic with a small deceleration tail and per-step
    jitter, 11ms (+9ms on the last few steps) - a linear instant move is a
    bot tell and invites errorCode 12/50.
"""
from __future__ import annotations

from dataclasses import dataclass

from .log import log

EASING_STEPS = 30
HOLD_MS = 90
STEP_MS = 11
TAIL_MS = 9


@dataclass
class DragResult:
    scale: float
    dx_px: float
    x0: float
    y0: float


def _ease(t: float) -> float:
    """ease-in-out cubic."""
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def execute(page, frame, logical: list, candidate: dict) -> DragResult | None:
    """Drag the slider `candidate['dx']` logical pixels. None when the DOM is missing."""
    # wait out any loading veil
    for _ in range(30):
        loading = frame.query_selector(".tc-cover.tc-loading")
        if not loading or not loading.is_visible():
            break
        page.wait_for_timeout(400)

    bg_el = frame.query_selector(".tc-bg-placeholder") or frame.query_selector(".tc-bg-img")
    bb = bg_el.bounding_box() if bg_el else None
    if not bb:
        log("drag", "bg element not found")
        return None

    handle = frame.query_selector(".tc-fg-item") or frame.query_selector(".tc-slider-normal")
    if not handle:
        log("drag", "handle not found")
        return None
    hb = handle.bounding_box()

    scale = bb["width"] / logical[0]
    dx_px = candidate["dx"] * scale
    x0 = hb["x"] + hb["width"] / 2
    y0 = hb["y"] + hb["height"] / 2
    log("drag", f"chosen={candidate.get('_label', '?')} dx={candidate['dx']} logical -> "
                f"{dx_px:.1f}px screen scale={scale:.6f} from ({x0:.1f},{y0:.1f})")

    page.mouse.move(x0, y0)
    page.mouse.down()
    page.wait_for_timeout(HOLD_MS)
    steps = EASING_STEPS
    for i in range(1, steps + 1):
        t = i / steps
        e = _ease(t)
        page.mouse.move(x0 + dx_px * e + (1 if i % 3 == 0 else 0),
                        y0 + (1 if i % 4 == 0 else 0), steps=1)
        page.wait_for_timeout(STEP_MS + (TAIL_MS if i > steps - 5 else 0))
    page.mouse.up()
    return DragResult(scale=scale, dx_px=dx_px, x0=x0, y0=y0)
