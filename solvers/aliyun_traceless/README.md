# `aliyun_traceless` — Aliyun NoCaptcha **TRACELESS** (Camoufox checkbox)

`solvers/aliyun` handles the slide/overlay challenges (pure protocol, `scene_id` + `prefix`).
TRACELESS is the other Aliyun mode: `InitCaptchaV3` answers
`CaptchaType: "TRACELESS"`, the widget renders a bare `确认您不是机器人` checkbox, and
**there is no puzzle to answer** — the verdict comes from device/browser telemetry. No
DOM emulator survives that; the only thing that works is a real anti-detect browser
clicking with real pointer events.

## Request

```json
{"type": "aliyun_traceless",
 "url": "https://host/login",
 "email": "user@example.com",
 "password": "…",
 "user_selector": "#normal_login_account, input[type=text]",
 "submit_selector": "button:has-text(\"登录\")",
 "warmup_ms": 1200,
 "timeout_s": 180}
```

Only `url` is required; the selectors default to the reference flow. `proxy` is honoured
(playwright proxy dict built from `scheme://user:pass@host:port`).

## How it decides "solved"

`验证通过` appearing in `#aliyunCaptcha-checkbox-text` **and** the popup going
`window-hidden`. A screenshot and the full SDK log land in `/tmp/tcaptcha/`; without
`验证通过` it is not a solve, no matter how the screenshot looks.

## Expectation (measured, not a claim)

Reference site: **4/5 (80%)**, 34–39 s per run. Failures stop at `确认您不是机器人` (the
challenge is rejected at init) and correlate with the longest run — throttle, not a bad
click. To raise the rate: 30–60 s between runs, rotate IPs, warm up the pointer first.

## Host requirement

Camoufox must be importable **in the server interpreter**:

```bash
pip install "camoufox[geoip]"
export CAMOUFOX_EXECUTABLE_PATH=/path/to/camoufox-bin   # only if outside ~/.cache
```

Without it the type returns `{"solved": false, "error": "…camoufox not installed…"}`
instead of failing the HTTP request. `humanize=True` is not optional — it is what makes
the pointer telemetry count.

## Diagnostic table (from the reference skill)

| symptom | meaning |
|---|---|
| `btn=校验中` forever, `canvases: 0` | the `cx.*.js` bundle never executed |
| checkbox reverts to `确认您不是机器人` | challenge rejected at init, not a bad click |
| `#aliyunCaptcha-errorCode` filled | read it — usually `F0xx` |
| `getLocationInfo → overseas: true` | IP outside CN; align timezone/locale with the IP |

## Verified

Full Camoufox session exercised end-to-end against a page without the widget: browser
launch → form fill attempts → submit attempt → state polling → screenshot, returning a
structured failure. The captcha itself needs a real TRACELESS target.
