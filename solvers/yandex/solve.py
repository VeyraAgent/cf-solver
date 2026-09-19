"""Yandex SmartCaptcha solver — browser-harvest (checkbox click / invisible execute).

Yandex Cloud SmartCaptcha (smartcaptcha.yandexcloud.net / smartcaptcha.cloud.yandex.ru)
renders its widget in cross-origin iframes (`checkbox.<lang>.<hash>.html` + a hidden
`backend` iframe + an `advanced` challenge iframe). All RE facts below were extracted
from the shipped `captcha.js` bundle AND verified live (2026-09-13) against a real
production deployment (metropol-moscow.ru booking page, sitekey ysc1_RCSc...).

RE facts (ground truth from captcha.js, not guesses):
  - The token is exposed on the host page as a hidden input
    `<input type="hidden" name="smart-token" data-testid="smart-token">` appended to
    the widget container (one per widget).
  - Public JS API on `window.smartCaptcha`: render / reset / destroy / showError /
    execute / **getResponse(widgetId)** / subscribe(widgetId, event, cb) / setTheme.
  - Subscribe events: `success` (callback receives the token), `challenge-visible`,
    `challenge-hidden`, `network-error`, `token-expired`, `javascript-error`.
  - Checkbox click target inside the checkbox iframe: the visible square is
    `.CheckboxCaptcha-Checkbox` (dataset.checked flips "false"->"true" on click);
    `input#js-button[role=checkbox]` stretches over the whole widget and clicking its
    CENTER (the label area) does NOT register — click the square.
  - On click the checkbox iframe POSTs `https://smartcaptcha.<host>/check?host=...&sitekey=...`;
    a silent pass fires `success` in ~1-2 s. A risky fingerprint escalates to a
    challenge (slider/puzzle) surfaced via `challenge-visible` + a visible
    `advanced-iframe`.
  - Token: base64url (~372 chars), decodes to
    `t=<unix_ts>;i=<client-ip>;D=<hex-signature>;u=<micro-ts>;h=<hex>` — the client IP
    is EMBEDDED, so the token is IP-bound: replay the validate() call from the same
    egress IP the solve ran on. One-time, TTL 5 min (official docs).

Flow implemented here (browser harvest, no protocol forgery):
  1. Navigate to the caller's `url` (the page embedding the widget).
  2. Install a per-frame init hook that subscribes widget ids 0..15 (widgets render
     sequentially; `window.smartCaptcha.widgets` is NOT a public property) and
     mirrors every event into `window.__yasc` (survives late renders + re-inits).
  3. Wait for the widget: checkbox iframe visible -> humanized click on the square;
     invisible-only page -> `smartCaptcha.execute(id)` fallback.
  4. Poll for the token via (a) captured `success` events, (b) `getResponse(id)`,
     (c) any non-empty `input[name="smart-token"]`. First non-empty wins.
  5. `challenge-visible` (authoritative), or an expanded advanced iframe that ALSO
     shows challenge text, while no token exists => honest failure:
     "puzzle requires interaction beyond checkbox".

The solver knows nothing about any specific site. The CALLER passes the SmartCaptcha-
fronted `url`. Cookies harvested after success (yandex* + page domain) ride along in
the result for session replay.
"""
import asyncio
import logging
import time

import cloakbrowser

from solvers.common.browser import browser_kwargs

log = logging.getLogger("yandex")

_solve_lock = asyncio.Lock()

_HOOK_JS = """
(() => {
  if (window.__yascHook) return;
  window.__yascHook = true;
  window.__yasc = {tokens: {}, events: [], hooked: {}};
  let tries = 0;
  const iv = setInterval(() => {
    tries += 1;
    const sc = window.smartCaptcha;
    if (!sc || typeof sc.subscribe !== 'function') return;
    // `window.smartCaptcha.widgets` is NOT a public property (it only appears in a
    // captcha.js error message); rendered widgets are numbered SEQUENTIALLY from 0,
    // so subscribe a fixed plausible range and skip ids that subscribe() rejects.
    for (let id = 0; id < 16; id++) {
      const k = 'w' + id;
      if (window.__yasc.hooked[k]) continue;
      try {
        sc.subscribe(id, 'success', (t) => {
          window.__yasc.tokens[k] = t || null;
          window.__yasc.events.push([k, 'success']);
        });
        sc.subscribe(id, 'challenge-visible', () => window.__yasc.events.push([k, 'challenge-visible']));
        sc.subscribe(id, 'challenge-hidden', () => window.__yasc.events.push([k, 'challenge-hidden']));
        sc.subscribe(id, 'network-error', () => window.__yasc.events.push([k, 'network-error']));
        sc.subscribe(id, 'token-expired', () => window.__yasc.events.push([k, 'token-expired']));
        sc.subscribe(id, 'javascript-error', () => window.__yasc.events.push([k, 'javascript-error']));
        window.__yasc.hooked[k] = true;
      } catch (e) {
        window.__yasc.hooked[k] = true;  // invalid id — do not retry
      }
    }
    if (tries > 240) clearInterval(iv);
  }, 250);
})();
"""

