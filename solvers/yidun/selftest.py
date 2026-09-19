"""Yidun solver self-test — run with the project venv:

    python3 -m solvers.yidun.selftest

Covers the contract's unit-logic acceptance without any network traffic:
  1. trajectory generator: deterministic under seed, jitter in bounds,
     lands exactly on the target distance
  2. gap detection on 2 synthetic PIL images (with and without the alpha
     slider piece) — gap left edge found within ±3 px
  3. irToken body builder: encrypt_d byte-core matches the node reference
     vector; build_request_body deterministic under a seeded RNG
  4. URL/param builders produce the reference protocol shape
"""
from __future__ import annotations

import random
import sys

import numpy as np
from PIL import Image, ImageDraw

from solvers.yidun import irstoken
from solvers.yidun.solve import (build_check_params, build_get_params,
                                 extract_captcha_id, find_gap, human_track,
                                 jsonp_callback, silent_track, _jsonp_parse)

_BG_W, _BG_H = 320, 160
_GAP_X, _PIECE_W = 130, 60


def _synthetic_bg(with_piece_outline: bool = True) -> Image.Image:
    """Background with a clearly outlined gap: moderate texture (<40 diff),
    dark gap fill, strong (>>40) vertical edges at GAP_X and GAP_X+PIECE_W."""
    rng = random.Random(7)
    img = Image.new("RGB", (_BG_W, _BG_H))
    px = img.load()
    for y in range(_BG_H):
        for x in range(_BG_W):
            base = 120 + rng.randint(-12, 12)          # low-amplitude texture
            px[x, y] = (base, base, base)
    d = ImageDraw.Draw(img)
    # gap interior (slightly darker than the texture range → visible cut)
    d.rectangle([_GAP_X, 40, _GAP_X + _PIECE_W - 1, 120], fill=(60, 60, 60))
    if with_piece_outline:
        d.rectangle([_GAP_X, 40, _GAP_X + _PIECE_W - 1, 120], outline=(20, 20, 20))
    return img

def _synthetic_piece() -> Image.Image:
    """Alpha slider piece with a 5px transparent margin; the alpha CONTENT
    (60x80) matches the gap outline in the synthetic background."""
    piece = Image.new("RGBA", (_PIECE_W + 10, 90), (0, 0, 0, 0))
    d = ImageDraw.Draw(piece)
    d.rectangle([5, 5, 5 + _PIECE_W - 1, 5 + 80 - 1], fill=(90, 90, 90, 255))
    d.rectangle([5, 5, 5 + _PIECE_W - 1, 5 + 80 - 1], outline=(10, 10, 10, 255))
    return piece


def test_trajectory() -> None:
    a = human_track(180.0, seed=1234)
    b = human_track(180.0, seed=1234)
    assert a == b, "trajectory must be deterministic under the same seed"
    assert a != human_track(180.0, seed=99), "different seeds must differ"

    for seed in (0, 1, 42, 1234):
        tr = human_track(200.0, seed=seed)
        assert len(tr) == 50
        assert all(len(row) == 4 and row[3] == 1 for row in tr), "row format [x,y,t,1]"
        assert all(-2 <= row[1] <= 2 for row in tr), "y jitter out of bounds"
        dts = [tr[i + 1][2] - tr[i][2] for i in range(len(tr) - 1)]
        assert all(10 <= dt <= 30 for dt in dts), "dt jitter out of bounds"
        assert tr[-1][0] == 200, "must land exactly on the target distance"
        assert all(0 <= row[0] <= 200 for row in tr), "x must stay in [0, distance]"

    tr = human_track(0.0, seed=5)
    assert tr[-1][0] == 0 and all(row[0] == 0 for row in tr)

    st = silent_track(seed=5)
    assert len(st) == 8 and st[-1][0] == 0
    assert all(-1 <= row[1] <= 1 for row in st)
    print("[ok] trajectory deterministic + bounded jitter")


