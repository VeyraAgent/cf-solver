"""Offline self-test for the dingxiang solver — no network required.

Run:  <venv>/python3 solvers/dingxiang/selftest.py
Covers: URL-builder wire format (captured from the live v5 demo),
trajectory determinism/shape, gap detection on a synthetic pair and on the
Apache-2.0 reference fixture (DingxiangCaptchaBreak example, annotated
ground truth "distance.(268, 49)").
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from solvers.dingxiang.solve import (  # noqa: E402
    DEMO_APP_ID,
    build_challenge_url,
    build_constid_url,
    build_image_url,
    build_submit_fields,
    build_submit_url,
    detect_gap,
    extract_app_id,
    generate_trajectory,
    new_aid,
    new_cid,
)

FIXTURES = Path(__file__).parent / "fixtures"
PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if cond:
        PASS += 1
    else:
        sys.exit(1)


# ---------------------------------------------------------------- URL builders
u = build_challenge_url(DEMO_APP_ID, w=300, h=165, aid="dx-1789272440506-57163126-1", cid="04167336")
ok("challenge url host/path", u.startswith("https://cap.dingxiang-inc.com/api/a?"), u)
ok("challenge url ak param", "ak=12610a3853150e888ccd0c6d4c415626" in u)
ok("challenge url geometry", "w=300&h=165&s=50" in u)
ok("challenge url sdk fields", "jsv=5.1.53" in u and "wp=1&de=0" in u and "lf=0&tpc=" in u)
ok("challenge url aid format", "aid=dx-" in u and "-57163126-1" in u)
ok("challenge url cid format", "cid=04167336" in u)
ok("challenge url cache buster", "_r=0." in u)
ok("aid generator format", len(new_aid().split("-")) == 4 and new_aid().startswith("dx-"))
ok("cid generator format", len(new_cid()) == 8 and new_cid().isdigit())
ok("image url prefix", build_image_url("/dx/vJFY4rWvu8/zib3/x.webp")
   == "https://static4.dingxiang-inc.com/picture/dx/vJFY4rWvu8/zib3/x.webp")
ok("image url absolute passthrough", build_image_url("https://a.b/c.webp") == "https://a.b/c.webp")
ok("submit url", build_submit_url() == "https://cap.dingxiang-inc.com/api/v1")
ok("constid url", build_constid_url(ts=1789272440.506)
   == "https://constid.dingxiang-inc.com/udid/c1?_t=40506")
f = build_submit_fields("BLOB", DEMO_APP_ID, sid="62258730f53065b76348cc824f2bb887",
                        aid="dx-1-2-3", x=250, y=79, w=300, h=165, cid="04167336")
ok("submit field set", set(f) == {"ac", "ak", "c", "uid", "jsv", "sid", "aid", "x", "y", "w", "h", "cid"},
   ",".join(sorted(f)))
ok("submit x int-string", f["x"] == "250" and f["y"] == "79" and f["sid"].isalnum())
ok("extract_app_id from js", extract_app_id("var x={appId:'9c1f7c8e26aa4e0f9d0f1c5f6e7a8b9c'}") ==
   "9c1f7c8e26aa4e0f9d0f1c5f6e7a8b9c")
ok("extract_app_id from query", extract_app_id("https://x.y/p?ak=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") ==
   "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

# ---------------------------------------------------------------- trajectory
t1 = generate_trajectory(180.0, seed=42)
t2 = generate_trajectory(180.0, seed=42)
ok("trajectory deterministic", t1 == t2)
ok("trajectory starts at 0", t1[0]["x"] <= 2 and t1[0]["t"] == 0)
ok("trajectory ends exact", abs(t1[-1]["x"] - 180.0) < 0.01, f"end={t1[-1]['x']}")
xs = [p["x"] for p in t1]
ok("trajectory x monotone-ish", all(b >= a - 3.0 for a, b in zip(xs, xs[1:])))
ok("trajectory has overshoot peak", max(xs) > 180.0, f"peak={max(xs):.1f}")
ok("trajectory y bounded", all(abs(p["y"]) <= 2.5 for p in t1))
ok("trajectory timing plausible", 300 <= t1[-1]["t"] <= 1600, f"total={t1[-1]['t']}ms")
ok("trajectory empty for 0 distance", generate_trajectory(0.0) == [{"x": 0.0, "y": 0.0, "t": 0}])

# ---------------------------------------------------------------- synthetic gap
rng = np.random.default_rng(7)
W, H = 300, 165
bg = np.zeros((H, W, 3), dtype=np.uint8)
bg[..., 0] = np.linspace(40, 200, W)[None, :]
bg[..., 1] = rng.integers(30, 220, (H, W))
bg[..., 2] = np.tile(np.linspace(200, 60, H)[:, None], (1, W))

# synthetic piece: rounded square with a bottom tab, alpha silhouette
piece_img = Image.new("RGBA", (61, 61), (0, 0, 0, 0))
d2 = ImageDraw.Draw(piece_img)
d2.rounded_rectangle([8, 4, 52, 56], radius=10, fill=(180, 90, 40, 255))
d2.ellipse([22, 46, 40, 62], fill=(180, 90, 40, 255))
piece = np.asarray(piece_img)

GX_TRUE, GY_TRUE = 173, 52
CANVAS = 71
OX, OY = 5, 3  # piece content margin inside its canvas
sil_img = Image.new("L", (61, 61), 0)
d3 = ImageDraw.Draw(sil_img)
d3.rounded_rectangle([8, 4, 52, 56], radius=10, fill=255)
d3.ellipse([22, 46, 40, 62], fill=255)
sil = np.asarray(sil_img)

# piece: canvas copy of the anchor region; content only where alpha says so
region = bg[GY_TRUE:GY_TRUE + CANVAS, GX_TRUE:GX_TRUE + CANVAS].astype(np.uint8)
piece = np.zeros((CANVAS, CANVAS, 4), dtype=np.uint8)
piece[..., :3] = region
piece[..., 3] = 0
piece[OY:OY + 61, OX:OX + 61, 3] = sil

# v5-style hole: bg darkened through the piece alpha canvas anchored at truth
bg_with_hole = bg.copy()
win = bg_with_hole[GY_TRUE:GY_TRUE + CANVAS, GX_TRUE:GX_TRUE + CANVAS]
alpha = piece[..., 3] > 0
win[alpha] = (win[alpha].astype(np.float64) * 0.30).astype(np.uint8)
bg_with_hole[GY_TRUE:GY_TRUE + CANVAS, GX_TRUE:GX_TRUE + CANVAS] = win

det = detect_gap(bg_with_hole, piece, y_hint=GY_TRUE)
ok("synthetic gap x ±3", abs(det["x"] - GX_TRUE) <= 3, f"det={det['x']} true={GX_TRUE} score={det['score']}")
ok("synthetic gap y ±4", abs(det["y"] - GY_TRUE) <= 4, f"det={det['y']} true={GY_TRUE}")
det2 = detect_gap(bg_with_hole, piece)
ok("synthetic gap full-search", abs(det2["x"] - GX_TRUE) <= 3, f"det={det2['x']}")

# ---------------------------------------------------------------- reference fixture
bg_f = FIXTURES / "example_bg.png"
pc_f = FIXTURES / "example_piece.png"
if bg_f.exists() and pc_f.exists():
    # ground truth from aidencaptcha/DingxiangCaptchaBreak example annotation
    # "distance.(268, 49).png" → piece placement (268, 49) on the 400x200 bg
    ref = detect_gap(Image.open(bg_f), Image.open(pc_f))
    ok("reference gap x ±3 (truth 268)", abs(ref["x"] - 268) <= 3, f"det={ref['x']} score={ref['score']}")
    ok("reference gap y ±5 (truth 49)", abs(ref["y"] - 49) <= 5, f"det={ref['y']}")
else:
    print("[SKIP] reference fixtures missing")

print(f"\n{PASS} checks passed.")
