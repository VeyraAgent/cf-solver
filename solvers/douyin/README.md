# douyin — Douyin/TikTok puzzle (slide) captcha solver

Pure-HTTP solver for the ByteDance slide-puzzle captcha used by douyin.com and
TikTok. Ported from **onurkun/puzzle-captcha-resolver** (the OpenCV gap detector)
plus live protocol RE of the current (2026-09-13) douyin web captcha endpoint.

Credit: gap-detection algorithm and the local solve contract are ported from
https://github.com/onurkun/puzzle-captcha-resolver (library.py `build()`).

## How it works

```
challenge fetch (mode 2)          gap solve (both modes)            output
┌───────────────────────┐   ┌───────────────────────────────┐   ┌─────────────────┐
│ GET verify.zijieapi   │   │ gap.py detect_gap():          │   │ x, y (native px)│
│ .com/captcha/get      │──▶│  edges-ncc  (primary)         │──▶│ x_display (340w)│
│ → question.url1/url2  │   │  reference  (fusion fallback) │   │ tip_y, id       │
│ → download images     │   │ gen_trajectory(): overshoot + │   │ trajectory      │
└───────────────────────┘   │ correct-back (kinematics-safe)│   │ POST verify try │
                            └───────────────────────────────┘   └─────────────────┘
```

1. **Challenge** — mode 2 fetches a live challenge (`GET /captcha/get`, aid 6383
   douyin / 1988 tiktok) and downloads `question.url1` (background JPEG, 552px)
   + `question.url2` (piece PNG, alpha-cutout). Mode 1 skips this: the caller
   passes the images directly — exactly the reference webserver's
   `full_image` / `image_partial` POST contract.
2. **Gap detection** — `gap.py`:
   - `detect_gap_edges` (primary, PIL+numpy, no cv2): Pearson NCC over gradient
     maps with an integral-image window pass. Template = piece **silhouette
     ring** (the hole boundary is a darkened copy of the piece outline, and the
     ring aligns 1:1 with it) + masked content gradients. A 3×3 blur tolerates
     ±1px. Verified pixel-exact on real douyin captures (vision-checked crop).
   - `detect_gap_reference` (ported from the reference repo): invert → in-range
     palette mask `[80,80,70]-[130,120,120]` on the 404×150 resize, pure-white
     mask + invert on the 83×55 piece, `cv2.matchTemplate(TM_CCORR_NORMED)`.
     Kept verbatim, but its CCORR_NORMED score inflates on bright templates —
     on a live 2026-09-13 capture it scored 0.99 at a wrong spot.
   - **Fusion** (`detect_gap`): edges result wins unless it is weak (< 0.35);
     the reference may then override only if the edges NCC *at the reference
     point* also clears 0.45 — so an inflated reference score alone can never
     hijack a confident edges match.
3. **Trajectory** — `gen_trajectory()`: ballistic ease-out (`1-(1-t)³`) to
   ~6-11px **overshoot**, then a 10-step correction back to the target. Same
   profile proven against kinematic scoring on solvers/aliyun (monotonic drags
   get rejected even at pixel-perfect positions). Deterministic under `seed`.
4. **Verify** — `POST /captcha/verify` is attempted with the solved
   `x_display`/`tip_y`. **Known boundary:** the endpoint requires the client's
   encrypted `captchaBody` — a plain JSON body returns `504 参数错误[5011]`
   (confirmed live). The encryption is SDK-version-bound (`h5_sdk_version`) and
   drifts, same as the reference repo's scope (it also stops at x/y). The
   solver therefore returns the complete solve artifacts with `verified: false`
   and never fabricates a ticket.

## Request / Response

```json
POST /solve
{
  "type": "douyin",
  "image_b64": "...",          // local mode: full puzzle image (b64/URL ok)
  "piece_b64": "...",          // local mode: piece image (b64/URL ok)
  "url": "https://www.douyin.com/",   // optional — sets Referer
  "web_id": "7xx...",          // optional — binds challenge to caller session
  "platform": "douyin",        // douyin (default) | tiktok
  "proxy": "http://user:pass@host:port",
  "timeout_s": 90,
  "seed": 7,                   // optional — deterministic trajectory
  "params_extra": {}           // optional — override/extend GET params
}
```

```json
{
  "type": "douyin",
  "solved": true,
  "method": "douyin-edges-ncc",       // local-… when images were caller-supplied
  "token": {                          // submit artifacts (verified:false path)
    "x": 256, "y": 82, "score": 0.414,
    "x_display": 158, "tip_y": 41,
    "challenge_id": "37163195f0e2...",
    "trajectory": {"points": [{"x": 8.4, "y": 0.9, "t_ms": 12.3}, "..."],
                   "total_ms": 649},
    "note": "replay verify from a session that holds fp/msToken/captchaBody"
  },
  "verified": false,                  // true only when the server accepted verify
  "elapsed": 1.0,
  "error": null
}
```

Coordinate spaces: `x`/`y` are **native image pixels** (552px background);
`x_display` scales to the widget's 340px render width — the space the verify
endpoint expects. `tip_y` is the server-provided piece y (the drag is
horizontal-only; the jigsaw tab pokes ~10px above the hole top).

## Self-test (no network, no captures)

```bash
python -m solvers.douyin.selftest
```

Builds PIL puzzles with hole-shape == piece-silhouette geometry (alpha-cutout,
white-background, and full-rect piece variants, clean + noise-degraded) and
asserts the gap is hit within ±3px across all 24 variants (observed: 24/24
pixel-exact), plus reference-port sanity and trajectory determinism
(overshoot > target > corrected end).

## Live protocol notes (2026-09-13)

- `GET https://verify.zijieapi.com/captcha/get?aid=6383&app_name=douyin_web&lang=unsup&h5_sdk_version=2.29.0&sdk_version=&iid=0&device_id=0&did=0&web_id=0&ch=&os_type=2&os_version=Mac+OS+10.16.0&category=1&subtype=slide&double_check=1&fp=<19-digit>&sub_sec_timestamp=<ms>`
  → `{"code":200,"data":{"id","challenge_code":99999,"mode":"slide",
  "question":{"url1","url2","tip_y","backup_url1/2"},"cyfreso","codifica"}}`.
  A random 19-digit `fp` is accepted; the old `mcs.zijieapi.com` host 404s.
- Images: `p3-catpcha.byteimg.com/...~tplv-188rlo5p4y-2.jpeg` (bg, 552×344) and
  `...-1.png` (piece, 110×110 RGBA, jigsaw cutout).
- `POST https://verify.zijieapi.com/captcha/verify?<same query params>` with
  JSON `{challenge_code, id, mode, subtype, x, y, reply, fixed_ratio}` →
  `{"code":504,"message":"参数错误[5011]"}` without the encrypted `captchaBody`.
  To make this solver fully server-verified, replay the verify from a session
  that holds the captcha JS state (fp/msToken/captchaBody), or port the
  `captchaBody` encryption for the pinned `h5_sdk_version`.
- The challenge is IP-bound: fetch and replay through the same `proxy`.
