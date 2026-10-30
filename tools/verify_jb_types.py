"""Regression sweep for the jb-drop types (friendly_v2, awswaf_js, ocr, tcaptcha,
aliyun_traceless) plus one pre-existing type and the async job path.

Usage:  SOLVER_URL=http://127.0.0.1:8877 python tools/verify_jb_types.py
Exit 0 = every check passed. Live network needed for friendly_v2 / awswaf_js / tencent.
"""
"""Final verification sweep for the jb-drop types wired into cf-solver."""
import base64
import io
import json
import subprocess
import sys
import time
import urllib.request

import numpy as np
from PIL import Image, ImageDraw

import os

BASE = os.getenv("SOLVER_URL", "http://127.0.0.1:8877")


def post(path, payload, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return d, round(time.time() - t0, 1)


def get(path, timeout=30):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


def b64(im):
    b = io.BytesIO()
    im.save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()


def slider_pair():
    rng = np.random.default_rng(11)
    W, H, PW, PH, TX, TY = 300, 160, 60, 60, 180, 50
    bg = rng.normal(128, 30, (H, W, 3)).clip(0, 255).astype(np.uint8)
    orig = bg.copy()
    bg[TY:TY + PH, TX:TX + PW] = (bg[TY:TY + PH, TX:TX + PW].astype(np.int16) * 0.6).clip(0, 255).astype(np.uint8)
    img = Image.fromarray(bg)
    ImageDraw.Draw(img).rectangle([TX, TY, TX + PW - 1, TY + PH - 1], outline=(40, 40, 40), width=2)
    mask = Image.new("L", (PW, PH), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, PW - 1, PH - 1], radius=8, fill=255)
    fg = np.dstack([orig[TY:TY + PH, TX:TX + PW], np.array(mask)]).astype(np.uint8)
    return b64(Image.fromarray(np.array(img))), b64(Image.fromarray(fg)), TX, TY, PW, PH


def code_image():
    from PIL import ImageFont
    img = Image.new("RGB", (260, 90), "white")
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56)
    except Exception:
        f = ImageFont.load_default(size=48)
    ImageDraw.Draw(img).text((20, 10), "7Q4M2", fill="black", font=f)
    return b64(img)


results = []


def check(name, ok, info):
    results.append((name, bool(ok), info))
    print(("PASS" if ok else "FAIL"), name, "|", info)


# 1. health
h = get("/health")
types = h.get("supported_types") or []
check("health lists 55 types", len(types) == 55, f"{len(types)} types, mode={h.get('mode')}")

# 2. friendly_v2
d, el = post("/solve", {"type": "friendly_v2", "sitekey": "FCMGEMUD2LR7ICLH"})
check("friendly_v2 live token", d.get("solved") and str(d.get("token", "")).startswith("AQQA"),
      f"{el}s cost={d.get('cost')} solve_ms={d.get('solve_ms')}")

# 3. awswaf_js
d, el = post("/solve", {"type": "awswaf_js", "url": "https://www.turnitin.com/login_page.asp?lang=en_us"})
check("awswaf_js live token", d.get("solved") and ":" in str(d.get("token", "")),
      f"{el}s waf_elapsed={d.get('waf_elapsed')}")

# 4. ocr
d, el = post("/solve", {"type": "ocr", "image_b64": code_image()})
check("ocr text", d.get("solved") and d.get("text"), f"{el}s text={d.get('text')!r} backend={d.get('method')}")

# 5. tcaptcha detect
bg, fg, TX, TY, PW, PH = slider_pair()
d, el = post("/solve", {"type": "tcaptcha", "mode": "detect",
                        "challenge_json": {"bg_b64": bg, "fg_b64": fg,
                                           "piece": {"sprite_pos": [0, 0], "size_2d": [PW, PH],
                                                     "init_pos": [0, TY]}}})
check("tcaptcha detect consensus", d.get("solved") and abs(int(d.get("gap") or 0) - TX) <= 3,
      f"{el}s gap={d.get('gap')} (expected {TX}) dx={d.get('dx')}")

# 6. aliyun_traceless degrades cleanly (no camoufox in this interpreter)
d, el = post("/solve", {"type": "aliyun_traceless", "url": "https://example.com/login", "timeout_s": 90})
check("aliyun_traceless clean error", d.get("solved") is False and d.get("error"),
      f"{el}s error={str(d.get('error'))[:60]}")
print("   (with camoufox present it runs the browser path — see README)")

# 7. regression: tencent (pre-existing, self-contained)
d, el = post("/solve", {"type": "tencent"}, timeout=120)
check("regression tencent", bool(d.get("solved") or d.get("token") or d.get("error") is None),
      f"{el}s method={d.get('method')} token={'yes' if d.get('token') else 'no'} err={str(d.get('error'))[:50]}")

# 8. async job path for a new type
req = urllib.request.Request(BASE + "/solve/async", data=json.dumps(
    {"type": "ocr", "image_b64": code_image()}).encode(), headers={"Content-Type": "application/json"})
job = json.loads(urllib.request.urlopen(req, timeout=60).read())
jid = job.get("job_id") or job.get("id")
res = None
for _ in range(20):
    time.sleep(0.5)
    r = get(f"/solve/job/{jid}")
    if r.get("status") in ("ready", "error", "done", "finished", "completed"):
        res = r
        break
text = (res or {}).get("text") or ((res or {}).get("result") or {}).get("text")
check("async job (ocr)", bool((res or {}).get("solved") and text),
      f"job={jid} status={(res or {}).get('status')} text={text!r}")

print("\n=== summary ===")
bad = [n for n, ok, _ in results if not ok]
print(f"{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED:", bad)
sys.exit(1 if bad else 0)
