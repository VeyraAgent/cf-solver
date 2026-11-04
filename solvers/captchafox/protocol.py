"""CaptchaFox protocol mode — sitekey + page URL in, live token out.

The image-mode solver in `solve.py` only locates the slider offset; the caller
still has to fetch config, solve the PoW, post the challenge and verify. This
module runs the whole flow against the CaptchaFox API and returns a token the
site would accept.

Core logic is vendored in `_protocol_core.py` (third-party, attribution there).
Everything here is thin adaptation: uniform result dict, proxy normalisation,
timeout handling, sync->async bridge.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from ._protocol_core import CaptchaFoxSolver, format_proxy

log = logging.getLogger("captchafox.protocol")


def _run(**kw) -> dict[str, Any]:
    t0 = time.monotonic()
    solver = CaptchaFoxSolver(**kw)
    token, resolved = solver.solve()
    return {
        "solved": bool(token),
        "type": "captchafox",
        "token": token,
        "mode": "protocol",
        "challenge_type": resolved,
        "elapsed": round(time.monotonic() - t0, 2),
    }


async def solve_protocol(
    sitekey: str,
    site_url: str,
    captcha_type: str = "auto",
    proxy: Optional[str] = None,
    api_base: Optional[str] = None,
    pulse: Optional[str] = None,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """Solve a CaptchaFox challenge end-to-end.

    Args:
        sitekey: public sitekey (`sk_...`).
        site_url: full URL of the page hosting the widget (drives host/enterprise detection).
        captcha_type: "normal" | "slider" | "auto".
        proxy: host:port:user:pass or full URL; used for the whole session.
    """
    t0 = time.monotonic()
    if not sitekey:
        return {"solved": False, "type": "captchafox", "mode": "protocol",
                "token": "", "error": "captchafox protocol: sitekey required"}
    if not site_url:
        return {"solved": False, "type": "captchafox", "mode": "protocol",
                "token": "", "error": "captchafox protocol: url required"}

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _run,
                sitekey=sitekey,
                site_url=site_url,
                captcha_type=captcha_type or "auto",
                api_base=api_base,
                pulse=pulse,
                proxy=format_proxy(proxy),
            ),
            timeout=max(timeout_s, 15),
        )
        log.info("captchafox protocol: %s in %ss", result.get("challenge_type"), result.get("elapsed"))
        return result
    except asyncio.TimeoutError:
        return {"solved": False, "type": "captchafox", "mode": "protocol", "token": "",
                "error": f"captchafox protocol: exceeded {timeout_s}s",
                "elapsed": round(time.monotonic() - t0, 2)}
    except Exception as exc:  # noqa: BLE001 - surface provider errors verbatim
        return {"solved": False, "type": "captchafox", "mode": "protocol", "token": "",
                "error": f"captchafox protocol: {type(exc).__name__}: {exc}",
                "elapsed": round(time.monotonic() - t0, 2)}
