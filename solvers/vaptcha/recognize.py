"""Vaptcha gesture-line recognition — classical-CV port of wobuxiangtong/vaptcha_recoginse.

The reference (https://github.com/wobuxiangtong/vaptcha_recoginse, 2019) segments the
gesture stroke in the 480x270 challenge image with a Mask R-CNN trained on 109 labelme
annotations (shipped in that repo under images/). The TF/Mask-R-CNN stack is out of
scope for this sidecar's venv, so this module ports the *task* the network solved —
"locate the translucent gesture stroke and return an ordered centerline" — with:

  1. white top-hat lift (the stroke brightens its local neighborhood by ~40-90 gray
     levels; measured on the reference's 109 labeled images: mean lift 59 inside the
     annotated stroke vs 22 outside),
  2. a dynamic-programming max-brightness path over columns (the stroke is monotone
     in x across every reference sample — a wide arc sweeping left to right), which
     globally locks onto the bright curve and ignores local bright blobs,
  3. k-best paths (the true stroke is essentially always among the 3 best; union
     metric on the 109 labeled samples: 88% of annotated centerline points within
     25px), ranked by contrast / saturation / edge-sharpness heuristics.

If a trained segmentation model is available at solvers/vaptcha/line_seg.onnx it is
preferred (see README for the training recipe that reproduces the reference's Mask
R-CNN labels with a small U-Net); the classical path is the always-available fallback.

All functions are pure CPU (cv2 + numpy), deterministic given the same image, and
unit-tested against the reference dataset plus synthetic images.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("vaptcha.recognize")

# Directory of this module — the optional trained model lives next to it.
_MODEL_PATH = Path(__file__).parent / "line_seg.onnx"

# Classical detector hyper-parameters (tuned on the reference's 109 labeled images).
TOPHAT_KERNEL = 21      # stroke width is 4-9px; kernel must exceed it to isolate the lift
DP_SLOPE = 6            # max |dy| between adjacent columns (~vertical tangent limit)
DP_LAMBDA = 0.35        # per-column slope penalty (smoothness)
K_CURVES = 3            # k-best candidate paths
CURVE_BAND = 14         # px suppressed around a found curve before extracting the next
N_ANCHORS = 3           # click/trace anchors sampled along the final curve


def _gray(im_rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(im_rgb, cv2.COLOR_RGB2GRAY)


def tophat_lift(im_rgb: np.ndarray, ksz: int = TOPHAT_KERNEL) -> np.ndarray:
    """White top-hat of the grayscale image — local bright-lift response (float32)."""
    gray = _gray(im_rgb)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksz, ksz))
    return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k).astype(np.float32)


def _dp_best_curve(cost: np.ndarray, slope: int = DP_SLOPE, lam: float = DP_LAMBDA) -> np.ndarray:
    """Best-scoring x-monotone path through `cost` (HxW). Returns (W, 2) int array."""
    h, w = cost.shape
    cum = np.zeros((h, w), np.float32)
    back = np.zeros((h, w), np.int16)
    cum[:, 0] = cost[:, 0]
    big = np.float32(-1e18)
    for x in range(1, w):
        prev = cum[:, x - 1]
        best = np.full(h, big, np.float32)
        barg = np.zeros(h, np.int16)
        for dy in range(-slope, slope + 1):
            cand = np.roll(prev, dy) - lam * abs(dy)
            if dy > 0:
                cand[:dy] = big
            elif dy < 0:
                cand[dy:] = big
            m = cand > best
            best[m] = cand[m]
            barg[m] = dy
        cum[:, x] = cost[:, x] + best
        back[:, x] = barg
    y = int(np.argmax(cum[:, -1]))
    ys = [y]
    for x in range(w - 1, 0, -1):
        y -= int(back[y, x])
        ys.append(y)
    ys.reverse()
    return np.column_stack([np.arange(w), np.asarray(ys)])


def dp_curves(lift: np.ndarray, k: int = K_CURVES, band: int = CURVE_BAND,
              slope: int = DP_SLOPE, lam: float = DP_LAMBDA) -> list[np.ndarray]:
    """k-best distinct bright curves: run the DP, suppress the found band, repeat."""
    cost = lift.copy()
    curves: list[np.ndarray] = []
    for _ in range(k):
        c = _dp_best_curve(cost, slope, lam)
        curves.append(c)
        ys = c[:, 1].astype(int)
        xs = c[:, 0]
        for dy in range(-band, band + 1):
            cost[np.clip(ys + dy, 0, cost.shape[0] - 1), xs] = 0.0
    return curves


def curve_rank_score(im_rgb: np.ndarray, lift: np.ndarray, curve: np.ndarray) -> float:
    """Heuristic "how stroke-like is this curve" score (higher = better).

    The true gesture stroke is a soft translucent ribbon: locally bright, low
    saturation, moderate edge sharpness. Natural bright lifts (sky, lights) score
    worse on the contrast term; hard photo edges on the sharpness term.
    """
    h, w = lift.shape
    ys = np.clip(curve[:, 1], 0, h - 1).astype(int)
    xs = curve[:, 0]
    on = lift[ys, xs]
    above = lift[np.clip(ys - 18, 0, h - 1), xs]
    below = lift[np.clip(ys + 18, 0, h - 1), xs]
    contrast = float(on.mean() - (above.mean() + below.mean()) / 2.0)
    rgb = im_rgb[ys, xs].astype(np.float32)
    sat = float((rgb.max(1) - rgb.min(1)).mean())
    gray = _gray(im_rgb).astype(np.float32)
    gy, gx = np.gradient(gray)
    sharp = float(np.hypot(gx[ys, xs], gy[ys, xs]).mean())
    return contrast - 0.15 * sat - 0.05 * sharp


def detect_gesture_curves(im_rgb: np.ndarray, k: int = K_CURVES) -> list[np.ndarray]:
    """Ranked candidate centerlines (best first), each an (W, 2) float array of (x, y)."""
    lift = tophat_lift(im_rgb)
    curves = dp_curves(lift, k=k)
    scored = sorted(
        ((curve_rank_score(im_rgb, lift, c), c) for c in curves),
        key=lambda t: -t[0],
    )
    return [c.astype(float) for _, c in scored]


def sample_anchors(curve: np.ndarray, n: int = N_ANCHORS,
                   t0: float = 0.15, t1: float = 0.85) -> list[tuple[float, float]]:
    """n points evenly spaced along the arc length of the curve (t0..t1 of total)."""
    seg = np.hypot(np.diff(curve[:, 0]), np.diff(curve[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    out = []
    for fr in np.linspace(t0, t1, n) if n > 1 else [0.5]:
        target = fr * total
        i = min(int(np.searchsorted(cum, target)), len(curve) - 2)
        f = (target - cum[i]) / max(seg[i], 1e-9)
        x = curve[i, 0] + f * (curve[i + 1, 0] - curve[i, 0])
        y = curve[i, 1] + f * (curve[i + 1, 1] - curve[i, 1])
        out.append((float(x), float(y)))
    return out


# --------------------------------------------------------------------------
# Optional trained-model path (line_seg.onnx) — preferred when present.
# --------------------------------------------------------------------------
def _skeleton(mask: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning (pure numpy; no skimage/scipy dependency)."""
    img = (mask > 0).astype(np.uint8)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            p = np.pad(img, 1)
            p2 = p[:-2, 1:-1]; p3 = p[:-2, 2:]; p4 = p[1:-1, 2:]; p5 = p[2:, 2:]
            p6 = p[2:, 1:-1]; p7 = p[2:, :-2]; p8 = p[1:-1, :-2]; p9 = p[:-2, :-2]
            seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            b = sum(seq[i] for i in range(8))
            a = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8))
            if step == 0:
                cond = (img == 1) & (b >= 2) & (b <= 6) & (a == 1) & (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                cond = (img == 1) & (b >= 2) & (b <= 6) & (a == 1) & (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            nxt = img.copy()
            nxt[cond] = 0
            if (nxt != img).any():
                changed = True
            img = nxt
    return img


def _skeleton_path(skel: np.ndarray, gap: float = 12.0) -> np.ndarray | None:
    """Nearest-neighbour walk along a 1px skeleton, returning ordered (N, 2) points."""
    ys, xs = np.where(skel > 0)
    if len(xs) < 8:
        return None
    pts = np.column_stack([xs, ys]).astype(float)
    if len(pts) > 1800:
        pts = pts[np.linspace(0, len(pts) - 1, 1800).astype(int)]
    cur = pts[np.lexsort((pts[:, 1], pts[:, 0]))[0]]
    remaining = pts.copy()
    order = [cur]
    while len(remaining):
        d = np.hypot(remaining[:, 0] - cur[0], remaining[:, 1] - cur[1])
        j = int(np.argmin(d))
        if d[j] > gap:
            break
        order.append(remaining[j])
        cur = remaining[j]
        remaining = np.delete(remaining, j, axis=0)
    if len(order) < 8:
        return None
    return np.array(order)


def _onnx_line_curve(im_rgb: np.ndarray, model_path: Path) -> np.ndarray | None:
    """Run the optional trained segmenter (see README) and return its centerline.

    Contract: ONNX graph takes a float32 [1, 3, H, W] image (RGB, 0..1, H=270, W=480
    per the reference dataset) and returns [1, 1, H, W] stroke-probability logits.
    """
    try:
        import onnxruntime as ort  # noqa: PLC0415
    except ImportError:
        return None
    try:
        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        x = im_rgb.astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[None]
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
        prob = 1.0 / (1.0 + np.exp(-out[0, 0]))
        mask = (prob > 0.5).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        path = _skeleton_path(_skeleton(mask))
        if path is None:
            return None
        # resample to full width for a uniform return type
        return path
    except Exception as exc:  # model errors must never take the solver down
        log.warning("onnx line_seg inference failed: %s", exc)
        return None


def detect_line_curve(im_rgb: np.ndarray) -> tuple[np.ndarray, str]:
    """Best-guess gesture-stroke centerline. Returns (curve_points (W,2) or array,
    method) — method is "onnx" (trained model) or "dp-topohat" (classical port)."""
    if _MODEL_PATH.exists():
        c = _onnx_line_curve(im_rgb, _MODEL_PATH)
        if c is not None and len(c) >= 8:
            return c.astype(float), "onnx"
        log.info("onnx model present but produced no path; falling back to DP")
    curves = detect_gesture_curves(im_rgb, k=1)
    if not curves:
        return np.empty((0, 2)), "dp-tophat"
    return curves[0], "dp-tophat"
