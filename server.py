"""Captcha solver HTTP sidecar — unified endpoints."""
import asyncio
import ipaddress
import itertools
import json
import logging
import os
import socket
import sys
import time
from collections import deque
from typing import Any, Optional
from urllib.parse import urlparse

from contextlib import asynccontextmanager
from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("captcha-solver")

_DESCRIPTION = """
Local captcha-solving HTTP sidecar built on **CloakBrowser** (self-hosted anti-detect
Chromium). Solves challenges by driving them in a real browser engine.

**Supported:** Turnstile · reCAPTCHA (v2 / v3 / invisible, incl. Enterprise) · hCaptcha ·
Cloudflare clearance (`cf_clearance` — full-page Managed / JS challenge) ·
AWS WAF (`aws-waf-token` — silent JS challenge).

Dispatch is by the `type` field of `POST /solve`; optional fields select the variant
(`version`, `real_page`, `verify_url`, …). `/health` is public; behind the public
domain every other path needs a Bearer token (enforced at the Caddy layer).

Caller-supplied URLs (`url`, `verify_url`, `page_url`, `post_fetch[].url`) are fetched
from the browser session and are **SSRF-guarded**: private/loopback/link-local targets
are rejected unless `SOLVER_ALLOW_PRIVATE=1`.
"""

_TAGS = [
    {"name": "solve", "description": "Solve a captcha challenge."},
    {"name": "monitoring", "description": "Liveness, current tasks, recent solve log."},
]

