# Shumei (数美) solver — spatial_select + icon_select

Pure-HTTP solver for Shumei's click captchas. No browser, no SDK download —
the protocol fields were reverse-verified byte-by-byte from live traffic, and
the DES field encryption is verified 6/6 against the vendor's own
`captcha-sdk.min.js` (v1.0.4-207).

Credits (algorithms ported from these RE repos):
- **[AhCheng1027/shumei-captcha-protocol-demo](https://github.com/AhCheng1027/shumei-captcha-protocol-demo)**
  — protocol flow, DES field keys, timing model, HSV shape detector (91%
  pass-rate over 100 live rounds as of 2026-09).
- **[taisuii/OpenCV_IconSelect](https://github.com/taisuii/OpenCV_IconSelect)**
  — icon_select red-mask + rotation template-matching approach, re-implemented
  here as pure numpy/PIL FFT-NCC (no cv2 needed for this mode).

## Flow

```
1. GET {base}/ca/v1/conf        config handshake (logged, non-fatal)
2. GET {base}/ca/v1/register    -> JSONP detail: rid, order, bg, [fg],
                                   domains, bg_width, bg_height
3. GET https://{domain}{bg}     challenge image (+ fg order bar for icon_select)
4. local detection:
     spatial_select -> HSV color-band segmentation + width-profile shape
                      classification; instruction ("点击图中最小的黄色六棱柱")
                      picks color -> shape family -> size
     icon_select    -> fg bar split by alpha into ordered icon templates;
                       red-masked NCC (scale x rotation sweep, coarse+refine)
                       locates each icon in the bg, click order = slot order
5. GET {base}/ca/v2/fverify     -> riskLevel PASS/REJECT
```

`fverify` carries three DES-ECB-encrypted fields (zero padding, per-field
ASCII keys captured from the SDK):

| field | plaintext | key |
|-------|-----------|-----|
| `sp`  | `[[fx, fy, ts], ...]` normalized click points + timestamps | `735c85df` |
| `ox`  | normalized mouse trail easing through every click | `b06aad3b` |
| `gt`  | elapsed ms since "register" | `ed4576ba` |

Plus static encrypted device-fingerprint params (`lo/eg/xz/te/fr/fq/gr/sn/xb`,
captured sample values) — accepted by the backend for this protocol version.

## Usage

```jsonc
{
  "type": "shumei",
  "organization": "d6tpAY1oV0Kv5jRSgxQr",  // optional, Shumei public trial org default
  "url": "https://site-with-shumei/",      // optional page URL -> Referer/Origin
  "mode": "spatial_select",                // spatial_select | icon_select | auto
  "proxy": "http://user:pass@host:port",   // optional (scheme optional)
  "timeout_s": 90
}
```

`mode: "auto"` registers `spatial_select` and switches to icon matching when
the reply carries an `fg` order bar instead of a text instruction.

## Result

Uniform solver contract, with extras:

```jsonc
{
  "solved": true,
  "type": "shumei",
  "token": "a38ec884...",        // requestId on PASS (server-side verify handle)
  "method": "spatial-hsv",       // spatial-hsv | icon-ncc
  "elapsed": 0.76,
  "error": null,
  "rid": "20260913122823ff...",  // challenge id (needed to verify server-side)
  "org": "d6tpAY1o...",
  "model": "spatial_select",     // which model the challenge came from
  "points": [[514, 103]],        // clicked pixel coords in click order
  "risk_level": "PASS",
  "request_id": "a38ec884...",
  "matches": [...]               // icon_select only: per-slot NCC diagnostics
}
```

## Implementation notes

- `imaging.py` holds both engines: `match_pieces` (icon NCC) and
  `detect_objects`/`select_target` (spatial HSV). `solve.py` is protocol only.
- DES = `DES.new(key, MODE_ECB)` + `\x00` padding + base64 — verified
  byte-identical against the vendor SDK on plaintexts of 1/8/9 bytes and the
  real `sp`/`ox` payloads (`solvers/shumei/_selfcheck.py` records the vectors).
- Icon NCC is exact (FFT over the red-masked luma; bg spectrum computed once),
  with a 1.4x-2.2x scale grid, 30-degree coarse + 5-degree refine rotation
  sweep, and min-distance de-duplication so two slots cannot click one icon.
- Offline checks (no network): `python -m solvers.shumei._selfcheck` from the
  repo root — DES vectors, synthetic 3-piece NCC match (±3 px), rotated
  instance recovery, fg-bar split, instruction parse, synthetic HSV detection,
  payload structure.

## Limits

- `spatial_select`: detection thresholds were tuned on 2026-09 samples of
  `spatial_select-1.0.0` sets; a server-side shape-model refresh can shift
  pass rates (same caveat as the reference repo).
- `icon_select`: best-effort. Icons occluded by the red UI bar or heavily
  foreshortened can fall under the score floor — the solver then fails
  honestly (`solved: false`) instead of submitting garbage points. Live
  smoke: 2/3 PASS; `spatial_select` PASS on first try (2026-09-13).
- `rid` is single-use and short-lived: verify server-side promptly.
