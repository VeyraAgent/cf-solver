"""mCaptcha PoW solver — pure-HTTP, no browser.

mCaptcha (https://mcaptcha.org, mCaptcha/mCaptcha — Rust, 2.5k⭐) issues a
SHA-256 PoW (mCaptcha/pow_sha256 crate):

    config  = {string, salt, difficulty_factor}
    prefix  = sha256(salt + bincode(string))     # midstate; bincode String =
                                                 # LE u64 length + utf8 bytes
    score   = first 16 bytes of sha256(prefix_state ++ str(nonce)) as BE u128
    valid   ⇔ score >= u128::MAX - u128::MAX // difficulty_factor
    result  = str(score)                          # decimal string of the u128

API (mCaptcha is self-hosted — every deployment registers its own sitekeys):
    POST {base}/api/v1/pow/config   {"key": <sitekey>}
                                  → {string, salt, difficulty_factor}
    POST {base}/api/v1/pow/verify   {"key", "nonce", "result", "string", "salt"}
                                  → {"verified": true, "token": ...}

A direct `challenge_json` mode ({string, salt, difficulty_factor} + optional
`verify_url`) is supported for callers that already hold the config (extracted
from their page).

Algorithm reference: mCaptcha/pow_sha256 (dev::score, get_difficulty),
pow-buster tests (exact port), mCaptcha/mCaptcha api routes.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Optional

log = logging.getLogger("mcaptcha")

U128_MAX = (1 << 128) - 1


def _bincode_string(s: str) -> bytes:
    """bincode (default, fixint LE) serialization of a String: u64 len + bytes."""
    b = s.encode()
    return len(b).to_bytes(8, "little") + b


def _get_difficulty(difficulty_factor: int) -> int:
    """pow_sha256 get_difficulty: u128::MAX - u128::MAX / factor."""
    return U128_MAX - U128_MAX // difficulty_factor


def _score(prefix_sha: "hashlib._Hash", nonce: int) -> int:
    h = prefix_sha.copy()
    h.update(str(nonce).encode())          # used to be to_be_bytes; now decimal string
    return int.from_bytes(h.digest()[:16], "big")


def solve_pow(string: str, salt: str, difficulty_factor: int,
              max_nonce: int = 100_000_000) -> tuple[int, int]:
    """Return (nonce, score). Message chain: sha256(salt + bincode(string)) ++ str(nonce)."""
    prefix_sha = hashlib.sha256(salt.encode() + _bincode_string(string))
    difficulty = _get_difficulty(difficulty_factor)
    nonce = 0
    while nonce < max_nonce:
        score = _score(prefix_sha, nonce)
        if score >= difficulty:
            return nonce, score
        nonce += 1
    raise RuntimeError(f"mcaptcha: nonce space exhausted (difficulty_factor={difficulty_factor})")


async def solve_mcaptcha(challenge_json: Optional[dict] = None,
                         base_url: Optional[str] = None,
                         site_key: Optional[str] = None,
                         verify_url: Optional[str] = None,
                         proxy: Optional[str] = None,
                         timeout_s: int = 90) -> dict:
    """Solve an mCaptcha PoW.

    Either `challenge_json` ({string, salt, difficulty_factor}) + optional
    `verify_url`, or `base_url` + `site_key` to fetch the config from the
    mCaptcha service automatically (POST {base}/api/v1/pow/config).
    """
    t0 = time.monotonic()
    result: dict[str, Any] = {"type": "mcaptcha", "solved": False, "token": "",
                              "method": "sha256-pow", "error": None}

    import httpx

    async with httpx.AsyncClient(timeout=25, proxy=proxy,
                                 headers={"User-Agent": "Mozilla/5.0"}) as cli:
        # ── 1. config ────────────────────────────────────────────────
        if not challenge_json:
            if not (base_url and site_key):
                result["error"] = ("mcaptcha: pass challenge_json, or base_url + site_key "
                                   "(mCaptcha is self-hosted — sitekeys are per-deployment)")
                return result
            base = base_url.rstrip("/")
            try:
                resp = await cli.post(f"{base}/api/v1/pow/config", json={"key": site_key})
                if resp.status_code != 200:
                    result["error"] = f"config fetch HTTP {resp.status_code}: {resp.text[:140]}"
                    return result
                challenge_json = resp.json()
            except Exception as e:
                result["error"] = f"config fetch failed: {type(e).__name__}: {str(e)[:120]}"
                return result

        string = challenge_json.get("string", "")
        salt = challenge_json.get("salt", "")
        df = challenge_json.get("difficulty_factor")
        if not string or not salt or not df:
            result["error"] = f"unexpected config format: {str(challenge_json)[:160]}"
            return result
        df = int(df)
        log.info("mcaptcha: string=%s… salt=%s… df=%d", string[:12], salt[:8], df)

        # ── 2. solve sha256 PoW ──────────────────────────────────────
        try:
            nonce, score = await asyncio.wait_for(
                asyncio.to_thread(solve_pow, string, salt, df),
                timeout=max(timeout_s - 5, 10))
        except RuntimeError as e:
            result["error"] = str(e)
            return result
        except asyncio.TimeoutError:
            result["error"] = f"mcaptcha: PoW exceeded {timeout_s}s (df={df})"
            return result
        result_str = str(score)
        log.info("mcaptcha: nonce=%d score=%s…", nonce, result_str[:16])

        result["nonce"] = nonce
        result["result"] = result_str
        result["config"] = {"string": string, "salt": salt, "difficulty_factor": df}

        # ── 3. verify ────────────────────────────────────────────────
        verify = verify_url
        if not verify and base_url and site_key:
            verify = f"{base_url.rstrip('/')}/api/v1/pow/verify"
        if verify:
            try:
                resp = await cli.post(verify, json={
                    "key": site_key, "nonce": nonce, "result": result_str,
                    "string": string, "salt": salt})
                body = resp.json() if resp.status_code == 200 else {}
                if body.get("verified"):
                    result["solved"] = True
                    result["token"] = body.get("token") or body
                    result["method"] = "sha256-pow+verify"
                else:
                    result["error"] = f"verify HTTP {resp.status_code}: {resp.text[:140]}"
                    result["token"] = result_str
            except Exception as e:
                result["error"] = f"verify failed: {type(e).__name__}: {str(e)[:120]}"
                result["token"] = result_str
        else:
            result["solved"] = True
            result["token"] = result_str
            result["method"] = "sha256-pow"

        result["elapsed"] = round(time.monotonic() - t0, 1)
        return result
