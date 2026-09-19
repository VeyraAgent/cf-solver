"""Douyin/TikTok puzzle gap detection.

Two detectors, same (score, x, y) contract:

1. detect_gap_reference — faithful port of onurkun/puzzle-captcha-resolver
   (library.py `build()`): invert → in-range palette mask ([80,80,70]-[130,120,120])
   on the 404x150 full image, pure-white mask + invert on the 83x55 piece, then
   cv2.matchTemplate(TM_CCORR_NORMED). Requires cv2. The palette window is tuned
   to douyin's slider rendering; captures that drift from it score low and the
   fusion in detect_gap() then defers to the generic detector.

2. detect_gap_edges — palette-independent fallback (PIL + numpy, no cv2):
   Pearson NCC over gradient maps. Template = piece silhouette ring (dominant)
   + masked content gradients. A puzzle hole is a darkened copy of the piece
   crop, so a constant gradient scale is removed by the zero-mean normalization
   and the ring aligns with the hole boundary.

detect_gap() runs the generic detector as primary and only overrides it with the
reference result when edges confidence is weak (< _EDGES_MIN) while the reference
is above its own accept threshold (> _REF_MIN, the `if result[0] > 0.5` line).
"""
from __future__ import annotations

import base64
import io
import logging

import numpy as np

log = logging.getLogger("douyin")

# Sizes the reference port renders to (douyin web slider, from the reference repo).
REF_FULL_SIZE = (404, 150)   # (w, h)
REF_PIECE_SIZE = (83, 55)    # (w, h)

# Fusion thresholds.
_REF_MIN = 0.5         # reference's own accept threshold
_EDGES_MIN = 0.35      # generic detector must fall below this for fusion to consider the reference
_REF_LOCAL_MIN = 0.45  # and the edges NCC at the reference point must clear this


def _b64_decode(data: str) -> bytes:
    """Decode base64 image payload (raw, data-URI prefixed, or url-safe)."""
    s = data.strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[1]
    s = s.replace("-", "+").replace("_", "/").replace(" ", "")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def _to_pil(data: str):
    from PIL import Image
    return Image.open(io.BytesIO(_b64_decode(data)))


def _to_rgb_array(data: str, size: tuple[int, int] | None = None) -> np.ndarray:
    """Decode an image payload to an HxWx3 uint8 RGB array (optionally resized)."""
    img = _to_pil(data)
    if size is not None:
        img = img.resize(size, Image.LANCZOS)
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


