# cf-solver type coverage matrix

engine: `https://tumbalkuncuy--cf-solver-solver.us-east.modal.run`  
api: `https://tumbalkuncuy--veyracaptcha-api.modal.run`

| type | layer | verdict | note |
|---|---|---|---|
| turnstile | api | **SOLVED** | XXXX.DUMMY.TOKEN.XXXX |
| recaptcha | api | **SOLVED** | 0cAFcWeA6ZQ29vTr1lYXnYpjtVBsS_5hK89FhJsI08LOZARnJPBEdPHVvfbD |
| hcaptcha | api | **SOLVED** | 10000000-aaaa-bbbb-cccc-000000000001 |
| hcaptcha_enterprise | api | **SOLVED** | 10000000-aaaa-bbbb-cccc-000000000001 |
| cloudflare | api | **DISPATCHED** |  |
| cloudflare_challenge | api | **REJECTED** | Unknown task type 'cloudflare_challenge'. Use a 2captcha-compatible type (e.g. TurnstileTaskProxyles |
| awswaf | api | **REJECTED** | all backends failed: primary: page has no silent AWS WAF challenge (no token.js/challenge.js) — not  |
| datadome | api | **TIMEOUT** |  |
| perimeterx | api | **REJECTED** | all backends failed: primary: PerimeterX press-hold gate never rendered (silent-pass, wrong render_f |
| akamai | api | **REJECTED** | all backends failed: primary: unsupported in akamai.solve: https://example.com names no Akamai senso |
| botguard | api | **SOLVED** | 8_Cl8JTNAAYkRIWRBERCZfulAivIcxE7AEABEArZ1Ej02vdB3uMwawoZ29sn |
| geetest | api | **REJECTED** | geetest: captchaId is required — pass it under task.websiteKey |
| geetest_v3 | api | **REJECTED** | Unknown task type 'geetest_v3'. Use a 2captcha-compatible type (e.g. TurnstileTaskProxyless) or a na |
| tencent | api | **SOLVED** | tr03oOF8MA1dp7___E-5HraIzjn5OwQpGeIOKP9s7lObPiLvJMWRXb-m9-By |
| mtcaptcha | api | **REJECTED** | all backends failed: primary: failed after 3 attempts: getchallenge code=1410 |
| altcha | api | **REJECTED** | altcha: websiteURL is required — pass it under task.websiteURL |
| friendly | api | **SOLVED** | db653b0e2c15464d0f9f4cc4f81a5d55.ataB/QdbzRWKY/UQAQwqsAAAAAA |
| arkose | api | **REJECTED** | arkose: websitePublicKey is required — pass it under task.websiteKey |
| kasada | api | **REJECTED** | kasada: websiteURL is required — pass it under task.websiteURL |
| imperva | api | **REJECTED** | all backends failed: primary: solver returned unsolved |
| x5sec | api | **REJECTED** | all backends failed: primary: no_slider: target never rendered a baxia/NC slider (attempts=3) diag={ |
| yandex | api | **REJECTED** | all backends failed: primary: no SmartCaptcha widget found on page (no checkbox iframe, no rendered  |
| image_to_text | api | **REJECTED** | all backends failed: primary: all OCR engines returned empty |
| recaptcha_audio | api | **REJECTED** | all backends failed: primary: audio_url or audio_b64 is required |
| steam | api | **REJECTED** | all backends failed: primary: steam: segmentation produced 0 characters |
| vk | api | **DISPATCHED** |  |
| zhihu | api | **REJECTED** | all backends failed: primary: zhihu: no text recognized |
| binance | api | **REJECTED** | all backends failed: primary: full mode requires biz_id + security_check_response_validate_id when n |
| basilisk | api | **REJECTED** | all backends failed: primary: basilisk: pass (site_key + site_domain) for live flow, or (slide_bg_b6 |
| captchafox | api | **REJECTED** | all backends failed: primary: captchafox: provide image_b64 or image_url (background) |
| tspd | api | **REJECTED** | all backends failed: primary: solver returned unsolved |
| yidun | api | **REJECTED** | all backends failed: primary: could not extract captchaId from page source |
| dingxiang | api | **REJECTED** | all backends failed: primary: no ac provider configured: the v5 /api/v1 endpoint requires the SDK-ge |
| shumei | api | **SOLVED** | 1020f85c920b52bb441222c30d8103fc |
| douyin | api | **REJECTED** | all backends failed: primary: captcha/get rejected: {'code': 502, 'data': None, 'message': '网络故障，请稍后 |
| vaptcha | api | **REJECTED** | all backends failed: primary: vid is required (or server+knock for direct mode) |
| procaptcha | api | **REJECTED** | primary: sitekey is required for type=procaptcha |
| cybersiara | api | **REJECTED** | all backends failed: primary: rate limited by CyberSiara (HttpStatusCode 400) |
| aliyun | api | **REJECTED** | aliyun: sceneId is required — pass it under task.websiteKey |
| cap | api | **REJECTED** | all backends failed: primary: cap: pass challenge_json, or base_url + site_key (self-hosted Cap back |
| mcaptcha | api | **REJECTED** | all backends failed: primary: mcaptcha: pass challenge_json, or base_url + site_key (mCaptcha is sel |
| anubis | api | **REJECTED** | all backends failed: primary: anubis: make-challenge failed: JSONDecodeError: Expecting value: line  |
| goaway | api | **REJECTED** | all backends failed: primary: goaway: pass challenge (64 hex) + difficulty, or challenge_json |
| cerberus | api | **REJECTED** | all backends failed: primary: cerberus: pass challenge/difficulty/nonce/ts/signature or challenge_js |
| rotate | api | **DISPATCHED** |  |
| grid | api | **REJECTED** | all backends failed: primary: recognition: no valid result from vision (raw: ) |
| coordinates | api | **REJECTED** | all backends failed: primary: recognition: no valid result from vision (raw: ) |
| draw_around | api | **REJECTED** | all backends failed: primary: recognition: no valid result from vision (raw: ) |
| drag_drop | api | **REJECTED** | all backends failed: primary: recognition: no valid result from vision (raw: ) |
| bounding_box | api | **REJECTED** | all backends failed: primary: recognition: no valid result from vision (raw: ) |

## tally

- REJECTED: 38
- SOLVED: 8
- DISPATCHED: 3
- TIMEOUT: 1