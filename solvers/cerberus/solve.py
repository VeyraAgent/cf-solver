"""Cerberus PoW solver (sjtug/cerberus) — pure compute, no browser.

Cerberus is the Caddy anti-bot module from SJTUG (blake3 proof-of-work, same
family as TecharoHQ/anubis). A protected page embeds:

    <script id="challenge-script"
            x-challenge='{"challenge":"<64hex>","difficulty":N,
                          "nonce":<u32>,"ts":<u64>,"signature":"<ed25519>"}'
            x-meta='{"baseURL":"https://site/cerberus-endpoint"}'>

The browser solves a blake3 PoW and POSTs the answer to `{baseURL}/answer`
with form fields: response=<hash hex>, solution=<u64 nonce>, nonce, ts,
signature, redir. On success the server sets the `cerberus-auth` cookie.

Protocol (verified against sjtug/cerberus web/js + directives/common.go and
eternal-flame-AD/pow-buster validators — not guessed):

  merged = "{challenge}|{nonce}|{ts}|{signature}|"     (exact trailing pipe)
  salt_hex = blake3(merged).hexdigest()                 (64 ASCII chars)

  BINARY format (cerberus >= 0.4.6, the current protocol):
      message = salt_hex (64 ASCII bytes) || u64LE(rotate32(solution))
              = salt_hex || u32LE(bank) || u32LE(inner)
      solution = (bank << 32) | inner
      hash     = blake3(message)                       (72 bytes, one chunk)
  Server side (blake3Prf): rotate32(solution) == ((s<<32)|(s>>32)) & 2^64-1,
  serialized u64 LE — byte-identical to u32LE(bank)||u32LE(inner).

  DECIMAL format (cerberus < 0.4.6, auto-selected from `version`):
      message = merged (raw ASCII) || str(solution)
      solution = addend + inner   (bank decomposition per pow-buster
                                   CerberusDecimalMessage: head digit
                                   bank%9+1, then bank//9 digits, then the
                                   9-digit zero-padded inner nonce)

  Acceptance (both): top `difficulty*2` bits of the digest zero. Cerberus
  compares the first word as if big-endian while blake3 emits little-endian
  words, so the mask is byteswap(!(!0u32 >> (d*2))) and the check is
  (u32LE(hash[0:4]) & mask) == 0 — exactly compute_mask_cerberus from
  cerberus pow/src/lib.rs / pow-buster lib.rs. difficulty 16 ⇒ all 32 bits.

Performance: ~1.5-2M hashes/s per core with the blake3 wheel. Low
difficulties (<= 10, expected work 2^(2d) <= ~4M) run single-core in-process;
heavier challenges fan out one process per bank — the same thread_id/threads
split the official browser worker uses — and stop all workers on first hit.

Usage: pass the x-challenge fields directly (challenge/difficulty/nonce/ts/
signature) or everything as challenge_json ({...same fields..., "version"?,
"mode"?}). `solve_pow()` and `verify()` expose the raw search + reference
re-check for tooling; submit the returned `solution`/`hash` to
`{baseURL}/answer` yourself (or harvest the cookie from the redirect).

References:
  - sjtug/cerberus (Caddy module; web/js/pow.js.worker.js, web/js/blake3.js,
    directives/common.go blake3Prf, directives/endpoint.go checkAnswer)
  - eternal-flame-AD/pow-buster (adapter/cerberus.rs, message.rs
    CerberusDecimalMessage, solver/safe.rs, lib.rs compute_mask_cerberus)
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
from typing import Any, Optional

try:
    import blake3
except ImportError:  # optional dep — solver degrades with a clear error, not a 500
    blake3 = None

log = logging.getLogger("cerberus")

_U32 = 0xFFFFFFFF
_U64 = 0xFFFFFFFFFFFFFFFF
# cerberus >= 0.4.6 switched from decimal to binary nonce encoding
_BINARY_SINCE = (0, 4, 6)
_INNER_LIMIT = 1 << 32          # inner nonce space per bank
# expected work 2^(2d) above this → process pool (else in-thread loop)
_POOL_THRESHOLD = 1 << 22
_MAX_WORKERS = 8
_STOP_CHECK_MASK = 8191         # stop/deadline probe granularity (hashes)
_STOP_EVENT = None              # fork-inherited; set by the parent on first hit

def available() -> bool:
    """blake3 wheel present → solver usable (honest: False when the wheel is absent)."""
    return blake3 is not None


def compute_mask_cerberus(difficulty: int) -> int:
    """compute_mask_cerberus from cerberus pow/src/lib.rs (u32, byte-swapped).

    mask = (!(!0u32 >> (d*2))).swap_bytes(); d == 16 → 0xFFFFFFFF.
    Condition checked: (u32LE(hash[0:4]) & mask) == 0.
    """
    d = int(difficulty)
    if d < 1 or d > 16:
        raise ValueError(f"cerberus: difficulty {d} out of range [1,16]")
    if d == 16:
        return _U32
    top = ((0xFFFFFFFF >> (d * 2)) ^ 0xFFFFFFFF) & 0xFFFF_FFFF
    return int.from_bytes(top.to_bytes(4, "big"), "little")  # u32::swap_bytes


def _mask_check_fn(difficulty: int):
    """Prefix-bytes equivalent of the mask check: leading zero bits >= d*2."""
    zb = (difficulty * 2) // 8          # fully zero bytes
    rb = (difficulty * 2) % 8           # remaining bits inside the next byte
    zeros = b"\x00" * zb
    if rb:
        hi = 1 << (8 - rb)

        def check(h: bytes) -> bool:
            return h[:zb] == zeros and h[zb] < hi
    else:

        def check(h: bytes) -> bool:
            return h[:zb] == zeros
    return check


def _merged_challenge(challenge: str, nonce: int, ts: int, signature: str) -> str:
    # Go server: fmt.Sprintf("%s|%d|%d|%s|", challenge, nonce, ts, signature)
    return f"{challenge}|{int(nonce)}|{int(ts)}|{signature}|"


def _parse_version(v: str) -> Optional[tuple[int, ...]]:
    """'v0.4.5' / '0.4.5' → (0,4,5); None if unparseable (unknown ⇒ binary)."""
    try:
        return tuple(int(p) for p in str(v).strip().lstrip("vV").split(".")[:3])
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------- solver core
def _search_binary_bank(salt_hex: bytes, check, bank: int, step: int,
                        deadline: float) -> Optional[tuple[int, bytes]]:
    """Scan banks bank, bank+step, … in binary format. → (solution, hash)."""
    b3 = blake3.blake3
    to_le = int.to_bytes
    while bank <= _U32:
        prefix = salt_hex + bank.to_bytes(4, "little")
        # bank u32 word then inner u32 word == u64LE(rotate32(solution))
        for inner in range(_INNER_LIMIT):
            if not inner & _STOP_CHECK_MASK:
                if _STOP_EVENT is not None and _STOP_EVENT.is_set():
                    return None
                if time.monotonic() >= deadline:
                    return None
            msg = prefix + to_le(inner, 4, "little")
            if check(b3(msg).digest(4)):
                h = b3(msg).digest()
                log.info("hit bank=%d inner=%d hash=%s…",
                         bank, inner, h.hex()[:16])
                return (bank << 32) | inner, h
        bank += step
    return None


def _decimal_addend(bank: int, salt_len: int) -> Optional[int]:
    """pow-buster CerberusDecimalMessage bank → leading digits × 1e9.

    Normal case (salt_len % 64 < 55): digits = str(bank%9+1) + str(bank//9).
    Mid-block case (>= 55): head bank%8+1, then bank//8 zero-padded to
    63 - salt_len%64 digits, then a second head group ("1").
    pow-buster writes the bank//9 digits LSB-first; we use plain decimal
    order — both are valid: the searched message is always
    prefix + str(addend + inner), so only the scan order differs.
    """
    rem = salt_len % 64
    if rem < 55:
        digits = f"{bank % 9 + 1}{bank // 9 or ''}"
        if rem + len(digits) >= 56:     # 9-digit inner field must fit (lib.rs)
            return None
        return int(digits) * 1_000_000_000
    pad = 64 - rem - 1
    rest1 = bank // 8
    if rest1 >= 10 ** pad:
        return None
    digits = f"{bank % 8 + 1}{str(rest1).zfill(pad)}1"
    return int(digits) * 1_000_000_000

def _search_decimal_bank(prefix: bytes, check, bank: int, step: int,
                         deadline: float) -> Optional[tuple[int, bytes]]:
    """Scan banks bank, bank+step, … in legacy decimal format."""
    b3 = blake3.blake3
    while bank <= _U32:
        addend = _decimal_addend(bank, len(prefix))
        if addend is None:
            return None
        base = prefix + str(addend // 1_000_000_000).encode()  # digit string
        for inner in range(1_000_000_000):
            if not inner & _STOP_CHECK_MASK:
                if _STOP_EVENT is not None and _STOP_EVENT.is_set():
                    return None
                if time.monotonic() >= deadline:
                    return None
            msg = base + b"%09d" % inner
            if check(b3(msg).digest(4)):
                h = b3(msg).digest()
                log.info("decimal hit bank=%d nonce=%d hash=%s…",
                         bank, addend + inner, h.hex()[:16])
                return addend + inner, h
        bank += step
    return None


def _search_worker(args: tuple) -> Optional[tuple[int, bytes]]:
    """Process-pool entry: scan one bank chain until hit/stop/deadline."""
    fmt, salt_or_prefix, difficulty, bank, step, deadline = args
    check = _mask_check_fn(difficulty)
    if fmt == "binary":
        return _search_binary_bank(salt_or_prefix, check, bank, step,
                                   deadline)
    return _search_decimal_bank(salt_or_prefix, check, bank, step,
                                deadline)


def solve_pow(challenge: str, difficulty: int, nonce: int, ts: int,
              signature: str, timeout_s: float = 90.0,
              version: Optional[str] = None,
              mode: Optional[str] = None) -> tuple[str, str, str]:
    """Search for the winning nonce. → (format, solution, hash_hex).

    format: "binary" (cerberus >= 0.4.6) or "decimal" (legacy) — auto from
    `version` when given, overridable with mode="binary"/"decimal".
    Raises ValueError on bad input, RuntimeError when the budget is exhausted.
    """
    d = int(difficulty)
    compute_mask_cerberus(d)                 # validates range
    check = _mask_check_fn(d)
    merged = _merged_challenge(challenge, nonce, ts, signature)
    if mode in ("binary", "decimal"):
        fmt = mode
    elif version and (parsed := _parse_version(version)) is not None:
        fmt = "decimal" if parsed < _BINARY_SINCE else "binary"
    else:
        fmt = "binary"

    expected = 1 << (d * 2)
    workers = min(os.cpu_count() or 1, _MAX_WORKERS)
    use_pool = expected > _POOL_THRESHOLD and workers > 1
    log.info("start fmt=%s difficulty=%d (~%s hashes, %s)",
             fmt, d, f"{expected:,}",
             f"pool x{workers}" if use_pool else "single process")

    if fmt == "binary":
        salt_arg: bytes = blake3.blake3(merged.encode()).hexdigest().encode()
    else:
        salt_arg = merged.encode()

    deadline = time.monotonic() + max(timeout_s, 1.0)
    if not use_pool:
        search = _search_binary_bank if fmt == "binary" else _search_decimal_bank
        hit = search(salt_arg, check, 0, 1, deadline)
    else:
        hit = _solve_pooled(fmt, salt_arg, d, workers, deadline, timeout_s)
    if hit is None:
        raise RuntimeError(
            f"cerberus: no solution within {timeout_s}s "
            f"(difficulty={d}, ~{expected:,} hashes expected)")
    return fmt, str(hit[0]), hit[1].hex()


def _solve_pooled(fmt: str, salt_arg: bytes, d: int, workers: int,
                  deadline: float, timeout_s: float) -> Optional[tuple[int, bytes]]:
    """Fan one fork process per bank; first hit stops the others.

    The stop Event reaches the workers via fork inheritance (module global
    assigned before the pool forks), not through the call queue — Python 3.11+
    forbids pickling synchronize primitives between unrelated queue peers.
    """
    global _STOP_EVENT
    import concurrent.futures as cf
    import multiprocessing as mp

    ctx = mp.get_context("fork")
    _STOP_EVENT = ctx.Event()
    pool = cf.ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
    futs = [pool.submit(_search_worker,
                        (fmt, salt_arg, d, bank, workers, deadline))
            for bank in range(workers)]
    hit: Optional[tuple[int, bytes]] = None
    try:
        for fut in cf.as_completed(futs, timeout=max(timeout_s, 1.0) + 5):
            got = fut.result()
            if got is not None:
                hit = got
                break
    except cf.TimeoutError:
        pass
    finally:
        _STOP_EVENT.set()                # tell survivors to bail out
        pool.shutdown(wait=False, cancel_futures=True)
    return hit


def verify(challenge: str, difficulty: int, nonce: int, ts: int,
           signature: str, solution: int, hash_hex: str,
           mode: str = "binary") -> dict:
    """Re-verify a solution against the reference formulas (all must be True).

      mask_ok       — (u32LE(hash[:4]) & compute_mask_cerberus(d)) == 0
      hash_ok       — hash_hex == blake3(<reconstructed message>)
      answer_ok     — hash == server-side blake3Prf(saltStr, solution)
                      (binary) / blake3(merged + str(solution)) (decimal)
      difficulty_ok — endpoint.go checkAnswer: d//2 zero hex nibbles,
                      odd difficulty → next nibble < '8'
    """
    d = int(difficulty)
    mask = compute_mask_cerberus(d)
    merged = _merged_challenge(challenge, nonce, ts, signature)
    h = bytes.fromhex(hash_hex)
    mask_ok = (int.from_bytes(h[:4], "little") & mask) == 0
    if mode == "decimal":
        msg = merged.encode() + str(int(solution)).encode()
        expected = blake3.blake3(msg).digest()
        answer = expected
    else:
        salt_hex = blake3.blake3(merged.encode()).hexdigest().encode()
        rotated = ((int(solution) << 32) | (int(solution) >> 32)) & _U64
        msg = salt_hex + struct.pack("<Q", rotated)
        # server reconstruction: blake3Prf(blake3sum(merged), solution)
        expected = blake3.blake3(msg).digest()
        answer = blake3.blake3(salt_hex + struct.pack("<Q", rotated)).digest()
    hx = hash_hex.lower()
    nib = d // 2
    return {
        "mask_ok": mask_ok,
        "hash_ok": h == expected,
        "answer_ok": h == answer,
        "difficulty_ok": hx[:nib] == "0" * nib and (
            d % 2 == 0 or hx[nib] < "8"),
    }


# ----------------------------------------------------------------- entrypoint
def _fields(challenge, difficulty, nonce, ts, signature,
            challenge_json: Optional[dict]) -> tuple:
    if challenge_json:
        challenge = challenge or challenge_json.get("challenge")
        difficulty = difficulty if difficulty is not None else challenge_json.get("difficulty")
        nonce = nonce if nonce is not None else challenge_json.get("nonce")
        ts = ts if ts is not None else challenge_json.get("ts")
        signature = signature or challenge_json.get("signature")
    return challenge, difficulty, nonce, ts, signature


async def solve_cerberus(challenge: Optional[str] = None,
                         difficulty: Optional[int] = None,
                         nonce: Optional[int] = None,
                         ts: Optional[int] = None,
                         signature: Optional[str] = None,
                         challenge_json: Optional[dict] = None,
                         timeout_s: int = 90) -> dict:
    """Solve a Cerberus blake3 PoW. Uniform result dict; never raises."""
    t0 = time.monotonic()
    result: dict[str, Any] = {"type": "cerberus", "solved": False, "token": "",
                              "method": "blake3-pow", "error": None}

    def _fail(error: str) -> dict:
        result["elapsed"] = round(time.monotonic() - t0, 2)
        result["error"] = error
        log.warning("%s", error)
        return result

    if blake3 is None:
        return _fail("cerberus: 'blake3' package not installed "
                     "(pip install blake3)")

    cj = challenge_json or {}
    c, d, n, t, s = _fields(challenge, difficulty, nonce, ts, signature, cj)
    if not c or d is None or n is None or t is None or not s:
        return _fail("cerberus: pass challenge/difficulty/nonce/ts/signature "
                     "or challenge_json with those fields")
    try:
        fmt, solution, digest = await asyncio.wait_for(
            asyncio.to_thread(solve_pow, str(c), int(d), int(n), int(t),
                              str(s), timeout_s, cj.get("version"),
                              cj.get("mode")),
            timeout=max(timeout_s, 10))
    except ValueError as e:
        return _fail(str(e))
    except RuntimeError as e:
        return _fail(str(e))
    except asyncio.TimeoutError:
        return _fail(f"cerberus: PoW exceeded {timeout_s}s")
    except Exception as exc:  # noqa: BLE001 — contract: never raise
        return _fail(f"cerberus: {type(exc).__name__}: {exc}"[:200])

    result.update({
        "solved": True,
        "token": solution,               # form field `solution` (decimal str)
        "solution": int(solution),
        "hash": digest,                  # form field `response`
        "format": fmt,
        "method": "blake3-pow" if fmt == "binary" else "blake3-pow-decimal",
        "difficulty": int(d),
        "elapsed": round(time.monotonic() - t0, 2),
        "warning": ("POST to {baseURL}/answer: response=<hash>, solution=<u64>, "
                    "nonce=<challenge nonce>, ts, signature, redir — the "
                    "cerberus-auth cookie comes back on the redirect."),
    })
    log.info("solved d=%s fmt=%s solution=%s hash=%s… in %.1fs",
             d, fmt, solution, digest[:16], result["elapsed"])
    return result
