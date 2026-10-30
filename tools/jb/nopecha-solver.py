#!/usr/bin/env python3
"""nopecha-solver.py — Turnstile token solver untuk halaman demo NopeCHA.

Kapabilitas (dibangun dari hasil tes 2026-10-08, evidence:
/root/reports/nopecha_com_demo_cloudflare/):
  1. Buka URL demo nopecha di browser patchright (headed via Xvfb / auto-headless).
  2. Kalau kena Cloudflare managed challenge ("Just a moment..."): coba lewati
     dengan klik checkbox (maks --attempts, backoff) — EGRESS DATACENTER SERING
     GAGAL; hasil BLOCKED dilaporkan jujur, bukan diklaim lolos.
  3. Render semua widget Turnstile visible, klik checkbox-nya (mouse human-ish),
     tangkap token dari DOM (cf-turnstile-response) MAUPUN dari outbound POST
     ke api.nopecha.com/captcha/verify/turnstile.
  4. Opsional: verifikasi token via siteverify (test secret default, ganti
     dengan --secret untuk secret produksi).

Default target = /captcha/turnstile (path 200, widget live). Path
/demo/cloudflare diproteksi managed challenge (403) — pakai --url bila mau
mengujinya, siap menerima exit 3.

Exit codes:
  0 = minimal 1 token terbit (dan siteverify sukses bila diminta)
  2 = halaman termuat tapi TIDAK ada token terbit
  3 = managed challenge BLOCKED (tidak pernah lolos)
  1 = error teknis

Pemakaian:
  xvfb-run -a python3 nopecha-solver.py --siteverify
  python3 nopecha-solver.py --headless --url https://nopecha.com/captcha/turnstile
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://nopecha.com/captcha/turnstile"
TEST_SECRET = "1x0000000000000000000000000000000AA"  # Cloudflare always-pass test secret
SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def log(msg, buf):
    line = "[%s] %s" % (datetime.datetime.now().isoformat(timespec="seconds"), msg)
    print(line, flush=True)
    buf.append(line)


def siteverify(token, secret, label):
    data = urllib.parse.urlencode({"secret": secret, "response": token}).encode()
    req = urllib.request.Request(
        SITEVERIFY, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
        out = {"label": label, "http": r.status, "body": body,
               "latency_s": round(time.time() - t0, 2)}
        try:
            out["json"] = json.loads(body)
        except Exception:
            pass
    except Exception as e:
        out = {"label": label, "error": str(e), "latency_s": round(time.time() - t0, 2)}
    return out


def is_challenge(page):
    try:
        return "Just a moment" in (page.title() or "")
    except Exception:
        return False


def click_at(page, box, x_frac, y_frac):
    x = box["x"] + box["width"] * x_frac
    y = box["y"] + box["height"] * y_frac
    page.mouse.move(box["x"] + box["width"] * 0.12, box["y"] + box["height"] / 2, steps=14)
    time.sleep(0.6)
    page.mouse.click(x, y)


def solve(args, logbuf):
    from patchright.sync_api import sync_playwright

    result = {
        "target": args.url,
        "started": datetime.datetime.now().isoformat(timespec="seconds"),
        "status": "unknown",
        "challenge": [],
        "widgets": [],
        "verify_posts": [],
        "siteverify": [],
        "tokens": [],
        "errors": [],
    }

    def flush():
        if args.out:
            with open(args.out, "w") as f:
                json.dump(result, f, indent=2)

    with sync_playwright() as p:
        headless = args.headless
        if not headless and not os.environ.get("DISPLAY"):
            log("WARN: DISPLAY kosong -> auto headless (sebaiknya xvfb-run -a)", logbuf)
            headless = True
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1300, "height": 900},
                                  locale="en-US", timezone_id="Asia/Jakarta")
        page = ctx.new_page()

        # capture token yang dikirim demo ke backend nopecha (sumber token alternatif)
        def on_req(req):
            if "api.nopecha.com" in req.url and "verify" in req.url:
                rec = {"url": req.url, "method": req.method,
                       "post": (req.post_data or "")[:2000],
                       "ts": datetime.datetime.now().isoformat(timespec="seconds")}
                result["verify_posts"].append(rec)
                log("verify POST %s" % req.url, logbuf)

        def on_resp(resp):
            if "api.nopecha.com" in resp.url and "verify" in resp.url:
                try:
                    body = resp.text()[:500]
                except Exception:
                    body = "<unreadable>"
                for rec in result["verify_posts"]:
                    if rec["url"] == resp.url and not rec.get("resp"):
                        rec["resp"] = {"status": resp.status, "body": body}

        page.on("request", on_req)
        page.on("response", on_resp)

        log("goto %s" % args.url, logbuf)
        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout * 1000)

        # ---- lapis 1: managed challenge (opsional, jujur) ----
        if is_challenge(page):
            page.screenshot(path=os.path.join(args.save_dir, "challenge_initial.png"))
            for n in range(1, args.attempts + 1):
                log("challenge attempt %d/%d" % (n, args.attempts), logbuf)
                rec = {"n": n, "clicked": False, "solved": False}
                try:
                    loc = page.locator("iframe[title*='Cloudflare']")
                    box = loc.first.bounding_box()
                    if box:
                        click_at(page, box, 0.08, 0.5)
                        rec["clicked"] = True
                    else:
                        page.frame_locator("iframe[title*='Cloudflare']") \
                            .get_by_role("checkbox").click(timeout=5000)
                        rec["clicked"] = True
                except Exception as e:
                    result["errors"].append("challenge click%d: %s" % (n, e))
                    log("click err: %s" % e, logbuf)
                page.screenshot(path=os.path.join(args.save_dir, "challenge_attempt%d.png" % n))
                dl = time.time() + 20
                while time.time() < dl:
                    time.sleep(2)
                    if not is_challenge(page):
                        rec["solved"] = True
                        break
                result["challenge"].append(rec)
                if rec["solved"]:
                    break
                if n < args.attempts:
                    backoff = 6 + n * 4
                    log("backoff %ds" % backoff, logbuf)
                    time.sleep(backoff)
            if is_challenge(page):
                result["status"] = "challenge_blocked"
                page.screenshot(path=os.path.join(args.save_dir, "challenge_blocked.png"))
                log("STATUS=challenge_blocked (egress kena deteksi; jujur, bukan lolos)", logbuf)
                browser.close()
                result["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
                flush()
                return 3

        # ---- lapis 2: widget Turnstile ----
        try:
            page.wait_for_selector("iframe[title*='Cloudflare']",
                                   state="attached", timeout=15000)
        except Exception:
            log("tidak ada widget iframe di halaman ini", logbuf)
        time.sleep(2)
        result["page"] = {"title": page.title(), "url": page.url}
        result["iframes"] = page.eval_on_selector_all(
            "iframe",
            "els => els.map(e => ({title:e.title, id:e.id,"
            " w:e.getBoundingClientRect().width, h:e.getBoundingClientRect().height,"
            " src:(e.src||'').slice(0,180)}))")

        frames = page.locator("iframe[title*='Cloudflare']")
        visible = []
        for i in range(frames.count()):
            try:
                b = frames.nth(i).bounding_box()
            except Exception:
                b = None
            if b and b["width"] > 5 and b["height"] > 5:
                visible.append(i)
        log("widget visible: %s (total iframe: %d)" % (visible, frames.count()), logbuf)

        for i in visible:
            wrec = {"iframe_index": i, "clicks": 0, "token": None}
            try:
                for _ in range(3):
                    b = frames.nth(i).bounding_box()
                    if not b:
                        break
                    click_at(page, b, 0.07, 0.5)
                    wrec["clicks"] += 1
                    log("widget[%d] click #%d" % (i, wrec["clicks"]), logbuf)
                    page.screenshot(path=os.path.join(
                        args.save_dir, "widget_%d_click%d.png" % (i, wrec["clicks"])))
                    dl = time.time() + args.widget_wait
                    tok = ""
                    while time.time() < dl:
                        time.sleep(2)
                        vals = page.eval_on_selector_all(
                            "textarea[name='cf-turnstile-response'],"
                            "input[name='cf-turnstile-response']",
                            "els => els.map(e => e.value || '')")
                        nonempty = [v for v in vals if v]
                        # ambil token urutan ke-i di antara field non-kosong
                        tok = (nonempty[i] if i < len(nonempty)
                               else (nonempty[-1] if nonempty else ""))
                        if tok or result["verify_posts"]:
                            break
                    if tok:
                        wrec["token"] = {"len": len(tok), "value": tok}
                        break
            except Exception as e:
                wrec["error"] = str(e)
                result["errors"].append("widget[%d]: %s" % (i, e))
            result["widgets"].append(wrec)

        time.sleep(2)
        result["response_fields"] = page.eval_on_selector_all(
            "textarea[name='cf-turnstile-response'],input[name='cf-turnstile-response']",
            "els => els.map((e,idx) => ({idx, len:(e.value||'').length,"
            " head:(e.value||'').slice(0,60)}))")
        page.screenshot(path=os.path.join(args.save_dir, "final.png"), full_page=True)

        # ---- kumpulkan token unik ----
        tokens = []
        for w in result["widgets"]:
            if w.get("token"):
                tokens.append(("widget_%d" % w["iframe_index"], w["token"]["value"]))
        for f in result.get("response_fields", []):
            if f["len"]:
                vals = page.eval_on_selector_all(
                    "textarea[name='cf-turnstile-response'],input[name='cf-turnstile-response']",
                    "els => els.map(e => e.value || '')")
                v = vals[f["idx"]]
                if v and v not in [t for _, t in tokens]:
                    tokens.append(("dom_%d" % f["idx"], v))
        for k, vp in enumerate(result["verify_posts"]):
            m = re.search(r'"token"\s*:\s*"([^"]+)"', vp.get("post", ""))
            if m and m.group(1) not in [t for _, t in tokens]:
                tokens.append(("verify_post_%d" % k, m.group(1)))
        result["tokens"] = [{"src": s, "len": len(t), "head": t[:60]} for s, t in tokens]

        # ---- siteverify (opsional) ----
        if args.siteverify:
            for label, tok in tokens[:args.max_verify]:
                sv = siteverify(tok, args.secret, label)
                result["siteverify"].append(sv)
                log("siteverify[%s]: %s" % (label, json.dumps(sv.get("json", sv))[:220]), logbuf)
                time.sleep(3)

        browser.close()

    result["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    if not tokens:
        result["status"] = "no_token"
        code = 2
    elif args.siteverify:
        oks = [s for s in result["siteverify"] if (s.get("json") or {}).get("success")]
        if oks:
            result["status"] = "token_verified"
            code = 0
        else:
            result["status"] = "token_minted_verify_failed"
            code = 2
    else:
        result["status"] = "token_minted"
        code = 0
    log("STATUS=%s exit=%d" % (result["status"], code), logbuf)
    flush()
    return code


def main():
    ap = argparse.ArgumentParser(description="NopeCHA Turnstile demo token solver (jujur exit code-nya)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--attempts", type=int, default=3,
                    help="maks percobaan lewati managed challenge (default 3)")
    ap.add_argument("--widget-wait", type=int, default=18,
                    help="detik tunggu token per widget (default 18)")
    ap.add_argument("--timeout", type=int, default=60, help="navigasi timeout dtk")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--siteverify", action="store_true", help="verifikasi token via siteverify")
    ap.add_argument("--secret", default=TEST_SECRET,
                    help="secret untuk siteverify (default: test secret always-pass)")
    ap.add_argument("--max-verify", type=int, default=2)
    ap.add_argument("--out", default="nopecha_solver_result.json")
    ap.add_argument("--save-dir", default="nopecha_solver_shots")
    args = ap.parse_args()

    os.makedirs(args.save_dir, exist_ok=True)
    logbuf = []
    try:
        code = solve(args, logbuf)
    except Exception:
        traceback.print_exc()
        code = 1
        if args.out:
            try:
                json.dump({"status": "error", "traceback": traceback.format_exc()},
                          open(args.out, "w"), indent=2)
            except Exception:
                pass
    finally:
        try:
            with open(os.path.join(args.save_dir, "solver_log.txt"), "w") as f:
                f.write("\n".join(logbuf) + "\n")
        except Exception:
            pass
    # ringkasan stdout terakhir utk pemanggil
    if os.path.exists(args.out):
        try:
            r = json.load(open(args.out))
            print(json.dumps({"status": r.get("status"),
                              "tokens": r.get("tokens", []),
                              "siteverify": [
                                  {"label": s.get("label"),
                                   "success": (s.get("json") or {}).get("success")}
                                  for s in r.get("siteverify", [])]}, indent=2))
        except Exception:
            pass
    sys.exit(code)


if __name__ == "__main__":
    main()
