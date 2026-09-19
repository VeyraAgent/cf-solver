# Yandex SmartCaptcha Solver — browser-harvest (checkbox / invisible)

Local solver for **Yandex Cloud SmartCaptcha** (`smartcaptcha.yandexcloud.net`,
legacy `smartcaptcha.cloud.yandex.ru`) — the "Я не робот" checkbox widget used across
RU web. Browser-harvest via CloakBrowser: navigate the caller's page, click the
checkbox like a human, harvest the token from the widget's own callback surface.
No protocol forgery, no third-party API.

All widget internals below were extracted from the shipped `captcha.js` bundle and
**verified live** (2026-09-13) against a real production deployment
(metropol-moscow.ru booking page) — silent-pass checkbox solve in ~2-4 s headless.

## Widget RE facts (ground truth, not guesses)

- Token surface: per-widget hidden input `<input name="smart-token" data-testid="smart-token">`
  on the host page + `window.smartCaptcha.getResponse(widgetId)` + the `success`
  subscribe event (callback receives the token). Three independent harvest channels.
- Events: `success`, `challenge-visible`, `challenge-hidden`, `network-error`,
  `token-expired`, `javascript-error`.
- Checkbox click target: the visible square `.CheckboxCaptcha-Checkbox`
  (`data-checked` flips `false`→`true` on a real click). Gotcha: the actual control
  `input#js-button[role=checkbox]` stretches over the whole widget — clicking its
  CENTER (the label area) **does nothing**; click the square on the left.
- On click the iframe POSTs `https://smartcaptcha.<host>/check?host=<page>&sitekey=...`;
  silent pass → `success` in ~1-2 s. Risky fingerprint → `challenge-visible`
  (slider/puzzle in the `advanced` iframe).
- Token: base64url ~372 chars, decodes to
  `t=<unix_ts>;i=<client-ip>;D=<hex sig>;u=<micro-ts>;h=<hex>` — **the client IP is
  embedded** → the token is IP-bound. One-time, TTL ~5 min (official docs).

## Solving modes

### 1. Checkbox page (`solve_yandex`, method `browser-harvest`) — primary

Page shows the checkbox widget → humanized multi-step mouse click on the square →
poll token from success events / getResponse / smart-token inputs.

```bash
curl -X POST http://localhost:8877/solve \
  -H 'Content-Type: application/json' \
  -d '{"type":"yandex","url":"https://target.com/form-with-widget","timeout_s":60}'
```

### 2. Invisible-only page (method `invisible-execute`)

Page renders SmartCaptcha in `invisible: true` mode (no checkbox iframe, e.g.
hotel-booking forms) → solver calls the documented `window.smartCaptcha.execute(id)`
on every rendered widget and polls for the token. Same uniform contract.

## Honest boundary: the puzzle case

If Yandex scores the session as risky, the widget escalates to a **challenge
(slider / image puzzle)** — surfaced via `challenge-visible` and/or the advanced
iframe becoming visible. This solver does **not** solve those: it returns
`solved:false` with `error: "puzzle requires interaction beyond checkbox"` plus the
challenge text for triage. Same verdict when the checkbox accepts but no token
arrives within `timeout_s` (risk-scoring, not a solver bug) — retry with a cleaner
residential proxy.

## Token handling contract

- `token` is returned raw (base64url string, ~372 chars) — hand it to the site's
  validate call verbatim, **within ~5 min**, **from the same egress IP** the solve
  ran on (IP is embedded; the validate endpoint checks it).
- Server-side verification (the site owner's side):
  `POST https://smartcaptcha.yandexcloud.net/validate` with `secret=<server key>&token=<token>&ip=<client ip>`.
- `cookies` extra: post-success cookies for `*.yandex.ru` / `*.yandexcloud.net` /
  the page's own domain (name→value map) for session replay.
- `proxy` is per-request only (`http://user:pass@host:port`, normalized for
  Chromium auth by `common.browser`). No env fallback.

## Response

Uniform contract, no exceptions:

```json
{"solved": true, "type": "yandex", "token": "dD0xNzg5...", "method": "browser-harvest",
 "elapsed": 8.3, "error": null, "url": "...", "proxy": false, "user_agent": "...",
 "cookies": {...}, "events": [...], "warning": "..."}
```

Failure keeps the same shape with `solved:false` + `error`
("puzzle requires interaction beyond checkbox", "widget error: the key is invalid
or expired", "no SmartCaptcha widget found on page …", "no token within Ns …").

## Known limitations (honest)

- **Challenge (slider/puzzle) escalation is not solved** — returned as a clean,
  honest failure (see above). Risk-scoring is IP/fingerprint-driven; clean
  residential egress usually silent-passes.
- The solver harvests the widget **as rendered by the caller's page**; it never
  injects its own widget/sitekey (host binding would reject it anyway —
  SmartCaptcha validates `host` server-side; keys are per-domain).
- Pages that lazy-load the widget behind an interaction need the caller to trigger
  that interaction first (the solver surfaces "no SmartCaptcha widget found").

## Files

| File         | Description                                        |
| ------------ | -------------------------------------------------- |
| `solve.py`   | `solve_yandex(url, proxy, timeout_s)` — harvest flow |
| `selftest.py`| Contract + helper checks (offline) + live smoke run |
| `__init__.py`| Package marker                                     |

## Selftest

```bash
cd <repo root>
# offline: contract + token decoder
python3 -m solvers.yandex.selftest
# live smoke (default: metropol-moscow.ru booking, real production deployment):
... -m solvers.yandex.selftest --live
```

Dependencies: `cloakbrowser` (anti-detect Playwright) — same venv as the rest of
the solvers.
