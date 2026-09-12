"""GeeTest v4 solver adapter — bridges the geeked package (ported from
xKiian/GeekedTest, MIT) to the sidecar's uniform result contract.

Pure-HTTP: /load → solve (slide/icon/gobang via OpenCV + ONNX, or invisible ai)
→ /verify with the W-signed payload → {captcha_output, pass_token, lot_number}.

Constants note: GeeTest rotates the sign constants with gt4 JS versions. When
solves return `result: forbidden`, regenerate `sign.py` mapping/abo via the
deobfuscate routine (see geetest/README.md in the upstream repo).
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


def _run_sync(captcha_id: str, risk_type: str) -> dict:
    from solvers.geetest.geeked import Geeked

    g = Geeked(captcha_id, risk_type=risk_type)
    sec = g.solve()
    solved = bool(sec.get("captcha_output"))
    return {
        "solved": solved,
        "type": "geetest",
        "token": sec.get("captcha_output", ""),
        "captcha_output": sec.get("captcha_output", ""),
        "pass_token": sec.get("pass_token", ""),
        "lot_number": sec.get("lot_number", ""),
        "gen_time": sec.get("gen_time", ""),
        "method": f"geetest-v4-{risk_type}",
        "elapsed": 0.0,
        "error": None if solved else "no captcha_output in seccode",
    }


async def solve_geetest(captcha_id: str = "54088bb07d2df3c46b79f80300b0abbe",
                        risk_type: str = "slide", timeout_s: int = 90) -> dict:
    t0 = time.monotonic()

    def _run() -> dict:
        r = _run_sync(captcha_id, risk_type)
        r["elapsed"] = round(time.monotonic() - t0, 1)
        return r

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return {
            "solved": False, "type": "geetest", "token": "", "captcha_output": "",
            "method": f"geetest-v4-{risk_type}",
            "elapsed": round(time.monotonic() - t0, 1),
            "error": f"geetest solve timed out after {timeout_s}s",
        }
    except Exception as exc:
        log.warning("geetest solver failed: %s", exc)
        return {
            "solved": False, "type": "geetest", "token": "", "captcha_output": "",
            "method": f"geetest-v4-{risk_type}",
            "elapsed": round(time.monotonic() - t0, 1),
            "error": str(exc).splitlines()[0][:200],
        }
