"""Headless Akamai sensor backend via wre-client-akamai (proofofbots, MIT).

Runs the vendor sensor script inside a V8 sandbox (wred sidecar) with a captured
real-device browser surface, POSTs the sensor payload through a TLS/HTTP2-matching
transport — no browser needed, and unlike the browser-harvest path it validates
`_abck` from datacenter IPs (the sensor itself is byte-accurate, so the server
accepts it regardless of IP reputation).

Contract matches akamai.solve.solve_akamai: `{solved, _abck, cookies, user_agent,
method, elapsed, error}`.

Optional dependency: `pip install wre-client-akamai` (wheels manylinux x86_64).
When the sidecar binary is missing, `available()` returns False and the caller
falls back to the browser harvest path.
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


def available() -> bool:
    try:
        import wre_client_akamai  # noqa: F401
        return True
    except Exception:
        return False


def _attempt(url: str, proxy: str, rounds: int, wait_ms: int) -> dict:
    import wre_client_akamai as w

    cfg = w.AkamaiConfig(
        page_url=url,
        random_profile=True,
        random_machine=True,
        proxy=proxy or None,
    )
    client = w.open_client(cfg)
    try:
        info = client.info()
        ua = (info or {}).get("user_agent")
        r = client.solve({
            "url": url,
            "rounds": max(1, int(rounds)),
            "wait_ms": int(wait_ms),
            "post": True,
        })
    finally:
        try:
            client.close()
        except Exception:
            pass

    cookies = r.get("cookies") or {}
    present = cookies.get("present") or []
    abck = cookies.get("abck") or {}
    value = abck.get("value") or ""
    # Akamai semantics: an invalidated _abck carries the `~-1~-1~-1~` sentinel
    # triple; a server-accepted one drops it. The sidecar token is always present,
    # so `validated` — not token presence — is the honest success signal.
    validated = bool(value) and "~-1~-1~-1~" not in value

    return {
        "solved": validated,
        "type": "akamai",
        # token only when validated — the generic `_is_solved` predicate treats a
        # truthy token as success, and an unvalidated cookie is not a success.
        "token": (abck.get("token") or value) if validated else "",
        "_abck": {"name": "_abck", "value": value or abck.get("token", "")},
        "cookies": None,
        "method": "wre-sensor",
        "elapsed": 0.0,
        "error": None if validated else f"_abck not validated (posts={len(r.get('posts') or [])})",
        "user_agent": ua,
        "endpoint": r.get("endpoint"),
        "posts": r.get("posts"),
        "cookie_names": present,
        "warning": ("_abck is bound to IP + JA3/TLS + User-Agent and has a short TTL. "
                    "Replay ONLY from the same IP with this exact User-Agent over a "
                    "matching TLS stack."),
    }


async def solve_akamai_wre(url: str, proxy: str = None, timeout_s: int = 90,
                           rounds: int = 3, wait_ms: int = 5000) -> dict:
    """Generate + POST a real Akamai sensor for `url` and return a validated _abck.

    Up to 3 attempts with fresh device profiles — Akamai's server-side acceptance
    varies per session even with a byte-accurate sensor.
    """
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "akamai", "token": "", "_abck": None,
            "bm_sz": None, "cookies": None, "method": "wre-sensor",
            "elapsed": round(time.monotonic() - t0, 1), "error": error,
            "user_agent": None,
            "warning": ("_abck is bound to IP + JA3/TLS + User-Agent. Replay ONLY from "
                        "the same IP with this User-Agent over a matching TLS stack."),
        }

    def _run() -> dict:
        attempts = max(1, min(4, int(timeout_s) // 30) or 1)
        last = None
        for i in range(attempts):
            try:
                last = _attempt(url, proxy, rounds, wait_ms)
            except Exception as exc:
                last = _fail(str(exc).splitlines()[0][:200])
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
        return _fail(f"wre solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("wre akamai backend failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
