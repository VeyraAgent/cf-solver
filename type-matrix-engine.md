# cf-solver type coverage matrix

engine: `https://tumbalkuncuy--cf-solver-solver.us-east.modal.run`  
api: `https://tumbalkuncuy--veyracaptcha-api.modal.run`

| type | layer | verdict | note |
|---|---|---|---|
| turnstile | engine | **SOLVED** | route |
| recaptcha | engine | **SOLVED** | checkbox-no-challenge |
| hcaptcha | engine | **SOLVED** | route |
| hcaptcha_enterprise | engine | **SOLVED** | real-page |
| cloudflare | engine | **SOLVED** | interstitial |
| cloudflare_challenge | engine | **SOLVED** | interstitial |
| awswaf | engine | **DISPATCHED** | page has no silent AWS WAF challenge (no token.js/challenge.js) — not a valid silent-WAF target; pass a real WAF-protect |
| datadome | engine | **DISPATCHED** | no datadome cookie (endpoint_status=None); DataDome may have returned a challenge — check raw_json |
| perimeterx | engine | **DISPATCHED** | PerimeterX press-hold gate never rendered (silent-pass, wrong render_flow, or flow changed) — no token to harvest |
| akamai | engine | **DISPATCHED** | unsupported in akamai.solve: https://example.com names no Akamai sensor script |
| botguard | engine | **SOLVED** | route-intercept |
| geetest | engine | **SOLVED** | geetest-v4-slide |
| geetest_v3 | engine | **DISPATCHED** | No module named 'bili_ticket_gt_python' |
| tencent | engine | **DISPATCHED** | 重试 5 次后仍未通过 |
| mtcaptcha | engine | **DISPATCHED** | failed after 3 attempts: getchallenge code=1410 |
| altcha | engine | **DISPATCHED** | url (challenge endpoint) or challenge JSON is required |
| friendly | engine | **SOLVED** | pow-blake2b |
| arkose | engine | **REJECTED** | public_key is required for type=arkose |
| kasada | engine | **DISPATCHED** | bad_input in kasada.solve: no url, pass one or set page_url in the config |
| imperva | engine | **DISPATCHED** |  |
| x5sec | engine | **DISPATCHED** | no_slider: target never rendered a baxia/NC slider (attempts=3) diag={'title': 'Example Domain', 'url': 'https://example |
| yandex | engine | **DISPATCHED** | no SmartCaptcha widget found on page (no checkbox iframe, no rendered widget) — page may lazy-load it behind an interact |
| image_to_text | engine | **DISPATCHED** | all OCR engines returned empty |
| recaptcha_audio | engine | **DISPATCHED** | no ASR backend available — pip install faster-whisper (recommended, no ffmpeg) or openai-whisper |
| steam | engine | **DISPATCHED** | steam: segmentation produced 0 characters |
| vk | engine | **SOLVED** | ctc-ocr |
| zhihu | engine | **DISPATCHED** | zhihu: no text recognized |
| binance | engine | **DISPATCHED** | failed to decode image |
| basilisk | engine | **DISPATCHED** | basilisk: pass (site_key + site_domain) for live flow, or (slide_bg_b64 + slide_piece_b64 + slide_y) for offline solve,  |
| captchafox | engine | **DISPATCHED** | captchafox: provide image_b64 or image_url (background) |
| tspd | engine | **DISPATCHED** |  |
| yidun | engine | **DISPATCHED** | could not extract captchaId from page source |
| dingxiang | engine | **DISPATCHED** | no ac provider configured: the v5 /api/v1 endpoint requires the SDK-generated encrypted 'ac' blob (greenseer family — se |
| shumei | engine | **SOLVED** | spatial-hsv |
| douyin | engine | **DISPATCHED** | captcha/get rejected: {'code': 502, 'data': None, 'message': '网络故障，请稍后重试[5014]'} |
| vaptcha | engine | **DISPATCHED** | vid is required (or server+knock for direct mode) |
| procaptcha | engine | **REJECTED** | sitekey is required for type=procaptcha |
| cybersiara | engine | **DISPATCHED** | rate limited by CyberSiara (HttpStatusCode 400) |
| aliyun | engine | **REJECTED** | scene_id and prefix are required for type=aliyun |
| cap | engine | **DISPATCHED** | cap: pass challenge_json, or base_url + site_key (self-hosted Cap backend has no global endpoint) |
| mcaptcha | engine | **DISPATCHED** | mcaptcha: pass challenge_json, or base_url + site_key (mCaptcha is self-hosted — sitekeys are per-deployment) |
| anubis | engine | **DISPATCHED** | anubis: make-challenge failed: JSONDecodeError: Expecting value: line 1 column 1 (char 0) |
| goaway | engine | **DISPATCHED** | goaway: pass challenge (64 hex) + difficulty, or challenge_json |
| cerberus | engine | **DISPATCHED** | cerberus: pass challenge/difficulty/nonce/ts/signature or challenge_json with those fields |
| rotate | engine | **SOLVED** | cnn-angle |
| grid | engine | **DISPATCHED** | recognition: no valid result from vision (raw: ) |
| coordinates | engine | **DISPATCHED** | recognition: no valid result from vision (raw: ) |
| draw_around | engine | **DISPATCHED** | recognition: no valid result from vision (raw: ) |
| drag_drop | engine | **DISPATCHED** | recognition: no valid result from vision (raw: ) |
| bounding_box | engine | **DISPATCHED** | recognition: no valid result from vision (raw: ) |

## tally

- DISPATCHED: 35
- SOLVED: 12
- REJECTED: 3