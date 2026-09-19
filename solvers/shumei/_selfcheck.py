"""Offline self-check for the shumei solver: python -m solvers.shumei._selfcheck

Covers, with no network:
  1. DES-ECB field encryption against 6 recorded ground-truth vectors from the
     real vendor captcha-sdk.min.js (incl. 1/8/9-byte padding edges).
  2. NCC template matching on a synthetic background: 3 icon templates must be
     located within +/-3 px and in template order, plus one rotated instance
     recovered through the rotation sweep.
  3. fg order-bar splitting into per-slot templates.
  4. Instruction parsing / shape-family selection and HSV object detection on
     synthetic images.
  5. sp/ox/gt payload structure and normalization bounds.
"""
import base64
import json

import numpy as np
from PIL import Image, ImageDraw

from solvers.shumei.imaging import (
    classify_shape,
    detect_objects,
    match_pieces,
    parse_instruction,
    select_target,
    shape_family,
    split_fg_templates,
)
from solvers.shumei.solve import FIELD_KEYS, build_submit_payload, encrypt_field

RED = (208, 33, 19)  # vendor's flat red


def _icon_canvas(kind: str, size: int = 40) -> Image.Image:
    """One flat-red glyph on a black canvas (black is dropped by red masking)."""
    img = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "triangle":
        d.polygon([(size // 2, 4), (4, size - 6), (size - 6, size - 6)], fill=RED)
    elif kind == "diamond":
        d.polygon([(size // 2, 4), (size - 6, size // 2),
                   (size // 2, size - 6), (4, size // 2)], fill=RED)
    elif kind == "square":
        d.rectangle([6, 6, size - 8, size - 8], fill=RED)
    else:
        d.ellipse([4, 4, size - 6, size - 6], fill=RED)
    return img


def _stamp(bg: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    """Paste a glyph canvas onto bg, keying out the black backdrop."""
    keep = patch @ np.array([0.299, 0.587, 0.114]) > 40
    h, w = patch.shape[:2]
    region = bg[y:y + h, x:x + w]
    region[keep] = patch[keep]


def _check_des():
    # recorded from the live captcha-sdk.min.js (v1.0.4-207) via DES(key, data, 1, 0)
    vectors = [
        ("ed4576ba", "26564", "WRRKLrBQxfI="),
        ("735c85df", "[[0.75,0.6666666666666666,1788402278747]]",
         "EDiA8TuKSMGP0LPosI/yKg+AwGz86Ozim8JKsWJpR9Ae4CZtuD4HEONn085oXXKr"),
        ("b06aad3b",
         "[[0.1,0.2,1788402270000],[0.75,0.6666666666666666,1788402278747]]",
         "kudrMjUJaAqtTUneZVlO4YmzAUr2MsulKGeSjmd6qbLx5Zy+o2Def4G0lkI5aW+M0Nbk2IRJC1N6Yx7K/tbf+XNZJ3SR/Wup"),
        ("ed4576ba", "A", "8O5Vh/rZN5c="),
        ("ed4576ba", "12345678", "mIeaGMI1JLk="),
        ("ed4576ba", "123456789", "mIeaGMI1JLnhcY/3fdGFzw=="),
    ]
    for key, pt, want in vectors:
        got = encrypt_field(key, pt)
        assert got == want, f"DES mismatch for {pt[:30]!r}: {got} != {want}"
    assert set(FIELD_KEYS) == {"sp", "ox", "gt"}
    print("1. DES field encryption vs vendor SDK vectors: ok (6/6)")


def _check_ncc():
    rng = np.random.default_rng(7)
    # vendor-like backdrop: low-saturation gradient + mild noise (nothing that
    # passes the red mask, unlike pure-random RGB which speckles through it)
    _, xx = np.mgrid[0:240, 0:320]
    base = np.dstack([110 + xx / 3, 115 + xx / 3, 120 + xx / 3])
    bg = np.clip(base + rng.integers(-3, 4, size=base.shape), 0, 255).astype(np.uint8)
    pos = [(30, 40), (150, 30), (230, 160)]
    kinds = ["triangle", "diamond", "circle"]
    templates = []
    for (x, y), kind in zip(pos, kinds):
        tpl = np.asarray(_icon_canvas(kind))
        _stamp(bg, tpl, x, y)
        templates.append(tpl)
    # one extra 30-degree rotated instance of a fourth glyph elsewhere
    rot = np.asarray(_icon_canvas("square").rotate(30, expand=True, fillcolor=(0, 0, 0)))
    ry, rx = 120, 70
    _stamp(bg, rot, rx, ry)

    hits = match_pieces(bg, templates, scales=(1.0,), min_dist=30)
    assert len(hits) == 3, hits
    for hit, (x, y), kind in zip(hits, pos, kinds):
        exp = (x + 20, y + 20)  # matcher reports the glyph center
        assert abs(hit["x"] - exp[0]) <= 3 and abs(hit["y"] - exp[1]) <= 3, \
            (kind, hit, exp)
        assert hit["score"] > 0.8, (kind, hit)
    assert len({(h["x"], h["y"]) for h in hits}) == 3, "slots must land apart"
    print("2. NCC synthetic match: ok (3/3 pieces within ±3px, ordered)")

    # rotated instance: the matcher must recover it via the rotation sweep
    t_rot = match_pieces(bg, [np.asarray(_icon_canvas("square"))],
                         scales=(1.0,), min_dist=30)[0]
    exp = (rx + rot.shape[1] // 2, ry + rot.shape[0] // 2)
    assert abs(t_rot["x"] - exp[0]) <= 4 and abs(t_rot["y"] - exp[1]) <= 4, (t_rot, exp)
    print(f"   rotated instance recovered: {t_rot['x']},{t_rot['y']} "
          f"(expected {exp[0]},{exp[1]}), angle {t_rot['angle']}, score {t_rot['score']}")


def _check_fg_split():
    bar = np.zeros((40, 148, 4), dtype=np.uint8)
    for x0 in (8, 45, 82, 118):
        bar[6:34, x0:x0 + 24, :3] = RED
        bar[6:34, x0:x0 + 24, 3] = 255
    tpls = split_fg_templates(bar)
    assert len(tpls) == 4, [t.shape for t in tpls]
    assert all(t.shape[0] == 40 and 20 <= t.shape[1] <= 30 for t in tpls), \
        [t.shape for t in tpls]
    print("3. fg order-bar split: ok (4 templates from alpha runs)")


def _check_geometry():
    color, shape, size = parse_instruction("点击图中最小的黄色六棱柱")
    assert (color, shape, size) == ("黄色", "六棱柱", "最小"), (color, shape, size)
    assert "棱柱" in shape_family("六棱柱")

    objs = [
        {"color": "黄色", "type": "球体", "center": (100, 100), "area": 900},
        {"color": "黄色", "type": "棱柱", "center": (200, 120), "area": 3000},
        {"color": "红色", "type": "圆锥", "center": (300, 90), "area": 1500},
    ]
    pick = select_target(objs, "点击图中最小的黄色六棱柱")
    assert pick["center"] == (200, 120), pick
    pick = select_target(objs, "点击图中最大的红色甜甜圈")  # unknown shape -> color fallback
    assert pick["center"] == (300, 90), pick

    # synthetic HSV detection: red square + yellow disc on a gray gradient
    yy, xx = np.mgrid[0:300, 0:600]
    grad = (40 + (xx / 600) * 60).astype(np.uint8)
    bgr = np.dstack([grad, grad, grad])
    import cv2

    cv2.rectangle(bgr, (80, 120), (130, 170), (0, 0, 255), -1)   # hue 0
    cv2.circle(bgr, (420, 180), 25, (0, 255, 255), -1)           # hue 30
    found = detect_objects(bgr)
    by_color = {o["color"]: o for o in found}
    for want, got in (((105, 145), by_color["红色"]["center"]),
                      ((420, 180), by_color["黄色"]["center"])):
        assert abs(got[0] - want[0]) <= 2 and abs(got[1] - want[1]) <= 2, (want, got)
    cyl = {"prof": [0.9] * 20, "maxw": 0.9, "tw5": 1.0, "flat": 1.0, "peak": 0,
           "circ": 0.5, "span": 60, "hgt": 60, "topw": 0.9}
    assert classify_shape(cyl) == "圆柱体"
    prism = {"prof": [0.1] * 5 + [0.9] * 15, "maxw": 0.9, "tw5": 0.112, "flat": 0.75,
             "peak": 17, "circ": 0.5, "span": 60, "hgt": 60, "topw": 0.1}
    assert classify_shape(prism) == "棱柱"
    print("4. instruction parse / selection / HSV detection: ok")


def _check_payload():
    payload = build_submit_payload([(450, 200), (100, 80)], 600, 300,
                                   now_ms=1788402280000)
    sp = json.loads(_dec(FIELD_KEYS["sp"], payload["sp"]))
    assert len(sp) == 2, sp
    for fx, fy, ts in sp:
        assert 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0, (fx, fy)
        assert isinstance(ts, int) and ts > 0
    assert abs(sp[0][0] - 0.75) < 1e-6 and abs(sp[0][1] - 2 / 3) < 1e-6, sp[0]
    ox = json.loads(_dec(FIELD_KEYS["ox"], payload["ox"]))
    assert len(ox) > 2 and ox[-1][0] == sp[-1][0], "mouse trail must end on the click"
    gt = int(_dec(FIELD_KEYS["gt"], payload["gt"]))
    assert 0 < gt < 60_000, gt
    print(f"5. submit payload: ok (sp 2 pts normalized, ox {len(ox)} steps, gt {gt}ms)")


def _dec(key: str, b64: str) -> str:
    from Crypto.Cipher import DES

    plain = DES.new(key.encode()[:8], DES.MODE_ECB).decrypt(base64.b64decode(b64))
    return plain.rstrip(b"\x00").decode()


if __name__ == "__main__":
    _check_des()
    _check_ncc()
    _check_fg_split()
    _check_geometry()
    _check_payload()
    print("ok")
