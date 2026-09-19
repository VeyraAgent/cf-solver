# Basilisk Captcha Solver

Solves Basilisk captcha (basiliskcaptcha.com) — a two-phase challenge requiring both a slide puzzle and icon-click step to get a token.

## Captcha Type

**Basilisk** — visual slide + icon ordering captcha.

### Phase 1: Slide Puzzle
Background image with a cut-out hole + a puzzle piece. The piece texture is removed (can't do simple template matching), but the hole outline remains visible. Solver uses **Sobel edge detection** + **normalized cross-correlation (NCC)** to match the piece's outline against edges in the background, finding the correct x-offset.

### Phase 2: Icon Click
Three colored icons (star=cyan, calendar=blue, buy=teal) on a background. Solver detects each icon by:
1. **Color distance** — pixel-to-target Euclidean distance in RGB, with mutual exclusion (closest-of-3 assignment)
2. **Edge weighting** — Sobel gradient magnitude to distinguish icons from similar-colored backgrounds (e.g., blue calendar vs blue sky)
3. **FFT convolution** — disk kernel density estimation to find the center of each icon cluster

Both phases require **human-like mouse trails**:
- Slide: cosine-eased movement with jitter + occasional pauses
- Icons: recorded mouse path template warped to actual icon positions

## Solver Approach

Pure Python + NumPy/SciPy image processing. No ML models, no browser automation for the core solving. The HTTP client uses `curl_cffi` (Chrome impersonation) since the Basilisk API blocks plain Python requests.

## Modes

| Mode | Parameters | Output |
|------|-----------|--------|
| **Live HTTP** | `site_key`, `site_domain` | Final `captcha_response` token |
| **Offline full** | `slide_bg_b64`, `slide_piece_b64`, `slide_y`, `icons_bg_b64`, `icons_order` | Slide x + icon coordinates |
| **Slide-only** | `image_b64`, `slide_piece_b64`, `slide_y` | Slide x offset |

## Entry Point

```python
await solve_basilisk(
    image_b64=None,          # base64 slide background (Mode C)
    url=None,                # unused, kept for interface compat
    site_key=None,           # basilisk site key (Mode B)
    site_domain=None,        # basilisk site domain (Mode B)
    slide_bg_b64=None,       # base64 slide background (Mode A)
    slide_piece_b64=None,    # base64 puzzle piece (Mode A/C)
    slide_y=None,            # y-offset of the slide (required)
    icons_bg_b64=None,       # base64 icons background (Mode A)
    icons_order=None,        # list of icon names (Mode A)
    proxy=None,              # HTTP proxy for live flow
    timeout_s=60,
)
# Returns: {solved, type:"basilisk", token, method, elapsed, error, ...}
```

## Dependencies

- numpy, pillow, scipy (image processing)
- curl_cffi (HTTP client with Chrome impersonation, live mode only)

## Performance

~20-40ms for the image processing pipeline (slide + icons). The HTTP flow adds network latency.

## Data Files

- `_icons_trail_template.json` — recorded human mouse trail for icon clicks (warped to target positions)

## Reference

Ported from [aqelionie/basilisk-captcha-solver](https://github.com/aqelionie/basilisk-captcha-solver) (MIT).
