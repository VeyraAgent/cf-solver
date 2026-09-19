# Cerberus — blake3 Proof-of-Work solver

`solvers/cerberus/` — pure-compute (no browser) solver for
**sjtug/cerberus**, the Caddy anti-bot module that protects contest/CTF and
open-source infra sites with a blake3 proof-of-work challenge. Same PoW family
as TecharoHQ/anubis, but blake3 instead of SHA-256*RandomX.

## How Cerberus works

A protected page embeds the challenge inline:

```html
<script id="challenge-script"
        x-challenge='{"challenge": "<64 hex>", "difficulty": 14,
                      "nonce": 293834, "ts": 1726000000,
                      "signature": "<ed25519 hex>"}'
        x-meta='{"baseURL": "https://site/some/cerberus-endpoint"}'
        src="/cerberus/pow.js?v=0.4.6"></script>
```

The browser computes a blake3 PoW and POSTs the answer to
`{baseURL}/answer`:

```
response = <64 hex digest>        solution = <u64 nonce, decimal>
nonce    = <the challenge nonce>  ts = <the challenge ts>
signature = <the challenge signature>       redir = /
```

On success the server sets the **`cerberus-auth`** cookie (303 redirect to
`redir`).

### Proof construction (verified, not guessed)

Sources: sjtug/cerberus `web/js/pow.js.worker.js` + `web/js/blake3.js` +
`directives/common.go` (`blake3sum`, `blake3Prf`) + `directives/endpoint.go`
(`checkAnswer`), and eternal-flame-AD/pow-buster `adapter/cerberus.rs`,
`message.rs`, `solver/safe.rs`, `lib.rs` (incl. the exact validator tests).

```
merged   = "{challenge}|{nonce}|{ts}|{signature}|"     # nonce/ts as decimal
salt_hex = blake3(merged).hexdigest()                   # 64 ASCII chars
```

**Binary format** (cerberus >= 0.4.6, the current protocol / this repo):

```
solution = (bank << 32) | inner          # bank = working-set id
message  = salt_hex (64 ASCII bytes) || u32LE(bank) || u32LE(inner)
hash     = blake3(message)               # 72 bytes, single chunk
```

The server re-derives it with `blake3Prf(saltStr, solution)`:
`saltStr = blake3sum(merged)` then
`blake3(saltStr || u64LE(rotate32(solution)))` — byte-identical to the
`u32LE(bank)||u32LE(inner)` layout above.

**Decimal format** (cerberus < 0.4.6) — used automatically when the challenge
JSON carries a `version` below `0.4.6`, or `mode="decimal"`:

```
message  = merged (raw ASCII) || str(solution)   # plain decimal digits
```

Bank decomposition reproduces pow-buster `CerberusDecimalMessage` (head digit
`bank%9+1`, then `bank//9`, then a 9-digit zero-padded inner field). Note the
digits are written in plain decimal order here (pow-buster writes `bank//9`
LSB-first) — both are valid, only the scan order differs: every solution
satisfies `message == merged + str(solution)`.

### Acceptance condition

```
mask  = compute_mask_cerberus(difficulty)     # see below
ok    ⇔ (u32LE(hash[0:4]) & mask) == 0
     ⇔ top `2*difficulty` bits of the digest are zero
     ⇔ endpoint.go checkAnswer: `difficulty/2` leading zero hex nibbles,
        odd difficulty → next nibble < '8'
```

`compute_mask_cerberus(d)` = `(!(!0u32 >> (d*2))).swap_bytes()` (0xFFFFFFFF for
`d=16`), ported verbatim from cerberus `pow/src/lib.rs` / pow-buster
`src/lib.rs` — the byte-swap exists because Cerberus compares the first word as
if big-endian while blake3 emits little-endian words.

## Usage

