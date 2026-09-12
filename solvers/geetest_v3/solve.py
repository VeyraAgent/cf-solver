"""GeeTest v3 solver — generic gt+challenge flow via the biliTicker_gt Rust/Python
binding (Amorter/biliTicker_gt, AGPL-3.0).

ClickPy.simple_match(gt, challenge) drives the full v3 protocol: get_c_s →
get_type → calculate_key → generate_w → verify, with the Siamese grid model
auto-downloaded to ~/projects/models on first run.

Note: needs Python 3.12/3.13 on Linux (no cp311 manylinux wheel; build from
source requires cargo + libssl-dev).
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


def available() -> bool:
    try:
        import bili_ticket_gt_python  # noqa: F401
        return True
    except Exception:
        return False


def _run_sync(gt: str, challenge: str) -> dict:
    from bili_ticket_gt_python import ClickPy

    click = ClickPy()
    validate = click.simple_match(gt, challenge)
    solved = bool(validate)
    return {
        "solved": solved, "type": "geetest_v3", "token": validate or "",
        "challenge": challenge, "validate": validate or "",
        "seccode": (validate + "|jordan") if validate else "",
        "method": "gt3-click", "elapsed": 0.0,
        "error": None if solved else "simple_match returned no validate",
    }


async def solve_geetest_v3(gt: str, challenge: str, timeout_s: int = 90) -> dict:
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "geetest_v3", "token": "",
            "method": "gt3-click", "elapsed": round(time.monotonic() - t0, 1),
            "error": error,
        }

    if not gt or not challenge:
        return _fail("gt and challenge are required")

    def _run() -> dict:
        r = _run_sync(gt, challenge)
        r["elapsed"] = round(time.monotonic() - t0, 1)
        return r

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _fail(f"geetest v3 solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("geetest v3 failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
