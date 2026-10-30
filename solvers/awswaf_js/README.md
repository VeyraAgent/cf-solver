# `awswaf_js` — AWS WAF challenge, solved in Node (no browser)

`solvers/awswaf` drives a real browser and waits for the silent challenge to set
`aws-waf-token`. This module is the **protocol port**: it never starts Chromium, so it is
~0.8 s per token instead of ~10–20 s, and it keeps working from IPs where the browser
path gets a CloudFront error page.

## Layout

```
waf_core.js      the challenge client (WafSolver/WafSignal/WafMetrics/WafCrypto/HttpClient)
run_once.js      CLI bridge: one solve, one JSON line on stdout
node_modules/    http(s)-proxy-agent + deps, vendored (no npm install at runtime)
solve.py         async wrapper: subprocess + JSON parse + uniform result dict
```

`waf_core.js` is the reference solver (`@Real_zezomod`) split so the HTTP server half is
dropped and `module.exports` is added. `solve.py` runs it per request so a WAF stall can
never wedge the FastAPI worker.

## What it does

1. Fetch the protected URL → discover the challenge base (`*.token.awswaf.com`,
   same-origin `__challenge_*`, or a linked JS bundle) + `window.gokuProps` and
   `awsWafCookieDomainList`.
2. `GET {chal}/inputs?client=browser` → challenge blob, `challenge_type`, `difficulty`.
3. Build the `Zoey` telemetry signal (canvas histogram, GPU pool, capabilities, math),
   CRC32 it, AES-256-GCM encrypt with the WAF key.
4. Solve: SHA256 hashcash (`verify`) or the `mp_verify` bandwidth multipart.
5. POST `/{verify|mp_verify}`, retry 400/429 with a fresh signal, twice round-trip, return
   the token.

## Request

```json
{"type": "awswaf_js", "url": "https://target/login"}
```

Optional: `proxy` (the token is IP+UA bound — replay from the same egress and keep the
`proxy_used` echo), `challenge` (paste the challenge base URL when auto-discovery can't
see it through an obfuscated SPA), `user_agent`.

## Verified

Live: `aws-waf-token` from a WAF-protected login page in ~0.8 s, `waf_elapsed=0.70`.

## Limits

- Bound to the UA/IP that fetched it. A token minted through a proxy must be replayed
  through the same proxy.
- The interactive visual CAPTCHA (grid/carousel) is a different challenge type — this
  solver answers the silent JS/PoW challenge only.
