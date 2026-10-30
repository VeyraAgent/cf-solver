"""AWS WAF **pure-JS** token solver — no browser, no Chromium.

`solvers/awswaf/solve.py` drives a real browser and lets the silent challenge JS
set `aws-waf-token`. That works, but it costs a browser boot, needs a clean IP,
and cannot answer anything the WAF decides to escalate.

This module is the protocol port: it re-implements the challenge client in Node
(`waf_core.js`, vendored from the @Real_zezomod reference solver) and drives it
per request through `run_once.js`. What it does:

  1. GET the protected URL, discover the challenge base
     (`*.token.awswaf.com/...` / same-origin `__challenge_*` / linked JS bundles)
     and scrape `window.gokuProps` + `awsWafCookieDomainList`.
  2. GET `{chal}/inputs?client=browser` → challenge blob + difficulty.
  3. Build the telemetry signal (canvas/GPU/audio/capabilities metrics), CRC32 it,
     AES-256-GCM encrypt it with the WAF `Zoey` key.
  4. Solve the PoW (SHA256 hashcash / `mp_verify` bandwidth multipart), POST it,
     retry 400/429 with a fresh signal, twice round-trip for a bound token.
  5. Return the `aws-waf-token` value.

The token is IP+UA bound exactly like the browser path — replay it from the same
egress and keep `proxy_used` in the response.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_RUNNER = _HERE / "run_once.js"


def _node_env() -> dict:
    env = dict(os.environ)
    # keep node from trying to write caches next to the package on read-only images
    env.setdefault("NODE_OPTIONS", "--no-warnings")
    env.setdefault("SOLVER_DELAY", "100")
    return env


def available() -> bool:
    """True when node + the vendored core are usable."""
    if not _RUNNER.is_file() or not (_HERE / "waf_core.js").is_file():
        return False
    if not (_HERE / "node_modules" / "https-proxy-agent").is_dir():
        return False
    from shutil import which
    return which("node") is not None


def _fail(error: str, t0: float) -> dict:
    return {"solved": False, "token": "", "error": str(error)[:300],
            "method": "waf-js", "elapsed": round(time.monotonic() - t0, 1)}


async def solve_awswaf_js(url: str, proxy: str | None = None, timeout_s: int = 90,
                          challenge_url: str | None = None,
                          user_agent: str | None = None) -> dict:
    """Return `{solved, token, elapsed, method}` for an AWS WAF-protected page."""
    t0 = time.monotonic()
    if not url:
        return _fail("url is required", t0)
    if not available():
        return _fail("node + vendored waf_core/node_modules missing", t0)

    args = ["node", str(_RUNNER), url, proxy or "", challenge_url or "", user_agent or ""]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(_HERE), env=_node_env())
    # the JS itself can burn up to ~60 s on PoW retries; give it headroom
    budget = max(30, int(timeout_s or 90)) + 30
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=budget)
    except asyncio.TimeoutError:
        proc.kill()
        return _fail(f"waf_js exceeded {budget}s", t0)

    payload: dict | None = None
    for line in (out or b"").decode(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except Exception:
                continue
    if payload is None:
        tail = (err or b"").decode(errors="replace").strip().splitlines()[-3:]
        return _fail(f"no JSON from node (rc={proc.returncode}): {' | '.join(tail)[:200]}", t0)

    if not payload.get("ok"):
        return _fail(payload.get("error") or "waf solve failed", t0)

    token = payload.get("token") or ""
    if not token:
        return _fail("node reported ok but no token", t0)
    return {"solved": True, "token": token, "method": "waf-js",
            "elapsed": round(time.monotonic() - t0, 1),
            "waf_elapsed": payload.get("elapsed"),
            "proxy_used": proxy or None}


async def solve(*args, **kwargs) -> dict:  # pragma: no cover - dispatch alias
    return await solve_awswaf_js(*args, **kwargs)


if __name__ == "__main__":  # python -m solvers.awswaf_js.solve <url> [proxy]
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m solvers.awswaf_js.solve <url> [proxy]")
    print(asyncio.run(solve_awswaf_js(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)))
