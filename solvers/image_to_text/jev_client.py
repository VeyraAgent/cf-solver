"""Jev (TypeSafe System One) client for cf-solver image_to_text.

Talks to the local Jev captcha API (jev-captcha.service, :8917, FastAPI
wrapper around HybridSolver — see ~/work/jev_captcha/). Reads:
  JEV_URL   (default http://127.0.0.1:8917)
  JEV_TIMEOUT (default 30s)
Stdlib only (urllib in a thread) — no new deps in the cf-solver venv.
"""
import base64
import json
import os
import time
import urllib.request

JEV_URL = os.environ.get("JEV_URL", "http://127.0.0.1:8917").rstrip("/")
JEV_TIMEOUT = int(os.environ.get("JEV_TIMEOUT", "30"))

# Friendly task names → HybridSolver internal task ids (jev_hybrid.solve()).
TASK_MAP = {
    "grid": "grid_select_all",
    "single": "classify",
    "classify": "classify",
    "text": "text_captcha",
    "locate": "locate",
    "rotate": "rotate",
    "slider": "slider",
    "match": "image_match",
    "rank": "rank",
    "puzzle": "text_puzzle",
}


def map_task(task: str) -> str:
    return TASK_MAP.get((task or "").strip().lower(), task or "")


def _extract_token(jev: dict) -> str:
    """Map the various HybridSolver result shapes onto one text/token string."""
    if not isinstance(jev, dict):
        return ""
    for key in ("text", "answer", "value", "choice", "best_angle_deg", "offset_x"):
        if jev.get(key) not in (None, ""):
            v = jev[key]
            return v if isinstance(v, str) else json.dumps(v)
    if isinstance(jev.get("cells"), list):  # grid_select_all
        return json.dumps(jev["cells"])
    r = jev.get("result")
    if isinstance(r, dict):
        return _extract_token(r)
    return ""


def _post(payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        f"{JEV_URL}/solve",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


async def jev_solve(image_b64: str, prompt: str = "", task: str = None,
                    options: list = None, ensemble_votes: int = 5,
                    timeout_s: int = JEV_TIMEOUT) -> dict:
    """Call the local Jev /solve endpoint. Returns:
    {"ok": bool, "method": "jev", "token": str, "jev": <raw dict>, "error": str}
    """
    t0 = time.monotonic()
    payload = {
        "image": image_b64,
        "prompt": prompt or "",
        "ensemble_votes": ensemble_votes,
    }
    if task:
        payload["task"] = map_task(task)
    if options:
        payload["options"] = options

    def _call() -> dict:
        return _post(payload, timeout_s)

    import asyncio
    try:
        jev = await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout_s + 5)
        token = _extract_token(jev)
        return {
            "ok": bool(token) or bool(jev.get("solved")),
            "method": "jev",
            "token": token,
            "jev": jev,
            "elapsed": round(time.monotonic() - t0, 1),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False, "method": "jev", "token": "", "jev": None,
            "elapsed": round(time.monotonic() - t0, 1),
            "error": f"jev: {str(exc)[:160]}",
        }
