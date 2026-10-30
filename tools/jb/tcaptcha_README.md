# tcaptcha

Tencent **TCaptcha** slider solver, rebuilt from a measured end-to-end pipeline.

```
host flow  ->  widget attaches  ->  prehandle (ticket + geometry)
          ->  bg/fg challenge images  ->  5 detectors  ->  consensus
          ->  arbiter (LLM or rule)  ->  human-like drag  ->  verify
          ->  risk-aware retry policy
```

No credentials, no proxy list and no API keys live in this repository.
Everything secret comes from the environment or the command line at runtime.

---

## Install / run

Needs the Python env that already carries `playwright`, `opencv-python`,
`numpy` and `pillow`:

```bash
PY=/path/to/venv/bin/python        # e.g. /root/crackTCaptcha/.venv/bin/python
cd tcaptcha
$PY -m tcaptcha env               # print a .env template
$PY -m tcaptcha doctor            # dependencies, browser, credentials, proxy, arbiter
$PY -m tcaptcha solve --arbiter heuristic
```

Chromium is **provisioned on demand**: if Playwright's browser binary is
missing, `ensure_chromium()` runs `python -m playwright install chromium`
automatically (first `doctor` or first `solve`), so a bare env only needs the
Python packages, not a pre-installed browser. That browser download is the
*only* thing this tool fetches on its own - credentials, proxies and the four
captcha assets all come from the environment / runtime.

### Credentials

| variable              | purpose                              |
|-----------------------|--------------------------------------|
| `TCAPTCHA_EMAIL`      | host account e-mail (login flows)    |
| `TCAPTCHA_PASSWORD`   | host account password                |
| `TCAPTCHA_PROXY`      | one proxy url `http://user:pass@host:port` |
| `TCAPTCHA_PROXY_FILE` | file of one proxy per line (up to 4 candidates, health-checked) |
| `TCAPTCHA_OUT`        | artifact directory (default `/tmp/tcaptcha`) |

Or the CLI equivalents: `--email`, `--password`, `--proxy`, `--proxy-file`.
Proxy credentials are redacted to `***:***@host:port` in every log line -
including bare `user:pass@host:port` proxies with no scheme (the old
urlsplit-only rule passed those through untouched) and fatal errors.

Before any browser time is spent, up to **4 proxy candidates are screened**
(`ipify` + `TCaptcha.js` through the proxy), with a ~4s settle between a
passing check and browser spawn. All candidates dead -> the run fails closed
with `no healthy proxy candidate (N tried, ...)` instead of a misleading
`IFRAME NOT FOUND`.

Exit contract of `solve`: `0` solved, `3` attempts exhausted, `4` risk hold
(placeholder), `2` config/transport error; `doctor` returns `0` ready /
`1` not ready. `outcome.json` is only trustworthy together with this run's
`run.log` - stale verdict files are unlinked at startup.

### Subcommands

```
solve      run one solve
doctor     dependency / config / connectivity check
detect     run detectors offline: --bg bg.png --fg fg.png --piece piece.json
classify   classify a saved prehandle response (challenge | placeholder)
env        print a .env template
```

---

## Pipeline pieces

| module | responsibility |
|--------|----------------|
| `config.py` | env/CLI resolution, proxy parsing, **redaction** |
| `browser.py` | launch, proxy context, **captcha-asset cache + route** |
| `network.py` | passive tap: prehandle / verify / bg+fg bytes |
| `flows.py` | site adapters (`minimax-login`, add your own with `@register`) |
| `detect.py` | 5 detectors + 8px clustering consensus |
| `decision.py` | arbiter: `heuristic` rule or `jev` LLM over numbers only |
| `drag.py` | eased, jittered drag with logical->screen scaling |
| `risk.py` | placeholder / errorCode 12 / 50 classification |
| `engine.py` | the loop and its retry policy |

### Detectors

* **ncc** - template match of the piece's opaque RGB onto the background
* **canny** - Canny edges x piece contour
* **contour** - Otsu + dilate + area/size/row filtered blobs
* **dark** - piece shape x relative darkness, ranked by surviving interior texture
* **hole** - interior/ring brightness ratio (evidence only, never overrides)

