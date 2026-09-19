# Yidun (网易易盾) — pure-HTTP protocol solver

Solver for NetEase Yidun slider challenges over the **v3 protocol (2.28.5)** —
no browser, no headless page. Implements the parameter scheme documented by
the protocol research reference (id / token / fp / irToken / cb / data /
validate / **NECaptchaValidate**) and runs the vendor crypto verbatim through
a small `node` bridge.

## Credits

| Source | What was taken |
| --- | --- |
| [decodecaptcha/YidunCaptchaBreak](https://github.com/decodecaptcha/YidunCaptchaBreak) (aidencaptcha, decodecaptcha.com) | Assigned reference — the protocol research this port follows: id / token / fp / actoken / data / validate / NECaptchaValidate param scheme for 无感/滑块/点选/语序/空间推理 (the repo itself ships no code — teaser repo) |
| [CodeEmpower/yidun-silder](https://github.com/CodeEmpower/yidun-silder) | Working pure-protocol 2.28.5 implementation: `encrypt.js` / `fp.js` / `webpack.js` vendored under `js/` (cb / data / encryptValidate / fp), v3 endpoint + param shape, JSONP flow |
| [wenbo-chen-dev/yidun-captcha-solver](https://github.com/wenbo-chen-dev/yidun-captcha-solver) | Gap-detection structure (column edge-scan → two-peak pairing → alpha-contour NCC) and accel/cruise/decel trajectory format |

## Flow

```text
1. captcha_id            param, else regex-extracted from `url` page source
2. node bridge (js/)     fp(hostname) + get_cb()
3. POST ir-sdk.dun.163.com/v4/j/up   (irstoken.py, Python port of encrypt.js
                                      build_request_body)  ->  irToken
4. GET  c.dun.163.com/api/v3/get     (JSONP) -> {bg[], front[], token, type}
5. type=2 slider: download bg+front -> numpy gap detect -> human trajectory
   type=1 silent  (无感): skip images, short idle trajectory
6. node bridge (js/):    get_data(trace, token, gap_x) -> encrypted `data`
7. GET  c.dun.163.com/api/v3/check   (same JSONP callback)
8. node bridge (js/):    get_encryptvalidate(check_result, fp) -> final validate
```

Risk scoring rejects a share of otherwise-valid submissions (the reference
loops until pass) — the solver retries internally (`attempts=3`) within the
time budget; every attempt fetches a fresh challenge.

## Usage

```python
from solvers.yidun import solve_yidun

result = await solve_yidun(
    captcha_id="314d356dc2a24c76972661b5f37a6cdf",   # or url= to extract it
    url="https://target.com/page",                   # referer/origin + fp hostname
    proxy=None,                                      # scheme://user:pass@host:port
    timeout_s=90,
    mode="slider",                                   # or "silent" (无感)
)
# result["token"] = final encryptValidate (submit as NECaptchaValidate)
```

Result contract: `{"solved": bool, "type": "yidun", "token": str,
"method": "protocol-v3-slider"|"protocol-v3-silent", "elapsed": float,
"error": str|None, "attempts", "captcha_id", "gap_x", "raw_validate",
"check_response", "version"}` — never raises; all failures return the dict.

### Parameters

| Param | Default | Notes |
| --- | --- | --- |
| `captcha_id` | — | target's 32-hex captchaId; extracted from `url` page if omitted |
| `url` | — | target page — used as referer/origin and fp hostname binding |
| `proxy` | `None` | forwarded to curl_cffi (`impersonate="chrome"`) |
| `timeout_s` | `90` | overall budget (retries included) |
| `mode` | `"slider"` | `"silent"` requests type=1; the response type still decides the path |
| `dt` / `initial_token` | reference constants | session artifacts baked in the reference run; strict deployments may bind them to a real page session |
| `zone_id` | `"CN31"` | zone prefix of the returned validate |
| `ir_app_id` | `"YD00192283058223"` | ir-sdk app id from the reference flow |
| `seed` | `None` | deterministic trajectories (testing only) |
| `attempts` | `3` | internal retries on risk rejection |

## Files

```text
solvers/yidun/
├── solve.py       entry point (solve_yidun), gap detect, trajectory, param builders
├── irstoken.py    Python port of encrypt.js build_request_body (CRC32 / S-box /
│                  operation-sequence / custom Base64 / TLV fingerprint) —
│                  verified byte-for-byte against the node reference
├── jsbridge.py    one-shot `node` subprocess bridge (sentinel JSON protocol)
├── js/            vendored CodeEmpower/yidun-silder crypto (encrypt.js, fp.js,
│                  webpack.js) + bridge.js dispatcher
├── selftest.py    unit tests (no network)
└── README.md
```

Requirements: `node` on PATH (any recent version), plus the repo venv
(`numpy`, `Pillow`, `curl_cffi`).

## Self-test

```bash
python3 -m solvers.yidun.selftest
```

Covers: trajectory determinism + jitter bounds, gap detection on synthetic
images (±3 px, with piece / full-bleed / no-piece), the `encrypt_d` node
cross-check vector, param/URL builder shapes, JSONP parsing.

## Calibration notes (owner)

- **Verified live** (dun.163.com demo captcha_id, direct IP): solved end-to-end
  (check `error=0`, `result: true`), ~1-4 s including a risk-rejection retry.
- **Silent (无感) path is UNVERIFIED**: the reference implements the slider
  flow only; `silent_track` submits an idle trajectory — calibrate against a
  real type-1 deployment before trusting it.
- `dt` / `initial_token` mirror the reference session constants; most products
  accept them, but a strict deployment may require per-session values (grab
  from a real page run and pass via kwargs).
- The vendor JS pins version `2.28.5` — when Yidun ships a new build, re-diff
  `js/encrypt.js` against the target's `load.min.js` bundle.
- Strict sites may bind `fp` to the embed domain (`fp(hostname)`); the solver
  derives the hostname from `url` automatically.
