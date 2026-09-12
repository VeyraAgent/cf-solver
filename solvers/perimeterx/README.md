# perimeterx solver (`type: perimeterx`)

Solves the **HUMAN / PerimeterX "Press & Hold"** gate and harvests the `_px3`
clearance cookie — HARVEST-ONLY, site-agnostic core.

## What it solves

"Press & Hold" is a UX cover over a **SHA-256 hashcash Proof-of-Work run in a Web
Worker** + behavioral biomechanics scoring by the sensor VM (`client.hsprotect.net`).
A genuine `mouseDown → hold → mouseUp` (via the input layer) lets the Worker finish the
PoW and the sensor record real hold biomechanics → PerimeterX mints the `_px3` cookie.
Unlike an Arkose visual puzzle, this is automatable — a real CDP press-hold clears it.
(RE'd 2026-07-14, evidence in `fingerprint-generator/re-artifacts/outlook-signup/PX_PRESSHOLD_RE.json`.)

## How this solver works

`solve_perimeterx()` is the **site-agnostic core**:

```
reach gate  →  detect visible hsprotect iframe + "press and hold" text
            →  real CDP mouseDown at iframe center → hold (Worker runs PoW) → mouseUp
            →  poll until _px3 rotates (challenge consumed)  →  harvest cookie bundle
```

It harvests `_px3` / `_pxvid` / `_pxde` / `pxcts`. It does **NOT** create accounts —
account creation (and its per-IP velocity/risk) belongs to the caller's auto-register
script, which reuses the harvested `_px3` under the same proxy + UA.

## Reaching the gate is pluggable (`render_flow`)

Some PerimeterX deployments render the gate on a plain `goto(url)`; others surface it
only after navigation. That per-site "how to make the gate render" logic lives in
`renderers/` and is selected by the `render_flow` param (mirrors the `datadome` caller
passing its own `url` / octocaptcha's `origin_page`).

- `render_flow="outlook_signup"` → runs `renderers/outlook.py`, which walks a **throwaway**
  Outlook signup form (username → password → birthdate → name) because that gate only
  renders at the last step — there is **no standalone URL** (`iframe.hsprotect.net` returns
  an empty body, `captcha.hsprotect.net` returns 403). The typed values are disposable
  triggers, **not** account data; the solver never submits CreateAccount.
- `render_flow=null` + a `url` → for deployments whose gate renders on load.

Add a new PerimeterX site by registering a renderer in `renderers/__init__.py`.

## Request

```json
{ "type": "perimeterx", "render_flow": "outlook_signup", "proxy": "http://user:pass@ip:port" }
```

- `render_flow` (optional): named site trigger. Default `outlook_signup`.
- `url` (optional): target page when the gate renders on load (`render_flow=null`).
- `proxy` (**required** for a usable token): `_px3` is bound to the exit IP.

## Response (superset of the uniform envelope)

```json
{
  "type": "perimeterx", "solved": true,
  "px3": "<651-char cookie value>",
  "cookies": { "_px3": "...", "_pxvid": "...", "_pxde": "...", "pxcts": "..." },
  "cookie_header": "pxcts=...; _pxvid=...; _px3=...; _pxde=...",
  "user_agent": "Mozilla/5.0 ... Chrome/146 ...",
  "gate_reached": true, "press_hold_actuated": true, "px3_rotated": true,
  "replay_contract": { "bound_to": ["_pxvid", "client_ip", "user_agent"] },
  "elapsed": 94.0
}
```

## Replay contract

1. Reuse the whole cookie bundle (`_px3` + `_pxvid` + `_pxde` + `pxcts`) via the returned
   `cookie_header` on the protected request — `_px3` alone is not enough.
2. `_px3` is **bound to `_pxvid` + IP + UA** with a **short TTL** (minutes to ~1h). Replay
   from the SAME proxy IP + the SAME `user_agent`, within TTL.
3. NOT a portable offline token — harvest under the same proxy the caller will replay with.

## Notes

- The gate is **intermittent** (silent-pass on some sessions). No gate → no token; the
  solver reports `solved:false` + `gate_reached:false` honestly (never a fake success).
- `px3_rotated:false` after actuation → PerimeterX may have scored the biomechanics as
  bot; retry / cleaner IP.
- "Account creation blocked — unusual activity" is a **caller-side** risk-engine outcome
  (per-IP signup velocity), NOT a solver failure — see `RISK_BLOCK_RE.json`.