def detect_gap_reference(full_b64: str, piece_b64: str,
                         full_size: tuple[int, int] = REF_FULL_SIZE,
                         piece_size: tuple[int, int] = REF_PIECE_SIZE) -> tuple[float, int, int]:
    """Port of onurkun/puzzle-captcha-resolver library.build(): (score, x, y).

    Raises ImportError when cv2 is unavailable — callers go through detect_gap().
    """
    import cv2

    full = _to_rgb_array(full_b64)
    piece = np.asarray(_to_pil(piece_b64).convert("RGB"), dtype=np.uint8)

    full_bgr = cv2.cvtColor(full, cv2.COLOR_RGB2BGR)
    image2 = cv2.resize(full_bgr, full_size, interpolation=cv2.INTER_AREA)
    image = cv2.bitwise_not(image2)

    # Full-image palette window (reference constants, converted BGR→RGB).
    lower = np.array([70, 80, 80], dtype="uint8")    # R, G, B
    upper = np.array([120, 120, 130], dtype="uint8")
    mask = cv2.inRange(image, lower, upper)
    output = cv2.bitwise_and(image, image, mask=mask)
    gray = cv2.cvtColor(output, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    output_new = np.zeros_like(output)
    output_new[:, :, 0] = gray
    output_new[:, :, 1] = gray
    output_new[:, :, 2] = gray

    test = cv2.resize(cv2.cvtColor(piece, cv2.COLOR_RGB2BGR), piece_size,
                      interpolation=cv2.INTER_AREA)
    template_width = cv2.resize(cv2.cvtColor(piece, cv2.COLOR_RGB2GRAY), piece_size,
                                interpolation=cv2.INTER_AREA)
    white = np.array([255, 255, 255], dtype="uint8")
    wmask = cv2.inRange(test, white, white)
    template = cv2.bitwise_and(test, test, mask=wmask)
    template = cv2.bitwise_not(template)

    tW, tH = template_width.shape[::-1]
    result = cv2.matchTemplate(output_new, template, cv2.TM_CCORR_NORMED)
    _min_val, _max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    return (float(_max_val), int(max_loc[0]), int(max_loc[1]))


def _grad_mag(gray: np.ndarray) -> np.ndarray:
    """Gradient magnitude of a 2-D array (central differences, numpy only)."""
    gx = np.empty_like(gray, dtype=np.float32)
    gy = np.empty_like(gray, dtype=np.float32)
    gx[:, 1:-1] = (gray[:, 2:] - gray[:, :-2]) / 2.0
    gy[1:-1, :] = (gray[2:, :] - gray[:-2, :]) / 2.0
    gx[:, 0] = gx[:, 1]
    gx[:, -1] = gx[:, -2]
    gy[0, :] = gy[1, :]
    gy[-1, :] = gy[-2, :]
    return np.sqrt(gx * gx + gy * gy)


def _blur3(a: np.ndarray) -> np.ndarray:
    """3x3 box blur (edge padding) — tolerates ±1px alignment."""
    p = np.pad(a, 1, mode="edge")
    return (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] + p[1:-1, :-2] + p[1:-1, 1:-1]
            + p[1:-1, 2:] + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) / 9.0


def _piece_mask(pimg) -> np.ndarray:
    """Piece silhouette as float32 0/1: alpha channel when it varies, else
    non-near-white pixels (the reference repo's pure-white mask, tolerant of
    compression noise), else the 1px canvas border ring (fully opaque JPEG)."""
    if pimg.mode in ("RGBA", "LA", "PA"):
        a = np.asarray(pimg.convert("RGBA"))[:, :, 3]
        if a.min() < 250:
            return (a > 8).astype(np.float32)
    rgb = np.asarray(pimg.convert("RGB"), dtype=np.int16)
    m = (rgb.min(axis=2) < 243).astype(np.float32)
    if m.all():
        m[:, :] = 0.0
        m[0, :] = m[-1, :] = m[:, 0] = m[:, -1] = 1.0
    return m


