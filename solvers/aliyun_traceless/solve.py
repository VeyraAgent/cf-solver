"""Aliyun NoCaptcha **TRACELESS** mode — the checkbox with no puzzle.

`solvers/aliyun` handles the slide/overlay challenges (`scene_id` + `prefix`,
pure protocol). TRACELESS is a different animal: `InitCaptchaV3` answers
`CaptchaType: "TRACELESS"`, the widget renders a bare `确认您不是机器人`
checkbox, and the score comes entirely from device/browser telemetry. There is
nothing visual to answer — so the only thing that works is a *real* anti-detect
browser that emits believable telemetry, clicking the box with real pointer
events.

That is what this solver is: Camoufox (Firefox, humanised) → open the host
flow → fill the form → submit → wait for the popup → humanised click →
verify `验证通过`.

**The session runs in a subprocess** (`_run.py`). Two reasons, both proven:
Camoufox's driver is the *sync* Playwright API (it loses its connection object
when the async drivers of the other solvers are live in the same process), and a
sync browser must never block uvicorn's loop. Same shape as `solvers/aliyun`.

Measured on `sso.openxlab.org.cn` (the reference run): 4/5 (80%), 34–39 s each.
Failures stop at the checkbox (challenge rejected at init) and correlate with
long runs — throttle, not a bad click. Give it ≥30 s between runs and rotate
IPs, or the rate will fall.

Env notes:
  * Camoufox must be importable in the **serving** interpreter
    (`pip install "camoufox[geoip]"`); it is required, not optional.
  * If the binary lives outside the cache, export `CAMOUFOX_EXECUTABLE_PATH`.
  * `humanize=True` is mandatory — it is what makes the pointer telemetry count.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import time

log = logging.getLogger(__name__)

PASS_TEXT = "验证通过"
FAIL_TEXT = "确认您不是机器人"


def available() -> bool:
    """True when this interpreter can launch Camoufox."""
    try:
        return importlib.util.find_spec("camoufox.sync_api") is not None
    except (ImportError, ValueError):
        return False


async def solve_aliyun_traceless(url: str, username: str | None = None,
                                 password: str | None = None,
                                 user_selector: str = "#normal_login_account, input[type=text]",
                                 pass_selector: str = "input[type=password]",
                                 submit_selector: str = 'button:has-text("登录")',
                                 proxy: str | None = None, timeout_s: int = 180,
                                 headless: bool = True, locale: str = "zh-CN",
                                 viewport: tuple[int, int] = (1920, 1080),
                                 warmup_ms: int = 0,
                                 out_dir: str = "/tmp/tcaptcha") -> dict:
    """Pass a TRACELESS Aliyun checkbox. `solved` ⇔ `验证通过` was observed."""
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {"solved": False, "method": "aliyun-traceless",
                "elapsed": round(time.monotonic() - t0, 1), "error": error}

    if not url:
        return _fail("url is required")
    if not available():
        return _fail("camoufox is not installed in the serving interpreter "
                     "(pip install 'camoufox[geoip]')")

    payload = {
        "url": url, "username": username or "captcha_probe_dummy",
        "password": password or "Probe!2345xyz",
        "user_selector": user_selector, "pass_selector": pass_selector,
        "submit_selector": submit_selector, "headless": bool(headless),
        "locale": locale, "viewport": [int(viewport[0]), int(viewport[1])],
        "warmup_ms": int(warmup_ms or 0), "proxy": proxy, "out_dir": out_dir,
    }
    budget = max(60, int(timeout_s or 180))
    # display sanity: a headed Camoufox needs a live X socket, same rule as the
    # Chromium solvers (WSLg exports a stale DISPLAY).
    from solvers.common.browser import display_usable

    if not payload["headless"] and not display_usable():
        log.warning("DISPLAY has no live X socket — running aliyun_traceless headless")
        payload["headless"] = True

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "solvers.aliyun_traceless._run",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env,
        cwd=env["PYTHONPATH"])
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()),
                                          timeout=budget + 30)
    except asyncio.TimeoutError:
        proc.kill()
        return _fail(f"timed out after {budget + 30}s")

    result: dict | None = None
    for line in (out or b"").decode(errors="replace").splitlines():
        if line.startswith("__ALIYUN_TRACELESS__"):
            try:
                result = json.loads(line[len("__ALIYUN_TRACELESS__"):])
            except Exception:  # noqa: BLE001
                result = None
    if result is None:
        tail = (err or b"").decode(errors="replace").strip().splitlines()[-4:]
        return _fail(f"runner produced no result (rc={proc.returncode}): "
                     f"{' | '.join(tail)[:240]}")

    detail = result.get("detail") or {}
    if detail.get("error"):
        return _fail(f"runner error: {detail['error']}")
    solved = bool(result.get("solved"))
    return {"solved": solved, "passed": solved, "verify_success": solved,
            "method": "aliyun-traceless",
            "token": detail.get("token") or "",
            "elapsed": round(time.monotonic() - t0, 1),
            "detail": {k: v for k, v in detail.items() if k != "log"},
            **({} if solved else {"error": detail.get("err") or
                                  f"checkbox state: {detail.get('final_chk')!r}"})}


async def solve(*args, **kwargs) -> dict:  # pragma: no cover - dispatch alias
    return await solve_aliyun_traceless(*args, **kwargs)


if __name__ == "__main__":  # python -m solvers.aliyun_traceless.solve <url> [user] [pass]
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m solvers.aliyun_traceless.solve <url> [user] [pass]")
    print(asyncio.run(solve_aliyun_traceless(sys.argv[1], *sys.argv[2:4], headless=True)))
