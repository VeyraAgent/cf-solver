#!/usr/bin/env python3
"""Cerberus solver self-test.

Run from anywhere:  python3 solvers/cerberus/selftest.py
(or `python3 selftest.py` inside the solver directory).

Proves, against the reference formulas (sjtug/cerberus web JS + Go server,
pow-buster validators):
  1. compute_mask_cerberus matches the Rust u32 byte-swapped mask
  2. the fast prefix check == mask check == BE leading-zero-bits >= 2d
  3. binary-format solve: solution re-verifies through the exact server
     reconstruction blake3Prf(blake3sum(merged), solution)
  4. decimal-format solve (legacy, version < 0.4.6): blake3(merged+str(nonce))
  5. bank decomposition of the legacy decimal working set
  6. async entry contract: uniform dict, never raises, timeouts graceful
"""
import asyncio
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blake3

from solve import (_BINARY_SINCE, _U64, _decimal_addend, _mask_check_fn,
                   _parse_version, compute_mask_cerberus, solve_cerberus,
                   solve_pow, verify)

PASS = 0


def ok(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL  {name} {extra}")
        sys.exit(1)
    PASS += 1
    print(f"ok    {name} {extra}")


def test_mask_vectors():
    # hand-expanded !(!0u32 >> (d*2)).swap_bytes()
    expect = {1: 0x000000C0, 2: 0x000000F0, 5: 0x0000C0FF, 8: 0x0000FFFF,
              14: 0xF0FFFFFF, 16: 0xFFFFFFFF}
    for d, want in expect.items():
        got = compute_mask_cerberus(d)
        ok(f"mask d={d}", got == want, f"= 0x{got:08X}")
    for bad in (0, 17, -1):
        try:
            compute_mask_cerberus(bad)
            ok(f"mask rejects {bad}", False)
        except ValueError:
            ok(f"mask rejects {bad}", True)


def test_check_equivalence():
    rng = struct.Struct("<I")
    for d in range(1, 17):
        mask = compute_mask_cerberus(d)
        check = _mask_check_fn(d)
        for k in range(400):
            h4 = blake3.blake3(bytes([d, k & 0xFF, 7, 3])).digest(4)
            word_le = int.from_bytes(h4, "little")
            word_be = int.from_bytes(h4, "big")
            leading = 32 - word_be.bit_length()
            via_mask = (word_le & mask) == 0
            via_leading = leading >= d * 2
            if not (check(h4) == via_mask == via_leading):
                ok(f"check d={d}", False, f"h4={h4.hex()}")
                return
    ok("prefix check == mask check == BE leading zeros (d=1..16)", True)


def test_binary_solve():
    challenge = "5f1b3d2a4c6e8f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f607182"
    nonce, ts, sig = 12345, 1726000000, "ed25519sigabc123"
    merged = f"{challenge}|{nonce}|{ts}|{sig}|"
    fmt, solution, hash_hex = solve_pow(challenge, 5, nonce, ts, sig,
                                        timeout_s=30)
    ok("binary solve_pow format", fmt == "binary", f"solution={solution}")
    v = verify(challenge, 5, nonce, ts, sig, int(solution), hash_hex)
    ok("binary verify all-true", all(v.values()), str(v))

    # independent re-derivation of the Go server's blake3Prf:
    #   saltStr = blake3sum(merged) (hex), answer = blake3(saltStr ++ LE64(rot32(solution)))
    salt_hex = blake3.blake3(merged.encode()).hexdigest().encode()
    rotated = ((int(solution) << 32) | (int(solution) >> 32)) & _U64
    answer = blake3.blake3(salt_hex + struct.pack("<Q", rotated)).digest()
    ok("server blake3Prf reconstruction", answer.hex() == hash_hex)
    # and the browser-side message layout u32LE(bank)||u32LE(inner):
    bank, inner = int(solution) >> 32, int(solution) & 0xFFFFFFFF
    browser_msg = blake3.blake3(
        salt_hex + struct.pack("<II", bank, inner)).digest()
    ok("browser msg[0]=bank msg[1]=inner", browser_msg.hex() == hash_hex)


def test_decimal_solve():
    challenge = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
    nonce, ts, sig = 777, 1726000100, "legacy-sig"
    fmt, solution, hash_hex = solve_pow(challenge, 5, nonce, ts, sig,
                                        timeout_s=30, version="0.4.5")
    ok("decimal solve_pow format", fmt == "decimal", f"solution={solution}")
    v = verify(challenge, 5, nonce, ts, sig, int(solution), hash_hex,
               mode="decimal")
    ok("decimal verify all-true", all(v.values()), str(v))
    # validator formula: blake3(merged + str(nonce))
    merged = f"{challenge}|{nonce}|{ts}|{sig}|"
    rehash = blake3.blake3((merged + solution).encode()).digest()
    ok("decimal blake3(merged+str(nonce))", rehash.hex() == hash_hex)
    # solution really is the concatenated decimal digits: head + bank//9 + 9-digit inner


def test_decimal_addend():
    cases = {0: 1_000_000_000, 8: 9_000_000_000, 9: 11_000_000_000,
             10: 21_000_000_000}
    for bank, want in cases.items():
        got = _decimal_addend(bank, 215)   # 215 % 64 = 23 → normal branch
        ok(f"decimal addend bank={bank}", got == want, f"= {got:,}")
    # mid-block branch: salt_len % 64 == 55 → pad = 8
    got = _decimal_addend(3, 119)          # 119 % 64 = 55
    ok("decimal addend mid-block bank=3",
       got == 4_000_000_001_000_000_000, f"= {got:,}")
    got = _decimal_addend(10, 119)
    ok("decimal addend mid-block bank=10",
       got == 3_000_000_011_000_000_000, f"= {got:,}")


def test_version_routing():
    ok("version 0.4.5 → decimal",
       _parse_version("0.4.5") < _BINARY_SINCE)
    ok("version v0.4.6 → binary",
       _parse_version("v0.4.6") >= _BINARY_SINCE)
    ok("version 1.2.3 → binary",
       _parse_version("1.2.3") >= _BINARY_SINCE)
    ok("version garbage → None", _parse_version("nonsense!") is None)


async def _async_contract():
    cj = {"challenge": "5f1b3d2a4c6e8f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f607182",
          "difficulty": 5, "nonce": 999, "ts": 1726000200,
          "signature": "sig-json"}
    r = await solve_cerberus(challenge_json=cj)
    keys = {"solved", "type", "token", "method", "elapsed", "error"}
    ok("contract keys", keys <= set(r) and r["solved"] and r["error"] is None,
       f"keys={sorted(r)}")
    ok("contract type/method", r["type"] == "cerberus"
       and r["method"] == "blake3-pow")
    v = verify(cj["challenge"], cj["difficulty"], cj["nonce"], cj["ts"],
               cj["signature"], r["solution"], r["hash"])
    ok("async binary solution verifies", all(v.values()))

    r2 = await solve_cerberus(challenge_json={**cj, "version": "v0.4.5"})
    ok("async decimal via version", r2["solved"]
       and r2["format"] == "decimal" and r2["method"] == "blake3-pow-decimal")

    r3 = await solve_cerberus()
    ok("missing args → error dict", not r3["solved"]
       and isinstance(r3["error"], str) and r3["type"] == "cerberus"
       and isinstance(r3["elapsed"], float))

    r4 = await solve_cerberus(challenge_json={**cj, "difficulty": 0})
    ok("bad difficulty → error dict", not r4["solved"]
       and "range" in r4["error"])

    r5 = await solve_cerberus(challenge_json={**cj, "difficulty": "abc"})
    ok("non-numeric difficulty → error dict", not r5["solved"]
       and isinstance(r5["error"], str))

    # deterministic deadline handling: a past deadline stops the search
    from solve import _mask_check_fn as _mcf, _search_binary_bank as _sbb
    salt = blake3.blake3(b"deterministic").hexdigest().encode()
    hit = _sbb(salt, _mcf(5), 0, 1, time.monotonic() - 1.0)
    ok("past deadline stops search", hit is None)

    # live pool timeout: d=16 (expected 2^32 hashes) cannot finish in 1s;
    # either way the call must return the uniform contract gracefully
    t0 = time.monotonic()
    r6 = await solve_cerberus(challenge_json={**cj, "difficulty": 16},
                              timeout_s=1)
    keys = {"solved", "type", "token", "method", "elapsed", "error"}
    ok("d=16 1s graceful contract", keys <= set(r6)
       and time.monotonic() - t0 < 12
       and (r6["solved"] or "within 1s" in r6["error"]),
       f"elapsed={r6['elapsed']} solved={r6['solved']}")
    cj = {"challenge": "c0ffee00a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f607182",
          "difficulty": 12, "nonce": 424242, "ts": 1726000300,
          "signature": "pool-sig"}
    t0 = time.monotonic()
    r = await solve_cerberus(challenge_json=cj, timeout_s=60)
    ok("pooled d=12 solve", r["solved"], f"in {time.monotonic()-t0:.1f}s")
    if r["solved"]:
        v = verify(cj["challenge"], 12, cj["nonce"], cj["ts"],
                   cj["signature"], r["solution"], r["hash"])
        ok("pooled d=12 verifies", all(v.values()), str(v))


def test_xof():
    ok("digest(4) == digest()[:4]",
       blake3.blake3(b"cerberus").digest(4)
       == blake3.blake3(b"cerberus").digest()[:4])


def main() -> None:
    t0 = time.monotonic()
    test_mask_vectors()
    test_check_equivalence()
    test_xof()
    test_binary_solve()
    test_decimal_solve()
    test_decimal_addend()
    asyncio.run(_async_contract())
    print(f"\nALL {PASS} CHECKS PASSED in {time.monotonic()-t0:.1f}s")


if __name__ == "__main__":
    main()
