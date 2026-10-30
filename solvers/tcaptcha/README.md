# `tcaptcha` — Tencent TCaptcha slider toolkit (2 modes)

`solvers/tencent` is the **防水墙/TDC** flow (prehandle → gap → MD5 PoW → TDC XTEA,
pure HTTP). This module is the classic **TCaptcha slider widget**: challenge images,
5 independent gap detectors with an 8 px consensus, an arbiter, and a humanised drag —
driven in a real browser.

`pkg/` is the vendored toolkit (v1.1.0). `solve.py` is the sidecar adapter.

## mode=detect (default) — pure compute, no browser

```json
{"type": "tcaptcha", "mode": "detect",
 "challenge_json": {"bg_b64": "…", "fg_b64": "…",
                    "piece": {"sprite_pos": [0,0], "size_2d": [60,60], "init_pos": [0,40]}}}
```

→ `{solved, token: "<dx>", gap, dx, agree, pair_ok, blocker, clusters, candidates, detect}`

Detectors: `ncc` (piece RGB template match), `canny` (edges × piece contour),
`contour` (Otsu + area/size/row filter), `dark` (shape × relative darkness ranked by
interior texture), `hole` (evidence only — it used to override and lost 5/5, so it never
overrides now). Consensus: a ≥2-detector cluster **and** `dark` within 8 px. `token`
carries the drag distance so callers get a real artifact, not just a verdict.

This is reusable for any Tencent-style slider pair, and it is the pre-drag step of the
browser flow.

## mode=solve — run a registered browser flow

```json
{"type": "tcaptcha", "mode": "solve", "flow": "minimax-login",
 "email": "…", "password": "…", "proxy": "http://…", "timeout_s": 240}
```

The toolkit owns a Playwright **sync** session, so it runs as a subprocess
(`python -m solvers.tcaptcha.pkg solve`) — same reason `aliyun` is a subprocess: a sync
browser cannot live inside uvicorn's loop, and a Chromium crash must not take the worker
with it. Credentials pass by env (`TCAPTCHA_EMAIL/PASSWORD/PROXY`), never argv. The
verdict is read from `outcome.json`; the artifact dir is echoed as `out_dir`.

Only the shipped flow is `minimax-login`. To add a target, drop a `@register("name")`
function into `pkg/flows.py` — it must navigate until the widget attaches and return the
frame (see `pkg/flows.py`).

## Hard-won rules encoded in `pkg/`

- Chromium drops concurrent proxy CONNECTs → the four captcha static assets are
  prefetched and `route.fulfill()`-ed from memory, or the widget never boots
  (`IFRAME NOT FOUND`).
- A sync Playwright route handler must be sync (`async def` hangs every request).
- Placeholder prehandle (`ticket: ""` + `template/new_placeholder.html`) = risk control:
  sticky across IPs/accounts/UA, so classify early and stop instead of burning attempts.
- `errorCode=12` is a rate-limit (back off ~70 s), `errorCode=50` is a wrong drag
  (refresh and re-solve).
- The widget's refresh button does not mint a new challenge — re-enter the flow.

## Verified

`mode=detect` on a generated slider pair: `gap=201`, `agree=true`, 4/4 detectors
clustered (`drag_target`, `drag_ncc`, `drag_canny`, `drag_contour`), 0.26 s.
`mode=solve` needs a real flow + credentials.
