"""Cap.js PoW solver — pure-HTTP, no browser.

Cap (https://trycap.dev) is the popular self-hosted open-source CAPTCHA
alternative. Its challenge is a SHA-256 PoW: the server issues
{challenge:{c,s,d}, token}; the client derives, for i in 1..c:
    salt   = prng(f"{token}{i}", s)
    target = prng(f"{token}{i}d", d)
    nonce  = smallest n where sha256(salt + str(n)).hex().startswith(target)
and redeems {token, solutions[]} at the site's redeem endpoint.

Algorithm ported 1:1 from cap's core (tiagozip/cap, core/src/prng.js —
FNV-1a + xorshift PRNG with exact JS number semantics) — verified against the
upstream test vectors. Reference solvers: cap benchmark.js, pow-buster
capjs adapter (POST {base}/{sitekey}/challenge → /redeem).

Endpoints (caller supplies the Cap backend base + site key — Cap has no
global vendor URL; every deployment is self-hosted):
    POST {base}/{sitekey}/challenge   body {}     → challenge descriptor
    POST {base}/{sitekey}/redeem      solutions   → {success, token}
A direct challenge_json + redeem_url mode is supported for callers that
already hold the descriptor (e.g. extracted from the page).

PRNG wire-format note: JS numbers are doubles — the FNV-1a fold must be
executed with int32 wraparound per step (ToUint32 at the end only), NOT with
a straight 32-bit mask on every op, or derived salts drift off the wire
format and every solution fails validation.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Optional

log = logging.getLogger("cap")

MAX_NONCE = 5_000_000  # upstream solver give-up guard (cap benchmark.js)


def _to_int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def _js_shift(x: int, shift: int) -> int:
    """JS `x << n` — ToInt32(x) << n, wrapped to int32."""
    return _to_int32(_to_int32(x) << shift)


def fnv1a(s: str) -> int:
    """FNV-1a with exact JS number semantics (cap core/src/prng.js)."""
    h = 2166136261
    for ch in s:
        h = _to_int32(h ^ ord(ch))
        h = h + (_js_shift(h, 1) + _js_shift(h, 4) + _js_shift(h, 7)
                 + _js_shift(h, 8) + _js_shift(h, 24))
    return h & 0xFFFFFFFF  # >>> 0


def _prng_from_hash(initial: int, length: int) -> str:
    """xorshift32 hex stream (cap core/src/prng.js prngFromHash)."""
    state = initial & 0xFFFFFFFF
    out = []
    total = 0
    while total < length:
        state ^= _js_shift(state, 13) & 0xFFFFFFFF
        state ^= (state & 0xFFFFFFFF) >> 17      # >>> unsigned shift
        state ^= _js_shift(state, 5) & 0xFFFFFFFF
        state &= 0xFFFFFFFF                       # >>>= 0
        out.append(format(state, "08x"))
        total += 8
    return "".join(out)[:length]


def prng(seed: str, length: int) -> str:
    return _prng_from_hash(fnv1a(seed), length)


def solve_pow(token: str, c: int, s: int, d: int,
              max_nonce: int = MAX_NONCE) -> list[int]:
    """Derive the solutions array for a Cap challenge descriptor."""
    solutions: list[int] = []
    for i in range(1, c + 1):
        salt = prng(f"{token}{i}", s)
        target = prng(f"{token}{i}d", d)
        n = 0
        while not hashlib.sha256((salt + str(n)).encode()).hexdigest().startswith(target):
            n += 1
            if n > max_nonce:
                raise RuntimeError(f"solver gave up at challenge {i}/{c} (target={target})")
        solutions.append(n)
    return solutions


async def solve_cap(challenge_json: Optional[dict] = None,
                    base_url: Optional[str] = None,
                    site_key: Optional[str] = None,
                    redeem_url: Optional[str] = None,
                    proxy: Optional[str] = None,
                    timeout_s: int = 90) -> dict:
    """Solve a Cap.js PoW challenge.

    Either pass `challenge_json` (the {challenge:{c,s,d}, token} descriptor)
    + optional `redeem_url`, or `base_url` + `site_key` to fetch from the
    site's Cap backend automatically.
    """
    t0 = time.monotonic()
    result: dict[str, Any] = {"type": "cap", "solved": False, "token": "",
                              "method": "sha256-pow", "error": None}

    import httpx

    async with httpx.AsyncClient(timeout=20, proxy=proxy,
                                 headers={"User-Agent": "Mozilla/5.0",
                                          "Content-Type": "application/json"}) as cli:
        # ── 1. challenge descriptor ───────────────────────────────────
        if not challenge_json:
            if not (base_url and site_key):
                result["error"] = ("cap: pass challenge_json, or base_url + site_key "
                                   "(self-hosted Cap backend has no global endpoint)")
                return result
            base = base_url.rstrip("/")
            try:
                resp = await cli.post(f"{base}/{site_key}/challenge", content="{}")
                if resp.status_code != 200:
                    result["error"] = f"challenge fetch HTTP {resp.status_code}: {resp.text[:120]}"
                    return result
                challenge_json = resp.json()
            except Exception as e:
                result["error"] = f"challenge fetch failed: {type(e).__name__}: {str(e)[:120]}"
                return result

        challenge = challenge_json.get("challenge") or {}
        token = challenge_json.get("token", "")
        c, s, d = challenge.get("c"), challenge.get("s"), challenge.get("d")
        if not token or not (isinstance(c, int) and isinstance(s, int) and isinstance(d, int)):
            result["error"] = f"unexpected challenge format: {json.dumps(challenge_json)[:160]}"
            return result
        log.info("cap: c=%d s=%d d=%d token=%s...", c, s, d, token[:16])

        # ── 2. solve PoW (CPU-bound → thread) ─────────────────────────
        try:
            solutions = await asyncio.wait_for(
                asyncio.to_thread(solve_pow, token, c, s, d),
                timeout=max(timeout_s - 5, 10))
        except RuntimeError as e:
            result["error"] = f"cap: {e}"
            return result
        except asyncio.TimeoutError:
            result["error"] = f"cap: PoW exceeded {timeout_s}s (c={c}, d={d})"
            return result
        log.info("cap: solved %d challenges → %s", len(solutions), solutions[:5])

        result["solutions"] = solutions
        result["challenge"] = {"c": c, "s": s, "d": d}

        # ── 3. redeem ─────────────────────────────────────────────────
        redeem = redeem_url
        if not redeem and base_url and site_key:
            redeem = f"{base_url.rstrip('/')}/{site_key}/redeem"
        if redeem:
            try:
                resp = await cli.post(redeem, json={"token": token, "solutions": solutions})
                body = resp.json() if resp.status_code == 200 else {}
                if resp.status_code == 200 and (body.get("success") or body.get("token")):
                    result["solved"] = True
                    result["token"] = body.get("token") or body
                    result["method"] = "sha256-pow+redeem"
                else:
                    result["error"] = f"redeem HTTP {resp.status_code}: {resp.text[:120]}"
                    result["token"] = solutions  # caller may redeem itself
            except Exception as e:
                result["error"] = f"redeem failed: {type(e).__name__}: {str(e)[:120]}"
                result["token"] = solutions
        else:
            # no redeem endpoint — hand back the solutions, solved by definition
            result["solved"] = True
            result["token"] = solutions
            result["method"] = "sha256-pow"

        result["elapsed"] = round(time.monotonic() - t0, 1)
        return result


# back-compat alias used by the dispatch table
async def solve(*args, **kwargs) -> dict:  # pragma: no cover
    return await solve_cap(*args, **kwargs)
