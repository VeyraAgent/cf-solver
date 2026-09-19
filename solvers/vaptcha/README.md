# vaptcha — Vaptcha 手势验证 (gesture captcha) solver

Pure-HTTP solver for **Vaptcha V4** gesture verification. The challenge shows a photo
with a translucent bright stroke ("gesture line"); the user must trace it. This solver
fetches the challenge over the wire protocol, locates the stroke with classical CV
(a deterministic port of the Mask R-CNN method from `wobuxiangtong/vaptcha_recoginse`),
synthesizes a human-like trace, solves the PoW, and submits to `/api/validate`.

## Protocol (RE notes — from the live V4 widget stack)

RE'd from `c4.vaptcha.com/src/v4.js`, `/src/core.js`, `/src/verify.html` and
`/src/pow.js` (widget build `202609041954`, 2026-09-13). Note: the old
`api.vaptcha.com` / `cdn.vaptcha.com` hosts are gone (NXDOMAIN since ~Aug 2026); the
V4 stack lives under `c4.` (static), `v4c.` (config), `v41.` (server-side validate of
tokens) plus a per-deployment challenge `server` returned by config.

```
GET  https://v4c.vaptcha.com/api/config?v=1&d=<wire>
       fields: [vid, tz, z, lang, sdkv, href, tag, dfu, ip, ua, _t]  (only
       vid/lang/sdkv/tag/dfu/ip/_t filled — matches v4.js fetchConfig exactly)
     -> {code, data:{server, knock, ad, active_probe, ...}}

POST {server}/api/knock        (wire envelope, KNOCK_FIELDS — 45 positional fields)
     -> {code, data:{knock, trajectory (image url), type, title, pow_start,
                     result, salt, dfu, knockParams, ...}}

GET  {trajectory}?_t={ms}      -> challenge image

PoW (pow.js VaptchaPow.solve port): find n in [pow_start, pow_start+1e6) with
     sha256(f"{n}{salt}") == result   → order = str(n)

POST {server}/api/validate     (wire envelope, VALIDATE_FIELDS — 47 fields)
       trajectory -> [points, encoded, totalDuration];  pow -> [nonce, order]
     -> {code, data:{result: true, token, dfu, ip}}                on pass
     -> {code, data:{result: false, message: refresh_required | try_smaller |
                     try_larger | retry}}                          on miss
```

### Wire envelope (v4.js `i()` / verify.html `encodeWire`/`decodeWire`)

```
request  = {"v": 1, "d": base64url(JSON.stringify(<positional values, field order>))}
response = {"v": 1, "d": base64url(JSON.stringify(<plain object>))}
```

Special positional mappings: `trajectory` → `[points, encoded, totalDuration]`,
`pow` → `[nonce, order]`, `undefined` → `null`. The endpoint answers in wire format
even for errors (`{"v":1,"d":...}` wraps `{"code":1,"msg":"invalid request"}`), which
is how the envelope was confirmed live.

### Trajectory encoding (verify.html `encodeSegment`)

Microsecond timestamps. Per point: `x` (4 hex, clamp 0..420) + `y` (4 hex, clamp
0..280) + `dt` (5 hex, cap 1048575 µs), concatenated, uppercased, first point
`dt=0`. Widget constraints enforced by the server: trace duration ≥ 250 000 µs,
≥ 5 points after the motion filter (a point is kept if |dx|>3 or |dy|>3), max 200
kept points. The canvas is fixed 420×280 and stretches the challenge image, so
detected image-space points are scaled to canvas space before encoding.

## Recognition — port of wobuxiangtong/vaptcha_recoginse

The reference (2019) segments the gesture stroke with a **Mask R-CNN** trained on 109
labelme-annotated 480×270 challenge images (`images/*.png + *.json` in that repo,
label `line`, one stroke per image). TF/Mask-R-CNN is out of scope for this sidecar's
venv, so `recognize.py` ports the *task* (locate the stroke, return its ordered
centerline) with classical CV, calibrated on that same labeled dataset:

1. **White top-hat lift** (elliptical kernel 21) — the stroke brightens its local
   neighborhood by ~40-90 gray levels (measured: mean lift 59 inside the annotated
   stroke vs 22 outside across 109 samples).
2. **DP max-brightness path** — the stroke is x-monotone in every reference sample,
   so a per-column dynamic program (slope limit ±6, smoothness penalty 0.35) globally
   locks onto it while ignoring local bright blobs.
3. **k=3 candidate paths** + heuristic ranking (contrast, saturation, edge
   sharpness).

Measured on the reference's 109 labeled images (1553 ground-truth centerline points):

