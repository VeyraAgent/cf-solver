"""Headless Kasada interrogation backend via wre-client-kasada (proofofbots, MIT).

Same runtime family as akamai/wre_backend.py: runs the vendor's ips.js inside a
V8 sandbox (wred sidecar) with a captured real-device browser surface, answers
the PoW, and produces the x-kpsdk-* headers + KP_UIDz cookie that Kasada's edge
expects — no browser needed.

Target must be Kasada-fronted (the page loads ips.js); non-Kasada targets fail
fast with a clear error from the sidecar itself.

Contract: `{solved, token, headers, cookies, method, elapsed, error}` where
`token` = the x-kpsdk-ct value.
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


def available() -> bool:
    try:
        import wre_client_kasada  # noqa: F401
        return True
    except Exception:
        return False


def _attempt(url: str, proxy: str, wait_ms: int) -> dict:
    import wre_client_kasada as k

    cfg = k.KasadaConfig(
        page_url=url,
        proxy=proxy or None,
    )
    client = k.open_client(cfg)
    try:
        info = client.info()
        ua = (info or {}).get("user_agent")
        r = client.solve({
            "url": url,
            "wait_ms": int(wait_ms),
        })
    finally:
        try:
            client.close()
        except Exception:
            pass

    if not isinstance(r, dict):
        return {"solved": False, "error": f"unexpected solve result: {str(r)[:120]}"}

    # the solve result carries cookies + the request it made; pull x-kpsdk-ct
    cookies = r.get("cookies") or {}
    cookie_jar = cookies.get("jar") if isinstance(cookies.get("jar"), dict) else {}
    kp_uidz = None
    for name, rec in (cookie_jar.items() if isinstance(cookie_jar, dict) else []):
        if name == "KP_UIDz" and isinstance(rec, dict):
            kp_uidz = rec.get("value")

    headers_out = {}
    # x-kpsdk headers surface via the request op; solve returns the sealed values
    for hk, hv in (r.get("headers") or {}).items() if isinstance(r.get("headers"), dict) else []:
        headers_out[hk] = hv

    ct = (r.get("x_kpsdk_ct") or r.get("ct") or headers_out.get("x-kpsdk-ct") or "")
    solved = bool(ct or kp_uidz)

    return {
        "solved": solved,
        "type": "kasada",
        "token": ct,
        "x_kpsdk_ct": ct,
        "headers": headers_out or None,
        "cookies": [{"name": "KP_UIDz", "value": kp_uidz}] if kp_uidz else None,
        "method": "wre-kasada",
        "elapsed": 0.0,
        "error": None if solved else "no x-kpsdk-ct in interrogation result",
        "user_agent": ua,
        "posts": r.get("posts"),
        "warning": ("Kasada tokens are short-lived and tied to the interrogation "
                    "session. Replay immediately with this exact User-Agent from the "
                    "same IP."),
    }


async def solve_kasada(url: str, proxy: str = None, timeout_s: int = 90,
                       wait_ms: int = 6000) -> dict:
    """Run a Kasada interrogation for `url` and return x-kpsdk headers/cookies."""
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "kasada", "token": "", "x_kpsdk_ct": "",
            "headers": None, "cookies": None, "method": "wre-kasada",
            "elapsed": round(time.monotonic() - t0, 1), "error": error,
            "user_agent": None,
            "warning": "Replay immediately with this exact User-Agent from the same IP.",
        }

    def _run() -> dict:
        attempts = max(1, min(3, int(timeout_s) // 30) or 1)
        last = None
        for i in range(attempts):
            try:
                last = _attempt(url, proxy, wait_ms)
            except Exception as exc:
                msg = str(exc)
                # fail fast on non-Kasada targets — retrying won't help
                if "no interrogation" in msg or "no ips.js" in msg:
                    return _fail(f"target is not Kasada-fronted: {msg[:150]}")
                last = _fail(msg.splitlines()[0][:200])
            if last.get("solved"):
                last["attempts"] = i + 1
                last["elapsed"] = round(time.monotonic() - t0, 1)
                return last
        last["attempts"] = attempts
        last["elapsed"] = round(time.monotonic() - t0, 1)
        return last

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _fail(f"kasada solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("kasada backend failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