# Public base URL shown in the OpenAPI docs (contact + servers dropdown). The repo ships a
# neutral placeholder; the live service injects its real domain at runtime via SOLVER_PUBLIC_URL.
_PUBLIC_URL = os.getenv("SOLVER_PUBLIC_URL", "https://solver.example.com")

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Background proxy-pool health checker (SOLVER_POOL_CHECK_INTERVAL, 0 = off)."""
    from solvers.common.proxypool import background_checker
    task = asyncio.create_task(background_checker())
    yield
    task.cancel()

app = FastAPI(
    title="Captcha Solver",
    description=_DESCRIPTION,
    version="1.1.0",
    openapi_tags=_TAGS,
    lifespan=_lifespan,
    contact={"name": "solver", "url": _PUBLIC_URL},
    servers=[
        {"url": _PUBLIC_URL, "description": "Public (Bearer token required)"},
        {"url": "http://127.0.0.1:8877", "description": "Local (no auth)"},
    ],
    swagger_ui_parameters={
        "docExpansion": "list",
        "persistAuthorization": True,     # keep the Bearer token across reloads
        "tryItOutEnabled": True,
        "displayRequestDuration": True,
        "filter": True,
    },
)

# Non-enforcing Bearer scheme: makes Swagger UI show an Authorize button and forward the
# token on "Try it out". auto_error=False means a missing/malformed token yields None and
# the endpoint proceeds — real enforcement stays at the Caddy layer (public domain only).
_bearer = HTTPBearer(auto_error=False, description="Bearer token (required on the public "
                     "domain; enforced by the reverse proxy). Ignored for local calls.")
SUPPORTED = ["turnstile", "recaptcha", "hcaptcha", "cloudflare", "awswaf", "botguard", "datadome", "perimeterx", "akamai", "aliyun", "arkose", "x5sec", "tencent", "geetest", "kasada", "mtcaptcha", "altcha", "cybersiara", "imperva", "friendly", "geetest_v3", "image_to_text"] + ["cap", "anubis", "mcaptcha", "procaptcha", "rotate", "yidun", "dingxiang", "shumei", "douyin", "vaptcha", "goaway", "recaptcha_audio", "cerberus", "yandex", "hcaptcha_enterprise", "zhihu", "steam", "grid", "coordinates", "draw_around", "drag_drop", "bounding_box", "tspd", "vk", "cloudflare_challenge", "basilisk", "binance", "captchafox"]
# Page-level solvers that harvest a cookie/token from the live page (no sitekey needed).
_PAGE_LEVEL = ("cloudflare", "awswaf", "botguard", "datadome", "perimeterx", "akamai", "x5sec")
# Solvers that supply their own canonical URL (caller need not pass `url`).
# datadome is NOT here: the caller passes the DataDome-fronted url (+ referer) itself.
_SELF_URL = ("botguard", "perimeterx", "aliyun", "arkose", "tencent", "geetest", "kasada", "mtcaptcha", "altcha", "cybersiara", "friendly", "geetest_v3", "image_to_text",
             "cap", "anubis", "mcaptcha", "rotate", "yidun", "dingxiang", "shumei", "douyin", "vaptcha", "goaway", "recaptcha_audio", "cerberus", "yandex", "hcaptcha_enterprise", "zhihu", "steam", "grid", "coordinates", "draw_around", "drag_drop", "bounding_box", "vk", "cloudflare_challenge", "basilisk", "binance", "captchafox")
# Allow private/loopback targets only when explicitly opted in (dev/testing).
_ALLOW_PRIVATE = os.getenv("SOLVER_ALLOW_PRIVATE") == "1"

# ── Monitoring ring buffer ───────────────────────────────────────────
_solve_log = deque(maxlen=100)
# Concurrent solves of different types can run at once (per-type locks), so track
# current tasks by id rather than a single global that they'd clobber.
_solve_current: dict = {}
_task_ids = itertools.count(1)


def _is_solved(result: dict) -> bool:
    """The ONE success predicate for every solver type — the single source of truth for
    the injected `solved` field + logs. Token solvers signal via truthy `token`, realpage
    variants via `verify_success`, page-level cookie solvers via `success`/`cf_clearance`;
    a truthy value in ANY of these = solved.
    """
    return bool(result.get("token") or result.get("cf_clearance")
                or result.get("verify_success") or result.get("success"))


def _log_solve(type_: str, sitekey: Optional[str], url: str, result: dict):
    """Push a solve event to the ring buffer."""
    sitekey = sitekey or ""  # cloudflare has no sitekey
    url = url or ""          # self-hosted solvers (aliyun, botguard, ...) carry no url
    solved = _is_solved(result)
    _solve_log.appendleft({
        "type": type_,
        "sitekey": sitekey[:12] + ("..." if len(sitekey) > 12 else ""),
        "url": url[:60] + ("..." if len(url) > 60 else ""),
        "token": solved,
        "error": result.get("error"),
        "elapsed": result.get("elapsed"),
        "method": result.get("method"),
        "timestamp": time.time(),
        "success": solved and not result.get("error"),
    })


def _assert_public_url(raw: str, field: str):
    """Reject non-http(s) schemes and private/loopback/link-local/reserved hosts.

    Guards the SSRF surface: /solve navigates and fetches caller-supplied URLs from
    the server's browser session (credentials:'include'). ponytail: validate-then-
    fetch has a DNS-rebinding TOCTOU window; add pinned resolution if it matters.
    """
    if not raw:
        return
    u = urlparse(raw)
    if u.scheme not in ("http", "https"):
        raise HTTPException(400, f"{field}: only http/https URLs allowed")
    host = u.hostname
    if not host:
        raise HTTPException(400, f"{field}: URL has no host")
    if _ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(400, f"{field}: host does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise HTTPException(400, f"{field}: private/loopback host blocked")


def _validate_urls(req: "SolveRequest"):
    _assert_public_url(req.url, "url")
    _assert_public_url(req.verify_url, "verify_url")
    _assert_public_url(req.page_url, "page_url")
    for pf in (req.post_fetch or []):
        _assert_public_url(pf.url, "post_fetch.url")


class PreAction(BaseModel):
    """One UI step to run before the captcha appears (real_page mode)."""
    type: str = Field(..., description="click | fill | select | press | wait",
                      examples=["click"])
    selector: Optional[str] = Field(
        None, description="Target selector. Formats: CSS (default), XPath (//…), "
        "text=…, regex=…, role=name[name='…']", examples=["text=Continue with Email"])
    value: Optional[str] = Field(
        None, description="Value for fill/select/press, or seconds for wait")
    timeout: Optional[int] = Field(10000, description="Element wait timeout (ms)")


class PostFetch(BaseModel):
    """An API call fired from the SAME browser session after solving."""
    url: str = Field(..., description="Endpoint to call (SSRF-guarded, same as top-level url)",
                     examples=["https://target.com/api/verify"])
    method: Optional[str] = Field("POST", examples=["POST"])
    body: Optional[dict] = Field(
        None, description="JSON body. Use the literal __TOKEN__ anywhere to inject the "
        "solved token.", examples=[{"token": "__TOKEN__"}])


class SolveRequest(BaseModel):
    # Required
    type: str = Field(..., description="Captcha type — dispatch key.",
                      examples=["turnstile"])
    sitekey: Optional[str] = Field(
        None, description="Site key from the target page. Required for turnstile/recaptcha/"
        "hcaptcha; not used for type=cloudflare (page-level clearance).",
        examples=["0x4AAAAAAA..."])
    url: Optional[str] = Field(None, description="Page the captcha is on (also the intercept origin). "
                     "Required for all types except botguard (which defaults to the Google sign-in page).",
                     examples=["https://target.com"])

    # All-captcha optional
    action: Optional[str] = Field(
        None, description="Turnstile action, or reCAPTCHA v3/invisible action. "
        "For hCaptcha, the literal \"invisible\" selects the invisible-execute path.")
    cdata: Optional[str] = Field(None, description="Turnstile customer data bound into the token.")
    real_page: Optional[bool] = Field(
        False, description="Solve on the live target page (navigate + drive) instead of a stub.")
    timeout_s: Optional[int] = Field(
        60, description="Overall solve deadline (seconds). Enforced server-side; on expiry the "
        "call returns 408 and the browser is released.")
    pre_actions: Optional[list[PreAction]] = Field(None, description="Steps to run before solving (real_page).")
    post_fetch: Optional[list[PostFetch]] = Field(None, description="API calls after solving (real_page).")
    proxy: Optional[str] = Field(
        None, description="Per-request proxy (scheme://user:pass@host:port). Honored for ALL "
        "solver types. Magic value \"pool\" acquires the next healthy proxy from the server's "
        "proxy pool (POST /pool to populate) — the response echoes `proxy_used` (host:port) so "
        "IP-bound cookies (cloudflare/datadome/akamai/imperva/awswaf/perimeterx/x5sec) can be "
        "replayed from the same IP. No env fallback — omit to solve without proxy.")

    # reCAPTCHA-only
    version: Optional[str] = Field(None, description="reCAPTCHA only: v2 | v3 | invisible (default v2).")
    secret: Optional[str] = Field(None, description="reCAPTCHA v3 only: target's secret key, to also return the score.")
    enterprise: Optional[bool] = Field(False, description="reCAPTCHA only: load enterprise.js / grecaptcha.enterprise.")
    classifier: Optional[str] = Field(
        None,
        description="reCAPTCHA tile classifier for image challenges — "
        "'yolo' (local ONNX, no fallback), 'mistral' (vision API only), "
        "'hybrid' (ONNX-first + Mistral for unknown targets), "
        "'auto' or omit (hybrid if ONNX model present, else mistral). "
        "Used by v2 checkbox and by invisible real_page when a bframe "
        "image challenge appears. Ignored for pure score-based v3.")

    # solve-and-verify (turnstile)
    verify_url: Optional[str] = Field(None, description="Turnstile: verify the token from the same session at this URL.")
    verify_payload: Optional[dict] = Field(None, description="Turnstile: body for verify_url; token is injected as \"token\".")
    page_url: Optional[str] = Field(None, description="Turnstile: origin to intercept (defaults to verify_url).")

    # botguard-only (Google OAuth token extraction)
    email: Optional[str] = Field(None, description="BotGuard: account email to enter — drives the sign-in flow to the token-bearing RPC.")
    password: Optional[str] = Field(None, description="BotGuard: optional password — if set, drives to the password step and grabs the B4hajb hard-gate token instead of the MI613e lookup token.")

    # datadome-only (DataDome bot-management clearance cookie)
    referer: Optional[str] = Field(None, description="datadome: optional framing Referer so DataDome serves the same config/scoring as the real flow. The caller supplies its own site's referer (e.g. https://github.com/ when harvesting via octocaptcha). Pair with a `url` pointing at the DataDome-fronted page that loads tags.js.")

    # perimeterx-only (HUMAN/PerimeterX 'Press & Hold')
    render_flow: Optional[str] = Field(None, description="perimeterx: named site trigger that makes the gate render when it doesn't show on plain load (default 'outlook_signup'). Throwaway navigation only — NOT account creation. Pass null with a `url` for deployments whose gate renders on goto(). Harvests the _px3 clearance cookie (bound to _pxvid+IP+UA; replay under the same proxy+UA within TTL).")

    # aliyun-only (Aliyun Captcha 2.0 slide-puzzle). No sitekey — the challenge identity
    # is scene_id + prefix (prefix selects the captcha-open endpoint). Harvest-only:
    # returns {sceneId, certifyId, deviceToken, data}; the caller replays it immediately
    # into VerifyCaptchaV3 (token is session-bound + one-time-use, deviceToken time-bound).
    scene_id: Optional[str] = Field(None, description="aliyun: the SceneId of the target site's captcha (e.g. read from the page config). Required for type=aliyun.")
    prefix: Optional[str] = Field(None, description="aliyun: the captcha-open endpoint prefix (e.g. '13lbkb5' -> <prefix>.captcha-open-southeast.aliyuncs.com). Required for type=aliyun.")
    region: Optional[str] = Field(None, description="aliyun: captcha region — 'sgp' (default), 'cn', or 'intl'.")

    # tencent-only (腾讯防水墙 / Tencent Captcha)
    appid: Optional[str] = Field(None, description="tencent: the target site's CaptchaAppId (the aid in the tencent.js embed). Default '199999861' = Tencent's public test appid.")

    # geetest-only (GeeTest v4)
    captcha_id: Optional[str] = Field(None, description="geetest v4: the captcha_id from the gt4.js embed (e.g. from the /load request). Default = GeeTest's public slide demo id.")
    risk_type: Optional[str] = Field(None, description="geetest v4: slide | icon | gobang | ai (default 'slide').")
    hostname: Optional[str] = Field(None, description="mtcaptcha: the hostname (bd) the widget runs on — must match the sitekey's allowlist. Default '2captcha.com' = public demo sitekey.")
    challenge_json: Optional[dict] = Field(None, description="altcha: pass the challenge JSON directly (skips fetching from `url`).")
    gt: Optional[str] = Field(None, description="geetest v3: the gt key from the target page (register call).")
    challenge: Optional[str] = Field(None, description="geetest v3: the challenge string from the target page.")
    image_b64: Optional[str] = Field(None, description="image_to_text: base64-encoded captcha image (data-URI prefix accepted).")
    masterurl_id: Optional[str] = Field(None, description="cybersiara: the site's MasterUrlId. Default = CyberSiara's demo id.")
    user_agent: Optional[str] = Field(None, description="imperva: optional User-Agent override for the sensor session (cookies are UA-bound — keep the same UA on replay).")
    # arkose-only (Arkose FunCaptcha)
    public_key: Optional[str] = Field(None, description="arkose: Arkose public key from the target site's embed. Required for type=arkose.")
    game_type: Optional[str] = Field("4", description="arkose: Arkose game type (default '4').")

    # PoW / vendor-param types (cap, anubis, mcaptcha, procaptcha, yidun, shumei, douyin, vaptcha)
    base_url: Optional[str] = Field(None, description="cap/mcaptcha: self-hosted service base — POST {base}/{sitekey}/challenge|redeem (cap) or {base}/api/v1/pow/config|verify (mcaptcha).")
    redeem_url: Optional[str] = Field(None, description="cap: explicit redeem endpoint (skips the {base}/{site_key}/redeem default).")
    difficulty: Optional[int] = Field(None, description="anubis: leading-zero difficulty (BITS for algorithm=sha256, HEX chars for fast/preact).")
    algorithm: Optional[str] = Field(None, description="anubis: sha256 (new) | fast (old, hex-prefix) | preact (midstate).")
    mode: Optional[str] = Field(None, description="yidun: slider | silent. shumei: spatial_select | icon_select | auto. douyin: douyin | tiktok.")
    nonce: Optional[int] = Field(None, description="cerberus/goaway: PoW nonce echo from the challenge blob.")
    ts: Optional[int] = Field(None, description="cerberus: challenge timestamp (unix).")
    signature: Optional[str] = Field(None, description="cerberus: challenge signature from the x-challenge blob.")
    rqdata: Optional[str] = Field(None, description="hCaptcha enterprise: rqdata blob from the target site's hCaptcha config. "
                                  "Pass to trigger enterprise path (sets data-rqdata on widget + a11y text handling).")


# Named request examples → Swagger UI renders these as a dropdown picker on /solve.
_SOLVE_EXAMPLES = {
    "turnstile": {
        "summary": "Turnstile (route-intercept)",
        "value": {"type": "turnstile", "sitekey": "0x4AAAAAAA...", "url": "https://target.com"},
    },
    "recaptcha_v3": {
        "summary": "reCAPTCHA v3 Enterprise (score)",
        "value": {"type": "recaptcha", "version": "v3", "enterprise": True,
                  "sitekey": "6Lc...", "url": "https://target.com", "action": "login"},
    },
    "recaptcha_v2": {
        "summary": "reCAPTCHA v2 checkbox",
        "value": {"type": "recaptcha", "version": "v2", "sitekey": "6Lf...",
                  "url": "https://target.com/form", "classifier": "hybrid"},
    },
    "hcaptcha": {
        "summary": "hCaptcha (checkbox)",
        "value": {"type": "hcaptcha", "sitekey": "10000000-ffff-ffff-ffff-000000000001",
                  "url": "https://target.com"},
    },
    "turnstile_realpage": {
        "summary": "Turnstile on the live page (pre_actions + post_fetch)",
        "value": {"type": "turnstile", "real_page": True, "url": "https://app.example.com/login",
                  "pre_actions": [{"type": "fill", "selector": "input[type=email]", "value": "u@ex.com"},
                                  {"type": "click", "selector": "button[type=submit]"}],
                  "post_fetch": [{"url": "https://app.example.com/api/verify",
                                  "body": {"token": "__TOKEN__"}}]},
    },
    "cloudflare_clearance": {
        "summary": "Cloudflare clearance (cf_clearance — Managed or JS challenge)",
        "value": {"type": "cloudflare", "url": "https://protected.example.com",
                  "proxy": "http://user:pass@ip:port"},
    },
    "aws_waf": {
        "summary": "AWS WAF token (silent JS challenge → aws-waf-token)",
        "value": {"type": "awswaf", "url": "https://protected.example.com/waitlist",
                  "proxy": "http://user:pass@ip:port"},
    },
    "botguard": {
        "summary": "BotGuard (Google OAuth bgRequest token + session cookies)",
        "value": {"type": "botguard", "email": "user@example.com",
                  "password": "optional-for-hard-gate-token"},
    },
    "datadome": {
        "summary": "DataDome clearance cookie — caller passes the DataDome-fronted url (+ referer)",
        "value": {"type": "datadome",
                  "url": "https://octocaptcha.com/datadome?origin_page=github_signup_redesign",
                  "referer": "https://github.com/",
                  "proxy": "http://user:pass@ip:port"},
    },
    "akamai": {
        "summary": "Harvest an Akamai Bot Manager _abck clearance cookie (caller passes the Akamai-fronted url)",
        "value": {"type": "akamai",
                  "url": "https://www.example-akamai-site.com/",
                  "proxy": "http://user:pass@ip:port"},
    },
    "perimeterx": {
        "summary": "PerimeterX/HUMAN 'Press & Hold' → harvest _px3 clearance cookie (render_flow trigger)",
        "value": {"type": "perimeterx", "render_flow": "outlook_signup",
                  "proxy": "http://user:pass@ip:port"},
    },
    "arkose": {
        "summary": "Arkose FunCaptcha (ONNX image prediction)",
        "value": {"type": "arkose", "public_key": "0x0000000000000000000000000000000"},
    },
}


# ── Response models (documentation shapes; solvers return supersets) ──
# Solvers are numpy/cv2-heavy and leak numpy scalars (np.int32/int64/float32/bool_)
# into result dicts. FastAPI/pydantic refuses to serialize those -> HTTP 500 on an
# otherwise successful solve. Normalize the whole result tree before returning.
def _jsonable(o: Any) -> Any:
    """Recursively convert numpy scalars/arrays and odd mappings into plain JSON types."""
    if o is None or isinstance(o, (str, bool, int, float)):
        return o
    if hasattr(o, "item") and getattr(o, "shape", None) == ():   # numpy scalar
        return o.item()
    if hasattr(o, "tolist"):                                     # numpy array
        return _jsonable(o.tolist())
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    return o


class SolveResponse(BaseModel):
    type: str = Field(..., description="Echoes the request type — the dispatch discriminator.",
                      examples=["turnstile"])
    solved: bool = Field(..., description="THE success signal. True iff the captcha was solved, "
                         "uniform across every type — read this instead of branching per-type.")
    token: Optional[str | int | float | dict | list] = Field(None, description="Solved token for token types (turnstile/"
                                 "recaptcha/hcaptcha). Absent for type=cloudflare (see cf_clearance); "
                                 "empty string on a failed/realpage solve — trust `solved`, not this. "
                                 "aliyun returns a dict {sceneId, certifyId, deviceToken, data}; "
                                 "cap returns a list of int solutions; goaway/anubis return the "
                                 "integer nonce.")
    method: Optional[str] = Field(None, description="Which path solved it (route | execute | real-page | image | …).")
    elapsed: Optional[float] = Field(None, description="Solve time (seconds).")
    error: Optional[str] = Field(None, description="Set when the solve failed but returned 200.")
    # Per-type success/detail discriminators (present only for their type):
    verify_success: Optional[bool] = Field(None, description="realpage variants: token harvested + verified.")
    success: Optional[bool] = Field(None, description="Page-level (cloudflare/awswaf): cookie obtained.")
    cf_clearance: Optional[dict] = Field(None, description="type=cloudflare: the cf_clearance cookie record.")
    model_config = {"extra": "allow"}  # solvers add expires_in, score, cookies, user_agent, post_fetch, …


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Human-readable error message")


# Schematized non-2xx responses for /solve (422 is auto-documented by FastAPI).
_SOLVE_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Bad request — unsupported type, missing sitekey for a widget type, or an SSRF-rejected URL"},
    408: {"model": ErrorResponse, "description": "Global deadline (timeout_s) exceeded before a result"},
    500: {"model": ErrorResponse, "description": "Unhandled solver error"},
}


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    supported_types: list[str] = Field(examples=[["turnstile", "recaptcha", "hcaptcha"]])
    mode: str = Field("local", description="Solve mode: local (browser/ONNX on Modal only).")
    arkose_models: bool = Field(False, description="True if arkose/models/*.onnx present.")
    proxy_pool: Optional[dict] = Field(None, description="Pool summary {size, healthy} — null when the pool module is unavailable.")


class StatusResponse(BaseModel):
    services: dict[str, str]
    current: list[dict[str, Any]] = Field(description="Currently running solve tasks.")


class LogsResponse(BaseModel):
    logs: list[dict[str, Any]]
    total: int


@app.get("/health", response_model=HealthResponse, tags=["monitoring"],
         operation_id="health",
         summary="Liveness + supported types (public, no auth)")
async def health():
    """Public liveness probe. Lists the captcha types this service can solve."""
    from pathlib import Path
    models = Path(__file__).parent / "arkose" / "models"
    has_models = models.is_dir() and any(models.glob("*.onnx"))
    pool = None
    try:
        from solvers.common.proxypool import get_pool
        pool = get_pool().status()
    except Exception:
        pass
    return {
        "status": "ok",
        "supported_types": SUPPORTED,
        "mode": "local",
        "arkose_models": has_models,
        "proxy_pool": {"size": pool["size"], "healthy": pool["healthy"]} if pool else None,
    }


def _extract(req: SolveRequest):
    """Unpack pre_actions + post_fetch for realpage endpoints."""
    actions = [a.model_dump() for a in req.pre_actions] if req.pre_actions else None
    fetches = [f.model_dump() for f in req.post_fetch] if req.post_fetch else None
    return actions, fetches

# ── Proxy pool integration ───────────────────────────────────────────
def _resolve_pool_proxy(req: "SolveRequest") -> Optional[object]:
    """proxy:"pool" → acquire next healthy proxy from the pool (503 if exhausted).
    Returns the acquired _Proxy (or None for direct/custom proxy). Mutates req.proxy."""
    if not (req.proxy or "").strip().lower() == "pool":
        return None
    from solvers.common.proxypool import get_pool
    try:
        p = get_pool().acquire()
    except KeyError as e:
        raise HTTPException(503, str(e))
    req.proxy = p.url
    return p


def _report_pool(req: "SolveRequest", result: dict, acquired):
    """Feed the solve outcome back into pool health + echo proxy_used."""
    if acquired is None:
        return
    from solvers.common.proxypool import get_pool, _mask
    solved = bool(result.get("solved"))
    get_pool().report(acquired.url, solved, error=result.get("error"))
    if not result.get("proxy_used"):
        result["proxy_used"] = acquired.hostport      # host:port — caller replays from this IP
        result["proxy_masked"] = _mask(acquired.url)

async def _dispatch(req: SolveRequest) -> dict:
    """Run the actual solver for req.type/version and return its result dict.

    Result always carries a top-level "type"; the caller logs + returns it.
    """
    # strip data-URI prefix so callers can pass "data:image/png;base64,..." as-is
    if req.image_b64 and "," in req.image_b64 and req.image_b64.startswith("data:"):
        req.image_b64 = req.image_b64.split(",", 1)[1]
    if req.type == "turnstile":
        from solvers.turnstile.solve import solve_turnstile, solve_and_verify, solve_turnstile_realpage
        # route-intercept turnstile raises TimeoutError on no-token; catch it here so an
        # unsolved turnstile returns a uniform 200 {error}, not a collision with the real
        # asyncio deadline (408).
        try:
            if req.verify_url and req.verify_payload:
                r = await solve_and_verify(
                    req.sitekey, req.verify_url, req.verify_payload, req.action,
                    cdata=req.cdata, page_url=req.page_url, proxy=req.proxy)
            elif req.real_page:
                actions, fetches = _extract(req)
                r = await solve_turnstile_realpage(
                    req.url, req.sitekey, req.timeout_s, actions, fetches, proxy=req.proxy)
            else:
                r = await solve_turnstile(
                    req.sitekey, req.url, req.action, req.cdata, proxy=req.proxy)
        except TimeoutError as e:
            r = {"token": "", "error": str(e), "method": "route"}
        return {"type": "turnstile", **r}

    if req.type == "hcaptcha":
        from solvers.hcaptcha.solve import solve_hcaptcha, solve_hcaptcha_invisible, solve_hcaptcha_realpage
        if req.action == "invisible":
            r = await solve_hcaptcha_invisible(req.sitekey, req.url, proxy=req.proxy)
        elif req.real_page:
            actions, fetches = _extract(req)
            r = await solve_hcaptcha_realpage(
                req.url, req.sitekey, req.timeout_s, actions, fetches, proxy=req.proxy)
        else:
            r = await solve_hcaptcha(req.sitekey, req.url, proxy=req.proxy)
        return {"type": "hcaptcha", **r}

    if req.type == "cloudflare":
        from solvers.cloudflare.solve import solve_cf_clearance
        actions, fetches = _extract(req)
        r = await solve_cf_clearance(req.url, req.proxy, req.timeout_s, actions, fetches)
        return {"type": "cloudflare", **r}

    if req.type == "awswaf":
        from solvers.awswaf.solve import solve_aws_waf
        actions, fetches = _extract(req)
        r = await solve_aws_waf(
            req.url, req.proxy, req.timeout_s or 90, actions, fetches)
        if "solved" not in r:
            r["solved"] = bool(r.get("success") or r.get("token"))
        return {"type": "awswaf", **r}

    if req.type == "botguard":
        from solvers.botguard.solve import solve_botguard
        actions, _ = _extract(req)
        r = await solve_botguard(
            url=req.url, email=req.email, password=req.password,
            proxy=req.proxy, timeout_s=req.timeout_s or 90, pre_actions=actions)
        return {"type": "botguard", **r}

    if req.type == "datadome":
        from solvers.datadome.solve import solve_datadome
        r = await solve_datadome(
            req.url, referer=req.referer,
            proxy=req.proxy, timeout_s=req.timeout_s or 60)
        return {"type": "datadome", **r}

    if req.type == "perimeterx":
        from solvers.perimeterx.solve import solve_perimeterx
        # Try with proxy first (caller IP binding), then without if tunnel dies
        r = await solve_perimeterx(
            url=req.url, render_flow=req.render_flow,
            proxy=req.proxy, timeout_s=req.timeout_s or 200)
        if not r.get("solved") and req.proxy:
            err = str(r.get("error") or "")
            if any(x in err for x in ("TUNNEL", "SSL", "AUTH", "PROXY", "net::")):
                r2 = await solve_perimeterx(
                    url=req.url, render_flow=req.render_flow,
                    proxy=None, timeout_s=min(req.timeout_s or 200, 120))
                r2["proxy_retry"] = "direct-after-proxy-fail"
                if r2.get("solved") or r2.get("gate_reached"):
                    r = r2
                else:
                    r["direct_retry_error"] = r2.get("error")
        return {"type": "perimeterx", **r}

    if req.type == "akamai":
        # Backend 1 (preferred): wre-client-akamai sensor generator — headless, fast,
        # validates _abck even from datacenter IPs (byte-accurate vendor sensor).
        try:
            from solvers.akamai.wre_backend import available as wre_available, solve_akamai_wre
            if wre_available():
                r = await solve_akamai_wre(req.url, req.proxy, req.timeout_s or 90)
                if "solved" not in r:
                    r["solved"] = bool(r.get("success"))
                # normalize token ONLY on a validated solve — an unvalidated cookie
                # must not trip the generic truthy-token success predicate.
                if r.get("solved") and not r.get("token") and isinstance(r.get("_abck"), dict):
                    r["token"] = r["_abck"].get("value") or ""
                return {"type": "akamai", **r}
        except Exception as e:
            log.warning("akamai wre backend unavailable (%s) — falling back to browser", str(e)[:80])
        # Backend 2: browser harvest (works from residential IPs)
        from solvers.akamai.solve import solve_akamai
        actions, fetches = _extract(req)
        r = await solve_akamai(req.url, req.proxy, req.timeout_s or 90, actions, fetches)
        if "solved" not in r:
            r["solved"] = bool(r.get("success"))
        # normalize token for clients expecting token field
        if not r.get("token") and isinstance(r.get("_abck"), dict):
            r["token"] = r["_abck"].get("value") or ""
        return {"type": "akamai", **r}

    if req.type == "aliyun":
        # Dispatch to a SUBPROCESS (aliyun._run), not an inline await. The drag trajectory
        # depends on precise CDP Input.dispatchMouseEvent timing that is fidelity-sensitive
        # to running on the MAIN thread with a clean event loop. Proven empirically:
        #   asyncio.run(solve_aliyun) on main thread   -> 3/3 T001
        #   awaited on uvicorn's loop                  -> 0/12 F001
        #   asyncio.run inside asyncio.to_thread       -> 0/12 F001 (off main thread)
        # A subprocess runs its own main-thread asyncio.run, exactly reproducing the
        # working direct-call conditions -> T001.
        import os as _os
        _to = req.timeout_s or 90
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "solvers.aliyun._run",
            req.scene_id or "", req.prefix or "", req.region or "sgp",
            str(_to), req.proxy or "",
            cwd=_os.path.dirname(_os.path.abspath(__file__)),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_to + 30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"type": "aliyun", "solved": False, "error": "subprocess deadline"}
        r = {"solved": False, "error": "no result from runner"}
        for line in (out or b"").decode(errors="replace").splitlines():
            if line.startswith("__ALIYUN_RESULT__"):
                r = json.loads(line[len("__ALIYUN_RESULT__"):])
                break
        return {"type": "aliyun", **r}

    if req.type == "arkose":
        from solvers.arkose.solve import solve_arkose
        from solvers.common.proxypool import get_pool
        actions, _ = _extract(req)
        page = (req.page_url or req.url or "").strip()
        if not page:
            raise HTTPException(400, "arkose requires page_url (or url)")
        # Local only: browser + arkose/models/*.onnx (no external solver APIs)
        # pool-aware: gt2 DENIED ACCESS is per-IP — retry through distinct pool proxies
        pool = get_pool()
        use_pool = bool(req.proxy) and req.proxy in pool._items
        attempts = 3 if use_pool else 1
        tried: list[str] = []
        r: dict = {}
        for i in range(attempts):
            if i and use_pool:
                try:
                    from solvers.common.proxypool import _hostport
                    acq = pool.acquire(exclude_hostport=_hostport(req.proxy or ""))
                except KeyError:
                    break                            # pool exhausted
                req.proxy = acq.url
                tried.append(acq.hostport)
            r = await solve_arkose(
                public_key=req.public_key or "", page_url=page,
                game_type=req.game_type or "4", proxy=req.proxy,
                timeout_s=req.timeout_s or 120, pre_actions=actions)
            classified_gate = r.get("gt2_denied") or r.get("gt2_locked")
            slow_proxy = (not r.get("solved")) and r.get("mode") in ("real+inject", "stub")
            if r.get("solved") or not (classified_gate or (use_pool and slow_proxy)):
                break
            # mark the CURRENT proxy failed so the next acquire skips it
            if use_pool and req.proxy:
                pool.report(req.proxy, False, error=(r.get("error") or "gt2 gate")[:80])
            if i + 1 >= attempts:
                r["pool_tried"] = tried
        return {"type": "arkose", **r}

    if req.type == "recaptcha_audio":
        from solvers.recaptcha_audio.solve import solve_recaptcha_audio
        r = await solve_recaptcha_audio(audio_url=req.url, audio_b64=req.image_b64,
                                        proxy=req.proxy, timeout_s=req.timeout_s or 90)
        return {"type": "recaptcha_audio", **r}

    if req.type == "yandex":
        from solvers.yandex.solve import solve_yandex
        if not req.url:
            raise HTTPException(400, "yandex requires url (page with SmartCaptcha widget)")
        r = await solve_yandex(url=req.url, proxy=req.proxy,
                               timeout_s=req.timeout_s or 90)
        return {"type": "yandex", **r}

    if req.type == "cerberus":
        from solvers.cerberus.solve import solve_cerberus
        r = await solve_cerberus(challenge=req.challenge, difficulty=req.difficulty,
                                 nonce=req.nonce, ts=req.ts, signature=req.signature,
                                 challenge_json=req.challenge_json,
                                 timeout_s=req.timeout_s or 90)
        return {"type": "cerberus", **r}

    if req.type == "hcaptcha_enterprise":
        from solvers.hcaptcha.solve import solve_hcaptcha_realpage
        r = await solve_hcaptcha_realpage(url=req.url, sitekey=req.sitekey,
                                           timeout_s=req.timeout_s or 120,
                                           pre_actions=req.pre_actions,
                                           post_fetch=req.post_fetch,
                                           rqdata=req.rqdata or req.challenge,
                                           proxy=req.proxy)
        return {"type": "hcaptcha_enterprise", **r}

    if req.type == "captchafox":
        from solvers.captchafox.solve import solve_captchafox
        r = await solve_captchafox(image_b64=req.image_b64, image_url=req.url,
                                    piece_b64=req.challenge_json.get("piece_b64") if req.challenge_json else None,
                                    encrypted_data=req.challenge,
                                    host=req.page_url, proxy=req.proxy,
                                    timeout_s=req.timeout_s or 60)
        return {"type": "captchafox", **r}

    if req.type == "binance":
        from solvers.binance.solve import solve_binance
        r = await solve_binance(image_b64=req.image_b64, image_url=req.url,
                                 biz_id=req.action or "register",
                                 security_check_response_validate_id=req.challenge or "",
                                 proxy=req.proxy, timeout_s=req.timeout_s or 60)
        return {"type": "binance", **r}

    if req.type == "basilisk":
        from solvers.basilisk.solve import solve_basilisk
        r = await solve_basilisk(image_b64=req.image_b64, url=req.url,
                                  site_key=req.sitekey, site_domain=req.page_url,
                                  slide_bg_b64=req.challenge_json.get("slide_bg") if req.challenge_json else None,
                                  slide_piece_b64=req.challenge_json.get("slide_piece") if req.challenge_json else None,
                                  slide_y=req.challenge_json.get("slide_y") if req.challenge_json else None,
                                  icons_bg_b64=req.challenge_json.get("icons_bg") if req.challenge_json else None,
                                  icons_order=req.challenge_json.get("icons_order") if req.challenge_json else None,
                                  proxy=req.proxy, timeout_s=req.timeout_s or 60)
        return {"type": "basilisk", **r}

    if req.type == "cloudflare_challenge":
        from solvers.cloudflare.solve import solve_cf_clearance
        r = await solve_cf_clearance(url=req.url, proxy=req.proxy, timeout_s=req.timeout_s or 120)
        return {"type": "cloudflare_challenge", **r}

    if req.type == "vk":
        from solvers.vk.solve import solve_vk
        r = await solve_vk(image_b64=req.image_b64, image_url=req.url,
                            sid=req.challenge, s=req.cdata,
                            proxy=req.proxy, timeout_s=req.timeout_s or 30)
        return {"type": "vk", **r}

    if req.type in ("grid", "coordinates", "draw_around", "drag_drop", "bounding_box"):
        from solvers.recognition.solve import solve_recognition
        r = await solve_recognition(image_b64=req.image_b64, image_url=req.url,
                                     task=req.action or req.challenge or "",
                                     mode=req.type, grid_size=4,
                                     proxy=req.proxy, timeout_s=req.timeout_s or 60)
        return r

    if req.type == "tspd":
        from solvers.imperva.solve import solve_imperva
        r = await solve_imperva(url=req.url, proxy=req.proxy, timeout_s=req.timeout_s or 120)
        return {"type": "tspd", **r}

    if req.type == "steam":
        from solvers.steam.solve import solve_steam
        r = await solve_steam(image_b64=req.image_b64, url=req.url,
                               proxy=req.proxy, timeout_s=req.timeout_s or 60)
        return {"type": "steam", **r}

    if req.type == "zhihu":
        from solvers.zhihu.solve import solve_zhihu
        r = await solve_zhihu(image_b64=req.image_b64, url=req.url,
                               challenge_blob=req.challenge_json,
                               captcha_type=req.challenge,
                               proxy=req.proxy, timeout_s=req.timeout_s or 60)
        return {"type": "zhihu", **r}

    if req.type == "goaway":
        from solvers.goaway.solve import solve_goaway
        r = await solve_goaway(challenge=req.challenge, difficulty=req.difficulty,
                               challenge_json=req.challenge_json,
                               proxy=req.proxy, timeout_s=req.timeout_s or 60)
        return {"type": "goaway", **r}

    # ── PoW tier (pure-HTTP, no browser) ─────────────────────────────
    if req.type == "cap":
        from solvers.cap.solve import solve_cap
        r = await solve_cap(challenge_json=req.challenge_json,
                            base_url=req.base_url, site_key=req.sitekey,
                            redeem_url=req.redeem_url, proxy=req.proxy,
                            timeout_s=req.timeout_s or 90)
        return {"type": "cap", **r}

    if req.type == "anubis":
        from solvers.anubis.solve import solve_anubis
        r = await solve_anubis(url=req.url, challenge=req.challenge,
                               difficulty=req.difficulty,
                               algorithm=req.algorithm or "sha256",
                               proxy=req.proxy, timeout_s=req.timeout_s or 90)
        return {"type": "anubis", **r}

    if req.type == "mcaptcha":
        from solvers.mcaptcha_pow.solve import solve_mcaptcha
        r = await solve_mcaptcha(challenge_json=req.challenge_json,
                                 base_url=req.base_url, site_key=req.sitekey,
                                 verify_url=req.verify_url, proxy=req.proxy,
                                 timeout_s=req.timeout_s or 90)
        return {"type": "mcaptcha", **r}

    if req.type == "procaptcha":
        from solvers.procaptcha.solve import solve_procaptcha
        if not req.url or not req.sitekey:
            raise HTTPException(400, "procaptcha requires url + sitekey (dapp)")
        r = await solve_procaptcha(page_url=req.url, site_key=req.sitekey,
                                   node=req.prefix or "pronode7",
                                   proxy=req.proxy, timeout_s=req.timeout_s or 90)
        return {"type": "procaptcha", **r}

    # ── Chinese vendor tier (pure-HTTP protocol ports) ───────────────
    if req.type == "yidun":
        from solvers.yidun.solve import solve_yidun
        r = await solve_yidun(captcha_id=req.sitekey, url=req.url,
                              proxy=req.proxy, timeout_s=req.timeout_s or 90,
                              **({"mode": req.mode} if req.mode else {}))
        return {"type": "yidun", **r}

    if req.type == "dingxiang":
        from solvers.dingxiang.solve import solve_dingxiang
        r = await solve_dingxiang(app_id=req.sitekey, url=req.url,
                                  proxy=req.proxy, timeout_s=req.timeout_s or 90)
        return {"type": "dingxiang", **r}

    if req.type == "shumei":
        from solvers.shumei.solve import solve_shumei
        r = await solve_shumei(org_code=req.sitekey, url=req.url,
                               proxy=req.proxy, timeout_s=req.timeout_s or 90,
                               **({"mode": req.mode} if req.mode else {}))
        return {"type": "shumei", **r}

    if req.type == "douyin":
        from solvers.douyin.solve import solve_douyin
        r = await solve_douyin(url=req.url, web_id=req.sitekey,
                               proxy=req.proxy, timeout_s=req.timeout_s or 90,
                               **({"platform": req.mode} if req.mode else {}))
        return {"type": "douyin", **r}

    if req.type == "vaptcha":
        from solvers.vaptcha.solve import solve_vaptcha
        r = await solve_vaptcha(vid=req.sitekey, url=req.url,
                                proxy=req.proxy, timeout_s=req.timeout_s or 90)
        return {"type": "vaptcha", **r}

    if req.type == "rotate":
        from solvers.rotate.solve import solve_rotate
        r = await solve_rotate(image_b64=req.image_b64, url=req.url,
                               proxy=req.proxy, timeout_s=req.timeout_s or 60)
        return {"type": "rotate", **r}

    if req.type == "tencent":
        from solvers.tencent.solve import solve_tencent
        r = await solve_tencent(req.appid or "199999861", req.timeout_s or 90)
        return {"type": "tencent", **r}

    if req.type == "x5sec":
        from solvers.x5sec.solve import solve_x5sec
        actions, fetches = _extract(req)
        r = await solve_x5sec(req.url, req.proxy, req.timeout_s or 90, actions, fetches)
        return {"type": "x5sec", **r}

    if req.type == "geetest":
        from solvers.geetest.solve import solve_geetest
        r = await solve_geetest(req.captcha_id or "54088bb07d2df3c46b79f80300b0abbe",
                                req.risk_type or "slide", req.timeout_s or 90)
        return {"type": "geetest", **r}

    if req.type == "kasada":
        from solvers.kasada.wre_backend import available as kasada_available, solve_kasada
        if not kasada_available():
            return {"type": "kasada", "solved": False,
                    "error": "wre-client-kasada not installed (pip install wre-client-kasada)"}
        r = await solve_kasada(req.url, req.proxy, req.timeout_s or 90)
        return {"type": "kasada", **r}

    if req.type == "mtcaptcha":
        from solvers.mtcaptcha.solve import solve_mtcaptcha
        r = await solve_mtcaptcha(req.sitekey or "MTPublic-KzqLY1cKH",
                                  req.hostname or "2captcha.com", req.timeout_s or 90)
        return {"type": "mtcaptcha", **r}

    if req.type == "altcha":
        from solvers.altcha_solver.solve import solve_altcha
        r = await solve_altcha(req.url, req.timeout_s or 90, req.challenge_json)
        return {"type": "altcha", **r}

    if req.type == "cybersiara":
        from solvers.cybersiara.solve import solve_cybersiara
        r = await solve_cybersiara(req.url, req.masterurl_id or
                                   "OXR2LVNvCuXykkZbB8KZIfh162sNT8S2", req.timeout_s or 90)
        return {"type": "cybersiara", **r}

    if req.type == "friendly":
        from solvers.friendly.solve import solve_friendly
        r = await solve_friendly(req.url, sitekey=req.sitekey, proxy=req.proxy,
                                 timeout_s=req.timeout_s or 90)
        return {"type": "friendly", **r}

    if req.type == "geetest_v3":
        from solvers.geetest_v3.solve import solve_geetest_v3
        r = await solve_geetest_v3(req.gt or req.captcha_id or "", req.challenge or "",
                                   req.timeout_s or 90)
        return {"type": "geetest_v3", **r}

    if req.type == "image_to_text":
        from solvers.image_to_text.solve import solve_image_to_text
        r = await solve_image_to_text(req.image_b64, req.url, req.timeout_s or 60)
        return {"type": "image_to_text", **r}

    if req.type == "imperva":
        from solvers.imperva.solve import solve_imperva
        r = await solve_imperva(req.url, req.proxy, req.timeout_s or 120, req.user_agent)
        return {"type": "imperva", **r}

    # reCAPTCHA — browser / ONNX only (full local Modal)
    from solvers.recaptcha.solve import (
        solve_recaptcha_v3, solve_recaptcha_v3_realpage, solve_recaptcha_invisible,
        solve_recaptcha_invisible_realpage,
        solve_recaptcha_v2, solve_recaptcha_v2_realpage,
    )
    version = req.version or "v2"  # default v2 (checkbox)
    if version == "v3":
        if req.real_page:
            actions, _ = _extract(req)
            r = await solve_recaptcha_v3_realpage(
                req.url, req.sitekey, req.action or "submit",
                enterprise=req.enterprise, timeout_s=req.timeout_s or 90,
                pre_actions=actions, proxy=req.proxy)
        else:
            r = await solve_recaptcha_v3(
                req.sitekey, req.url, req.action or "submit",
                req.secret, enterprise=req.enterprise, proxy=req.proxy)
            # Fast execute() often fails on non-v3 keys / locked origins — one real_page retry
            if not r.get("token") and req.url and req.sitekey:
                actions, _ = _extract(req)
                r2 = await solve_recaptcha_v3_realpage(
                    req.url, req.sitekey, req.action or "submit",
                    enterprise=req.enterprise, timeout_s=min(int(req.timeout_s or 90), 90),
                    pre_actions=actions, proxy=req.proxy)
                if r2.get("token"):
                    r2["method"] = (r2.get("method") or "execute-realpage") + "+fallback"
                    r = r2
                else:
                    r["error"] = (
                        f"{r.get('error') or 'v3 execute failed'}; "
                        f"real_page: {r2.get('error') or 'no token'}"
                    )
    elif version == "invisible":
        # IMPORTANT: do NOT run stub execute() through residential proxy — Chromium often
        # hangs on Google via HTTP auth proxy; proxy is for real_page only (image/IP trust).
        if req.real_page:
            actions, _ = _extract(req)
            r = await solve_recaptcha_invisible_realpage(
                req.url, req.sitekey, req.action or "submit",
                enterprise=req.enterprise, timeout_s=req.timeout_s or 150,
                pre_actions=actions, proxy=req.proxy,
                classifier=req.classifier)
        else:
            r = await solve_recaptcha_invisible(
                req.sitekey, req.url, req.action or "submit",
                enterprise=req.enterprise, proxy=None)
            if not r.get("token") and req.url and req.sitekey:
                # One real_page attempt with proxy (if any). Keep tight — image grid on bad IP is a sink.
                budget = max(60, min(int(req.timeout_s or 150), 150))
                actions, _ = _extract(req)
                r2 = await solve_recaptcha_invisible_realpage(
                    req.url, req.sitekey, req.action or "submit",
                    enterprise=req.enterprise, timeout_s=budget,
                    pre_actions=actions, proxy=req.proxy,
                    classifier=req.classifier)
                if r2.get("token"):
                    r2["method"] = (r2.get("method") or "invisible-realpage") + "+fallback"
                    r = r2
                else:
                    r["error"] = (
                        f"{r.get('error') or 'invisible execute failed'}; "
                        f"real_page: {r2.get('error') or 'no token'}"
                    )
                    r["elapsed"] = (r.get("elapsed") or 0) + (r2.get("elapsed") or 0)
    elif version == "v2":
        if req.real_page:
            actions, fetches = _extract(req)
            r = await solve_recaptcha_v2_realpage(
                req.url, req.sitekey, actions, fetches,
                timeout_s=req.timeout_s, proxy=req.proxy,
                classifier=req.classifier)
        else:
            r = await solve_recaptcha_v2(
                req.sitekey, req.url, enterprise=req.enterprise,
                proxy=req.proxy, classifier=req.classifier)
    else:
        raise HTTPException(400, f"Unknown version: {version}. Use v3|invisible|v2")
    if r.get("token") and "solved" not in r:
        r["solved"] = True
    return {"type": "recaptcha", **r}


@app.post("/solve", response_model=SolveResponse, tags=["solve"],
          operation_id="solve",
          dependencies=[Depends(_bearer)],
          summary="Solve a captcha (dispatch by type)",
          responses=_SOLVE_ERROR_RESPONSES)
async def solve(req: SolveRequest = Body(..., openapi_examples=_SOLVE_EXAMPLES)):
    """Solve any supported captcha and return the token (sync — may hit edge timeouts if >~100s).

    Prefer **POST /solve/async** + **GET /solve/job/{id}** when calling through Cloudflare Workers
    or other proxies with ~100s limits (reCAPTCHA v2 image often 60–120s).
    """
    _precheck(req)
    acquired = _resolve_pool_proxy(req)
    sk = req.sitekey or ""
    log.info("Solve: type=%s sitekey=%s url=%s", req.type, sk[:12], req.url)
    task_id = next(_task_ids)
    _url = req.url or ""
    _solve_current[task_id] = {
        "type": req.type,
        "sitekey": sk[:12] + ("..." if len(sk) > 12 else ""),
        "url": _url[:60] + ("..." if len(_url) > 60 else ""),
        "version": req.version or None,
        "started_at": time.time(),
    }
    try:
        deadline = max(90, int(req.timeout_s or 90))
        async with asyncio.timeout(deadline):
            result = await _dispatch(req)
        result["solved"] = _is_solved(result)
        result = _jsonable(result)
        _report_pool(req, result, acquired)
        _log_solve(req.type, req.sitekey, req.url, result)
        return result
    except (TimeoutError, asyncio.TimeoutError):
        if acquired is not None:
            # browser hung past the deadline — almost always a dead pool proxy
            _report_pool(req, {"solved": False, "error": "solve deadline (408)"}, acquired)
        raise HTTPException(408, f"solve timed out after {max(90, int(req.timeout_s or 90))}s")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.error("Solve failed: %s", e, exc_info=True)
        if acquired is not None:
            _report_pool(req, {"solved": False, "error": str(e)[:120]}, acquired)
        raise HTTPException(500, str(e))
    finally:
        _solve_current.pop(task_id, None)


# ── Async jobs (anti edge-524 for long recaptcha image solves) ────────
# Multi-container Modal: configure_job_store(modal.Dict) from modal_app.
# In-memory / bare disk break when start hits container A and poll hits B.
import secrets as _secrets
from pathlib import Path as _Path

_JOB_TTL_S = 600


def configure_job_store(backend) -> None:
    """Wire multi-container job store (e.g. modal.Dict). Called from modal_app."""
    global _job_backend
    _job_backend = backend
    log.info("async job store configured: %s", type(backend).__name__)


def _job_dir() -> _Path:
    d = _Path(os.getenv("SOLVER_JOB_DIR", "/tmp/solver-jobs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_put(job_id: str, data: dict) -> None:
    data = dict(data)
    data.setdefault("started_at", time.time())
    _jobs_mem[job_id] = data
    if _job_backend is not None:
        try:
            _job_backend[job_id] = data
            return
        except Exception as e:
            log.warning("job put backend fail %s: %s", job_id, e)
    try:
        p = _job_dir() / f"{job_id}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        log.warning("job put disk fail %s: %s", job_id, e)


def _job_get(job_id: str) -> dict | None:
    if _job_backend is not None:
        try:
            if job_id in _job_backend:
                return _job_backend[job_id]
        except Exception:
            try:
                return _job_backend.get(job_id)  # type: ignore[attr-defined]
            except Exception as e:
                log.warning("job get backend fail %s: %s", job_id, e)
    if job_id in _jobs_mem:
        return _jobs_mem[job_id]
    try:
        p = _job_dir() / f"{job_id}.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("job get disk fail %s: %s", job_id, e)
    return None


def _precheck(req: SolveRequest) -> None:
    if req.type not in SUPPORTED:
        raise HTTPException(400, f"Unsupported type: {req.type}. Supported: {SUPPORTED}")
    if req.type == "botguard" and not req.url:
        req.url = "https://accounts.google.com/signin/v2/identifier?flowName=GlifWebSignIn"
    if req.type == "perimeterx" and not req.url:
        req.url = ("https://go.microsoft.com/fwlink/p/?linkid=2125440"
                   "&clcid=0x409&culture=en-us&country=us")
    if not req.url and req.type not in _SELF_URL:
        raise HTTPException(400, "url is required")
    if req.type == "aliyun" and (not req.scene_id or not req.prefix):
        raise HTTPException(400, "scene_id and prefix are required for type=aliyun")
    if req.type == "arkose" and not req.public_key:
        raise HTTPException(400, "public_key is required for type=arkose")
    if req.type not in _PAGE_LEVEL and req.type not in ("aliyun", "arkose", "tencent", "geetest", "kasada", "mtcaptcha", "altcha", "cybersiara", "imperva", "friendly", "geetest_v3", "image_to_text", "cap", "anubis", "mcaptcha", "yidun", "dingxiang", "shumei", "douyin", "vaptcha", "rotate", "goaway", "recaptcha_audio", "cerberus", "yandex", "hcaptcha_enterprise", "zhihu", "steam", "grid", "coordinates", "draw_around", "drag_drop", "bounding_box", "tspd", "vk", "cloudflare_challenge", "basilisk", "binance", "captchafox") and not req.sitekey:
        raise HTTPException(400, f"sitekey is required for type={req.type}")
    _validate_urls(req)


def _job_gc() -> None:
    now = time.time()
    try:
        for p in _job_dir().glob("*.json"):
            try:
                age = now - p.stat().st_mtime
                if age > _JOB_TTL_S:
                    p.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass
    dead = [k for k, v in _jobs_mem.items() if now - float(v.get("started_at") or 0) > _JOB_TTL_S]
    for k in dead:
        _jobs_mem.pop(k, None)


async def _run_job(job_id: str, req: SolveRequest) -> None:
    _precheck(req)
    acquired = _resolve_pool_proxy(req)
    sk = req.sitekey or ""
    task_id = next(_task_ids)
    _url = req.url or ""
    started = time.time()
    prev = _job_get(job_id) or {}
    started = float(prev.get("started_at") or started)
    _solve_current[task_id] = {
        "type": req.type,
        "sitekey": sk[:12] + ("..." if len(sk) > 12 else ""),
        "url": _url[:60] + ("..." if len(_url) > 60 else ""),
        "version": req.version or None,
        "started_at": started,
        "job_id": job_id,
    }
    try:
        # invisible may do execute (no proxy) + real_page (proxy) — needs headroom past timeout_s
        base = max(90, int(req.timeout_s or 90))
        if str(req.version or "") == "invisible" or str(req.type or "") == "recaptcha":
            deadline = base + 90
        else:
            deadline = base
        async with asyncio.timeout(deadline):
            result = await _dispatch(req)
        result["solved"] = _is_solved(result)
        result = _jsonable(result)
        _report_pool(req, result, acquired)
        _log_solve(req.type, req.sitekey, req.url, result)
        _job_put(job_id, {
            "status": "ready" if result.get("solved") else "error",
            "started_at": started,
            "result": result,
            "error": None if result.get("solved") else (result.get("error") or "not solved"),
        })
    except (TimeoutError, asyncio.TimeoutError):
        if acquired is not None:
            _report_pool(req, {"solved": False, "error": "solve deadline (408)"}, acquired)
        _job_put(job_id, {
            "status": "error",
            "started_at": started,
            "error": f"solve timed out after {max(90, int(req.timeout_s or 90))}s",
            "result": None,
        })
    except Exception as e:
        log.error("async job %s failed: %s", job_id, e, exc_info=True)
        if acquired is not None:
            _report_pool(req, {"solved": False, "error": str(e)[:120]}, acquired)
        _job_put(job_id, {
            "status": "error",
            "started_at": started,
            "error": str(e)[:400],
            "result": None,
        })
    finally:
        _solve_current.pop(task_id, None)


@app.post("/solve/async", tags=["solve"], operation_id="solveAsync",
          dependencies=[Depends(_bearer)],
          summary="Start solve in background (returns job_id immediately)")
async def solve_async(req: SolveRequest = Body(..., openapi_examples=_SOLVE_EXAMPLES)):
    """Start a solve job. Poll GET /solve/job/{job_id} until status is ready|error.

    Use this path when the HTTP client cannot hold a connection for 1–3 minutes
    (Cloudflare Worker → Modal edge often returns 524 on long sync /solve).

    Job state uses modal.Dict when configured (cross-container); disk is fallback.
    """
    _precheck(req)
    _job_gc()
    job_id = _secrets.token_hex(8)
    _job_put(job_id, {"status": "processing", "started_at": time.time(), "result": None, "error": None})
    sk = req.sitekey or ""
    log.info(
        "Solve async: job=%s type=%s sitekey=%s backend=%s",
        job_id, req.type, sk[:12],
        type(_job_backend).__name__ if _job_backend else "mem/disk",
    )
    asyncio.create_task(_run_job(job_id, req))
    return {"job_id": job_id, "status": "processing"}


@app.get("/solve/job/{job_id}", tags=["solve"], operation_id="solveJob",
         dependencies=[Depends(_bearer)],
         summary="Poll async solve job")
async def solve_job(job_id: str):
    j = _job_get(job_id)
    if not j:
        raise HTTPException(404, "job not found or expired")
    out: dict[str, Any] = {
        "job_id": job_id,
        "status": j.get("status"),
        "elapsed": round(time.time() - float(j.get("started_at") or time.time()), 1),
    }
    if j.get("status") == "ready" and j.get("result"):
        out.update(j["result"])
        out["solved"] = True
    elif j.get("status") == "error":
        out["solved"] = False
        out["error"] = j.get("error") or "not solved"
        if isinstance(j.get("result"), dict):
            for k in ("error", "method", "elapsed", "arkose_err", "ready", "mode"):
                if k in j["result"] and k not in out:
                    out[k] = j["result"][k]
    return out


@app.get("/logs", response_model=LogsResponse, tags=["monitoring"],
         operation_id="getLogs",
         dependencies=[Depends(_bearer)],
         summary="Recent solve events (ring buffer)")
async def get_logs(lines: int = Query(50, ge=1, le=200, description="How many recent events (max 200)")):
    """Last N solve events (max 200). Tokens are recorded as a boolean, never stored.
    `total` is the full ring-buffer size; `logs` is the requested slice of it."""
    # lines is already clamped to [1,200] by Query(ge/le) — no re-clamp needed.
    return {"logs": list(_solve_log)[:lines], "total": len(_solve_log)}


@app.get("/status", response_model=StatusResponse, tags=["monitoring"],
         operation_id="status",
         dependencies=[Depends(_bearer)],
         summary="Service status + currently running tasks")
async def solver_status():
    """Per-type online status and the list of in-flight solve tasks."""
    return {
        "services": {t: "online" for t in SUPPORTED},
        "current": list(_solve_current.values()),
    }


# ── Proxy pool management ─────────────────────────────────────────────
@app.get("/pool", tags=["pool"], operation_id="poolStatus",
         dependencies=[Depends(_bearer)],
         summary="Proxy pool status (health, uses, fails — credentials masked)")
async def pool_status():
    from solvers.common.proxypool import get_pool
    return get_pool().status()


@app.post("/pool", tags=["pool"], operation_id="poolAdd",
          dependencies=[Depends(_bearer)],
          summary="Add proxies to the pool (batch, deduped, persisted to proxies.txt)")
async def pool_add(body: dict = Body(..., examples={
    "batch": {"summary": "Paste a block of proxies",
               "value": {"proxies": ["http://user:pass@1.2.3.4:8080", "5.6.7.8:3128"]}}})):
    proxies = body.get("proxies") or []
    if not isinstance(proxies, list) or not proxies:
        raise HTTPException(400, "body must be {\"proxies\": [\"scheme://user:pass@host:port\", ...]}")
    from solvers.common.proxypool import get_pool
    added, skipped = get_pool().add([str(x) for x in proxies])
    st = get_pool().status()
    return {"added": added, "skipped": skipped, "size": st["size"], "healthy": st["healthy"]}


@app.delete("/pool", tags=["pool"], operation_id="poolRemove",
            dependencies=[Depends(_bearer)],
            summary="Remove one proxy (?proxy=host:port or ?all=1 to clear)")
async def pool_remove(proxy: Optional[str] = Query(None), all: bool = Query(False)):
    from solvers.common.proxypool import get_pool
    if all:
        return {"cleared": get_pool().clear()}
    if not proxy:
        raise HTTPException(400, "pass ?proxy=host:port or ?all=1")
    if not get_pool().remove(proxy):
        raise HTTPException(404, f"proxy not in pool: {proxy}")
    return {"removed": proxy}


@app.post("/pool/check", tags=["pool"], operation_id="poolCheck",
          dependencies=[Depends(_bearer)],
          summary="Health-check pool now (TCP always, HTTP via ?deep=1)")
async def pool_check(deep: bool = Query(False)):
    from solvers.common.proxypool import get_pool
    results = await get_pool().check_all(deep=deep)
    st = get_pool().status()
    return {"checked": len(results), "results": results,
            "size": st["size"], "healthy": st["healthy"]}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8877"))
    uvicorn.run(app, host="0.0.0.0", port=port)