# Per-frame polling source: tokens from hook + getResponse + token inputs.
_COLLECT_JS = """
() => {
  const out = {tokens: [], events: [], challenge_size: false};
  const sc = window.smartCaptcha;
  const y = window.__yasc || {tokens: {}, events: []};
  for (const k in y.tokens) { if (y.tokens[k]) out.tokens.push(y.tokens[k]); }
  if (y.events && y.events.length) out.events.push(...y.events);
  if (sc && typeof sc.getResponse === 'function') {
    // widget ids are sequential; widgets[] is not public — probe a fixed range.
    for (let id = 0; id < 16; id++) {
      try { const t = sc.getResponse(id); if (t) out.tokens.push(t); }
      catch (e) { /* invalid id / destroyed */ }
    }
  }
  document.querySelectorAll('input[name="smart-token"]').forEach((i) => {
    if (i.value) out.tokens.push(i.value);
  });
  // DOM signal only — a REAL challenge also emits challenge-visible and shows
  // challenge text in its /advanced frame (read cross-origin via Playwright).
  const adv = document.querySelector('iframe[data-testid="advanced-iframe"]');
  out.challenge_size = !!(adv && adv.offsetHeight > 40);
  out.events = out.events.slice(-20);
  return out;
}
"""


def _kwargs(proxy: str = None) -> dict:
    return browser_kwargs("TURNSTILE", proxy=proxy)


async def _collect(page):
    """Gather token/event/challenge state from every accessible frame."""
    agg = {"tokens": [], "events": [], "challenge_size": False}
    for fr in page.frames:
        try:
            st = await fr.evaluate(_COLLECT_JS)
        except Exception:
            continue  # cross-origin/pending frames are skipped silently
        agg["tokens"].extend(st.get("tokens") or [])
        agg["events"].extend(st.get("events") or [])
        if st.get("challenge_size"):
            agg["challenge_size"] = True
    return agg



_ADV_MARKERS = ("выберите", "перетащите", "ползунок", "соберите", "защита",
                "select", "drag the", "slide to", "slide the")


def _looks_like_challenge(text):
    """Advanced-frame text actually showing a task (size alone false-positives on
    invisible-mode pages whose advanced iframe is always laid out large)."""
    if not text:
        return False
    t = text.lower()
    return any(m in t for m in _ADV_MARKERS)


async def _challenge_check(page, events, agg):
    """(is_challenge, text): event is authoritative; DOM size needs text proof."""
    if any(e[1] == "challenge-visible" for e in events):
        return True, await _harvest_challenge_text(page)
    if agg.get("challenge_size"):
        txt = await _harvest_challenge_text(page)
        if _looks_like_challenge(txt):
            return True, txt
    return False, None

async def _find_checkbox_frame(page):
    """Return (frame, iframe_element_handle) for the visible checkbox iframe."""
    for fr in page.frames:
        if "/checkbox." in (fr.url or ""):
            try:
                el = await fr.frame_element()
                return fr, el
            except Exception:
                continue
    return None, None


async def _click_checkbox(page, cb_frame, iframe_el):
    """Humanized click on the visible checkbox square (the ONLY spot that registers).

    The square is `.CheckboxCaptcha-Checkbox` (~28px, left side). Clicking the stretched
    `#js-button` input center (label area) flips nothing — verified live.
    """
    # Locate the square inside the frame to compute viewport coords.
    loc = cb_frame.locator(".CheckboxCaptcha-Checkbox").first
    box = None
    try:
        box = await loc.bounding_box()
    except Exception:
        box = None
    if box:
        iframe_box = await iframe_el.bounding_box()
        if iframe_box:
            # locator bounding_box() of a cross-frame element can be frame-relative;
            # always compose from the iframe element's viewport box + in-frame rect.
            rect = await cb_frame.evaluate(
                """() => { const e = document.querySelector('.CheckboxCaptcha-Checkbox');
                     if (!e) return null; const r = e.getBoundingClientRect();
                     return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }"""
            )
            if rect:
                ax = iframe_box["x"] + rect["x"]
                ay = iframe_box["y"] + rect["y"]
                await page.mouse.move(ax - 38, ay - 27)
                await asyncio.sleep(0.25)
                await page.mouse.move(ax - 6, ay - 4)
                await asyncio.sleep(0.2)
                await page.mouse.move(ax, ay)
                await asyncio.sleep(0.15)
                await page.mouse.down()
                await asyncio.sleep(0.07)
                await page.mouse.up()
                return True
    # Fallbacks: plain locator clicks (Playwright resolves the cross-origin frame).
    for sel in (".CheckboxCaptcha-Checkbox", "#js-button"):
        try:
            await cb_frame.locator(sel).first.click(timeout=5000)
            return True
        except Exception as e:
            log.info("yandex checkbox click via %s failed: %s", sel, e)
    return False


