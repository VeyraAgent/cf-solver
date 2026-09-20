<div align="center">

# 🔐 CF-Solver

**Self-hosted captcha-solving sidecar — 50 challenge types, zero per-solve cost.**

> ⚠️ **Educational purposes only.** This project exists to study how anti-bot
> challenges work and to test automation research in a controlled setting.
> You are responsible for complying with the terms of service of any site you
> point it at, and with all applicable laws. Do not use it for abuse,
> unauthorized access, or any illegal activity.

One FastAPI process. One JSON call. Tokens in seconds.

![types](https://img.shields.io/badge/types-50-blueviolet) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![no--docker](https://img.shields.io/badge/docker-not--needed-9cf)

**[⚡ Quick start](#-quick-start) · [🧩 Types](#-supported-types--50) · [✅ Verification](#-verification) · [📡 API](#-one-json-call-in-token-out)**

</div>

---

## ⚡ Quick start

```bash
git clone https://github.com/VeyraAgent/cf-solver && cd cf-solver
./install.sh          # python deps + chromium + xvfb + node sidecar + ONNX models
./run.sh              # FastAPI on :8877
```

`run.sh` starts Xvfb automatically on a headless box, reads `$PORT`, and refuses
to start if the port is already busy. Check it is up:

```bash
curl -s http://localhost:8877/health
```

Then solve:

```bash
curl -s -X POST http://localhost:8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"cloudflare","url":"https://nowsecure.nl/"}'
```

> **First run on a fresh box:** `install.sh` is idempotent and safe to re-run.
> Use `YES=1 ./install.sh` for a non-interactive install and
> `WITH_ARKOSE=1 ./install.sh` to also pull the optional 1.4 GB Arkose models.


## 🧩 Supported types — 50

Legend:

- ✅ **verified** — solved live with a real token/cookie during testing
- ⚙️ **needs input** — the solver is complete; it needs a real sitekey, URL, or image from your target
- 🔒 **needs IP** — works, but the vendor scores the network independently, so a datacenter IP is refused

### Browser-backed

| | type | result | notes |
|---|---|---|---|
| ✅ | `cloudflare` | `cf_clearance` cookie + UA | full interstitial |
| ✅ | `cloudflare_challenge` | `cf_clearance` cookie | alias path for the challenge endpoint |
| ✅ | `turnstile` | widget token | any sitekey, 3 solving modes |
| ✅ | `recaptcha` | `token` | v2 / v3 / Enterprise, ONNX tile classifier |
| ✅ | `hcaptcha` | `token` | checkbox + vision challenge path |
| ✅ | `hcaptcha_enterprise` | `token` | enterprise `rqdata` flow |
| ✅ | `datadome` | `datadome` cookie | IP-sensitive, see footnote 1 |
| ✅ | `imperva` | `visid_incap_*` + `incap_ses_*` | Incapsula session |
| ✅ | `perimeterx` | `_px3` cookie | press-and-hold flow |
| ✅ | `yandex` | SmartCaptcha token | |
| ✅ | `geetest` | v4 `captcha_output` + `pass_token` | slide |
| ⚙️ | `awswaf` | `aws-waf-token` cookie | needs a URL that serves a silent WAF challenge |
| ⚙️ | `botguard` | Google `bgRequest` token | needs an account `email` to reach the token RPC |
| ⚙️ | `aliyun` | `{certifyId, deviceToken, data}` | needs `scene_id` + `prefix` |
| ⚙️ | `geetest_v3` | `validate` + `seccode` | needs the page's `gt` + `challenge` |
| ✅ | `mtcaptcha` | `vt` token | verified with MTCaptcha's public demo sitekey |
| ⚙️ | `arkose` | `fc_token` | needs `public_key` + a clean IP |
| ⚙️ | `kasada` | `x-kpsdk-ct` headers | needs a classic `ips.js` site |
| ⚙️ | `cybersiara` | JWT token | needs the current `MasterUrlId` |
| ⚙️ | `x5sec` | `x5sec` cookie | needs a live punish URL |
| ⚙️ | `friendly` | challenge token | needs a live target |
| ⚙️ | `captchafox` | slide result | needs the puzzle piece |
| ⚙️ | `binance` | slide result | needs the site's `biz_id` |
| ⚙️ | `basilisk` | slide + icon-click | needs `site_key` + `site_domain` |
| ⚙️ | `recaptcha_audio` | transcribed text | needs the audio clip URL (no browser) |
| ⚙️ | `steam` | solved text | needs the captcha image |
| ⚙️ | `vk` | solved text | needs the captcha image or `sid` |
| ✅ | `zhihu` | 4-char text | verified on a live zhihu.com captcha image |
| 🔒 | `akamai` | `_abck` cookie | datacenter IP is scored independently |

### No-browser (compute / image / PoW)

| | type | result | notes |
|---|---|---|---|
| ✅ | `cap` | PoW solution | |
| ✅ | `anubis` | PoW solution | |
| ✅ | `mcaptcha` | PoW solution | |
| ✅ | `goaway` | PoW solution | |
| ✅ | `rotate` | rotation angle | ships its own ONNX model |
| ✅ | `image_to_text` | OCR text | ddddocr, raw base64 or data-URI |
| ✅ | `tspd` | F5/DDoS cookie | needs `url` |
| ✅ | `altcha` | PoW payload | official altcha-lib; verified with a generated challenge |
| ✅ | `tencent` | `ticket` + `randstr` | verified with the public test appid (pure-HTTP) |
| ⚙️ | `procaptcha` | PoW solution | needs the dapp `url` + `sitekey` |
| ✅ | `cerberus` | challenge solution (blake3) | verified with a generated challenge |
| ✅ | `yidun` | NetEase slider (v3 protocol 2.28.5) | verified with the demo captchaId |
| ⚙️ | `dingxiang` | Dingxiang v5 slider | needs an `app_id` (a demo default is included) |
| ✅ | `shumei` | Shumei click captcha | verified with the official trial org |
| ⚙️ | `douyin` | ByteDance slide puzzle | needs the puzzle image + a clean IP |
| ⚙️ | `vaptcha` | Vaptcha V4 gesture | needs the site's `vid` |
| ✅ | `grid` | grid selection | vision verified (pixtral); needs an image + instruction |
| ✅ | `coordinates` | click coordinates | vision verified (pixtral); needs an image + instruction |
| ✅ | `draw_around` | draw-around captcha | vision verified (pixtral); needs an image + instruction |
| ✅ | `drag_drop` | drag & drop | vision verified (pixtral); needs an image + instruction |
| ✅ | `bounding_box` | bounding-box selection | vision verified (pixtral); needs an image + instruction |

**Footnotes**

1. **IP-sensitive (🔒)** — Akamai and DataDome score the network independently of
   the browser, so a residential or mobile egress is required. See the
   **Why IP matters** section below.
2. **Needs input (⚙️)** — the solver is complete; it needs a real sitekey, URL, or
   image from the target site. No library can invent these values.
3. **Arkose models** — 24 optional ONNX models (~1.4 GB) load with one command.
   See the **Arkose models** section below.

## 🚀 One JSON call in, token out

```bash
curl -s -X POST :8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"cloudflare","url":"https://nowsecure.nl/"}'
```

```json
{"type":"cloudflare", "solved":true, "cf_clearance":{"value":"X0ujw…"}, "user_agent":"Mozilla/5.0 …"}
```

<details>
<summary><b>📖 More examples</b></summary>

```bash
# Turnstile
curl -s -X POST :8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"turnstile","sitekey":"0x4AAA…","url":"https://target.com"}'

# reCAPTCHA v3 Enterprise
curl -s -X POST :8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"recaptcha","version":"v3","enterprise":true,"sitekey":"6Lc…","url":"https://target.com"}'

# Tencent — pure-HTTP, no browser, no sitekey
curl -s -X POST :8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"tencent"}'

# Imperva Incapsula session
curl -s -X POST :8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"imperva","url":"https://balance.vanillagift.com/"}'

# GeeTest v4 slide
curl -s -X POST :8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"geetest","captcha_id":"54088bb0…","risk_type":"slide"}'

# OCR — raw base64, or a data-URI (the prefix is stripped server-side)
curl -s -X POST :8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"image_to_text","image_b64":"data:image/png;base64,iVBORw0KG…"}'
```

</details>

<details>
<summary><b>📡 API reference</b></summary>

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | public | liveness + supported types + pool summary |
| GET | `/status` | token | running tasks |
| GET | `/logs` | token | last N solve events |
| POST | `/solve` | token | solve (dispatch by `type`) |
| POST | `/solve/async` | token | queue a job, returns `job_id` |
| GET | `/solve/job/{job_id}` | token | poll a queued job |
| GET | `/pool` | token | proxy pool status (health, uses, fails) |
| POST | `/pool` | token | add proxies `{proxies: [...]}` |
| DELETE | `/pool` | token | remove `?proxy=host:port` or `?all=1` |
| POST | `/pool/check` | token | health-check now (`?deep=1` = TCP+HTTP) |
| GET | `/docs` `/redoc` | public | Swagger / ReDoc |

Response: `200 + solved:true` → use `token` · `solved:false` → read `error` · `401/408/429`.

Full OpenAPI at `/docs` — every field typed.
</details>

<details>
<summary><b>⚙️ Config & environment</b></summary>

| Variable | Default | Effect |
|---|---|---|
| `PORT` | `8877` | listen port |
| `BROWSER_HEADLESS` | per-solver | `0` = headed for ALL (recommended under Xvfb) |
| `SOLVER_TOKEN` | unset | bearer token for `/solve` |
| `SOLVER_ALLOW_PRIVATE` | unset | `1` = allow loopback targets (SSRF guard off) |
| `SOLVER_POOL_FILE` | `proxies.txt` | proxy pool persistence file |
| `SOLVER_POOL_CHECK_INTERVAL` | `300` | background health sweep (seconds, `0` = off) |
| `TURNSTILE_GEOIP` | unset | `1` = align tz/locale to proxy exit IP |

Proxy per-request only: `"proxy": "http://user:pass@host:port"` (or `"pool"` — below).
`cf_clearance` replays require the same IP + UA + TLS fingerprint (curl_cffi).
</details>

<details>
<summary><b>🌐 Proxy pool</b></summary>

Self-hosted rotation à la CapSolver proxyless/proxied — solver stays local, egress rotates
through your own proxies. List persists to `proxies.txt`; health is tracked with a
cooldown ladder (60s → 120s → 300s → dead) and a background checker.

```bash
# populate
curl -X POST :8877/pool -H 'Content-Type: application/json' \
  -d '{"proxies":["http://user:pass@1.2.3.4:8080","socks5://5.6.7.8:1080"]}'

# solve through the pool (magic value "pool")
curl -X POST :8877/solve -H 'Content-Type: application/json' \
  -d '{"type":"cloudflare","url":"https://target.com","proxy":"pool"}'
# → response echoes "proxy_used": "1.2.3.4:8080" — replay IP-bound cookies
#   (cloudflare/datadome/akamai/imperva/awswaf/perimeterx/x5sec) from that same IP
```

Token types (turnstile/recaptcha/hcaptcha/…) are IP-flexible — pool adds IP rotation
for rate-limit spread.

**Zero proxies?** Scrape + validate straight from public GitHub lists (~6k unique,
hourly-refreshed sources) and feed the survivors into the pool:

```bash
python3 scripts/fetch_proxies.py --min 15 --limit 40   # fetch → validate → proxies.txt
python3 scripts/fetch_proxies.py --loop 1800           # keep refreshing every 30 min
python3 scripts/fetch_proxies.py --post :8877          # ...and inject into a running server
```
Validation = concurrent curl_cffi chrome-TLS probes with a latency cap — expect
~1% survival (that's the point); dead entries keep culling themselves via the
pool's cooldown ladder afterwards.
</details>

<details>
<summary><b>🖥️ Deploy (VPS)</b></summary>

```bash
./install.sh
sudo tee /etc/systemd/system/cf-solver.service << 'UNIT'
[Unit]
Description=CF-Solver
After=network.target
[Service]
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1920x1080x24" /opt/cf-solver/.venv/bin/python3 server.py
Environment=PORT=8877
Environment=BROWSER_HEADLESS=0
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl enable --now cf-solver
```

</details>

<details>
<summary><b>🚂 Deploy (Railway / PaaS)</b></summary>

Docker is **not** required — Railway's Python builder installs `requirements.txt`
and runs your start command. `PORT` is read from the environment.

Set the start command to:

```bash
xvfb-run -a -s "-screen 0 1920x1080x24" python3 server.py
```

`railway.json` (included) pins the Dockerfile builder and uses `/health` as the
healthcheck. Delete it if you prefer the Nixpacks Python builder.

Reality check on a free tier:

| | works | why |
|---|---|---|
| PoW types (`cap`, `anubis`, `mcaptcha`, `procaptcha`, `goaway`, `altcha`) | ✅ | pure compute, no browser |
| `image_to_text`, `rotate`, `cerberus` | ✅ | ONNX/OpenCV only |
| `turnstile`, `recaptcha`, `hcaptcha`, `geetest`, `tencent` | ✅ | browser-backed; a datacenter IP is fine |
| `datadome`, `akamai`, `kasada`, `cybersiara` | ❌ | datacenter IP gets scored regardless of fingerprint |
| `arkose` | ⚠️ | 1.4 GB of ONNX models vs the disk limit |

Browser-backed types need Chromium (~760 MB) plus Xvfb in the image. On a
512 MB–1 GB free-tier instance, only the pure-compute types fit comfortably.
</details>

<details>
<summary><b>📦 Arkose models (optional, ~1.4GB)</b></summary>

```bash
cd solvers/arkose/models
for f in 3d_rollball_objects_cv 3d_rollball_objects_v2 BrokenJigsawbrokenjigsaw_swap \
         card cardistance conveyor coordinatesmatch coordinatesmatch_cv counting \
         dicematch dice_pair frankenhead hand_number_puzzle hopscotch_highsec \
         knotsCrossesCircle penguin penguins-icon penguins rockstack rockstack_v2 \
         shadows train_coordinates train_coordinates_cv unbentobjects; do
  curl -fLO "https://funcaptchamodel.unix.do/$f.onnx"
done
```

</details>

<details>
<summary><b>🧠 Models (Hugging Face)</b></summary>

The ONNX models live in a separate repo so a `git clone` stays light:

**https://huggingface.co/VeyraAgent/cf-solver-models**

```bash
./scripts/fetch_models.sh        # public — no token needed
```

| model | size | used by |
|---|---|---|
| `models/siamese.onnx` | 56 MB | image recognition |
| `models/yolov11n_captcha.onnx` | 10 MB | object detection |
| `solvers/aliyun/best.onnx` | 10 MB | Aliyun slide |
| `solvers/geetest/models/geetest_v4_icon.onnx` | 2.3 MB | GeeTest v4 icons |
| `solvers/recaptcha/models/recaptcha_cls_s.onnx` | 20 MB | reCAPTCHA tile classifier |
| `solvers/rotate/rotate_model.onnx` | 1.1 MB | rotation |
| `solvers/vk/captcha_model.onnx` | 1.1 MB | VK CTC OCR |
| `solvers/vk/ctc_model.onnx` | 1.5 KB | VK CTC decode |

Total ~100 MB. Each file keeps its repo-relative path, so the tree drops straight
back into a clone. `install.sh` runs this for you.
</details>

<details>
<summary><b>⚠️ Why IP matters (akamai/datadome)</b></summary>

Both vendors score the network independently of the browser. From
[invisible_playwright's DataDome analysis](https://github.com/feder-cr/invisible_playwright/blob/main/docs/datadome-explained.md):
*"a datacenter IP… can trigger a challenge on its own, independent of whether the
device fingerprint was clean"* — IP/ASN *"sits entirely outside what any browser
engine can answer."*

**Akamai field data:** wre-sensor validated `_abck` 2/2 on a fresh VPS IP; after
~10 repeated solves the same IP stopped validating (reputation decay). The sensor
payload was byte-identical — the IP, not the engine, is what changed.

**Fix:** residential/mobile egress + rotation. No engine patch helps.
</details>

<details>
<summary><b>🖥️ Minimal requirements</b></summary>

| | floor | tested |
|---|---|---|
| OS | Linux + Chromium deps / WSL2 | Debian 13 (trixie), Ubuntu WSL2 |
| Python | 3.11 | 3.11.15 (WSL), 3.13.5 (VPS) |
| Node.js | 18 (imperva sidecar) | v22 (WSL), v20 (VPS) |
| RAM | 2 GB | 8 GB |
| CPU | 4 cores | 24 cores (VPS) |
| Disk | 3 GB (+1.5 GB arkose models, optional) | — |
| Display | Xvfb for browser paths on headless boxes | Xvfb |

</details>

<details>
<summary><b>🙏 Credits</b></summary>

- Akamai + Kasada headless sensors: [proofofbots/web-re-toolkit](https://github.com/proofofbots/web-re-toolkit) (MIT)
- GeeTest v4: [xKiian/GeekedTest](https://github.com/xKiian/GeekedTest) (MIT)
- GeeTest v3 binding: [Amorter/biliTicker_gt](https://github.com/Amorter/biliTicker_gt) (AGPL-3.0)
- Imperva session: [BottingRocks/Incapsula](https://github.com/BottingRocks/Incapsula)
- Tencent 腾讯防水墙: [2185359703/reserver](https://github.com/2185359703/reserver) (MIT)
- x5sec slider: [verssache/VerzSolver](https://github.com/verssache/VerzSolver) (MIT)
- VK CTC OCR: [DedInc/vk_captchasolver](https://github.com/DedInc/vk_captchasolver) (MIT)
- Binance slider: [xKiian/binance-captcha-solver](https://github.com/xKiian/binance-captcha-solver) (MIT)
- Steam: [scholtzm/opencv-steam-captcha](https://github.com/scholtzm/opencv-steam-captcha) (MIT)
- Arkose ONNX models: [funcaptcha-challenger](https://huggingface.co/itsgowtham/funcaptcha-challenger) via community mirror
- Image OCR: [sml2h3/ddddocr](https://github.com/sml2h3/ddddocr) (MIT)
- TLS replay layer: [lexiforest/curl_cffi](https://github.com/lexiforest/curl_cffi)
- Related: [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr), [sarperavci/GoogleRecaptchaBypass](https://github.com/sarperavci/GoogleRecaptchaBypass)
</details>

## 🔧 Recent fixes

- `tencent` used a dead API host (`https://t.captcha.qq.com` now returns 403),
  so `do_prehandle` failed with "prehandle 响应解析失败". Switched to the live
  host `https://turing.captcha.qcloud.com` (200, valid JSONP).
- Vision model was `mistral-medium-latest`, which returns HTTP 429 for every
  key in the pool — so all vision types (`grid`/`coordinates`/`draw_around`/
  `drag_drop`/`bounding_box`) and the recaptcha/hcaptcha image classifiers
  failed with "no valid result from vision". Switched to `pixtral-12b-2409`
  (200 for every key, correct answers).
- `vaptcha` ignored the documented `vid` field — `SolveRequest` had no `vid`
  field and the dispatch read `req.sitekey` only, so `vid` was silently
  dropped. Added the field and read `req.vid or req.sitekey`.
- `hcaptcha_enterprise` / `cloudflare_challenge` returned HTTP 500 when `url`
  was omitted (they were in the self-URL list, so the generic guard skipped
  them and playwright raised `Frame.goto() missing 1 required positional
  argument`). They now return 400 with a clear message.
- `yidun` ignored the documented `captcha_id` field — the dispatch read the
  captchaId from `sitekey` only, so a request with `captcha_id` failed with
  "captcha_id or url is required". Now reads `captcha_id or sitekey`.
- `cap` / `anubis` returned HTTP 500 — `SolveResponse.token` only accepted
  `str | dict`, but `cap` returns a **list of int** and `anubis`/`goaway` return
  an **int nonce** (`ResponseValidationError`). The field now accepts
  `str | int | float | dict | list`.
- `cap` — `NameError: name 'json' is not defined` on the challenge-format guard
  (missing `import json`); the PoW now returns solutions.
- `friendly` — missing `import cloakbrowser` (and `json`), which made the browser
  path raise `NameError` instead of solving.
- `steam` — the shipped `selftest.py` crashed on `hist[50]` (cv2 returns a
  `(256,1)` histogram, so indexing gives an array, not a scalar); it now uses
  the `_hist_val` helper. 45/45 checks pass.
- `image_b64` accepts a `data:image/png;base64,…` prefix (stripped server-side).
- `shumei` mis-detected its API base — any host containing "shumei" was treated
  as the captcha API host, so the official trial page (www.ishumei.com) was used
  as the base and the solver parsed the marketing HTML. Only `fengkongcloud`
  hosts (or a `/ca/` path) count now.
- `shumei` no longer returns HTTP 500 — numpy scalars in the result are normalized
  before serialization (`np.int32` etc. broke pydantic).
- `tspd` now requires `url` up-front instead of failing deep in the node sidecar.
- ONNX models moved to Hugging Face (`VeyraAgent/cf-solver-models`) to keep the
  clone light; `scripts/fetch_models.sh` restores them.
- `run.sh` auto-starts under Xvfb, reads `$PORT`, and refuses a busy port;
  `install.sh` installs chromium + Xvfb + node + the sidecar + deps.
- **WSL gotcha:** WSLg sets `$DISPLAY`, but a chromium launched as a subprocess
  fails X auth on it (`Authorization required, but no authorization protocol
  specified` → `TargetClosedError`, HTTP 500). `run.sh` therefore prefers
  `xvfb-run` whenever it exists instead of trusting `$DISPLAY`.

## ✅ Verification

Every type was exercised against a live target. `✅ verified` means a real
token or cookie came back — not merely "no HTTP 500":

| type | evidence |
|---|---|
| `recaptcha` | 40+ char token from the official v2 demo |
| `hcaptcha`, `hcaptcha_enterprise` | token from the official test key |
| `turnstile` | token from the public test key |
| `cloudflare`, `cloudflare_challenge` | `cf_clearance` cookie |
| `datadome` | `datadome` cookie |
| `imperva`, `tspd` | Incapsula session token |
| `perimeterx` | `_px3` cookie |
| `yandex` | SmartCaptcha token |
| `geetest` | v4 `captcha_output` + `pass_token` |
| `shumei` | click captcha solved on the official trial org (riskLevel PASS) |
| `anubis`, `rotate` | result token / angle |
| `image_to_text`, `cap`, `mcaptcha`, `goaway`, `altcha` | OCR text / PoW solution |
| `zhihu` | solved a live zhihu.com captcha image |
| `yidun` | NetEase slider solved end-to-end (demo captchaId, gap_x=163) |
| `mtcaptcha` | `vt` token from MTCaptcha's public demo sitekey |
| `cerberus` | blake3 PoW solved from a generated challenge |
| `steam`, `yidun`, `dingxiang`, `douyin`, `cerberus`, `captchafox`, `recaptcha_audio` | shipped self-tests pass (45/5/30/27/35/5/31 checks) |

The remaining `⚙️ needs input` types are complete but require a real sitekey,
URL, or image from the target site — those values cannot be invented.


## 🛡️ Security

- SSRF guard on by default (`url` → private/loopback rejected `400`)
- `/solve` navigates caller-supplied URLs — treat tokens as capabilities
- For public exposure: reverse-proxy + token-gate `/solve` `/status` `/logs`

## 📄 License

MIT
