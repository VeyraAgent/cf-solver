# akamai — Akamai Bot Manager `_abck` clearance harvester

Harvest-only solver for Akamai Bot Manager (same contract as `cloudflare`/`awswaf`):
loads an Akamai-fronted page in CloakBrowser, lets the `bmak` sensor boot and POST
accepted telemetry, then returns the `_abck` clearance cookie + everything needed to
replay it. No account creation, no form filling, no site hardcoded — the caller passes
the URL it wants cleared.

## Request

```json
POST /solve
{
  "type": "akamai",
  "url": "https://www.akamai-fronted-site.com/",  // required — page that arms bmak
  "proxy": "http://user:pass@host:port",          // recommended (residential)
  "timeout_s": 90,                                 // optional
  "pre_actions": [ ... ],                          // optional — steps before harvest
  "post_fetch":  [ ... ]                           // optional — gated calls after clear
}
```

## Response

```json
{
  "type": "akamai",
  "success": true,
  "_abck": { "name": "_abck", "value": "...", "domain": "...", ... },
  "bm_sz": { ... },
  "abck_validated_heuristic": true,
  "sensor_fires": 6,
  "sensor_posts": 3,
  "cookies": [ ... ],          // full jar → build a Cookie header
  "user_agent": "Mozilla/5.0 ...",
  "headers": { "User-Agent": "...", "Accept-Language": "..." },
  "method": "bmak-telemetry-harvest",
  "elapsed": 34.2,
  "warning": "replay only from the same IP + UA + TLS; short TTL; verify with a real replay"
}
```

## How it works (RE notes — see `re-artifacts/akamai/`)

A live browser is **mandatory** — the sensor payload cannot be built offline:
1. the sensor embeds a **server nonce** from the `bm_sz` cookie (challenge-response);
2. it carries **per-call accumulation state**, so offline replay drifts.

Flow: load URL → `bmak` fetches `bm_sz` + arms → drive real input (mouse/scroll/Tab) +
`bmak.get_telemetry()` + a same-site navigation so the sensor POSTs accepted telemetry →
poll `_abck` until it reaches its validated form.

**`_abck` validation heuristic:** a FRESH/unvalidated cookie carries the `~-1~-1~-1~`
sentinel triple; a validated one drops it (middle sentinels become request counters).
We flag `success` on that, but it is **not authoritative** — the only real proof is an
HTTP replay by the caller.

## Replay contract

`_abck` is bound to **IP + JA3/TLS + User-Agent** with a short TTL. Replay ONLY from the
same proxy IP, with the returned `user_agent`, over a matching TLS stack. The solver does
not bundle a replay — it returns the cookie jar + `warning`; the caller replays.

## Status / known limitation

Harvest works; validation is proxy-dependent. On a slow/residential proxy the sensor's
timer-based POST can starve, so the solver nudges with same-site navigations (up to 3x).
If the cookie stays unvalidated it returns `success:false` + `error` — give it more time
or a cleaner residential IP. Some Akamai deployments require a gated resource fetch
before the flip; supply that via `post_fetch`.