async def _checkbox_checked(cb_frame):
    try:
        return await cb_frame.evaluate(
            """() => { const e = document.querySelector('.CheckboxCaptcha-Checkbox');
                 return e ? e.dataset.checked === 'true' : null; }"""
        )
    except Exception:
        return None


async def _checkbox_error(cb_frame):
    try:
        txt = await cb_frame.evaluate("() => document.body.innerText.slice(0, 300)")
    except Exception:
        return None
    for marker in ("Ошибка:", "Error:"):
        i = txt.find(marker)
        if i >= 0:
            return txt[i:i + 120].replace("\n", " ").strip()
    return None


async def _harvest_challenge_text(page):
    """Best-effort read of the advanced (challenge) iframe contents."""
    for fr in page.frames:
        if "/advanced." in (fr.url or ""):
            try:
                return await fr.evaluate(
                    "() => document.body.innerText.slice(0, 200).replace(/\\n/g, ' | ')")
            except Exception:
                return None
    return None


def _relevant_cookies(cookies, page_url):
    """Cookies for yandex infra + the page's own host, for session replay."""
    host = (page_url.split("//", 1)[-1].split("/")[0] or "").lower()
    out = {}
    for c in cookies or []:
        dom = (c.get("domain") or "").lstrip(".").lower()
        if not dom:
            continue
        if dom in ("yandex.ru", "yandex.net", "yandexcloud.net", "cloud.yandex.ru",
                   "ya.ru") or dom.endswith((".yandex.ru", ".yandex.net",
                                             ".yandexcloud.net")) or dom in host:
            out[c["name"]] = c.get("value", "")
    return out


async def solve_yandex(url: str = None, proxy: str = None, timeout_s: int = 60) -> dict:
    """Harvest a Yandex SmartCaptcha token from the page at `url`.

    url      : REQUIRED. The page embedding the SmartCaptcha widget (caller's page;
               the solver renders nothing itself — it harvests what the site shows).
    proxy    : optional `http://user:pass@host:port` — the token is IP-bound, replay
               must originate from the same egress IP.
    timeout_s: overall budget (default 60).
    """
    t0 = time.monotonic()

    def _err(error, **extra):
        out = {"solved": False, "type": "yandex", "token": None, "method": "browser-harvest",
               "elapsed": round(time.monotonic() - t0, 1), "error": error,
               "url": url, "proxy": bool(proxy)}
        out.update(extra)
        return out

    if not url:
        return _err("url is required (the page embedding the Yandex SmartCaptcha widget)")

    async with _solve_lock:
        try:
            async with await cloakbrowser.launch_async(**_kwargs(proxy)) as browser:
                return await _drive(browser, url, proxy, timeout_s, t0)
        except Exception as e:
            log.warning("yandex solve failed: %s", e)
            return _err(f"browser/solve failed: {e}")


