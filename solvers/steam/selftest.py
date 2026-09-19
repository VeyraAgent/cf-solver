#!/usr/bin/env python3
"""Steam captcha solver self-test.

Tests the segmentation pipeline on the reference dataset from
scholtzm/opencv-steam-captcha (100 labeled PNGs in /tmp/recrefs/steam-captcha/data/).

Tests:
  1. Segmentation on all reference images (report success/fail rate)
  2. Character crop size verification (32×48)
  3. Uniform contract shape (no-model path)
  4. Descriptor dimension checks (simple + HOG)
  5. Histogram / threshold utilities
  6. Alias round-trip (filename → label → filename)
  7. Async entry contract

No trained model is needed — tests validate the engine only.
"""
import asyncio
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solve import (
    _ALLOWED_CHARS,
    _CHAR_HEIGHT,
    _CHAR_WIDTH,
    _hist_val,
    classify_character,
    create_histogram,
    get_ideal_threshold,
    hog_descriptor,
    segment_characters,
    simple_descriptor,
    solve_steam,
)

PASS = 0
FAIL = 0
DATA_DIR = "/tmp/recrefs/steam-captcha/data"


def ok(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"ok    {name} {extra}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {extra}")


# --- Special char alias (matches misc.cpp) ---
_ALIAS = {"@": "at", "%": "pct", "&": "and"}
_UNALIAS = {"at": "@", "pct": "%", "and": "&"}


def alias_to_special(s: str) -> str:
    """Convert filename alias to real captcha string."""
    result = s
    for alias, real in _UNALIAS.items():
        result = result.replace(alias, real)
    return result


def test_histogram_threshold():
    """Test histogram creation and threshold calculation on a synthetic image."""
    # Create a bimodal image: background ~50, foreground ~200
    img = np.zeros((100, 100), dtype=np.uint8)
    img[:60, :] = 50
    img[60:, :] = 200

    hist = create_histogram(img)
    ok("histogram shape", hist.ndim in (1, 2) and hist.size == 256,
       f"(got {hist.shape})")
    ok("histogram peak at 50", float(_hist_val(hist, 50)) > 0)
    ok("histogram peak at 200", float(_hist_val(hist, 200)) > 0)

    thresh = get_ideal_threshold(hist)
    ok("threshold is int", isinstance(thresh, int))
    ok("threshold in range", 0 <= thresh <= 255,
       f"(got {thresh})")


def test_segmentation_on_synthetic():
    """Segment a noisy synthetic image (mimics real captcha conditions)."""
    # 60x200 image with noise + dark text chars on light bg
    rng = np.random.RandomState(42)
    img = rng.randint(180, 255, (60, 200), dtype=np.uint8)
    # Draw 6 dark letter-like shapes with varying intensity (30-80)
    for i in range(6):
        x0 = 10 + i * 30
        dark = rng.randint(20, 80)
        img[5:55, x0:x0 + 18] = dark
        # Add some edge noise inside the "character"
        img[8:52, x0 + 3:x0 + 15] = rng.randint(
            dark - 10, dark + 30, (44, 12)
        ).clip(0, 255).astype(np.uint8)

    chars = segment_characters(img)
    ok("synthetic: segmented >= 1 char", len(chars) >= 1,
       f"(got {len(chars)})")
    if chars:
        ok("synthetic: crop is 32x48", chars[0].shape == (_CHAR_HEIGHT, _CHAR_WIDTH),
           f"(got {chars[0].shape})")


def test_segmentation_on_reference():
    """Run segmentation on reference Steam captcha images."""
    if not os.path.isdir(DATA_DIR):
        print(f"SKIP  reference segmentation (no {DATA_DIR})")
        return

    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".png"))
    ok("reference images exist", len(files) > 0, f"({len(files)} images)")

    total_chars = 0
    expected_chars = 0
    full_success = 0
    partial_success = 0

    for fname in files:
        path = os.path.join(DATA_DIR, fname)
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue

        code = alias_to_special(fname.replace(".png", ""))
        expected = len(code)
        expected_chars += expected

        chars = segment_characters(img)
        found = len(chars)
        total_chars += found

        if found == expected:
            full_success += 1
        elif found > 0:
            partial_success += 1

    total = len(files)
    ok("reference: segmentation rate",
       total_chars > 0,
       f"{total_chars}/{expected_chars} chars from {total} images "
       f"({full_success} exact, {partial_success} partial)")


def test_descriptor_dimensions():
    """Verify descriptor output shapes."""
    dummy = np.random.randint(0, 256, (_CHAR_HEIGHT, _CHAR_WIDTH), dtype=np.uint8)

    sd = simple_descriptor(dummy)
    ok("simple descriptor shape",
       sd.shape == (1, _CHAR_WIDTH * _CHAR_HEIGHT),
       f"(got {sd.shape})")
    ok("simple descriptor dtype", sd.dtype == np.float32)
    ok("simple descriptor range",
       sd.min() >= 0.0 and sd.max() <= 1.0,
       f"(range [{sd.min():.3f}, {sd.max():.3f}])")

    hd = hog_descriptor(dummy)
    ok("hog descriptor shape (1, N)",
       hd.shape[0] == 1 and hd.shape[1] > 0,
       f"(got {hd.shape})")
    ok("hog descriptor dtype", hd.dtype == np.float32)