| metric                        | result |
| ----------------------------- | ------ |
| union of 3 candidates ≤ 15 px | **100%** |
| union of 3 candidates ≤ 25 px | **100%** |
| top-1 candidate ≤ 25 px       | 57% |
| top-1 centerline median err   | 14.7 px |
| wall time                     | ~190 ms/image |

The top-1 miss rate is handled the cheap way: challenge refresh is free, so the flow
walks candidate 1 → 2 → 3 (and re-detects after a `refresh_required`) within
`_MAX_ATTEMPTS = 5`. For production-grade single-shot accuracy, drop a trained
segmenter next to this file (see below) — the ONNX path then takes precedence.

### Optional trained model (`line_seg.onnx`) — NOT committed (>2 MB rule)

If `solvers/vaptcha/line_seg.onnx` exists, it is used instead of the DP detector:

- input: `float32 [1, 3, 270, 480]` RGB in 0..1
- output: `float32 [1, 1, 270, 480]` stroke logits (sigmoid > 0.5 → mask)

To reproduce the reference's labels with a small U-Net (≈2 MB ONNX): clone the
reference repo for the dataset, then

```
git clone https://github.com/wobuxiangtong/vaptcha_recoginse /tmp/vaptcha_recoginse
pip install torch torchvision onnx  # training env, not the sidecar venv
python train_unet.py --data /tmp/vaptcha_recoginse/images --epochs 40 --export onnx
mv line_seg.onnx solvers/vaptcha/line_seg.onnx
```

`train_unet.py` is intentionally not part of this repo; any 1-class segmentation
model matching the I/O contract above works. Without the file the solver runs the
classical path — no download needed at deploy time.

## Request

```json
POST /solve
{
  "type": "vaptcha",
  "vid": "5afa0629c08a27234c00d591",  // required — site's Vaptcha VID
  "url": "https://target.site/page",  // optional — used as config `href`
  "proxy": "http://user:pass@host:port",  // optional
  "timeout_s": 90
}
```

Advanced (replay/direct mode, all optional): `server` + `knock` to skip config,
`image_b64` to run recognition on a caller-fetched challenge image.

## Response

```json
{
  "type": "vaptcha",
  "solved": true,
  "token": "....",                    // validate response token — replay to the site
  "method": "pure-http/dp-tophat",    // or pure-http/onnx
  "elapsed": 6.2,
  "error": null,
  "attempt": 1,
  "knock": "...", "server": "https://...", "dfu": "...", "ip": "...",
  "anchors": [[x, y], [x, y], [x, y]],   // 3 trace anchors in canvas coords
  "recognition_method": "dp-tophat",
  "points": 97
}
```

Replay `token` exactly like the widget's `vaptcha_pass` postMessage payload would
(`{token, dfu, ip}`). Tokens are short-lived and bound to the solving session/IP —
verify from the same IP if the site checks (use the same `proxy`).

## Status / known limitations (honest)

- **Protocol layer**: complete and confirmed against the live stack down to the wire
  envelope and error semantics (the endpoint answered a probe in valid wire format).
  Full end-to-end pass requires a real deployment's VID + `server`; the config/knock
  sequence was not live-verified against a customer site because no valid VID was
  available during the build.
- **Fingerprints**: V4 scores device fingerprints (dfc/dfa/dfb, webgl, canvas, ja3 —
  all 45 knock / 47 validate wire fields are sent, zeros/empties by default). Pure
  HTTP attempts can fail risk scoring on stricter deployments; failures surface as
  `solved:false` with the server's message (`refresh_required` / `try_smaller` /
  `try_larger` / `retry`), never as raised exceptions.
- **Recognition**: top-1 57% ≤ 25 px (classical DP), union 100% ≤ 15 px — retries
  cover the gap; add `line_seg.onnx` for single-shot reliability.
- The `p` / `_tp` commitment branch of `encodeTrajectory` (protocol v2 hover
  commitment) is not implemented — the plain `encodeSegment` path is used, which is
  what the widget sends when no `_tp` commitment is present in `dfc_components`.

## Files

- `solve.py` — entry `solve_vaptcha(vid, url, proxy, timeout_s)`, uniform result contract
- `protocol.py` — wire envelope, PoW, trajectory encode/synthesize, endpoints, HTTP client
- `recognize.py` — top-hat + DP stroke detector, ONNX hook, skeleton utilities
- `README.md` — this file

## Credit

- Recognition method + labeled dataset: **wobuxiangtong/vaptcha_recoginse**
  (https://github.com/wobuxiangtong/vaptcha_recoginse) — the Mask R-CNN approach and
  its 109 labelme annotations; ported here to a deterministic classical pipeline.
- Protocol: reverse-engineered from the public Vaptcha V4 widget stack
  (`c4.vaptcha.com/src/{v4,core}.js`, `/src/verify.html`, `/src/pow.js`).
