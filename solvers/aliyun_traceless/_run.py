"""Subprocess runner for the TRACELESS solver: one Camoufox session, one JSON line.

Why a subprocess: Camoufox ships a **sync** Playwright driver. Running it in the
same process as the async drivers the other solvers use (cloakbrowser/Playwright
async) makes the sync driver lose its connection object
(`'Connection' object has no attribute 'call_on_object_with_known_name'`), and a
sync browser inside uvicorn's loop would block it anyway. A dedicated process
gives a clean driver and isolates a Firefox crash.

Usage:  python -m solvers.aliyun_traceless._run   (payload JSON on stdin)
Out:    __ALIYUN_TRACELESS__<json>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

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


def human_click(page, x: float, y: float) -> None:
    """Approach along a path, then press — JS `.click()` emits no scored telemetry."""
    page.mouse.move(x - 100, y - 50)
    page.wait_for_timeout(150)
    page.mouse.move(x, y, steps=18)
    page.wait_for_timeout(200)
    page.mouse.down()
    page.wait_for_timeout(80)
    page.mouse.up()


def proxy_dict(proxy: str) -> dict:
    """user:pass@host:port → playwright proxy dict."""
    from urllib.parse import urlsplit

    raw = proxy if "://" in proxy else "http://" + proxy
    u = urlsplit(raw)
    d: dict = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        d["username"] = u.username
    if u.password:
        d["password"] = u.password
    return d


def run_once(cfg: dict) -> dict:
    from camoufox.sync_api import Camoufox

    t0 = time.time()
    out: dict = {"solved": False, "detail": {}, "t0": t0}
    vw = cfg.get("viewport") or [1920, 1080]
    kwargs: dict = {"headless": bool(cfg.get("headless", True)), "humanize": True,
                    "geoip": False, "locale": [cfg.get("locale") or "zh-CN", "en-US"],
                    "window": (int(vw[0]), int(vw[1]))}
    if cfg.get("proxy"):
        kwargs["proxy"] = proxy_dict(cfg["proxy"])

    out_dir = Path(cfg.get("out_dir") or "/tmp/tcaptcha")
    out_dir.mkdir(parents=True, exist_ok=True)

    with Camoufox(**kwargs) as browser:
        page = browser.new_page()
        page.add_init_script(HOOK)
        page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        try:
            account = page.locator(cfg["user_selector"]).first
            account.click(timeout=10000)
            account.fill(cfg.get("username") or "captcha_probe_dummy")
        except Exception as e:  # noqa: BLE001
            out["detail"]["fill_user_error"] = str(e)[:160]
        if cfg.get("password"):
            try:
                pw = page.locator(cfg["pass_selector"]).first
                pw.click(timeout=10000)
                pw.fill(cfg["password"])
            except Exception as e:  # noqa: BLE001
                out["detail"]["fill_pass_error"] = str(e)[:160]

        # consent checkboxes on the host form (skip the first: usually "remember me")
        boxes = page.locator("input[type=checkbox]")
        for i in range(boxes.count()):
            if i > 0 and not boxes.nth(i).is_checked():
                try:
                    boxes.nth(i).check(timeout=4000)
                except Exception:  # noqa: BLE001
                    try:
                        boxes.nth(i).click(force=True)
                    except Exception:  # noqa: BLE001
                        pass

        warmup = int(cfg.get("warmup_ms") or 0)
        if warmup:
            page.wait_for_timeout(warmup)

        try:
            page.locator(cfg["submit_selector"]).first.click(timeout=10000)
        except Exception as e:  # noqa: BLE001
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
                human_click(page, r["x"] + 28, r["y"] + r["h"] / 2)
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
        # try:
        # page.screenshot(path=str(out_dir / "aliyun_traceless.png"))
        # except Exception:
        #     pass
    return out


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    try:
        result = run_once(payload)
    except Exception as e:  # noqa: BLE001
        result = {"solved": False, "detail": {"error": f"{type(e).__name__}: {e}"[:400]}}
    print("__ALIYUN_TRACELESS__" + json.dumps(result, default=str))
    return 0 if result.get("solved") else 1


if __name__ == "__main__":
    sys.exit(main())
