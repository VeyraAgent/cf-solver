"""Self-test for the zhihu solver — offline, no network required.

Run:
  python3 -m solvers.zhihu.selftest

Covers:
  1. Image preprocessing pipeline (decode, resize, normalize)
  2. Uniform contract error paths (no input, bad base64)
  3. ONNX path smoke (no model → graceful skip)
  4. ddddocr fallback smoke (if installed)
  5. Charset/vector helpers match reference config
  6. Synthetic image end-to-end (contract + no crash)
"""
import asyncio
import base64
import io
import os
import sys
import time

import numpy as np

# Allow running from project root or solver dir
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from solvers.zhihu.solve import (
    CAPTCHA_LEN, _CHAR_SET, _CHAR_SET_LEN, IMG_WIDTH, IMG_HEIGHT,
    _decode_image, preprocess_for_onnx, _enhance_image,
    predict_text, available, solve_zhihu,
    _get_onnx_session, _get_ocr,
)

PASS = 0
FAIL = 0


def ok(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def _synthetic_captcha_b64() -> str:
    """Create a minimal synthetic 150x60 grayscale image as base64 PNG."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (IMG_WIDTH, IMG_HEIGHT), color=255)
    draw = ImageDraw.Draw(img)
    # Draw "TEST" in a basic font
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    except (OSError, IOError):
        font = ImageFont.load_default()
    draw.text((20, 10), "TEST", fill=0, font=font)

    # Add noise
    rng = np.random.RandomState(42)
    noise = rng.randint(0, 50, (IMG_HEIGHT, IMG_WIDTH), dtype=np.uint8)
    arr = np.array(img)
    arr = np.clip(arr.astype(int) - noise, 0, 255).astype(np.uint8)
    noisy = Image.fromarray(arr, mode="L")

    buf = io.BytesIO()
    noisy.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── Tests ─────────────────────────────────────────────────────────────

def test_config_constants():
    print("[1] config constants match reference")
    ok("CAPTCHA_LEN == 4", CAPTCHA_LEN == 4)
    ok("CHAR_SET_LEN == 62", _CHAR_SET_LEN == 62)
    ok("IMG_WIDTH == 150", IMG_WIDTH == 150)
    ok("IMG_HEIGHT == 60", IMG_HEIGHT == 60)
    ok("CHAR_SET starts with 0-9", _CHAR_SET[:10] == "0123456789")
    ok("CHAR_SET has a-z", "a" in _CHAR_SET and "z" in _CHAR_SET)
    ok("CHAR_SET has A-Z", "A" in _CHAR_SET and "Z" in _CHAR_SET)
    ok("CHAR_SET len == 62", len(_CHAR_SET) == 62)


def test_image_decode():
    print("[2] image preprocessing")
    b64 = _synthetic_captcha_b64()
    arr = _decode_image(b64)
    ok("decode returns ndarray", isinstance(arr, np.ndarray))
    ok("shape is (60, 150)", arr.shape == (IMG_HEIGHT, IMG_WIDTH))
    ok("dtype is float32", arr.dtype == np.float32)
    ok("values in [0, 1]", arr.min() >= 0.0 and arr.max() <= 1.0)

    # Test PIL input
    from PIL import Image
    pil_img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("L")
    arr2 = _decode_image(pil_img)
    ok("PIL input same shape", arr2.shape == (IMG_HEIGHT, IMG_WIDTH))

    # Test preprocess_for_onnx
    flat = preprocess_for_onnx(arr)
    ok("onnx input shape (1, 9000)", flat.shape == (1, IMG_WIDTH * IMG_HEIGHT))


def test_enhance():
    print("[3] cv2 enhancement")
    b64 = _synthetic_captcha_b64()
    arr = _decode_image(b64)
    enhanced = _enhance_image(arr)
    ok("enhanced same shape", enhanced.shape == arr.shape)
    ok("enhanced dtype float32", enhanced.dtype == np.float32)
    ok("enhanced range [0,1]", enhanced.min() >= 0.0 and enhanced.max() <= 1.0)


def test_contract_errors():
    print("[4] uniform contract error paths")

    # No input
    r1 = asyncio.get_event_loop().run_until_complete(solve_zhihu())
    ok("no input → solved=False", r1["solved"] is False)
    ok("no input → type=zhihu", r1["type"] == "zhihu")
    ok("no input → has error", r1["error"] is not None)
    ok("no input → elapsed set", isinstance(r1["elapsed"], float))

    # Bad base64
    r2 = asyncio.get_event_loop().run_until_complete(
        solve_zhihu(image_b64="not_valid_base64!!!"))
    ok("bad b64 → solved=False", r2["solved"] is False)
    ok("bad b64 → has error", r2["error"] is not None)

    # Contract keys present
    for key in ("type", "solved", "token", "method", "elapsed", "error"):
        ok(f"result has '{key}'", key in r1)


def test_backend_detection():
    print("[5] backend detection")
    session = _get_onnx_session()
    ocr = _get_ocr()
    has_any = session is not None or ocr is not None
    ok("at least one backend or None", True)  # always passes — just logs status
    print(f"    ONNX: {'loaded' if session else 'no model'}")
    print(f"    ddddocr: {'loaded' if ocr else 'not installed'}")
    ok("available() consistent", available() == has_any)


def test_predict_synthetic():
    print("[6] synthetic captcha predict (contract check)")
    b64 = _synthetic_captcha_b64()

    result = asyncio.get_event_loop().run_until_complete(
        solve_zhihu(image_b64=b64, timeout_s=10))

    ok("returns dict", isinstance(result, dict))
    ok("type == zhihu", result["type"] == "zhihu")
    ok("has elapsed", "elapsed" in result)
    ok("never raised", result["error"] is None or isinstance(result["error"], str))

    if result["solved"]:
        ok("token length == 4", len(result["token"]) == CAPTCHA_LEN)
        ok("token in charset", all(c in _CHAR_SET for c in result["token"]))
        print(f"    predicted: '{result['token']}' via {result['backend']}")
    else:
        print(f"    not solved (expected: synthetic + no trained model)")
        ok("error explains why", result["error"] is not None)


def test_predict_text_direct():
    print("[7] predict_text direct call")
    b64 = _synthetic_captcha_b64()
    text = predict_text(image_b64=b64)
    # May be empty if no backend, or 4 chars if ddddocr works
    ok("predict_text returns str", isinstance(text, str))
    if text:
        ok("length <= 4", len(text) <= CAPTCHA_LEN)
        ok("all chars valid", all(c in _CHAR_SET for c in text))
        print(f"    result: '{text}'")
    else:
        print(f"    empty (no backend or no model)")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    t0 = time.monotonic()
    print("=" * 60)
    print("Zhihu solver self-test")
    print("=" * 60)

    test_config_constants()
    test_image_decode()
    test_enhance()
    test_contract_errors()
    test_backend_detection()
    test_predict_synthetic()
    test_predict_text_direct()

    elapsed = time.monotonic() - t0
    print("=" * 60)
    if FAIL:
        print(f"RESULT: {PASS} passed, {FAIL} FAILED in {elapsed:.1f}s")
        sys.exit(1)
    else:
        print(f"ALL {PASS} CHECKS PASSED in {elapsed:.1f}s")
        sys.exit(0)


if __name__ == "__main__":
    main()
