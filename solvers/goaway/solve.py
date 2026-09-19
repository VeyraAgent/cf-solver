"""GoAway PoW solver — pure-HTTP, no browser.

GoAway ("js-pow-sha256") is a single-block SHA-256 proof-of-work anti-bot
challenge. The challenge string is 64 hex chars (32 bytes); the client must
find a nonce where the digest's top `difficulty` bits are zero.

Message (exactly one 64-byte SHA-256 block, from pow-buster GoAwaySolver):
    bytes 0-31  : challenge (32 bytes)
    bytes 32-35 : high_word (4 bytes BE)   ← 0 in the common case
    bytes 36-39 : nonce / key  (4 bytes BE) ← iterated
    byte 40     : 0x80 (padding)
    bytes 41-59 : zeros
    bytes 60-63 : MSG_LEN = 320 (BE)

Check: state[0] (digest bytes 0-4) top `difficulty` bits zero
        ⟺ int(digest[:4]) < (1 << (32 - difficulty))
Full nonce = (high_word << 32) | key.

Config (caller passes from the protected page's challenge blob):
    {"challenge": "<64 hex>", "target": "...", "difficulty": N}

Reference: eternal-flame-AD/pow-buster (safe.rs GoAwaySolver), Goaway JS clients.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Optional

log = logging.getLogger("goaway")

_MSG_LEN = 10 * 4 * 8  # 320 bits


def solve_pow(challenge_hex: str, difficulty: int,
              max_nonce: int = 2 ** 32) -> tuple[int, str]:
    """Return (nonce, digest_hex). high_word fixed to 0 → nonce == key."""
    if difficulty < 1 or difficulty > 32:
        raise RuntimeError(f"goaway: difficulty {difficulty} out of range [1,32]")
    try:
        challenge = bytes.fromhex(challenge_hex)
    except ValueError as e:
        raise RuntimeError(f"goaway: challenge is not hex: {e}")
    if len(challenge) != 32:
        raise RuntimeError(f"goaway: challenge must be 32 bytes, got {len(challenge)}")
    prefix = challenge + b"\x00\x00\x00\x00"               # + high_word (0) = bytes 32-35
    limit = 1 << (32 - difficulty)                         # top `difficulty` bits zero
    nonce = 0
    while nonce < max_nonce:
        block = prefix + nonce.to_bytes(4, "big") + b"\x80" \
                + b"\x00" * 19 + _MSG_LEN.to_bytes(4, "big")
        digest = hashlib.sha256(block).digest()
        if int.from_bytes(digest[:4], "big") < limit:
            return nonce, digest.hex()
        nonce += 1
    raise RuntimeError(f"goaway: nonce space exhausted (difficulty={difficulty})")


async def solve_goaway(challenge: Optional[str] = None,
                       difficulty: Optional[int] = None,
                       challenge_json: Optional[dict] = None,
                       proxy: Optional[str] = None,
                       timeout_s: int = 60) -> dict:
    """Solve a GoAway PoW.

    Pass `challenge` (64 hex) + `difficulty`, or `challenge_json`
    ({"challenge": <64hex>, "difficulty": N}) extracted from the page.
    Returns {solved, type:"goaway", token: nonce, nonce, hash, difficulty, error}.
    """
    t0 = time.monotonic()
    result: dict[str, Any] = {"type": "goaway", "solved": False, "token": "",
                              "method": "sha256-pow", "error": None}
    if challenge_json:
        challenge = challenge or challenge_json.get("challenge")
        difficulty = difficulty if difficulty is not None else challenge_json.get("difficulty")
    if not challenge or difficulty is None:
        result["error"] = "goaway: pass challenge (64 hex) + difficulty, or challenge_json"
        return result
    try:
        nonce, digest = await asyncio.wait_for(
            asyncio.to_thread(solve_pow, challenge, int(difficulty)),
            timeout=max(timeout_s, 10))
    except RuntimeError as e:
        result["error"] = str(e)
        return result
    except asyncio.TimeoutError:
        result["error"] = f"goaway: PoW exceeded {timeout_s}s"
        return result
    result["solved"] = True
    result["token"] = str(nonce)
    result["nonce"] = nonce
    result["hash"] = digest
    result["difficulty"] = int(difficulty)
    result["elapsed"] = round(time.monotonic() - t0, 2)
    log.info("goaway: nonce=%d hash=%s… d=%s", nonce, digest[:16], difficulty)
    return result