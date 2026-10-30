"""Browser session: launch, egress proxy, and the captcha-asset workaround.

The measured failure this module exists for:

    curl  -x PROXY https://turing.captcha.qcloud.com/TCaptcha.js   -> 200 / 136901
    Chromium through the same PROXY                               -> ERR_EMPTY_RESPONSE

Cheap proxy pools drop concurrent CONNECTs. Chromium opens many at once, so
TCaptcha.js never loads, the widget never attaches, and a solver reports a
misleading "IFRAME NOT FOUND". Fix: fetch the four static captcha assets
out-of-band (urllib through the proxy succeeds where Chromium does not) and
fulfill those exact URLs from memory via page.route.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

from .config import Config, ConfigError, redact
from .log import log

CAPTCHA_ASSETS = (
    "https://turing.captcha.qcloud.com/TCaptcha.js",
    "https://turing.captcha.qcloud.com/tcaptcha-frame.ba387dd1.js",
    "https://turing.captcha.gtimg.com/1/template/drag_ele.html",
    "https://turing.captcha.gtimg.com/1/tcaptcha-frame.ba387dd1.js",
)

# Third-party telemetry we can safely drop to keep proxy connection pressure low.
# DELIBERATELY EXCLUDES data.hailuo.ai: the MiniMax SPA hangs waiting on it.
TRACKING_HOSTS = (
    "facebook", "doubleclick", "googlesyndication", "google-analytics",
    "analytics.google", "googleads", "bat.bing", "analytics.twitter",
    "alb.reddit", "reddit.com", "t.co/", "clarity.ms",
)

LAUNCH_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]

# Proxy ladder (skill lesson: a dead proxy consumed a whole run - screen
# candidates with `curl -x PROXY ipify + target` BEFORE spawning, then let
# the run retry over up to 4 rotating candidates).
MAX_PROXY_CANDIDATES = 4
PROXY_SETTLE_S = 4.0        # curl health-check -> 4s settle -> spawn
PROBE_TIMEOUT_S = 6.0
PROBE_TARGETS = (
    "https://api.ipify.org?format=json",           # generic egress
    "https://turing.captcha.qcloud.com/TCaptcha.js",  # the actual dependency
)


def probe_proxy(proxy_url: str, targets=PROBE_TARGETS,
                timeout: float = PROBE_TIMEOUT_S) -> tuple[bool, str]:
    """urllib equivalent of `curl -x PROXY URL` - True only if every target answers.

    This is the health gate the pool ladder needs: a proxy that cannot fetch
    ipify AND the captcha dependency must never reach browser spawn.
    """
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    for url in targets:
        name = url.rsplit("/", 1)[-1] or url
        try:
            resp = opener.open(url, timeout=timeout)
            status = getattr(resp, "status", 200)
            head = resp.read(1024)
        except Exception as exc:  # noqa: BLE001 - transport variance is the point
            return False, f"{name}: {exc}"
        if status != 200 or not head:
            return False, f"{name}: status={status} bytes={len(head)}"
    return True, "egress+target 200"


def select_healthy_proxy(cfg: Config) -> str | None:
    """Pick the first healthy candidate (<=4), settle, and pin it on cfg.

    Returns the chosen raw proxy url, or None for a direct (no proxy) run.
    Raises ConfigError when every candidate fails - fail closed before any
    browser time is spent instead of dying inside the flow with a
    misleading IFRAME NOT FOUND.
    """
    cands = list(cfg.proxy_candidates or ([cfg.proxy] if cfg.proxy else []))
    if not cands:
        return None
    tried = cands[:MAX_PROXY_CANDIDATES]
    for i, cand in enumerate(tried, 1):
        ok, detail = probe_proxy(cand)
        log("proxy-check", f"candidate {i}/{len(tried)} {redact(cand)}: "
                           f"{'OK' if ok else 'FAIL'} ({detail})")
        if ok:
            if PROXY_SETTLE_S:
                time.sleep(PROXY_SETTLE_S)   # health check -> settle -> spawn
            cfg.proxy = cand
            return cand
    raise ConfigError(
        f"no healthy proxy candidate ({len(tried)} tried, all failed "
        f"{'+'.join(u.rsplit('/', 1)[-1] or 'url' for u in PROBE_TARGETS)}); "
        "check the pool or run without a proxy")


def prefetch_assets(proxy_url: str | None, urls=CAPTCHA_ASSETS, timeout: float = 25.0) -> dict:
    """Download captcha static assets (through the proxy when one is set)."""
    cache: dict[str, bytes] = {}
    handlers = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()
    for url in urls:
        try:
            resp = opener.open(url, timeout=timeout)
            data = resp.read()
            if getattr(resp, "status", 200) == 200 and len(data) > 500:
                cache[url] = data
            else:
                log("asset", f"BAD {url.rsplit('/', 1)[-1]} status={getattr(resp, 'status', '?')}")
        except Exception as exc:  # noqa: BLE001 - network variance is expected here
            log("asset", f"ERR {url.rsplit('/', 1)[-1]}: {exc}")
    log("asset", f"cached {len(cache)}/{len(urls)} captcha assets")
    return cache


def install_asset_route(page, cache: dict) -> None:
    """Serve pre-fetched assets from memory; drop tracking; pass everything else.

    NOTE: the handler MUST be a plain sync function. With Playwright's sync API
    an `async def` handler is never awaited and every request hangs, which
    silently breaks the whole page.
    """
    if not cache:
        return

    def handler(route):
        url = route.request.url
        if url in cache:
            route.fulfill(
                status=200,
                body=cache[url],
                content_type="application/javascript" if url.endswith(".js") else "text/html",
            )
        elif any(token in url for token in TRACKING_HOSTS):
            route.abort()
        else:
            route.continue_()

    page.route("**/*", handler)


def ensure_chromium(pw) -> str:
    """Provision the browser binary - the ONE thing this tool downloads.

    Nothing else is fetched at install time: credentials come from env/CLI,
    proxies from the environment, and the four captcha assets are prefetched
    through the proxy at solve time. Chromium, though, is mandatory and is
    provisioned on demand with Playwright's own downloader:

        python -m playwright install chromium

    Returns the absolute path of the browser executable.
    """
    exe = pw.chromium.executable_path
    if exe and os.path.exists(exe):
        return exe
    log("browser", "chromium missing -> running `python -m playwright install chromium`")
    try:
        res = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=900,
        )
        tail = ((res.stderr or "") + (res.stdout or ""))[-300:]
    except Exception as exc:  # noqa: BLE001 - surfaced with context below
        raise RuntimeError(f"chromium install failed: {exc}") from exc
    if not (exe and os.path.exists(exe)):
        raise RuntimeError(f"chromium still missing at {exe}: {tail.strip() or 'no output'}")
    log("browser", f"chromium installed -> {exe}")
    return exe


class Session:
    """Owns the Playwright objects for one solve."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pw = None
        self.browser = None
        self.ctx = None
        self.page = None

    def __enter__(self) -> "Session":
        from playwright.sync_api import sync_playwright as _sp
        # sync_playwright() returns a context manager; .start() returns the
        # Playwright object, and that is the thing carrying .stop().
        self._pw = _sp().start()
        ensure_chromium(self._pw)
        self.browser = self._pw.chromium.launch(headless=self.cfg.headless, args=LAUNCH_ARGS)
        proxy = self.cfg.proxy_dict
        if proxy:
            log("proxy", self.cfg.proxy_label)
        self.ctx = self.browser.new_context(
            viewport={"width": self.cfg.viewport[0], "height": self.cfg.viewport[1]},
            proxy=proxy,
            user_agent=self.cfg.user_agent,
        )
        self.page = self.ctx.new_page()
        if self.cfg.proxy:
            cache = prefetch_assets(self.cfg.proxy)
            install_asset_route(self.page, cache)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()