def test_no_model_classify():
    """Without a trained model, classify returns '?'. """
    dummy = np.zeros((_CHAR_HEIGHT, _CHAR_WIDTH), dtype=np.uint8)
    char, conf = classify_character(dummy, model=None)
    ok("no-model classify returns '?'", char == "?",
       f"(got '{char}')")
    ok("no-model confidence is 0.0", conf == 0.0)


def test_alias_roundtrip():
    """Verify filename alias → special char conversion."""
    ok("alias 'at' → '@'", alias_to_special("at") == "@")
    ok("alias 'pct' → '%'", alias_to_special("pct") == "%")
    ok("alias 'and' → '&'", alias_to_special("and") == "&")
    ok("alias combo", alias_to_special("at9JatU9") == "@9J@U9")
    ok("alias complex",
       alias_to_special("7andYatRK") == "7&Y@RK")
    ok("alias no-op", alias_to_special("ABCDEF") == "ABCDEF")


def test_allowed_chars():
    """Verify the allowed character set."""
    ok("allowed chars length", len(_ALLOWED_CHARS) == 32,
       f"(got {len(_ALLOWED_CHARS)})")
    # Should not contain 0, 1, 5, 6, O, I, S
    for bad in "0156OIS":
        ok(f"no '{bad}' in allowed", bad not in _ALLOWED_CHARS)


def test_contract_shape():
    """Uniform contract: dict with required keys."""
    res = asyncio.get_event_loop().run_until_complete(
        solve_steam(image_b64="iVBORw0KGgo=")  # invalid b64, should error
    )
    ok("contract: has 'type'", "type" in res and res["type"] == "steam")
    ok("contract: has 'solved'", "solved" in res)
    ok("contract: has 'token'", "token" in res)
    ok("contract: has 'method'", "method" in res)
    ok("contract: has 'elapsed'", "elapsed" in res)
    ok("contract: has 'error'", "error" in res)
    ok("contract: error on bad input", res["error"] is not None)


def test_contract_no_input():
    """No input → immediate error."""
    res = asyncio.get_event_loop().run_until_complete(solve_steam())
    ok("no-input: solved=False", res["solved"] is False)
    ok("no-input: error set", res["error"] is not None and "pass image" in res["error"])


def test_contract_no_model():
    """With a real image but no model → solved=False, specific error."""
    if not os.path.isdir(DATA_DIR):
        print("SKIP  no-model contract (no data dir)")
        return

    files = [f for f in os.listdir(DATA_DIR) if f.endswith(".png")]
    if not files:
        print("SKIP  no-model contract (no images)")
        return

    path = os.path.join(DATA_DIR, files[0])
    with open(path, "rb") as f:
        import base64
        b64 = base64.b64encode(f.read()).decode()

    res = asyncio.get_event_loop().run_until_complete(
        solve_steam(image_b64=b64)
    )
    ok("no-model: solved=False", res["solved"] is False)
    ok("no-model: error mentions model",
       res["error"] is not None and "no trained model" in res["error"])
    ok("no-model: char_count > 0",
       res.get("char_count", 0) > 0,
       f"(got {res.get('char_count', 0)})")
    ok("no-model: method is segment+no-model",
       res["method"] == "segment+no-model")


def test_synthetic_full_pipeline():
    """Create a synthetic captcha-like image, run full segmentation."""
    # Create a 60×200 grayscale image with 6 dark "text" blobs on light background
    img = np.full((60, 200), 230, dtype=np.uint8)
    rng = np.random.RandomState(42)
    # Add noise
    noise = rng.randint(0, 30, img.shape, dtype=np.uint8)
    img = cv2.add(img, noise)

    # Draw 6 distorted letter-like shapes
    for i in range(6):
        x0 = 12 + i * 30
        # vertical bar
        cv2.line(img, (x0 + 5, 8), (x0 + 5, 52), (30,), 2)
        # horizontal bar
        cv2.line(img, (x0 + 2, 30), (x0 + 18, 30), (30,), 2)
        # diagonal
        cv2.line(img, (x0 + 2, 10), (x0 + 15, 25), (40,), 1)

    chars = segment_characters(img)
    ok("pipeline: segmentation returns list", isinstance(chars, list))
    ok("pipeline: segmented at least 1", len(chars) >= 1,
       f"(got {len(chars)})")

    for idx, c in enumerate(chars):
        ok(f"pipeline: crop[{idx}] is uint8",
           c.dtype == np.uint8)
        ok(f"pipeline: crop[{idx}] is 32x48",
           c.shape == (_CHAR_HEIGHT, _CHAR_WIDTH),
           f"(got {c.shape})")


def main():
    t0 = time.monotonic()
    print("=== Steam solver self-test ===\n")

    test_allowed_chars()
    test_alias_roundtrip()
    test_histogram_threshold()
    test_segmentation_on_synthetic()
    test_descriptor_dimensions()
    test_no_model_classify()
    test_contract_shape()
    test_contract_no_input()
    test_contract_no_model()
    test_segmentation_on_reference()
    test_synthetic_full_pipeline()

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 40}")
    print(f"Result: {PASS} passed, {FAIL} failed in {elapsed:.1f}s")

    if FAIL:
        print(f"\nFAILED ({FAIL} failures)")
        sys.exit(1)
    else:
        print(f"\nALL {PASS} CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
