#!/usr/bin/env python3
"""Aliyun NoCaptcha (TRACELESS) solver via Camoufox.

Exit 0 = captcha passed (验证通过! detected).
Exit 1 = not passed. Artifacts always written to --out.

Requires:
    pip install "camoufox[geoip]"
    export CAMOUFOX_EXECUTABLE_PATH=/root/camoufox-bin/camoufox-bin
"""
import argparse
import json
import pathlib
import sys
import time

from camoufox.sync_api import Camoufox

PASS_TEXT = "验证通过"
FAIL_TEXT = "确认您不是机器人"

# Installed before any page script: capture the SDK config, its callbacks and the
# InitCaptchaV3 exchange so a failed run is diagnosable after the fact.
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


def human_click(page, x, y):
    """Approach the target along a path, then press. JS .click() often yields no
    telemetry the scorer will count; real pointer events are what matters."""
    page.mouse.move(x - 100, y - 50)
    page.wait_for_timeout(150)
    page.mouse.move(x, y, steps=18)
    page.wait_for_timeout(200)
    page.mouse.down()
    page.wait_for_timeout(80)
    page.mouse.up()


def run_once(args, out_dir, idx):
    result = {"run": idx, "solved": False, "detail": {}, "t0": time.time()}
    with Camoufox(headless=not args.headful, humanize=True, geoip=False,
                  locale=[args.locale, "en-US"],
                  window=(args.width, args.height)) as browser:
        page = browser.new_page()
        page.add_init_script(HOOK)
        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout)
        page.wait_for_timeout(2500)

        account = page.locator(args.user_selector)
        password = page.locator(args.pass_selector)
        account.click(timeout=10000)
        account.fill(args.username)
        password.click(timeout=10000)
        password.fill(args.password)

        boxes = page.locator('input[type=checkbox]')
        for i in range(boxes.count()):
            if i > 0 and not boxes.nth(i).is_checked():
                try:
                    boxes.nth(i).check(timeout=4000)
                except Exception:
                    boxes.nth(i).click(force=True)

        if args.warmup_ms:
            page.wait_for_timeout(args.warmup_ms)

        page.locator(args.submit_selector).first.click(timeout=10000)

        clicked = False
        for _ in range(50):
            page.wait_for_timeout(400)
            st = page.evaluate(STATE)
            chk = st.get("chk") or ""

            if PASS_TEXT in chk:
                result["solved"] = True
                result["detail"]["passed_text"] = chk
            if (st.get("popupCls") == "window-show" and st.get("rect")
                    and st["rect"]["w"] > 0 and not clicked):
                clicked = True
                r = st["rect"]
                human_click(page, r["x"] + 28, r["y"] + r["h"] / 2)
                result["detail"]["clicked_at"] = round(time.time() - result["t0"], 2)
            if result["solved"] and st.get("popupCls") == "window-hidden":
                break

        final = page.evaluate(STATE)
        result["detail"].update({
            "final_chk": final.get("chk"),
            "final_popup": final.get("popupCls"),
            "btn": final.get("btn"),
            "err": final.get("err"),
            "canvases": final.get("canvases"),
            "elapsed": round(time.time() - result["t0"], 2),
            "log": final.get("log"),
        })
        token = (final.get("state") or {}).get("success")
        if token:
            result["detail"]["token"] = str(token)[:300]
        for entry in final.get("log") or []:
            if "InitCaptchaV3" in str(entry.get("v")) or "CertifyId" in str(entry.get("v")):
                result["detail"]["init"] = str(entry["v"])[:250]

        try:
            page.screenshot(path=str(out_dir / f"run-{idx}.png"))
        except Exception:
            pass
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--username", default="captcha_probe_dummy")
    ap.add_argument("--password", default="Probe!2345xyz")
    ap.add_argument("--user-selector", default="#normal_login_account, input[type=text]")
    ap.add_argument("--pass-selector", default="input[type=password]")
    ap.add_argument("--submit-selector", default='button:has-text("登录")')
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--delay", type=float, default=30.0,
                    help="seconds between runs; bursts get throttled")
    ap.add_argument("--warmup-ms", type=int, default=0)
    ap.add_argument("--locale", default="zh-CN")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--timeout", type=int, default=60000)
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--out", default="evidence")
    args = ap.parse_args()

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    runs = []
    for i in range(1, args.runs + 1):
        print(f"===== run {i}/{args.runs} =====", flush=True)
        try:
            r = run_once(args, out_dir, i)
        except Exception as exc:
            r = {"run": i, "solved": False, "detail": {"error": str(exc)[:400]}}
        runs.append(r)
        print(f"  solved={r['solved']} chk={r['detail'].get('final_chk')} "
              f"t={r['detail'].get('elapsed')}s", flush=True)
        if i < args.runs:
            time.sleep(args.delay)

    solved = sum(1 for r in runs if r["solved"])
    summary = {"attempts": args.runs, "solved": solved, "rate": f"{solved}/{args.runs}", "runs": runs}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"\n===== {solved}/{args.runs} solved =====")
    return 0 if solved else 1


if __name__ == "__main__":
    sys.exit(main())
