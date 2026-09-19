"""Steam captcha solver — morphological reconstruction + segmentation + SVM/HOG.

Port of scholtzm/opencv-steam-captcha (C++) to pure Python cv2/numpy.
Steam captcha at steamcommunity.com shows 6 distorted character sprites that
must be identified. The character set is: A-Z, 0-9 minus {0,1,5,6,O,I,S}
plus special chars {@, %, &} — 32 classes total.

Algorithm (faithful port):
  1. Resize 2x, adaptive threshold, histogram-based optimal threshold,
     binary threshold, morphological close (dilate+erode 3x3 ellipse).
  2. Column-projection segmentation: vertical profile → find vertical bands
     (horizontal pairs), horizontal profile → find row span (vertical pair),
     merge → rectangles, shrink to content, take top-6 by area, sort by x.
  3. Per-character crop resized to 32×48, simple descriptor (flattened [0,1]
     float) or HOG descriptor, classify via SVM (sklearn or cv2.ml).

**MODEL STATUS:** No pre-trained SVM ships with this solver. The original C++
project trained only on 100 labeled images (~19 samples/char) and could only
reliably distinguish G and Y. To get a usable model:

  - Collect Steam captcha images (100+ per char, 32 classes = 3200+ minimum).
  - Run the segmentation engine (``segment_characters``) to extract 32×48
    character crops.
  - Train an SVM (``train_model``) or substitute any classifier that exposes
    ``.predict(descriptor) → class_index``.
  - Save the model to ``steam_svm.pkl`` or ``steam_svm.xml`` alongside this
    file.

Without a trained model the solver returns ``solved=False`` with
``error="no trained model"``.

Usage::

    from solvers.steam.solve import solve_steam, segment_characters
    res = await solve_steam(image_b64="<base64 png>")
    # or
    res = await solve_steam(url="https://steamcommunity.com/tradeoffer/new/captcha?...")
    # or
    chars = segment_characters(cv2.imread("captcha.png", cv2.IMREAD_GRAYSCALE))

Credits: scholtzm/opencv-steam-captcha (C++ original, MIT license).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("steam")

# --- Constants (ported from C++ main.cpp / segments.cpp) ---

_RESIZE_FACTOR = 2
# 0, 1, 5, 6, I, O and S are never used in Steam captcha
_ALLOWED_CHARS = "234789ABCDEFGHJKLMNPQRTUVWXYZ@&%"
_CHAR_WIDTH = 32   # resize target for character crops
_CHAR_HEIGHT = 48  # resize target for character crops

# Segmentation magic numbers (from segments.cpp)
_MAGIC_DIFF = 10       # min gap to consider a horizontal segment boundary
_MAGIC_SIZE = 14       # min segment width to keep
_MAGIC_SPLIT = 50      # segments wider than this get split in half

_MODEL_PATH = Path(__file__).resolve().parent / "steam_svm.pkl"


# --- Data structures ---

@dataclass
class Rectangle:
    """Mirrors C++ Rectangle struct."""
    id: int
    x: int
    y: int
    width: int
    height: int


# --- Image preprocessing (from image.cpp) ---

def create_histogram(source: np.ndarray) -> np.ndarray:
    """Create 256-bin histogram from 8-bit grayscale image."""
    hist = cv2.calcHist([source], [0], None, [256], [0, 256])
    return hist


def _hist_val(hist: np.ndarray, idx: int) -> float:
    """Read a histogram bin value, handling both (256,) and (256,1) shapes."""
    if hist.ndim == 1:
        return float(hist[idx])
    return float(hist[idx, 0])


def get_ideal_threshold(histogram: np.ndarray) -> int:
    """Calculate optimal binary threshold from histogram.

    Scans for the last histogram peak above MAGIC_PEAK=200, then adds
    4/10 of the remaining range (MAGIC_PERCENTAGE=4).
    """
    MAGIC_PEAK = 200
    MAGIC_PERCENTAGE = 4

    last_peak = 0
    for i in range(256):
        if _hist_val(histogram, i) > MAGIC_PEAK:
            last_peak = i

    return last_peak + int((255 - last_peak) / 10 * MAGIC_PERCENTAGE)


# --- Segmentation (from segments.cpp) ---

def horizontal_segments(src: np.ndarray) -> np.ndarray:
    """Column projection: count white pixels per column."""
    return np.sum(src > 0, axis=0).astype(np.int32)


def vertical_segments(src: np.ndarray) -> np.ndarray:
    """Row projection: count white pixels per row."""
    return np.sum(src > 0, axis=1).astype(np.int32)


def create_segment_pairs(seg: np.ndarray, seg_size: int) -> list[tuple[int, int]]:
    """Convert a 1D projection array to contiguous (start, end) pairs."""
    pairs = []
    top = bottom = 0
    in_seg = False

    for i in range(seg_size):
        if seg[i]:
            in_seg = True
        else:
            in_seg = False

        if in_seg:
            if not top:
                top = i
                bottom = i
            else:
                bottom = i
            # corner case: segment reaches end
            if i == seg_size - 1:
                pairs.append((top, bottom))
                top = bottom = 0
        else:
            if top and bottom:
                pairs.append((top, bottom))
                top = bottom = 0

    return pairs


def filter_vertical_pairs(vertical_pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Keep only the largest vertical band (the text row)."""
    if not vertical_pairs:
        return []
    biggest_idx = max(
        range(len(vertical_pairs)),
        key=lambda i: vertical_pairs[i][1] - vertical_pairs[i][0],
    )
    return [vertical_pairs[biggest_idx]]


