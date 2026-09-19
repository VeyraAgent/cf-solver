"""Tencent Captcha solver adapter — bridges tencent_core.solve_captcha to the
sidecar's uniform result contract.

Pure-HTTP solve (no browser): prehandle → image gap detect (3-algorithm fusion) →
local PoW → pure-Python TDC XTEA collect → verify → {ticket, randstr}.

Optional dependency: Node.js is needed ONCE when a new tdc.js version appears
(key extraction probe); afterwards fully Python.
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


def available() -> bool:
    try:
        from solvers.tencent import tencent_core  # noqa: F401
        return True
    except Exception:
        return False


def _run_sync(appid: str, timeout_s: int) -> dict:
    from solvers.tencent.tencent_core import solve_captcha

    r = solve_captcha(appid=appid, max_retries=max(2, min(10, int(timeout_s) // 12)))
    solved = bool(r.get("success") and r.get("ticket"))
    return {
        "solved": solved,
        "type": "tencent",
        "token": r.get("ticket", ""),
        "ticket": r.get("ticket", ""),
        "randstr": r.get("randstr", ""),
        "method": "pure-http",
        "elapsed": round(r.get("elapsed_ms", 0) / 1000, 1) or 0.0,
        "error": None if solved else str(r.get("errorMessage") or r.get("errorCode") or "no ticket"),
        "gap_x": r.get("gap_x"),
        "attempts": r.get("attempts"),
    }


async def solve_tencent(appid: str = "199999861", timeout_s: int = 90) -> dict:
    """Solve a Tencent captcha challenge for `appid` (default = the public test appid)."""
    t0 = time.monotonic()

    def _run() -> dict:
        return _run_sync(appid, timeout_s)

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return {
            "solved": False, "type": "tencent", "token": "", "ticket": "", "randstr": "",
            "method": "pure-http", "elapsed": round(time.monotonic() - t0, 1),
            "error": f"tencent solve timed out after {timeout_s}s",
        }
    except Exception as exc:
        log.warning("tencent solver failed: %s", exc)
        return {
            "solved": False, "type": "tencent", "token": "", "ticket": "", "randstr": "",
            "method": "pure-http", "elapsed": round(time.monotonic() - t0, 1),
            "error": str(exc).splitlines()[0][:200],
        }
