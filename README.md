<div align="center">

# 🔐 CF-Solver

**Self-hosted captcha-solving sidecar — 22 challenge types, zero per-solve cost.**

One FastAPI process. One JSON call. Tokens in seconds.

![types](https://img.shields.io/badge/types-22-blueviolet) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![no--docker](https://img.shields.io/badge/docker-not--needed-9cf)

**[⚡ Quick start](#-quick-start) · [🧩 Types](#-supported-types) · [📡 API](#-api)**

</div>


> [!IMPORTANT]
> **Educational purposes only.** CF-Solver was built to learn how anti-bot and
> challenge systems (Cloudflare, Akamai, DataDome, reCAPTCHA, GeeTest, Arkose, etc.)
> work under the hood — and to test defenses on your own infrastructure.
> Do not use it for anything illegal, to abuse third-party services, or to violate
> any platform's terms of service. You are solely responsible for how you use it.

---

## ⚡ Quick start

```bash
git clone https://github.com/VeyraAgent/cf-solver && cd cf-solver
./install.sh          # venv + deps + node sidecar (arkose models = optional prompt)
./run.sh              # FastAPI on :8877
```

```bash
curl -s http://localhost:8877/health
```

## 🧩 Supported types — 22

| | type | result | stability |
|---|---|---|---|
| 🟢 | `cloudflare` | `cf_clearance` cookie + UA | stable |
| 🟢 | `turnstile` | widget token | stable |
| 🟢 | `recaptcha` v2 / v3 / Enterprise | `token` | stable |
| 🟢 | `hcaptcha` | `token` | stable |
| 🟢 | `awswaf` | `aws-waf-token` cookie | stable |
| 🟢 | `botguard` | Google `bgRequest` token | stable |
| 🟢 | `perimeterx` | `_px3` cookie (press-hold) | stable |
| 🟢 | `aliyun` | Aliyun slide `{certifyId, deviceToken, data}` | stable |
| 🟢 | `geetest` v4 | `captcha_output` + `pass_token` | stable |
| 🟢 | `geetest_v3` | `validate` + `seccode` | stable (py3.12+ Linux) |
| 🟢 | `tencent` 腾讯防水墙 | `ticket` + `randstr` | stable |
| 🟢 | `mtcaptcha` | `vt` token | stable* |
| 🟢 | `altcha` | PoW payload | stable |
| 🟢 | `imperva` | `visid_incap_*` + `incap_ses_*` cookies | stable |
| 🟢 | `image_to_text` | OCR text | stable |
| 🟡 | `akamai` | `_abck` cookie | IP-sensitive¹ |
| 🟡 | `datadome` | `datadome` cookie | IP-sensitive¹ |
| 🟡 | `kasada` | `x-kpsdk-ct` headers | needs classic ips.js site² |
| 🟡 | `cybersiara` | JWT token | needs current MasterUrlId² |
| 🟡 | `x5sec` | `x5sec` cookie | needs live punish URL² |
| 🔴 | `arkose` | `fc_token` | needs ONNX models³ |

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
```

</details>

<details>
<summary><b>📡 API reference</b></summary>

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | public | liveness + supported types |
| GET | `/status` | token | running tasks |
| GET | `/logs` | token | last N solve events |
| POST | `/solve` | token | solve (dispatch by `type`) |
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
| `TURNSTILE_GEOIP` | unset | `1` = align tz/locale to proxy exit IP |

Proxy per-request only: `"proxy": "http://user:pass@host:port"`.
`cf_clearance` replays require the same IP + UA + TLS fingerprint (curl_cffi).

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
| OS | Linux + Chromium deps / WSL2 | Debian 13, Ubuntu WSL2 |
| Python | 3.11 | 3.11.15 (WSL), 3.13.5 (VPS) |
| Node.js | 18 (imperva sidecar) | v20 / v22 |
| RAM | 2 GB | 8 GB |
| CPU | 4 cores | 12 / 24 cores |
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
- Arkose ONNX models: [funcaptcha-challenger](https://huggingface.co/itsgowtham/funcaptcha-challenger) via community mirror
- Image OCR: [sml2h3/ddddocr](https://github.com/sml2h3/ddddocr) (MIT)
- TLS replay layer: [lexiforest/curl_cffi](https://github.com/lexiforest/curl_cffi)
- Related: [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr), [sarperavci/GoogleRecaptchaBypass](https://github.com/sarperavci/GoogleRecaptchaBypass)
</details>

## Security

- SSRF guard on by default (`url` → private/loopback rejected `400`)
- `/solve` navigates caller-supplied URLs — treat tokens as capabilities
- For public exposure: reverse-proxy + token-gate `/solve` `/status` `/logs`

## License

MIT
