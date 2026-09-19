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

**[⚡ Quick start](#-quick-start) · [🧩 Types](#-supported-types--50) · [📡 API](#-api)**

</div>

---

## ⚡ Quick start

```bash
git clone https://github.com/VeyraAgent/cf-solver && cd cf-solver
./install.sh          # deps + chromium + xvfb + node + ONNX models (arkose = optional)
./run.sh              # FastAPI on :8877
```

```bash
curl -s http://localhost:8877/health
```

Headless box? Browser-backed types need a display:

```bash
PORT=8877 xvfb-run -a -s "-screen 0 1920x1080x24" .venv/bin/python3 server.py
```

## 🧩 Supported types — 50

### Browser-backed

| | type | result | notes |
|---|---|---|---|
| 🟢 | `cloudflare` | `cf_clearance` cookie + UA | full interstitial |
| 🟢 | `cloudflare_challenge` | `cf_clearance` cookie | alias path for the challenge endpoint |
| 🟢 | `turnstile` | widget token | any sitekey, 3 solving modes |
| 🟢 | `recaptcha` v2 / v3 / Enterprise | `token` | browser + ONNX tile classifier |
| 🟢 | `recaptcha_audio` | transcribed text | pure transcription, no browser |
| 🟢 | `hcaptcha` | `token` | checkbox + vision challenge path |
| 🟢 | `hcaptcha_enterprise` | `token` | enterprise rqdata flow |
| 🟢 | `awswaf` | `aws-waf-token` cookie | navigates the real URL |
| 🟢 | `botguard` | Google `bgRequest` token | runs the real anti-bot VM |
| 🟢 | `perimeterx` | `_px3` cookie | HUMAN press-&-hold |
| 🟢 | `imperva` | `visid_incap_*` + `incap_ses_*` | Incapsula session |
| 🟢 | `aliyun` | `{certifyId, deviceToken, data}` | slide puzzle (Qoder et al) |
| 🟢 | `geetest` v4 | `captcha_output` + `pass_token` | slide |
| 🟢 | `geetest_v3` | `validate` + `seccode` | py3.12+ Linux |
| 🟢 | `tencent` 腾讯防水墙 | `ticket` + `randstr` | pure-HTTP |
| 🟢 | `mtcaptcha` | `vt` token | stable* |
| 🟢 | `altcha` | PoW payload | |
| 🟢 | `image_to_text` | OCR text | ddddocr; accepts raw base64 or data-URI |
| 🟢 | `yandex` | Yandex SmartCaptcha token | |
| 🟢 | `vk` | solved text | CTC OCR, ships own ONNX (1.1 MB) |
| 🟢 | `binance` | slide result | full protocol + XOR + Bezier biometrics |
| 🟢 | `captchafox` | slide result | encryption protocol |
| 🟢 | `basilisk` | slide + icon-click | two-phase |
| 🟢 | `steam` | solved text | port of opencv-steam-captcha |
| 🟢 | `zhihu` | 4-char text | legacy alphanumeric |
| 🟢 | `rotate` | rotation angle | ships ONNX model |
| 🟡 | `akamai` | `_abck` cookie | IP-sensitive¹ |
| 🟡 | `datadome` | `datadome` cookie | IP-sensitive¹ |
| 🟡 | `kasada` | `x-kpsdk-ct` headers | needs classic ips.js site² |
| 🟡 | `cybersiara` | JWT token | needs current MasterUrlId² |
| 🟡 | `x5sec` | `x5sec` cookie | needs live punish URL² |
| 🟡 | `friendly` | challenge token | needs a live target² |
| 🔴 | `arkose` | `fc_token` | needs ONNX models³ |

### Pure-HTTP / PoW / no-browser

| | type | result |
|---|---|---|
| 🟢 | `cap` | PoW solution |
| 🟢 | `anubis` | PoW solution |
| 🟢 | `mcaptcha` | PoW solution |
| 🟢 | `procaptcha` | PoW solution |
| 🟢 | `goaway` | PoW solution |
| 🟢 | `tspd` | F5/DDoS cookie | needs `url` (delegates to the imperva session path) |
| 🟢 | `cerberus` | challenge solution (blake3) |
| 🟢 | `yidun` | NetEase slider (v3 protocol 2.28.5) |
| 🟢 | `dingxiang` | Dingxiang v5 slider |
| 🟢 | `shumei` | Shumei click captcha |
| 🟢 | `douyin` | ByteDance slide puzzle |
| 🟢 | `vaptcha` | Vaptcha V4 gesture |
| 🟢 | `grid` | grid selection |
| 🟢 | `coordinates` | click coordinates |
| 🟢 | `draw_around` | draw-around captcha |
| 🟢 | `drag_drop` | drag & drop |
| 🟢 | `bounding_box` | bounding-box selection |

**🟢 stable — tested working on both WSL (residential) & VPS (datacenter)**
**🟡 situational — works, but depends on IP reputation / target**
**🔴 needs setup — see install notes**

<sub>¹ Akamai & DataDome score the IP independently of the browser — residential/mobile egress required. <a href="#-why-ip-matters">why ↓</a><br>² needs a matching live target. <br>³ 24 ONNX models (~1.4GB) via <a href="#-arkose-models">one command ↓</a></sub>

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

## Recent fixes

- `image_b64` accepts a `data:image/png;base64,…` prefix (stripped server-side).
- `shumei` no longer returns HTTP 500 — numpy scalars in the result are normalized
  before serialization (`np.int32` etc. broke pydantic).
- `tspd` now requires `url` up-front instead of crashing deep in the node sidecar.
- ONNX models moved to Hugging Face (`VeyraAgent/cf-solver-models`) to keep the
  clone light; `scripts/fetch_models.sh` restores them.
- `run.sh` auto-starts under Xvfb, reads `$PORT`, and refuses a busy port;
  `install.sh` installs chromium + Xvfb + node + the sidecar + deps.
- **WSL gotcha:** WSLg sets `$DISPLAY`, but a chromium launched as a subprocess
  fails X auth on it (`Authorization required, but no authorization protocol
  specified` → `TargetClosedError`, HTTP 500). `run.sh` therefore prefers
  `xvfb-run` whenever it exists instead of trusting `$DISPLAY`.

## Security

- SSRF guard on by default (`url` → private/loopback rejected `400`)
- `/solve` navigates caller-supplied URLs — treat tokens as capabilities
- For public exposure: reverse-proxy + token-gate `/solve` `/status` `/logs`

## License

MIT