```python
import asyncio
from solvers.cerberus.solve import solve_cerberus

# Fields straight from the page's x-challenge attribute:
res = asyncio.run(solve_cerberus(
    challenge="5f1b…82", difficulty=14, nonce=293834,
    ts=1726000000, signature="ed25519…"))

# …or pass the whole challenge JSON (all named args become optional):
res = asyncio.run(solve_cerberus(challenge_json={
    "challenge": "…", "difficulty": 14, "nonce": 293834,
    "ts": 1726000000, "signature": "…", "version": "v0.4.6",
}))

# Legacy decimal mode:
res = asyncio.run(solve_cerberus(challenge_json={…, "version": "v0.4.5"}))
# or explicitly: challenge_json={…, "mode": "decimal"}
```

Result (uniform contract, never raises):

```python
{
  "solved": True, "type": "cerberus", "method": "blake3-pow",
  "token": "841261",                 # = "solution" (submit as `solution`)
  "solution": 841261,                # u64 nonce
  "hash": "00000001baa4…fbf0",       # submit as `response`
  "format": "binary", "difficulty": 14,
  "nonce": 293834, "ts": 1726000000, "signature": "…",
  "elapsed": 0.72, "error": None,
  "warning": "POST to {baseURL}/answer: response=<hash>, solution=<u64>, … "
}
```

On `solved=False`, `error` holds the reason (`"difficulty 5 out of range
[1,16]"`, `"no solution within Ns (difficulty=…)"`, …).

Replay the answer yourself to harvest the credential:

```bash
curl -sS -D- -o /dev/null -X POST "$BASEURL/answer" \
  -d "response=$HASH" -d "solution=$SOLUTION" -d "nonce=$NONCE" \
  -d "ts=$TS" -d "signature=$SIG" -d "redir=/" \
  | grep -i '^set-cookie: cerberus-auth'
```

## Verify a solution

`solve_pow()` / `verify()` expose the raw search and the reference re-check for
tooling:

```python
from solvers.cerberus.solve import solve_pow, verify
fmt, solution, digest = solve_pow(challenge, 14, nonce, ts, sig, timeout_s=90)
assert all(verify(challenge, 14, nonce, ts, sig, int(solution), digest).values())
```

`verify` returns `mask_ok / hash_ok / answer_ok / difficulty_ok` — the last
three re-derive `blake3(merged||str(nonce))` (decimal) or the server-side
`blake3Prf` (binary) and the endpoint.go `checkAnswer` nibble rule, so a
solution is proven acceptable to the real server without a live site.

## Performance & difficulty

Measured with the `blake3` wheel on this 8-core WSL box: ~1.5–2M hashes/s per
core. Expected work is `2^(2·difficulty)`:

| difficulty | bits   | expected work | typical time |
|-----------|--------|---------------|--------------|
| 5         | 10     | 1,024         | < 5 ms       |
| 10        | 20     | ~1M           | ~1 s (1 core) |
| 12        | 24     | ~17M          | ~7 s pooled  |
| 14        | 28     | ~268M         | ~1–2 min pooled (Caddyfile default) |
| 16        | 32     | ~4.3G         | too slow for 60 s |

Difficulty ≤ 10 solves single-process in-thread. Higher difficulties fan out
one `fork` process per bank — the same thread_id/threads split the official
browser worker uses — and the first hit stops the siblings. `fork` is assumed
(Linux); on a platform without fork the solver degrades to single-process
(only practical up to ~difficulty 12). The deadline is checked ~every 8k
hashes, so a timeout returns promptly. Failure and timeout never raise — the
uniform error dict is returned.

Requires the Python `blake3` package (`pip install blake3`); bundled wheels for
CPython are universal.

## Files

- `solve.py` — mask, binary + decimal search, single/`fork`-pool solver, async
  entry `solve_cerberus`, `solve_pow`, `verify`.
- `selftest.py` — 35 checks: mask vectors, mask↔prefix-bytes↔leading-zero
  equivalence (d=1..16), binary + decimal end-to-end solving re-verified via
  the server `blake3Prf`/`checkAnswer` formulas, bank decomposition, version
  routing, uniform contract on garbage/timeout, and a real multiprocess
  d=12 solve. Run: `python3 solvers/cerberus/selftest.py`.

## References

- sjtug/cerberus — Caddy anti-bot module (Go server + wasm/JS client).
- eternal-flame-AD/pow-buster — Rust PoW solver suite; Cerberus adapter,
  message layouts and validator tests ported here.