def filter_horizontal_pairs(
    horizontal_pairs: list[tuple[int, int]], seg_size: int
) -> list[tuple[int, int]]:
    """Merge small horizontal segments that are close to neighbors."""
    seg = np.zeros(seg_size, dtype=np.int32)

    for i, (a, b) in enumerate(horizontal_pairs):
        size = b - a
        if size < _MAGIC_SIZE:
            # check right side
            if i < len(horizontal_pairs) - 1 and len(horizontal_pairs) > 1:
                if horizontal_pairs[i + 1][0] - b < _MAGIC_DIFF:
                    for k in range(a, horizontal_pairs[i + 1][1] + 1):
                        seg[k] = 1
            # check left side
            if i > 0 and len(horizontal_pairs) > 1:
                if a - horizontal_pairs[i - 1][1] < _MAGIC_DIFF:
                    for k in range(horizontal_pairs[i - 1][0], b + 1):
                        seg[k] = 1
        else:
            for k in range(a, b + 1):
                seg[k] = 1

    return create_segment_pairs(seg, seg_size)


def split_large(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Split segments wider than MAGIC_SPLIT into two halves."""
    new_pairs = []
    for a, b in pairs:
        size = b - a
        if size > _MAGIC_SPLIT:
            mid = (b - a) // 2 + a
            new_pairs.append((a, mid))
            new_pairs.append((mid + 2, b))
        else:
            new_pairs.append((a, b))
    return new_pairs


def get_rectangles(
    vertical_pairs: list[tuple[int, int]],
    horizontal_pairs: list[tuple[int, int]],
) -> list[Rectangle]:
    """Cross-product of vertical × horizontal spans → character bounding boxes."""
    rects = []
    idx = 0
    for vy0, vy1 in vertical_pairs:
        for hx0, hx1 in horizontal_pairs:
            rects.append(Rectangle(id=idx, x=hx0, y=vy0,
                                   width=hx1 - hx0, height=vy1 - vy0))
            idx += 1
    return rects


def shrink_rectangles(
    image: np.ndarray, squares: list[Rectangle]
) -> list[Rectangle]:
    """Shrink each rectangle to the tightest bounding box of white pixels."""
    new_squares = []
    h, w = image.shape[:2]
    for sq in squares:
        x0 = max(sq.x, 0)
        y0 = max(sq.y, 0)
        x1 = min(sq.x + sq.width, w)
        y1 = min(sq.y + sq.height, h)
        roi = image[y0:y1, x0:x1]

        # find bounding box of non-zero pixels
        ys, xs = np.nonzero(roi)
        if len(xs) == 0 or len(ys) == 0:
            continue

        top = int(ys.min()) - 1
        left = int(xs.min()) - 1
        bottom = int(ys.max()) + 1
        right = int(xs.max()) + 1

        # clamp
        top = max(top, 0)
        left = max(left, 0)
        bottom = min(bottom, roi.shape[0])
        right = min(right, roi.shape[1])

        new_squares.append(Rectangle(
            id=sq.id,
            x=sq.x + left,
            y=sq.y + top,
            width=right - left,
            height=bottom - top,
        ))
    return new_squares


def take_rectangles(squares: list[Rectangle], number: int) -> list[Rectangle]:
    """Keep the `number` largest rectangles, sorted by x-position."""
    by_size = sorted(squares, key=lambda r: r.width * r.height, reverse=True)
    top_n = by_size[:number]
    top_n.sort(key=lambda r: r.id)
    return top_n


def segment_characters(gray: np.ndarray) -> list[np.ndarray]:
    """Full segmentation pipeline: grayscale captcha → list of 32×48 crops.

    Args:
        gray: Grayscale captcha image (any size).

    Returns:
        List of 32×48 uint8 numpy arrays, one per detected character.
        May return fewer than 6 if segmentation fails on some.
    """
    h_orig, w_orig = gray.shape[:2]
    # Resize by factor
    img = cv2.resize(gray, (w_orig * _RESIZE_FACTOR, h_orig * _RESIZE_FACTOR),
                     interpolation=cv2.INTER_LINEAR)

    # Adaptive threshold
    adaptive = cv2.adaptiveThreshold(
        img, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 3, 1
    )
    # Apply adaptive mask to original
    masked = cv2.bitwise_and(img, img, mask=adaptive)

    # Histogram-based optimal threshold
    hist = create_histogram(masked)
    thresh_val = get_ideal_threshold(hist)

    # Binary threshold
    _, binary = cv2.threshold(masked, thresh_val, 255, cv2.THRESH_BINARY)

    # Morphological closing: dilate then erode with 3x3 ellipse
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.dilate(binary, kernel)
    binary = cv2.erode(binary, kernel)

    # Segmentation
    seg_h = horizontal_segments(binary)  # per-column
    seg_v = vertical_segments(binary)    # per-row

    # Create + filter pairs
    v_pairs = filter_vertical_pairs(create_segment_pairs(seg_v, binary.shape[0]))
    h_pairs = split_large(
        filter_horizontal_pairs(
            create_segment_pairs(seg_h, binary.shape[1]), binary.shape[1]
        )
    )

    # Get rectangles, shrink to content, take top 6
    rects = get_rectangles(v_pairs, h_pairs)
    rects = shrink_rectangles(binary, rects)
    rects = take_rectangles(rects, 6)

    # Extract character crops from the original (upscaled) grayscale image
    chars = []
    for sq in rects:
        x0 = max(sq.x, 0)
        y0 = max(sq.y, 0)
        x1 = min(sq.x + sq.width, img.shape[1])
        y1 = min(sq.y + sq.height, img.shape[0])
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        resized = cv2.resize(crop, (_CHAR_WIDTH, _CHAR_HEIGHT),
                             interpolation=cv2.INTER_AREA)
        chars.append(resized)

    return chars


# --- Descriptors (from descriptors.cpp) ---

def simple_descriptor(image: np.ndarray) -> np.ndarray:
    """Flatten image to [0,1] float32 row vector (C++ getSimpleDescriptor)."""
    return (image.astype(np.float32) / 255.0).reshape(1, -1)


def hog_descriptor(image: np.ndarray) -> np.ndarray:
    """Compute HOG descriptor using numpy (compatible with cv2 5.0+).

    Uses 8×8 cells, 8×8 block stride, 16×16 blocks, 9 orientation bins.
    Mirrors the C++ HOGDescriptor(winSize=32×48, blockSize=16×16,
    blockStride=8×8, cellSize=8×8, nbins=9) from descriptors.cpp.
    """
    img = image.astype(np.float32)
    if img.shape != (_CHAR_HEIGHT, _CHAR_WIDTH):
        img = cv2.resize(img, (_CHAR_WIDTH, _CHAR_HEIGHT))

    # Compute gradients (Sobel-like [-1, 0, 1])
    gx = np.zeros_like(img)
    gy = np.zeros_like(img)
    gx[:, 1:-1] = img[:, 2:] - img[:, :-2]
    gy[1:-1, :] = img[2:, :] - img[:-2, :]
    mag = np.sqrt(gx ** 2 + gy ** 2)
    angle = np.arctan2(gy, gx) % np.pi  # 0..pi unsigned

    cell_size = 8
    nbins = 9
    bin_step = np.pi / nbins
    h, w = img.shape
    cy = h // cell_size  # 6
    cx = w // cell_size  # 4

    # Cell histograms
    cell_hists = np.zeros((cy, cx, nbins), dtype=np.float32)
    for by in range(cy):
        for bx in range(cx):
            y0, y1 = by * cell_size, (by + 1) * cell_size
            x0, x1 = bx * cell_size, (bx + 1) * cell_size
            m = mag[y0:y1, x0:x1].ravel()
            a = angle[y0:y1, x0:x1].ravel()
            bin_idx = (a / bin_step).astype(np.int32)
            bin_idx = np.clip(bin_idx, 0, nbins - 1)
            for k in range(nbins):
                cell_hists[by, bx, k] = m[bin_idx == k].sum()

    # Block normalization (L2-norm, 16×16 blocks = 2×2 cells, stride 8)
    features = []
    for by in range(cy - 1):
        for bx in range(cx - 1):
            block = cell_hists[by:by + 2, bx:bx + 2].ravel()
            norm = np.sqrt(np.sum(block ** 2) + 1e-6)
            features.extend((block / norm).tolist())

    return np.array(features, dtype=np.float32).reshape(1, -1)


# --- Model loading ---

_model = None
_model_loaded = False


def _get_model():
    """Load trained SVM model (pickle) from disk.

    Looks for steam_svm.pkl next to this file.
    Returns None if no model found.
    """
    global _model, _model_loaded
    if _model_loaded:
        return _model
    _model_loaded = True

    pkl_path = Path(__file__).resolve().parent / "steam_svm.pkl"

    if pkl_path.exists():
        try:
            with open(pkl_path, "rb") as f:
                _model = pickle.load(f)
            log.info("steam: loaded model from %s", pkl_path)
            return _model
        except Exception as e:
            log.warning("steam: failed to load %s: %s", pkl_path, e)

    log.warning("steam: no trained model found at %s", pkl_path)
    return None


def classify_character(image: np.ndarray, model=None) -> tuple[str, float]:
    """Classify a single 32×48 character crop.

    Returns (predicted_char, confidence). If no model, returns ("?", 0.0).
    Supports any model with .predict() returning class indices or labels.
    """
    if model is None:
        model = _get_model()
    if model is None:
        return ("?", 0.0)

    desc = simple_descriptor(image)

    try:
        pred = model.predict(desc)
        label = pred[0] if hasattr(pred, '__len__') else pred
        # label could be char string or int index
        if isinstance(label, (int, np.integer)):
            if 0 <= int(label) < len(_ALLOWED_CHARS):
                return (_ALLOWED_CHARS[int(label)], 1.0)
            return (str(label), 1.0)
        return (str(label), 1.0)
    except Exception as e:
        log.debug("steam: classify error: %s", e)
        return ("?", 0.0)


# --- Training utility ---

def train_model(
    data_dir: str,
    output_path: Optional[str] = None,
    use_hog: bool = False,
) -> object:
    """Train an SVM on labeled character crops.

    Expected directory structure (matches C++ output/):
        data_dir/
          A/0.png  A/1.png  ...
          B/0.png  B/1.png  ...
          ...
          @/0.png  ...     (aliased as "at")
          %/0.png  ...     (aliased as "pct")
          &/0.png  ...     (aliased as "and")

    Uses sklearn SVC if available (recommended). Without sklearn, raises
    RuntimeError since cv2.ml is not available in OpenCV 5.0+.
    """
    from pathlib import Path as _Path

    _ALIAS_MAP = {"at": "@", "pct": "%", "and": "&"}

    X_list = []
    y_list = []

    data_path = _Path(data_dir)
    for subdir in sorted(data_path.iterdir()):
        if not subdir.is_dir():
            continue
        name = subdir.name
        # reverse alias
        char_label = _ALIAS_MAP.get(name, name)
        if len(char_label) != 1 or char_label not in _ALLOWED_CHARS:
            continue

        class_idx = _ALLOWED_CHARS.index(char_label)

        for img_file in sorted(subdir.glob("*.png")):
            img = cv2.imread(str(img_file), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            if img.shape != (_CHAR_HEIGHT, _CHAR_WIDTH):
                img = cv2.resize(img, (_CHAR_WIDTH, _CHAR_HEIGHT))
            if use_hog:
                desc = hog_descriptor(img)
            else:
                desc = simple_descriptor(img)
            X_list.append(desc.flatten())
            y_list.append(class_idx)

    if not X_list:
        raise RuntimeError(f"steam: no training data found in {data_dir}")

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int32)

    log.info("steam: training on %d samples, %d classes", len(X), len(set(y)))

    try:
        from sklearn.svm import SVC
    except ImportError:
        raise RuntimeError(
            "steam: sklearn is required for training (cv2.ml not in OpenCV 5.0+). "
            "Install with: pip install scikit-learn"
        )
    model = SVC(kernel="linear", C=1.0, max_iter=10000)
    model.fit(X, y)
    log.info("steam: sklearn SVC trained (%d samples, %d classes)", len(X), len(set(y)))
    out = output_path or str(_MODEL_PATH)
    with open(out, "wb") as f:
        pickle.dump(model, f)
    log.info("steam: model saved to %s", out)

    return model


# --- Main solver entry ---

async def solve_steam(
    image_b64: Optional[str] = None,
    url: Optional[str] = None,
    proxy: Optional[str] = None,
    timeout_s: int = 60,
    image=None,
) -> dict:
    """Solve a Steam captcha. Uniform result dict; never raises.

    Supply ONE of:
      - ``image_b64``: base64-encoded PNG/JPEG of the captcha image
      - ``url``: direct URL to fetch the captcha image
      - ``image``: cv2 ndarray (grayscale) or PIL Image

    Returns::

        {
            "solved": bool,
            "type": "steam",
            "token": "ABCDEF",       # the 6-char captcha text
            "method": "segment+svm", # or "segment+no-model"
            "char_count": 6,         # how many chars were segmented
            "elapsed": float,
            "error": str | None
        }
    """
    t0 = time.monotonic()
    result: dict = {
        "type": "steam",
        "solved": False,
        "token": "",
        "method": "segment+svm",
        "error": None,
    }

    gray = None
    try:
        # --- Load image ---
        if image is not None:
            if hasattr(image, "convert"):
                # PIL Image
                import PIL.Image
                gray = np.array(image.convert("L"))
            elif isinstance(image, np.ndarray):
                gray = image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            else:
                result["error"] = "steam: unsupported image type"
                result["elapsed"] = round(time.monotonic() - t0, 2)
                return result
        elif image_b64:
            raw = base64.b64decode(image_b64)
            arr = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if img is None:
                result["error"] = "steam: failed to decode base64 image"
                result["elapsed"] = round(time.monotonic() - t0, 2)
                return result
            gray = img
        elif url:
            import httpx
            async with httpx.AsyncClient(timeout=timeout_s, proxy=proxy) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            arr = np.frombuffer(resp.content, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if img is None:
                result["error"] = "steam: failed to decode fetched image"
                result["elapsed"] = round(time.monotonic() - t0, 2)
                return result
            gray = img
        else:
            result["error"] = "steam: pass image, image_b64, or url"
            result["elapsed"] = round(time.monotonic() - t0, 2)
            return result

        # --- Segment characters ---
        chars = segment_characters(gray)
        if not chars:
            result["error"] = "steam: segmentation produced 0 characters"
            result["elapsed"] = round(time.monotonic() - t0, 2)
            return result

        result["char_count"] = len(chars)

        # --- Classify each character ---
        model = _get_model()
        if model is None:
            result["method"] = "segment+no-model"
            result["error"] = (
                "no trained model — segmentation succeeded (%d chars) but "
                "classification requires a trained SVM. See README.md for "
                "training instructions." % len(chars)
            )
            result["elapsed"] = round(time.monotonic() - t0, 2)
            return result

        predicted = []
        for crop in chars:
            char, conf = classify_character(crop, model)
            predicted.append(char)

        captcha_text = "".join(predicted)
        result["solved"] = True
        result["token"] = captcha_text
        result["elapsed"] = round(time.monotonic() - t0, 2)
        log.info("steam: solved %s (%d chars, %.2fs)",
                 captcha_text, len(chars), result["elapsed"])
        return result

    except Exception as e:
        result["error"] = f"steam: {type(e).__name__}: {str(e)[:200]}"
        result["elapsed"] = round(time.monotonic() - t0, 2)
        log.error("steam: %s", result["error"])
        return result