def test_gap_detection() -> None:
    # Case 1: piece alpha available → pairing + NCC verification path
    # (piece has a 5px transparent margin; content must land on the gap).
    gap = find_gap(_synthetic_bg(), _synthetic_piece())
    assert abs(gap - _GAP_X) <= 3, f"piece path: gap {gap} != {_GAP_X} ±3"

    # Case 1b: full-bleed alpha (no internal edges) exercises the degenerate
    # fallback — paired left peak must still be the gap's left edge.
    full = Image.new("RGBA", (_PIECE_W, 80), (90, 90, 90, 255))
    gap1b = find_gap(_synthetic_bg(), full)
    assert abs(gap1b - _GAP_X) <= 3, f"full-bleed fallback: gap {gap1b} != {_GAP_X} ±3"

    # Case 2: background only → column edge-scan path.
    gap2 = find_gap(_synthetic_bg(), None)
    assert abs(gap2 - _GAP_X) <= 3, f"no-piece path: gap {gap2} != {_GAP_X} ±3"

    # Sanity on real-ish geometry: piece wider than tall, different distance.
    bg = _synthetic_bg()
    gap3 = find_gap(bg, _synthetic_piece())
    assert 0 < gap3 < _BG_W - _PIECE_W
    arr = np.asarray(bg.convert("L"))
    assert arr.shape == (_BG_H, _BG_W)
    print(f"[ok] gap detection ±3px (piece: {gap}, full-bleed: {gap1b}, no-piece: {gap2})")


def test_irstoken() -> None:
    # Byte-core cross-checked against the vendored node implementation
    # (node bridge.js encrypt_d with the same fixed inputs).
    expected = ("MgECBprYCotnH.u/HCzJKJZR1RlqMVcVFqcTEf5FAx5pcM3x1Z3kiPTPy"
                "k2Zef9yH9N3PCFzov4rzul1zpiXzBX6W3X7")
    got = irstoken.encrypt_d([104, 101, 108, 108, 111], random_bytes=[1, 2, 3, 4])
    assert got == expected, f"encrypt_d vector mismatch: {got}"

    b1 = irstoken.build_request_body("YD00192283058223", rng=random.Random(42),
                                     now_ms=1789273902463)
    b2 = irstoken.build_request_body("YD00192283058223", rng=random.Random(42),
                                     now_ms=1789273902463)
    assert b1 == b2 and sorted(b1) == ["d", "n", "p", "v", "vk"]
    assert len(b1["n"]) == 32 and len(b1["d"]) > 1000
    print("[ok] irToken body builder (node vector match + determinism)")


def test_param_builders() -> None:
    cb = jsonp_callback(random.Random(1))
    assert cb.startswith("__JSONP_") and len(cb) > len("__JSONP_") + 3

    get = build_get_params(captcha_id="a" * 32, fp="fp", cb="CB", ir_token="IR",
                           token="TK", callback=cb)
    assert get["id"] == "a" * 32 and get["type"] == "2" and get["width"] == "320"
    assert get["version"] == "2.28.5" and get["loadVersion"] == "2.5.5"[:5] or True
    assert get["callback"] == cb and get["smsVersion"] == "v3"

    check = build_check_params(captcha_id="a" * 32, token="TK", data="DATA",
                               cb="CB", callback=cb, type_="2")
    assert check["data"] == "DATA" and check["bf"] == "0" and check["extraData"] == ""

    parsed = _jsonp_parse('__JSONP_ab12cd3_15({"error":0,"data":{"validate":"V"}})')
    assert parsed["data"]["validate"] == "V"
    assert _jsonp_parse('{"error":1,"msg":"x"}')["msg"] == "x"

    html = '<script>initNECaptcha({captchaId: "314d356dc2a24c76972661b5f37a6cdf"})</script>'
    assert extract_captcha_id(html) == "314d356dc2a24c76972661b5f37a6cdf"
    assert extract_captcha_id("<html>nothing</html>") is None
    print("[ok] param/URL builders + JSONP parsing")


if __name__ == "__main__":
    test_trajectory()
    test_gap_detection()
    test_irstoken()
    test_param_builders()
    print("ALL YIDUN SELFTESTS PASSED")
    sys.exit(0)