Consensus: a cluster of >= 2 detectors **and** `dark` agreeing within 8px.
The hole detector used to override the cluster and lost 5/5 (errorCode 50);
3/3 runs without the override passed - so it only reports evidence now.

### Arbiters

* `--arbiter heuristic` - local rule, no network, deterministic
* `--arbiter jev` - LLM arbitration over numeric evidence only (never pixels),
  gated by `--min-probability 0.55`, via the `jev-decide` CLI

---

## Hard-won lessons (encoded in this code)

1. **Chromium drops concurrent proxy CONNECTs.** `curl -x PROXY .../TCaptcha.js`
   returns `200 / 136901` while Chromium through the same proxy gets
   `ERR_EMPTY_RESPONSE` - the widget never boots and the solver reports a
   misleading `IFRAME NOT FOUND`. Fix: prefetch the four static assets and
   `route.fulfill()` them from memory (`browser.py`).
2. **A sync Playwright route handler must be sync.** An `async def` handler in
   the sync API is never awaited: every request hangs and the page never
   renders.
3. **Never block `data.hailuo.ai`.** The host SPA waits on it and the login
   form never appears.
4. **Placeholder prehandle = stop.** `"ticket":""` + `src_2=template/new_placeholder.html`
   means risk-control: the widget renders but never posts a verify. It was
   observed to be sticky across IPs, accounts and UA changes - only time clears
   it, so classify early instead of burning attempts.
5. **errorCode=12 is rate-limit, not a wrong drag.** Back off (default 70s;
   sources: reserver/tds, pj-one) - retrying instantly keeps you inside the
   window.
6. **errorCode=50 is a wrong drag.** Refresh and re-solve.
7. **The widget's refresh button is a no-op for new challenges**: image fetches
   double while the prehandle count stays at 1. Re-enter the host flow.
8. **`resp.body()` is unreliable** for challenge images - re-fetch by URL
   through the page request context.
9. **Shape detectors correlate.** ncc/canny/dark can all lock onto the same
   solid decoy; a pair is necessary, not sufficient.
10. **A dead proxy consumed a whole run.** Screen candidates (egress +
    captcha dependency) before spawning the browser, settle ~4s after a
    passing check, and rotate over up to 4 candidates. Chromium-level
    failures still need lesson 1's prefetch - reachability is necessary,
    not sufficient.
11. **A stale `outcome.json` is a false PASS.** An aborted run leaves the
    previous verdict in `out_dir`; the harness then scores the old result.
    Fix: unlink verdict artifacts at the start of every run (fail closed on
    freshness), and keep the redacted full transcript in `run.log`.
12. **Redaction must be idempotent on both proxy forms.** `scheme://user:pass@`
    redacted fine, but a bare `user:pass@host:port` has no scheme, so
    urlsplit() read `user` as the scheme and returned the credentials
    unchanged. Fix: one shared `BARE_CRED_RE` (password class excludes `/`
    so an already-redacted `scheme://***:***@` can never re-match) used by
    both `config.redact()` and every `log()`/`die()` line.

---

## Output artifacts (`$TCAPTCHA_OUT`)

`outcome.json`, `capture.json`, `detect.json`, `geometry.json`,
`arbiter_request.json`, `arbiter_report.json`, `verify_resp.json`,
`run.log` (redacted, full run transcript),
`bg.png`, `fg.png`, `annotated.png` (piece box + detector guesses + x ruler),
`after.png` / `fail.png`.

## Adding a flow

```python
# tcaptcha/flows.py
@register("my-site")
def my_site(page, cfg, cap, tag="initial"):
    cfg.require_credentials()
    NetworkTap.reset(cap)
    page.goto("https://example.com/login", wait_until="domcontentloaded")
    ...  # reach the point where the captcha is requested
    return wait_for_widget(page, cap, cfg.frame_timeout, tag)
```

Run it with `tcaptcha solve --flow my-site`.
