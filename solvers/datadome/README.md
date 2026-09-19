# datadome solver (`type: datadome`)

Harvests a **DataDome clearance cookie** (`datadome=...`) — HARVEST-ONLY, site-agnostic.

## What it solves

DataDome (`js.datadome.co` / `api-js.datadome.co`) is a bot-management vendor used by
many sites. Its `tags.js` builds an encrypted `jspl` payload from live browser signals
+ a per-deploy client key (`ddk` — DataDome's equivalent of a sitekey; **tags.js already
holds it, we don't supply it**) and POSTs it to `api-js.datadome.co/js/`. On a **silent
pass** DataDome returns `{"status":200,"cookie":"datadome=...; Domain=..."}`. That cookie
is the clearance token.

## How this solver works

We let `tags.js` run in a real browser (CloakBrowser) — it builds the byte-accurate
`jspl` payload from live signals for free — and intercept the `api-js.datadome.co/js/`
**response**, parsing the `datadome` cookie out of its JSON body.

## Site-agnostic by design

The solver knows nothing about any specific site. The **caller** passes the
DataDome-fronted `url` (the page/iframe that loads `tags.js`) and, when the real flow is
framed, the matching `referer` so DataDome serves the same config/scoring. Site specifics
(URLs, ddk, referer, origin_page) belong to the caller — **not hardcoded here**.

## Request

```json
{
  "type": "datadome",
  "url": "https://octocaptcha.com/datadome?origin_page=github_signup_redesign",
  "referer": "https://github.com/",
  "proxy": "http://user:pass@ip:port"
}
```

- `url` (**required**): the DataDome-fronted page/iframe that loads `tags.js`. The caller
  builds it, including any site-specific query params (e.g. `origin_page`).
- `referer` (optional): framing Referer so DataDome scores the same config as the real flow.
- `proxy` (recommended): DataDome scores the requesting IP; harvest and replay from the
  same egress IP.
- No `sitekey` (page-level).

## Response (superset of the uniform envelope)

```json
{
  "type": "datadome", "solved": true, "success": true,
  "method": "datadome-silent-pass",
  "datadome_cookie": "<datadome cookie value>",
  "cookie_domain": ".github.com",
  "cookie_max_age": 31536000,
  "endpoint_status": 200,
  "user_agent": "Mozilla/5.0 ...",
  "elapsed": 2.1
}
```

## Replay contract

1. Set `Cookie: datadome=<datadome_cookie>` on the protected request (use the `Domain`
   the caller's site expects, e.g. `.github.com`).
2. The cookie is **IP + UA bound**. Replay ONLY from the same proxy IP with the returned
   `user_agent` (same contract as the cloudflare / awswaf solvers).

## Example: GitHub signup (octocaptcha)

`octocaptcha.com` is GitHub's captcha **broker** (embedded in `/signup` via
`js-octocaptcha-parent`); its current backend is **DataDome v5.8.0** — *not* Arkose
FunCaptcha as older notes assumed (RE'd 2026-07-14, evidence in
`fingerprint-generator/re-artifacts/octocaptcha/`). To harvest for GitHub signup:

```
url     = https://octocaptcha.com/datadome?origin_page=github_signup_redesign
referer = https://github.com/
```

The returned `datadome` cookie IS the octocaptcha-token — set it (Domain `.github.com`)
on the `github.com/signup` request. GitHub's server-echo form fields
(`timestamp_secret` + honeypot) are **not** this solver's job — the caller's
auto-register script scrapes those. (octocaptcha is a broker, not synonymous with
DataDome; if a future backend differs, the caller just points `url` elsewhere.)

## Notes

- Silent pass today; if DataDome escalates to an interactive challenge the response JSON
  carries a `captcha-delivery` URL instead of a cookie — then the visual DataDome path is
  needed. `error` + `raw_json` surface that case.
