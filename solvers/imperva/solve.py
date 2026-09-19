"""Imperva Incapsula solver — drives a Node-sidecar session (ported from
BottingRocks/Incapsula, protocol reference) that:

  1. Navigates the protected site,
  2. Generates the `___utmvc` fingerprint cookie (sensor table + digest/seed),
  3. Runs the site's own obfuscated reese84 script in a VM to produce the
     interrogation payload (xorshift128 + morphing encoding loops),
  4. POSTs both, harvesting `visid_incap_*`, `incap_ses_*`, `___utmvc=a` and
     `reese84` cookies.

Requires Node.js (the sidecar runs headless — no browser). Known-good targets:
balance.vanillagift.com / vanillaprepaid.com / pokemoncenter.com (hybrid w/
DataDome). Cookie contract: replay from the same IP + User-Agent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

_NODE_DIR = Path(__file__).parent / "node"
_RUNNER = _NODE_DIR / "runner.js"


def available() -> bool:
    return bool(shutil.which("node")) and _RUNNER.exists()


def _parse_cookie_string(raw: str) -> list[dict]:
    out = []
    for pair in raw.split("; "):
        if "=" in pair:
            name, _, value = pair.partition("=")
            if name.strip():
                out.append({"name": name.strip(), "value": value})
    return out


def _run_sync(url: str, proxy: str, ua: str, timeout_s: float) -> dict:
    node = shutil.which("node")
    proc = asyncio.run(_spawn(node, url, proxy, ua, timeout_s))
    return proc


async def _spawn(node: str, url: str, proxy: str, ua: str, timeout_s: float) -> dict:
    process = await asyncio.create_subprocess_exec(
        node, str(_RUNNER), url, proxy or "", ua or "",
        cwd=str(_NODE_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={"NODE_TLS_REJECT_UNAUTHORIZED": "0", "PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        process.kill()
        raise RuntimeError(f"node sidecar timed out after {timeout_s}s")

    marker = "__RESULT__"
    result = None
    for line in (out or b"").decode(errors="replace").splitlines():
        if line.startswith(marker):
            result = json.loads(line[len(marker):])
            break
    if result is None:
        stderr = (err or b"").decode(errors="replace")
        raise RuntimeError(f"no runner result; stderr: {stderr[-200:]}")
    return result


async def solve_imperva(url: str, proxy: str = None, timeout_s: int = 120,
                        user_agent: str = None) -> dict:
    """Run a full Incapsula session for `url` and return the incap cookies."""
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "imperva", "token": "", "cookies": None,
            "method": "node-session", "elapsed": round(time.monotonic() - t0, 1),
            "error": error,
            "warning": ("Incap cookies are bound to IP + User-Agent. Replay from the "
                        "same IP with this exact User-Agent."),
        }

    if not available():
        return _fail("node.js not found on PATH (imperva node sidecar requires node)")

    def _sync() -> dict:
        return _run_sync(url, proxy, user_agent, float(timeout_s))

    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_sync), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _fail(f"imperva session timed out after {timeout_s}s")
    except Exception as exc:
        return _fail(str(exc).splitlines()[0][:200])

    cookies_raw = raw.get("cookies")
    if isinstance(cookies_raw, str):
        cookies = _parse_cookie_string(cookies_raw)
    elif isinstance(cookies_raw, list):
        cookies = cookies_raw
    else:
        cookies = []

    names = [c["name"] for c in cookies]
    has_ses = any(n.startswith("incap_ses_") for n in names)
    has_vis = any(n.startswith("visid_incap_") for n in names)
    solved = bool(raw.get("success")) or (has_ses and has_vis)

    # token = the incap_ses value (the replay-critical session cookie)
    ses_value = next((c["value"] for c in cookies if c["name"].startswith("incap_ses_")), "")

    result = {
        "solved": solved,
        "type": "imperva",
        "token": ses_value,
        "cookies": cookies,
        "cookie_names": names,
        "method": "node-session",
        "elapsed": round(time.monotonic() - t0, 1),
        "error": None if solved else "session incomplete (incap cookies missing)",
        "user_agent": raw.get("userAgent"),
        "warning": ("Incap cookies are bound to IP + User-Agent. Replay from the same "
                    "IP with this exact User-Agent. Some sites layer DataDome on top "
                    "(datadome cookie in jar) — solve that separately if present."),
    }
    return result
