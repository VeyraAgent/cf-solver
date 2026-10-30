# `tools/jb/` — source material from the 2026-10-31 solver drop

Not imported at runtime. Kept because each folder carries knowledge that is expensive to
re-derive (measured quirks, working selectors, prompts) or a server variant we deliberately
did not wire in.

| folder / file | what it is | where its capability landed |
|---|---|---|
| `nopecha-solver.py` | Turnstile token extractor for the NopeCHA demo page (patchright headed, managed-challenge triage, `siteverify`). Not a general solver — a harness with honest exit codes (0 token, 2 no token, 3 blocked). | not wired: `turnstile` already covers the general case |
| `hcaptcha_grid/` | hCaptcha grid solver (`scripts/hcaptcha_solver.mjs`): Camoufox + wreq-js + one VLM call per grid, plus the WebGL/iframe-click/2-page pitfalls. | not wired: needs `camoufox-js` + `wreq-js` (~660 MB) and a VLM endpoint; `hcaptcha` already solves grids via CloakBrowser + vision |
| `captcha_solver_skill/` | Skill pack: `solve_local.py` / `solve_api.py` / `preprocess.py` / `train_ocr.py` + extend/token-flow references. | `ocr` (model + engine), rest is reference for training a private CTC model |
| `aliyun_traceless_ref/` | The TRACELESS skill: Camoufox recipe, `why-emulators-fail`, `http2-pitfall`, `wreq-js-combo`, and the reference solver script. | `solvers/aliyun_traceless` (ported, parameterised) |
| `awswaf_server/` | Original AWS WAF solver **with** its HTTP server + web GUI (`waf_solver.js`, port 3090). | `solvers/awswaf_js` vendored the solver core only and dropped the server |
| `mtcaptcha_ref/` | Standalone MTCaptcha client (`challenge.py`, `pow_solver.py`, `kee_simulator.py`, `ocr_solver.py`) + a captured widget JS dump. | not wired: `solvers/mtcaptcha` is already pure-HTTP with a local OCR path and a verified `vt` token |
| `tcaptcha_README.md` | The toolkit's own README (pipeline, artifacts, hard-won lessons) — the long version of `solvers/tcaptcha/README.md`. | `solvers/tcaptcha` |

Dropped on intake: the reference `OCR/` directory (its engine + `common.onnx` are already
committed under `solvers/ocr/`) and every `__pycache__`.

Nothing here is executed by `server.py`; delete a folder freely if the knowledge is no
longer needed.
