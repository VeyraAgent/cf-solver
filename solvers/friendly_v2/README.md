# `friendly_v2` — Friendly Captcha **v2** (pure HTTP + BLAKE2s PoW)

Complements `solvers/friendly` (which is **v1**, `api.friendlycaptcha.com/api/v1/puzzle`).
v2 is a different protocol under `global.frcapi.com` (or `eu.frcapi.com`).

## Flow

1. `GET /api/v2/captcha/agent?origin=…&sess_id=…&agent_id=…` → HTML with
   `window.SOLVE_CONTEXT_BASE64` / `_NONCE` / `_TIMESTAMP`
2. `GET /api/v2/captcha/widget?…&sitekey=SK`
3. `POST /api/v2/captcha/activate` — telemetry signals (`root.i = 2`), gzip+base64url
4. `POST /api/v2/captcha/quote` — server returns `quote` + `details.cost` (hash budget)
5. `node pow_engine.js` — 36 sub-puzzles of the widget permutation, work-stealing across
   every core; emits `puzzle.solutions.diagnostics`
6. `POST /api/v2/captcha/redeem` — signals advance to `root.i = 4`, `pnow` stays monotonic
   → `data.redeem_token`

The redeem token is the value the host page stores in `frc-captcha-response`.

## Request

```json
{"type": "friendly_v2", "sitekey": "FCMGEMUD2LR7ICLH"}
```

Optional: `page_url` (origin the widget runs on — default is the frcapi host, which is
what the demo uses), `base_url` (`https://eu.frcapi.com` for EU deployments), `proxy`.

## Notes

- `cost` is **IP reputation assigned**: a clean egress gets 5M–20M hashes (1–3 s), a
  hammered IP escalates to `mode: noninteractive` at up to 900M (~minutes). Rotate
  proxies / add a 10–20 s cooldown between runs; that is the whole tuning surface.
- The PoW runs in `pow_engine.js`; `hashlib` in CPython is ~20× slower and would blow
  the request deadline at the high tiers. `node` must be on `PATH`.
- `signals_template.json` is a captured baseline environment. Keep the sequence
  invariants (i: 2 → 4, module `pnow`/`ts` monotonic, widget offsets relative to the
  agent `pto`) or activate/quote gets rejected.
- `submit_to_demo` (off by default) posts the token to frcapi's demo form and reports
  whether the checkmark rendered — used as the self-test.

## Verified

Live solve against the demo sitekey: `AQQA.…` redeem token, `method=frc-v2-pow`.
