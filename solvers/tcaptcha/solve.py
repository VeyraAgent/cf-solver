"""Tencent TCaptcha slider — two modes on top of the vendored `pkg` toolkit.

`solvers/tencent` already solves the **Tencent 防水墙 / TDC** flow (pure HTTP:
prehandle → gap detect → MD5 PoW → TDC XTEA). This module is the other half:
the classic **TCaptcha slider widget**, driven in a real browser, with a
5-detector consensus on the gap and a humanised drag.

Modes (pick with `mode`):

  * `detect` (default, pure/compute, no browser):
      give it the two challenge images + the piece geometry and it runs
      ncc / canny / contour / dark / hole, then the 8px consensus rule.
      Input via `challenge_json`:
        {"bg_b64": "...", "fg_b64": "...",
         "piece": {"sprite_pos": [px, py], "size_2d": [pw, ph], "init_pos": [x, y]}}
      Output: `{solved, gap, dx, agree, candidates, clusters, blocker}`.
      This is the piece you reuse for ANY Tencent-style slider (also the
      pre-drag step of a browser flow).

  * `solve` (browser, needs account + a registered flow):
      runs the toolkit CLI (`python -m solvers.tcaptcha.pkg solve`) for a named
      flow — the only shipped flow today is `minimax-login`, which needs
      credentials. Everything is passed by env (creds never touch argv), the
      verdict comes from `outcome.json`.
      Output: `{solved, passed, attempts, outcome}`.

Why a subprocess for `solve`: the toolkit owns a Playwright **sync** session,
which cannot run inside uvicorn's event loop. Spawning it keeps the HTTP sidecar
non-blocking and isolates Chromium crashes (the same reason `aliyun` is a
subprocess).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_PKG = _HERE / "pkg"

_ALIASES = {
    "bg": "bg_b64", "bg_b64": "bg_b64", "background": "bg_b64",
    "fg": "fg_b64", "fg_b64": "fg_b64", "foreground": "fg_b64", "piece_img": "fg_b64",
}


def _b64(v: str | None) -> bytes:
    if not v:
        raise ValueError("missing image")
    raw = str(v).strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[1] if "," in raw else ""
    raw = raw.replace("\n", "").replace("\r", "").replace(" ", "")
    raw += "=" * ((-len(raw)) % 4)
    return base64.b64decode(raw)


def _detect(challenge: dict) -> dict:
    """Run the detectors + consensus in-process (no browser, ~0.2–1.5 s)."""
    from solvers.tcaptcha.pkg.detect import candidates as build_candidates
    from solvers.tcaptcha.pkg.detect import cluster, detect

    norm = {}
    for k, v in (challenge or {}).items():
        norm[_ALIASES.get(k, k)] = v
    piece = challenge.get("piece") or challenge.get("piece_json") or {}
    if isinstance(piece, str):
        piece = json.loads(piece)
    if not piece:
        raise ValueError("piece geometry is required: "
                         "{sprite_pos:[px,py], size_2d:[pw,ph], init_pos:[x,y]}")
    for key in ("sprite_pos", "size_2d", "init_pos"):
        if key not in piece:
            raise ValueError(f"piece.{key} is required")
    bg = _b64(norm.get("bg_b64"))
    fg = _b64(norm.get("fg_b64"))
    det = detect(bg, fg, piece)
    verdict = cluster(det)
    cands = build_candidates(det)
    gap = verdict.get("target_x")
    init_x = verdict.get("init_x") or 0
    agree = bool(verdict.get("agree")) and gap is not None
    dx = (int(gap) - int(init_x)) if gap is not None else None
    return {
        # `token` carries the answer (drag distance) so the generic success
        # predicate (`_is_solved`) sees a real artifact, not just consensus.
        "solved": agree,
        "token": str(dx) if dx is not None else "",
        "verify_success": agree,
        "gap": gap,
        "dx": dx,
        "agree": agree,
        "pair_ok": bool(verdict.get("pair_ok")),
        "blocker": verdict.get("blocker"),
        "clusters": verdict.get("clusters"),
        "candidates": cands,
        "detect": {k: det.get(k) for k in ("ncc", "canny", "contour", "dark", "hole")},
        "method": "tcaptcha-consensus",
    }


async def _run_flow(flow: str, email: str | None, password: str | None,
                    proxy: str | None, headless: bool, arbiter: str,
                    max_attempts: int, timeout_s: int) -> dict:
    """Drive the toolkit CLI for a registered flow; parse its outcome.json."""
    out_dir = tempfile.mkdtemp(prefix="tcaptcha-")
    env = dict(os.environ)
    env["TCAPTCHA_OUT"] = out_dir
    env.setdefault("PYTHONPATH", str(_HERE.parent.parent))  # repo root on sys.path
    if email:
        env["TCAPTCHA_EMAIL"] = email
    if password:
        env["TCAPTCHA_PASSWORD"] = password
    if proxy:
        env["TCAPTCHA_PROXY"] = proxy
    args = [sys.executable, "-m", "solvers.tcaptcha.pkg", "solve",
            "--flow", flow, "--arbiter", arbiter, "--max-attempts", str(max_attempts)]
    if not headless:
        args.append("--headed")
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(_HERE.parent.parent), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=max(60, timeout_s))
    except asyncio.TimeoutError:
        proc.kill()
        return {"solved": False, "error": f"tcaptcha flow exceeded {timeout_s}s",
                "method": "tcaptcha-flow", "out_dir": out_dir}

    outcome: dict | None = None
    p = Path(out_dir) / "outcome.json"
    if p.is_file():
        try:
            outcome = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            outcome = None
    log_tail = (err or b"").decode(errors="replace").strip().splitlines()[-4:]
    if outcome is None:
        shutil.rmtree(out_dir, ignore_errors=True)
        return {"solved": False, "method": "tcaptcha-flow", "rc": proc.returncode,
                "error": f"no outcome.json (rc={proc.returncode}): {' | '.join(log_tail)[:240]}"}
    ok = bool(outcome.get("ok") or outcome.get("solved"))
    res = {"solved": ok, "method": "tcaptcha-flow", "flow": flow,
           "attempts": outcome.get("attempts"), "passed": ok,
           "token": outcome.get("token") or (outcome.get("detail") or {}).get("token") or "",
           "outcome": {k: v for k, v in outcome.items() if k not in ("log",)},
           "out_dir": out_dir}
    if not ok:
        res["error"] = outcome.get("error") or outcome.get("reason") or "flow did not pass"
    return res


async def solve_tcaptcha(url: str | None = None, mode: str = "detect",
                         challenge_json: dict | None = None,
                         proxy: str | None = None, timeout_s: int = 240,
                         flow: str = "minimax-login", email: str | None = None,
                         password: str | None = None, headless: bool = True,
                         arbiter: str = "heuristic", max_attempts: int = 3) -> dict:
    """Tencent slider: offline gap consensus (`detect`) or a browser flow (`solve`)."""
    t0 = time.monotonic()
    mode = (mode or "detect").lower()
    try:
        if mode in ("detect", "gap", "offline") or (challenge_json and mode != "solve"):
            r = await asyncio.to_thread(_detect, challenge_json or {})
            r["elapsed"] = round(time.monotonic() - t0, 2)
            return r
        if mode not in ("solve", "flow", "browser"):
            return {"solved": False, "method": "tcaptcha", "elapsed": 0.0,
                    "error": f"unknown mode {mode!r} (detect|solve)"}
        r = await _run_flow(flow, email, password, proxy, headless, arbiter,
                            max_attempts, timeout_s)
        r["elapsed"] = round(time.monotonic() - t0, 1)
        return r
    except Exception as e:
        return {"solved": False, "method": "tcaptcha",
                "error": f"{type(e).__name__}: {e}"[:240],
                "elapsed": round(time.monotonic() - t0, 2)}


async def solve(*args, **kwargs) -> dict:  # pragma: no cover - dispatch alias
    return await solve_tcaptcha(*args, **kwargs)