def _integral(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Padded (H+1, W+1) integral images of a and a² for O(1) window sums."""
    af = a.astype(np.float64)
    ii = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    ii[1:, 1:] = np.cumsum(np.cumsum(af, axis=0), axis=1)
    isq = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    isq[1:, 1:] = np.cumsum(np.cumsum(af * af, axis=0), axis=1)
    return ii, isq


# Template weights: the silhouette ring is the precisely-aligned, high-signal
# feature (the hole boundary is a darkened copy of the piece outline); the
# content gradients disambiguate when multiple rings exist.
_W_RING = 2.5
_W_CONTENT = 1.0


def detect_gap_edges(full_b64: str, piece_b64: str,
                     full_size: tuple[int, int] | None = None,
                     return_map: bool = False) -> dict:
    """Generic puzzle gap locator: Pearson NCC over gradient maps (PIL + numpy).

    Template = silhouette-ring gradients (dominant) + masked content gradients.
    A puzzle hole is a darkened copy of the piece crop, and a constant gradient
    scale is removed by the zero-mean normalization. Returns {"score", "x", "y"}
    with (x, y) = best window top-left; return_map=True adds the full NCC map
    (used by detect_gap's fusion validation).
    """
    full = _to_rgb_array(full_b64, full_size)
    pimg = _to_pil(piece_b64)
    piece = np.asarray(pimg.convert("RGB"), dtype=np.uint8)

    fh, fw = full.shape[:2]
    ph, pw = piece.shape[:2]
    if ph > fh or pw > fw:
        raise ValueError(f"piece {pw}x{ph} larger than full {fw}x{fh}")

    mask = _piece_mask(pimg)
    I = _blur3(_grad_mag(full.mean(axis=2).astype(np.float32)))
    T = (_W_RING * _grad_mag(mask)
         + _W_CONTENT * _grad_mag((piece * mask[:, :, None]).mean(axis=2).astype(np.float32)))
    T[T < 4.0] = 0.0  # suppress faint texture edges; keep ring + strong content

    t_norm = T - T.mean()
    t_sq = float((t_norm * t_norm).sum())
    ii, isq = _integral(I)
    windows = np.lib.stride_tricks.sliding_window_view(I, (ph, pw))
    num = np.einsum("klij,ij->kl", windows, t_norm)

    H, W = num.shape
    ys = np.arange(H)[:, None]
    xs = np.arange(W)[None, :]
    sw = ii[ys + ph, xs + pw] - ii[ys, xs + pw] - ii[ys + ph, xs] + ii[ys, xs]
    sq = isq[ys + ph, xs + pw] - isq[ys, xs + pw] - isq[ys + ph, xs] + isq[ys, xs]
    mean = sw / (ph * pw)
    var = np.maximum(sq / (ph * pw) - mean * mean, 1e-9)
    ncc = num / np.sqrt(t_sq * var * ph * pw)

    y, x = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
    out = {"score": float(ncc[y, x]), "x": int(x), "y": int(y)}
    if return_map:
        out["ncc"] = ncc
    return out


def _ncc_at(ncc: np.ndarray | None, x: int, y: int) -> float:
    """Edges NCC value at (x, y), 0.0 when out of bounds / map unavailable."""
    if ncc is None or not (0 <= y < ncc.shape[0] and 0 <= x < ncc.shape[1]):
        return 0.0
    return float(ncc[y, x])


def detect_gap(full_b64: str, piece_b64: str) -> dict:
    """Solve for the puzzle gap: {"x", "y", "score", "method", "ref_score", "ref_x", "ref_y"}.

    (x, y) = top-left of the gap in full-image coordinates.
    """
    t_edges = detect_gap_edges(full_b64, piece_b64, return_map=True)
    ref = None
    try:
        ref = detect_gap_reference(full_b64, piece_b64)
    except ImportError:
        log.info("cv2 unavailable — edges detector only")
    except Exception as exc:  # palette drift on odd captures must not kill the solve
        log.info("reference detector failed: %s", exc)

    out = {
        "x": t_edges["x"], "y": t_edges["y"], "score": round(t_edges["score"], 4),
        "method": "edges-ncc", "ref_score": None, "ref_x": None, "ref_y": None,
    }
    if ref is not None:
        out.update(ref_score=round(ref[0], 4), ref_x=ref[1], ref_y=ref[2])
        if t_edges["score"] < _EDGES_MIN:
            # The reference's TM_CCORR_NORMED inflates on bright templates, so a
            # high ref_score alone proves nothing: re-score the reference point
            # under the edges NCC map and require it to be locally decent.
            ncc = t_edges.get("ncc")
            local = _ncc_at(ncc, ref[1], ref[2])
            if ref[0] > _REF_MIN and local >= _REF_LOCAL_MIN:
                log.info("edges weak (%.3f) — overriding with reference result "
                         "(%.3f @ %d,%d, local ncc %.3f)",
                         t_edges["score"], ref[0], ref[1], ref[2], local)
                out.update(x=ref[1], y=ref[2], score=round(ref[0], 4),
                           method="reference-palette")
    log.info("gap: method=%s x=%d y=%d score=%.3f ref_score=%s",
             out["method"], out["x"], out["y"], out["score"], out["ref_score"])
    return out
