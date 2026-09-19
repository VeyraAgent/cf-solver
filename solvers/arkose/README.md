# Arkose FunCaptcha Solver

Solves Arkose Labs FunCaptcha visual puzzles via browser-driven challenge interception + ONNX image classification. Multi-wave pipeline — handles challenges with multiple rounds (typically 5-10 waves).

## How It Works

```
caller → POST /solve {type:"arkose", public_key, page_url}
  │
  ▼
CloakBrowser navigates to page_url
  │ triggers Arkose widget via public_key
  ▼
Intercept /fc/gfct/ response (context-level route)
  │ extract: game_token, session_token, challenge images, variant
  ▼
For each wave (up to max_waves):
  │  1. Download challenge image from gfct response
  │  2. ONNX predict → answer index
  │  3. Encrypt answer: AES-CBC(session_token, [{index:N}])
  │     Output: CryptoJS JSON format {"ct":"base64","iv":"hex","s":"hex"}
  │  4. Send /fc/a/ actions (game loaded, verify clicked)
  │  5. POST /fc/ca/ → submit encrypted guess
  │  6. Parse response → next wave or solved
  ▼
Return fc_token on success
```

## Request

```json
{
  "type": "arkose",
  "public_key": "A0DE7B75-1138-44F2-B132-ED188CEB66F3",
  "page_url": "https://login.databricks.com/login",
  "surl": "https://client-api.arkoselabs.com",
  "max_waves": 10,
  "timeout_s": 120
}
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `public_key` | yes | — | Arkose site public key |
| `page_url` | yes | — | Page that triggers the Arkose widget |
| `surl` | no | auto-detected | Arkose service URL (from gfct domain) |
| `max_waves` | no | 10 | Max challenge rounds before giving up |
| `timeout_s` | no | 120 | Overall timeout |

## Response

```json
{
  "type": "arkose",
  "solved": true,
  "token": "{session_hex}.{challenge_id}|r=ap-southeast-1|...",
  "variant": "conveyor",
  "waves_completed": 7,
  "elapsed": 45.2
}
```

On failure: `solved: false` with `error` field describing what went wrong.

## ONNX Models

24 pre-trained models in `models/` (~1.4GB total), sourced from [funcaptcha-challenger](https://huggingface.co/itsgowtham/funcaptcha-challenger). Each model handles a specific Arkose variant:

| Model | Variant |
|-------|---------|
| `conveyor.onnx` | Conveyor belt sorting |
| `coordinatesmatch.onnx` / `_cv.onnx` | Coordinate matching |
| `penguins.onnx` / `penguin.onnx` / `penguins-icon.onnx` | Penguin puzzles |
| `rockstack.onnx` / `rockstack_v2.onnx` | Rock stacking |
| `counting.onnx` | Object counting |
| `dice_pair.onnx` / `dicematch.onnx` | Dice matching |
| `shadows.onnx` | Shadow matching |
| `3d_rollball_objects_v2.onnx` / `_cv.onnx` | 3D roll ball |
| `frankenhead.onnx` | Frankenhead puzzle |
| `BrokenJigsawbrokenjigsaw_swap.onnx` | Jigsaw swap |
| `card.onnx` / `cardistance.onnx` | Card puzzles |
| `hopscotch_highsec.onnx` | Hopscotch |
| `hand_number_puzzle.onnx` | Hand number |
| `knotsCrossesCircle.onnx` | Knots & crosses |
| `unbentobjects.onnx` | Unbent objects |
| `train_coordinates.onnx` / `_cv.onnx` | Train coordinates |

Inference: 4 threads, ~0.1s per prediction, ~10% CPU (4/40 cores).

## Architecture

- `solve.py` — CloakBrowser driver: navigate → intercept gfct → predict → encrypt → submit → multi-wave loop
- `predict.py` — ONNX predictor: loads model by variant name, runs inference, returns answer index
- `models/` — 24 ONNX model files

## Encryption

Answers are encrypted with AES-CBC using the `session_token` from gfct as the key. Output format matches CryptoJS JSON:

```json
{"ct": "<base64 ciphertext>", "iv": "<hex IV>", "s": "<hex salt>"}
```

This format was reverse-engineered from the [Negt-dev/Funcaptcha-Solve-RSA](https://github.com/Negt-dev/Funcaptcha-Solve-RSA) Go solver.

## Limitations

- **Model accuracy**: pre-trained models cover 24 variants but accuracy varies per variant. Some variants may fail on harder waves.
- **Variant coverage**: only 24 of potentially 50+ Arkose variants have models. Unknown variants return an error.
- **Speed**: ~5-15s per wave (browser interaction + ONNX inference). Full solve (7 waves) takes ~45-60s.
- **No BDA bypass**: relies on CloakBrowser's real Chromium fingerprint for BDA encryption. No custom BDA generation.

## Fine-Tuning (Planned)

Use the solver as a data collector — each `"not answered"` response from `/fc/ca/` gives a verified (image, label) pair. Fine-tune YOLOv8n-cls on collected data to improve accuracy per variant.
