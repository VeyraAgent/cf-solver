"""Aliyun NoCaptcha **TRACELESS** mode — the checkbox with no puzzle.

`solvers/aliyun` handles the slide/overlay challenges (`scene_id` + `prefix`,
pure protocol). TRACELESS is a different animal: `InitCaptchaV3` answers
`CaptchaType: "TRACELESS"`, the widget renders a bare `确认您不是机器人`
checkbox, and the score comes entirely from device/browser telemetry. There is
nothing visual to answer — so the only thing that works is a *real* anti-detect
browser that emits believable telemetry, clicking the box with real pointer
events.

That is what this solver is: Camoufox (Firefox, humanised) → open the host
flow → fill the form → submit → wait for the popup → humanised click →
verify `验证通过`.

Measured on `sso.openxlab.org.cn` (the reference run): 4/5 (80%), 34–39 s each.
Failures stop at the checkbox (challenge rejected at init) and correlate with
long runs — throttle, not a bad click. Give it ≥30 s between runs and rotate
IPs, or the rate will fall.

Env notes:
  * Camoufox must be importable in the running interpreter (`pip install camoufox`).
  * If the binary lives outside the cache, set `CAMOUFOX_EXECUTABLE_PATH`.
  * `humanize=True` is mandatory — it is what makes the pointer telemetry count.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

PASS_TEXT = "验证通过"
FAIL_TEXT = "确认您不是机器人"

# Installed before any page script: capture the SDK config, its callbacks and the
# InitCaptchaV3 exchange so a failed run stays diagnosable after the fact.
HOOK = r"""
(() => {
  if (window.__hooked) return 'already';
  window.__hooked = true;
  window.__log = [];
  window.__state = {};
  const L = (tag, v) => {
    try { window.__log.push({tag, v: (typeof v === 'string' ? v : JSON.stringify(v)).slice(0, 1000)}); } catch (e) {}
  };
  window.__glog = () => window.__log;

  const origInit = window.initAliyunCaptcha;
  if (typeof origInit === 'function') {
    window.initAliyunCaptcha = function (cfg) {
      try {
        L('cfg', {SceneId: cfg.SceneId, mode: cfg.mode,
                  CaptchaType: cfg.CaptchaType, verifyType: cfg.verifyType});
      } catch (e) {}
      ['success', 'failCb', 'fallbackCb', 'onBizResultCallback', 'onClose'].forEach(name => {
        const fn = cfg[name];
        if (typeof fn === 'function') {
          cfg[name] = function (v) {
            L('cb:' + name, v);
            window.__state[name] = v;
            return fn.apply(this, arguments);
          };
        }
      });
      return origInit.apply(this, arguments);
    };
  }

  const oOpen = XMLHttpRequest.prototype.open;
  const oSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__m = m; this.__u = u; return oOpen.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener('load', () => {
      try { L('XHR<', String(this.__u).slice(0, 90) + ' -> ' + String(this.responseText).slice(0, 500)); } catch (e) {}
    });
    return oSend.apply(this, arguments);
  };
  return 'hooked';
})()
"""

STATE = r"""
(() => {
  const popup = document.getElementById('aliyunCaptcha-window-popup');
  const body = document.getElementById('aliyunCaptcha-checkbox-body');
  const r = body ? body.getBoundingClientRect() : null;
  return {
    log: window.__glog ? window.__glog() : [],
    state: window.__state || {},
    popupCls: popup ? popup.className : null,
    chk: (document.getElementById('aliyunCaptcha-checkbox-text') || {}).textContent,
    err: (document.getElementById('aliyunCaptcha-errorCode') || {}).textContent,
    btn: (document.querySelector('button[class*=login]') || {}).textContent,
    rect: r ? {x: r.x, y: r.y, w: r.width, h: r.height} : null,
    canvases: document.querySelectorAll('canvas').length,
  };
})()
"""


def _human_click(page, x: float, y: float) -> None:
    """Approach along a path, then press — JS `.click()` emits no scored telemetry."""
    page.mouse.move(x - 100, y - 50)
    page.wait_for_timeout(150)
    page.mouse.move(x, y, steps=18)
    page.wait_for_timeout(200)
    page.mouse.down()
    page.wait_for_timeout(80)
    page.mouse.up()


def _run_once(*, url: str, username: str, password: str, user_selector: str,
              pass_selector: str, submit_selector: str, headless: bool,
              locale: str, viewport: tuple[int, int], warmup_ms: int,
              proxy: str | None, out_dir: Path) -> dict:
    """Blocking Camoufox session — called via asyncio.to_thread (sync Playwright API)."""
    from camoufox.sync_api import Camoufox  # lazy + host-dependent

    t0 = time.time()
    out = {"solved": False, "detail": {}, "t0": t0}
    kwargs: dict = {"headless": headless, "humanize": True, "geoip": False,
                    "locale": [locale, "en-US"], "window": viewport}
    if proxy:
        kwargs["proxy"] = {"server": proxy} if "@" not in proxy else _proxy_dict(proxy)

    with Camoufox(**kwargs) as browser:
        page = browser.new_page()
        page.add_init_script(HOOK)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        try:
            account = page.locator(user_selector).first
            account.click(timeout=10000)
            account.fill(username)
        except Exception as e:
            out["detail"]["fill_user_error"] = str(e)[:160]
        if password:
            try:
                pw = page.locator(pass_selector).first
                pw.click(timeout=10000)
                pw.fill(password)
            except Exception as e:
                out["detail"]["fill_pass_error"] = str(e)[:160]

        # consent checkboxes on the host form (skip the first, usually "remember me")
        boxes = page.locator("input[type=checkbox]")
        for i in range(boxes.count()):
            if i > 0 and not boxes.nth(i).is_checked():
                try:
                    boxes.nth(i).check(timeout=4000)
                except Exception:
                    try:
                        boxes.nth(i).click(force=True)
                    except Exception:
                        pass

        if warmup_ms:
            page.wait_for_timeout(warmup_ms)

        try:
            page.locator(submit_selector).first.click(timeout=10000)
        except Exception as e:
            out["detail"]["submit_error"] = str(e)[:160]

        clicked = False
        for _ in range(50):
            page.wait_for_timeout(400)
            st = page.evaluate(STATE)
            chk = st.get("chk") or ""
            if PASS_TEXT in chk:
                out["solved"] = True
                out["detail"]["passed_text"] = chk
            if (st.get("popupCls") == "window-show" and st.get("rect")
                    and st["rect"]["w"] > 0 and not clicked):
                clicked = True
                r = st["rect"]
                _human_click(page, r["x"] + 28, r["y"] + r["h"] / 2)
                out["detail"]["clicked_at"] = round(time.time() - out["t0"], 2)
            if out["solved"] and st.get("popupCls") == "window-hidden":
                break

        final = page.evaluate(STATE)
        out["detail"].update({
            "final_chk": final.get("chk"),
            "final_popup": final.get("popupCls"),
            "btn": final.get("btn"),
            "err": final.get("err"),
            "canvases": final.get("canvases"),
            "elapsed": round(time.time() - out["t0"], 2),
            "log": (final.get("log") or [])[-40:],
        })
        token = (final.get("state") or {}).get("success")
        if token:
            out["detail"]["token"] = str(token)[:300]
        for entry in final.get("log") or []:
            if "InitCaptchaV3" in str(entry.get("v")) or "CertifyId" in str(entry.get("v")):
                out["detail"]["init"] = str(entry["v"])[:250]
        out["detail"]["final_chk_raw"] = final.get("chk")
        try:
            page.screenshot(path=str(out_dir / "aliyun_traceless.png"))
        except Exception:
            pass
    return out


def _proxy_dict(proxy: str) -> dict:
    """user:pass@host:port → playwright proxy dict."""
    from urllib.parse import urlsplit

    raw = proxy if "://" in proxy else "http://" + proxy
    u = urlsplit(raw)
    d = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        d["username"] = u.username
    if u.password:
        d["password"] = u.password
    return d


async def solve_aliyun_traceless(url: str, username: str | None = None,
                                 password: str | None = None,
                                 user_selector: str = "#normal_login_account, input[type=text]",
                                 pass_selector: str = "input[type=password]",
                                 submit_selector: str = 'button:has-text("登录")',
                                 proxy: str | None = None, timeout_s: int = 180,
                                 headless: bool = True, locale: str = "zh-CN",
                                 viewport: tuple[int, int] = (1920, 1080),
                                 warmup_ms: int = 0,
                                 out_dir: str = "/tmp/tcaptcha") -> dict:
    """Pass a TRACELESS Aliyun checkbox. `solved` ⇔ `验证通过` was observed."""
    t0 = time.monotonic()
    if not url:
        return {"solved": False, "method": "aliyun-traceless", "elapsed": 0.0,
                "error": "url is required"}
    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)
    try:
        import importlib.util

        if importlib.util.find_spec("camoufox.sync_api") is None:
            raise RuntimeError("camoufox not installed in this interpreter "
                               "(pip install 'camoufox[geoip]')")
        r = await asyncio.wait_for(
            asyncio.to_thread(
                _run_once, url=url, username=username or "captcha_probe_dummy",
                password=password or "Probe!2345xyz", user_selector=user_selector,
                pass_selector=pass_selector, submit_selector=submit_selector,
                headless=headless, locale=locale, viewport=viewport,
                warmup_ms=int(warmup_ms or 0), proxy=proxy, out_dir=od),
            timeout=max(60, int(timeout_s or 180)))
    except asyncio.TimeoutError:
        return {"solved": False, "method": "aliyun-traceless",
                "elapsed": round(time.monotonic() - t0, 1),
                "error": f"timed out after {timeout_s}s"}
    except Exception as e:
        return {"solved": False, "method": "aliyun-traceless",
                "elapsed": round(time.monotonic() - t0, 1),
                "error": f"{type(e).__name__}: {e}"[:240]}

    detail = r.get("detail") or {}
    solved = bool(r.get("solved"))
    return {"solved": solved, "passed": solved, "verify_success": solved,
            "method": "aliyun-traceless",
            "token": detail.get("token") or "",
            "elapsed": round(time.monotonic() - t0, 1),
            "detail": {k: v for k, v in detail.items() if k != "log"},
            **({} if solved else {"error": detail.get("err") or
                                  f"checkbox state: {detail.get('final_chk')!r}"})}


async def solve(*args, **kwargs) -> dict:  # pragma: no cover - dispatch alias
    return await solve_aliyun_traceless(*args, **kwargs)


if __name__ == "__main__":  # python -m solvers.aliyun_traceless.solve <url>
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m solvers.aliyun_traceless.solve <url> [user] [pass]")
    print(asyncio.run(solve_aliyun_traceless(
        sys.argv[1], *sys.argv[2:4], headless=True)))