async def _drive(browser, url: str, proxy: str, timeout_s: int, t0: float) -> dict:
    def _err(error, **extra):
        out = {"solved": False, "type": "yandex", "token": None, "method": "browser-harvest",
               "elapsed": round(time.monotonic() - t0, 1), "error": error,
               "url": url, "proxy": bool(proxy)}
        out.update(extra)
        return out

    ctx = await browser.new_context()
    await ctx.add_init_script(_HOOK_JS)
    page = await ctx.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        log.warning("yandex goto: %s", e)

    deadline = time.monotonic() + max(timeout_s, 15)
    method = "browser-harvest"
    clicked = False
    executed = False
    challenge_seen = None
    widget_events = []

    def _merge_events(events):
        widget_events[:] = (widget_events + list(events or []))[-20:]

    # Phase 1: bounded wait for the widget. Checkbox iframe visible -> click flow;
    # invisible-only page (widgets rendered, no checkbox iframe) -> documented
    # execute() fallback. A token may already arrive here (auto pre-check pass).
    cb_frame = iframe_el = None
    cb_wait = time.monotonic() + min(20.0, max(deadline - time.monotonic() - 15, 5))
    while time.monotonic() < cb_wait:
        agg = await _collect(page)
        if agg["tokens"]:
            return await _success(ctx, page, agg["tokens"][0], method, t0, url, proxy,
                                  widget_events + agg["events"])
        _merge_events(agg["events"])
        challenge, challenge_seen = await _challenge_check(page, widget_events, agg)
        if challenge:
            await ctx.close()
            return _err("puzzle requires interaction beyond checkbox",
                        challenge_text=challenge_seen, events=widget_events)
        cb_frame, iframe_el = await _find_checkbox_frame(page)
        if cb_frame and iframe_el:
            break
        await asyncio.sleep(1)

    if not cb_frame:
        # Invisible-only page: try documented execute() on rendered widgets.
        # widgets[] is not public — a rendered widget shows as a smart-token input.
        n = await page.evaluate(
            "() => document.querySelectorAll('input[name=\"smart-token\"]').length")
        if not n:
            await ctx.close()
            return _err("no SmartCaptcha widget found on page (no checkbox iframe, "
                        "no rendered widget) — page may lazy-load it behind an "
                        "interaction the caller must trigger")
        for wid in range(min(int(n), 16)):
            try:
                await page.evaluate(
                    "((id) => { try { window.smartCaptcha.execute(id); return true; } "
                    "catch (e) { return false; } })", wid)
                executed = True
                log.info("yandex execute(widget %s)", wid)
            except Exception as e:
                log.info("yandex execute(%s) failed: %s", wid, e)
        if not executed:
            await ctx.close()
            return _err("SmartCaptcha widgets present but execute() failed on all of them")
        method = "invisible-execute"
    else:
        # Phase 2: humanized checkbox click (retry until the square flips to checked).
        while time.monotonic() < deadline and not clicked:
            if await _click_checkbox(page, cb_frame, iframe_el):
                for _ in range(4):
                    st = await _checkbox_checked(cb_frame)
                    if st is True:
                        clicked = True
                        log.info("yandex checkbox clicked (checked=true)")
                        break
                    await asyncio.sleep(0.5)
            if not clicked:
                cb_frame, iframe_el = await _find_checkbox_frame(page)
                if not cb_frame:
                    break
                await asyncio.sleep(1)
        if not clicked:
            ferr = await _checkbox_error(cb_frame) if cb_frame else None
            await ctx.close()
            return _err(ferr or "checkbox click did not register "
                                "(.CheckboxCaptcha-Checkbox stayed unchecked)")

    # Phase 3: poll for token; surface challenge escalation honestly.
    while time.monotonic() < deadline:
        agg = await _collect(page)
        if agg["tokens"]:
            return await _success(ctx, page, agg["tokens"][0], method, t0, url, proxy,
                                  widget_events + agg["events"])
        _merge_events(agg["events"])
        challenge, challenge_seen = await _challenge_check(page, widget_events, agg)
        if challenge:
            break
        if cb_frame:
            ferr = await _checkbox_error(cb_frame)
            if ferr:
                await ctx.close()
                return _err(f"widget error: {ferr}", events=widget_events)
        await asyncio.sleep(1)

    await ctx.close()

    if challenge_seen is not None or any(ev[1] == "challenge-visible"
                                         for ev in widget_events):
        return _err("puzzle requires interaction beyond checkbox",
                    challenge_text=challenge_seen, events=widget_events)
    reason = ("execute() fired but no token arrived (invisible pre-check may have "
              "failed)" if method == "invisible-execute" else
              "checkbox accepted but no success event")
    return _err(f"no token within {timeout_s}s ({reason}; Yandex may be scoring "
                f"this IP/fingerprint as risky)", events=widget_events)


async def _success(ctx, page, token, method, t0, url, proxy, events) -> dict:
    ua = None
    try:
        ua = await page.evaluate("() => navigator.userAgent")
    except Exception:
        pass
    try:
        cookies = await ctx.cookies()
    except Exception:
        cookies = []
    return {
        "solved": True,
        "type": "yandex",
        "token": token,
        "method": method,
        "elapsed": round(time.monotonic() - t0, 1),
        "error": None,
        "url": url,
        "proxy": bool(proxy),
        "user_agent": ua,
        "cookies": _relevant_cookies(cookies, url),
        "events": events[-10:],
        "warning": ("SmartCaptcha token is one-time, TTL ~5 min and IP-bound "
                    "(client IP is embedded). Validate from the SAME egress IP via "
                    "POST https://smartcaptcha.yandexcloud.net/validate "
                    "(secret_key + token) within the TTL."),
    }
