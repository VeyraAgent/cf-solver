# `ocr` — offline image-captcha text

Fixed-font captcha → text, fully local. Two interchangeable backends:

| backend | when | notes |
|---|---|---|
| `ddddocr` (project dep) | default | the standard `common` CTC model, ~0.01 s/image, `method: "ddddocr"` |
| vendored ONNX | ddddocr missing | `_onnx_engine.py` + `models/common.onnx` + `models/common.json` (54 MB, same architecture as ddddocr's `common`) |

`models/common.onnx` is **git-ignored** (repo convention: heavy models live outside git).
It is present on the machine where this landed and travels into the Modal image via
`add_local_dir(copy=True)`, but a fresh clone only has the `common.json` charset — copy the
54 MB model in, or just rely on `ddddocr` (the declared dependency).

Both were checked against each other on the same image (`8k9p2`), so the fallback is not a
guess. Note the model is **case-insensitive** (lowercases its output) — for a case-sensitive
target, widen the charset via ddddocr's `set_ranges` or add a per-target post-processor.

## Request

```json
{"type": "ocr", "image_b64": "data:image/png;base64,iVBORw0KG…"}
```

`url` (image URL) works too, and the `body` alias from the 2captcha-shaped API maps to
`image_b64`. Result: `{solved, text, token}` — `text` is the answer, `token` mirrors it so
the generic success predicate sees an artifact.

## When not to use it

Grids, tile picking, rotation and anything with a text instruction needs vision — use
`grid`, `coordinates`, `draw_around`, `bounding_box`, `rotate` or `image_to_text`.

## Verified

Generated image → `8k9p2` via the API (`/solve`), 0.25 s round-trip, both backends agree.
