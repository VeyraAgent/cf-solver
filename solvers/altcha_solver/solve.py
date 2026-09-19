"""ALTCHA (altcha.org) solver — Proof-of-Work captcha, zero browser.

Uses the official altcha-lib-py (MIT) for the PoW (v1 SHA-256 and v2
PBKDF2 / Argon2id / Scrypt / SHA-384/512 challenges). The caller supplies the
target site's challenge endpoint `url` (the endpoint the ALTCHA widget fetches
its challenge JSON from) — we fetch, solve, and return the base64 payload the
form submits in the `altcha` field.

Self-test: `solve` works against a locally generated challenge too, which makes
the pipeline provable without a live ALTCHA site.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

import requests

log = logging.getLogger(__name__)


def available() -> bool:
    try:
        import altcha  # noqa: F401
        return True
    except Exception:
        return False


def _fetch_challenge(url: str, timeout: float):
    r = requests.get(url, timeout=timeout, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Accept": "application/json",
    })
    r.raise_for_status()
    body = r.text.strip()
    try:
        return json.loads(body)
    except Exception:
        raise ValueError(f"challenge endpoint returned non-JSON: {body[:120]}")


def _solve_payload(challenge_data: dict, timeout_s: float) -> str:
    """Solve a v1 or v2 challenge dict via altcha-lib and return the base64 payload."""
    import altcha
    from altcha.v2 import Challenge, ChallengeParameters

    if "parameters" in challenge_data:  # v2
        chal = Challenge(ChallengeParameters(**challenge_data["parameters"]),
                         challenge_data.get("signature"))
        sol = altcha.solve_challenge(chal, timeout=timeout_s)
        if sol is None:
            raise RuntimeError("PoW not solved within timeout (v2)")
        payload_obj = dict(challenge_data["parameters"])
        payload_obj["number"] = sol.number
        payload_obj["signature"] = challenge_data.get("signature")
        raw = json.dumps(payload_obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    # v1: {algorithm, challenge, salt, signature?, maxnumber}
    algorithm = challenge_data.get("algorithm", "SHA-256")
    salt = challenge_data.get("salt", "")
    challenge_hex = challenge_data.get("challenge", "")
    max_number = int(challenge_data.get("maxnumber", 1000000))
    sol = altcha.solve_challenge_v1(challenge_hex, salt=salt, algorithm=algorithm,
                                    max_number=max_number)
    if sol is None:
        raise RuntimeError("PoW not solved within timeout (v1)")
    payload_obj = {
        "algorithm": algorithm,
        "challenge": challenge_hex,
        "number": sol.number,
        "salt": salt,
    }
    if challenge_data.get("signature"):
        payload_obj["signature"] = challenge_data["signature"]
    raw = json.dumps(payload_obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def solve_altcha(url: str = None, timeout_s: int = 90,
                       challenge: dict = None) -> dict:
    """Fetch (or take) an ALTCHA challenge, solve the PoW, return the payload."""
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "altcha", "token": "", "payload": "",
            "method": "pow", "elapsed": round(time.monotonic() - t0, 1),
            "error": error,
        }

    def _sync() -> dict:
        data = challenge or _fetch_challenge(url, timeout=15)
        payload = _solve_payload(data, timeout_s)
        return {
            "solved": True, "type": "altcha", "token": payload, "payload": payload,
            "method": "pow", "elapsed": round(time.monotonic() - t0, 1),
            "error": None,
            "warning": ("Submit the payload in the form's `altcha` field. The "
                        "signature is server-verified - do not tamper with the "
                        "challenge fields."),
        }

    if not url and not challenge:
        return _fail("url (challenge endpoint) or challenge JSON is required")

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _fail(f"altcha solve timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("altcha failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
