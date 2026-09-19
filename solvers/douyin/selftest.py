"""Synthetic self-test for the douyin solver — no network, no captures needed.

Run:  python -m solvers.douyin.selftest
Builds puzzle images with PIL (deterministic, seeded) with hole shape == piece
silhouette (real puzzle geometry), then asserts:
  1. edges NCC hits the hole within ±3px across piece variants:
     alpha-cutout PNG, white-background PNG (reference's white-mask case),
     fully-opaque full-rect canvas — clean and noise-degraded
  2. the reference palette port runs and returns sane output (its location is
     palette-window dependent, not asserted on synthetic data)
  3. detect_gap fusion returns a valid dict with a sane method tag
  4. gen_trajectory is deterministic under seed, ends at the target, overshoots
     and corrects back
"""
from __future__ import annotations

import base64
import io
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .gap import detect_gap, detect_gap_edges, detect_gap_reference
from .solve import gen_trajectory

FULL_W, FULL_H = 404, 150
PIECE_W, PIECE_H = 83, 55
INSET = 5  # transparent/white margin between piece canvas and piece content


def _b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _texture(seed: int) -> Image.Image:
    """Photo-like background: gradient + colored blobs with outlines (decoys)."""
    rng = np.random.default_rng(seed)
    xx, yy = np.meshgrid(np.linspace(0, 1, FULL_W), np.linspace(0, 1, FULL_H))
    arr = (40 + 60 * xx + 30 * yy)[:, :, None] * np.array([1.0, 0.9, 1.1])
    arr += rng.normal(0, 14, (FULL_H, FULL_W, 3))
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    for _ in range(18):
        x, y = int(rng.integers(0, FULL_W - 40)), int(rng.integers(0, FULL_H - 40))
        w, h = int(rng.integers(20, 60)), int(rng.integers(15, 45))
        col = tuple(int(c) for c in rng.integers(30, 240, 3))
        if rng.random() < 0.5:
            d.ellipse((x, y, x + w, y + h), fill=col, outline=(250, 250, 250), width=2)
        else:
            d.rectangle((x, y, x + w, y + h), fill=col, outline=(15, 15, 15), width=2)
    return img.filter(ImageFilter.GaussianBlur(1))


def _puzzle(seed: int, hole: tuple[int, int], mode: str = "alpha",
            noisy: bool = False):
    """Build (full_b64, piece_b64) with hole shape == piece silhouette.

    mode: "alpha"  — piece content on transparent margin (douyin-style PNG)
          "white"  — piece content on white margin (reference white-mask case)
          "full"   — fully-opaque full-rect piece, hole = strong outline only
    """
    gx, gy = hole
    base = _texture(seed)
    crop = base.crop((gx, gy, gx + PIECE_W, gy + PIECE_H))
    full = base.copy()
    d = ImageDraw.Draw(full)
    if mode != "full":
        region = np.asarray(crop, dtype=np.float32).copy()
        region[INSET:-INSET, INSET:-INSET] *= 0.45  # darkened hole interior
        full.paste(Image.fromarray(region.astype(np.uint8), "RGB"), (gx, gy))
        d.rectangle((gx + INSET, gy + INSET, gx + PIECE_W - INSET - 1,
                     gy + PIECE_H - INSET - 1), outline=(10, 10, 10), width=2)
    else:
        d.rectangle((gx, gy, gx + PIECE_W - 1, gy + PIECE_H - 1),
                    outline=(10, 10, 10), width=2)
    if noisy:
        arr = np.asarray(full, dtype=np.float32)
        arr += np.random.default_rng(seed + 1).normal(0, 6.0, arr.shape)
        arr *= 1.08
        full = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    if mode == "alpha":
        piece = Image.new("RGBA", (PIECE_W, PIECE_H), (0, 0, 0, 0))
        piece.paste(crop.crop((INSET, INSET, PIECE_W - INSET, PIECE_H - INSET)),
                    (INSET, INSET))
    elif mode == "white":
        piece = Image.new("RGBA", (PIECE_W, PIECE_H), (255, 255, 255, 255))
        piece.paste(crop.crop((INSET, INSET, PIECE_W - INSET, PIECE_H - INSET)),
                    (INSET, INSET))
    else:
        piece = Image.new("RGBA", (PIECE_W, PIECE_H), (0, 0, 0, 0))
        piece.paste(crop, (0, 0))
    return _b64(full), _b64(piece)


def _assert_near(name: str, got: tuple[int, int], want: tuple[int, int], tol=3):
    dx, dy = abs(got[0] - want[0]), abs(got[1] - want[1])
    ok = dx <= tol and dy <= tol
    print(f"  {'PASS' if ok else 'FAIL'} {name}: got ({got[0]},{got[1]}) "
          f"want ({want[0]},{want[1]}) d=({dx},{dy})")
    if not ok:
        raise AssertionError(f"{name}: ({got[0]},{got[1]}) vs ({want[0]},{want[1]})")


def main() -> int:
    holes = [(180, 60), (285, 40), (70, 20), (310, 80)]

    print("== edges NCC: piece variants x clean/noisy ==")
    n = 0
    for mode in ("alpha", "white", "full"):
        for noisy in (False, True):
            for i, hole in enumerate(holes):
                fb, pb = _puzzle(seed=42 + i, hole=hole, mode=mode, noisy=noisy)
                r = detect_gap_edges(fb, pb)
                _assert_near(f"{mode}{'-noisy' if noisy else ''}[{i}]",
                             (r["x"], r["y"]), hole)
                n += 1
    print(f"  ({n}/24 variants exercised)")

    print("== dispatcher detect_gap ==")
    fb, pb = _puzzle(seed=42, hole=holes[0], mode="alpha")
    r = detect_gap(fb, pb)
    _assert_near("dispatcher", (r["x"], r["y"]), holes[0])
    assert r["method"] in ("edges-ncc", "reference-palette"), r["method"]
    assert r["ref_score"] is None or 0.0 <= r["ref_score"] <= 1.0

    print("== reference palette port runs ==")
    score, rx, ry = detect_gap_reference(fb, pb)
    assert isinstance(score, float) and 0.0 <= score <= 1.0, score
    assert 0 <= rx <= FULL_W - PIECE_W and 0 <= ry <= FULL_H - PIECE_H, (rx, ry)
    print(f"  PASS reference ran: score={score:.3f} @ ({rx},{ry}) "
          f"(location not asserted — palette-window dependent)")

    print("== trajectory determinism + overshoot/correct ==")
    t1 = gen_trajectory(123.0, seed=7)
    t2 = gen_trajectory(123.0, seed=7)
    assert t1 == t2, "seeded trajectory not deterministic"
    end = t1["points"][-1]["x"]
    assert abs(end - 123.0) < 1.0, f"end x {end} != 123"
    xs = [p["x"] for p in t1["points"]]
    peak = max(xs)
    assert peak > 123.0, f"no overshoot (peak {peak})"
    assert xs[-1] < peak, "no correction phase back from overshoot"
    assert len({p["t_ms"] for p in t1["points"]}) == len(t1["points"]), "timestamps flat"
    print(f"  PASS traj: {len(t1['points'])} pts, peak {peak:.1f} > 123 > end "
          f"{end:.1f}, total {t1['total_ms']:.0f}ms")

    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
