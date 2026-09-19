"""Shumei image analysis: NCC icon matching (PIL+numpy) and geometric-shape
detection (cv2 port).

Two engines back the two captcha models:

* icon_select — the order bar (fg.png, RGBA) holds 4 flat red icons in click
  order; the background scatters the same icons rotated and ~2x larger. Each
  icon is extracted from the fg bar by alpha, red-masked, then located in the
  red-masked background with normalized cross-correlation (FFT-based, exact)
  over a scale x rotation grid, coarse-to-refined. Ported from
  taisuii/OpenCV_IconSelect (cv2.matchTemplate + 6-degree rotation sweep),
  re-expressed in pure numpy/PIL.

* spatial_select — the background holds colored 3D primitives on a radial
  gradient with gray distractors. HSV color bands segment each color family
  (immune to gray/white noise), per-object width profiles classify the shape
  family (sphere/cone/pyramid/cylinder/prism), and the instruction
  ("点击图中最小的黄色六棱柱") picks the target by color -> shape family ->
  size. Verbatim port of AhCheng1027/shumei-captcha-protocol-demo
  auto_detector.py.
"""
from __future__ import annotations

import io
import logging
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

log = logging.getLogger("shumei")

try:  # cv2 is an optional accelerant for the spatial_select detector
    import cv2

    _CV2 = True
except ImportError:  # pragma: no cover - documented dep opencv-python-headless
    _CV2 = False


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def load_rgb(data: bytes) -> np.ndarray:
    """Decode image bytes (JPEG/PNG, incl. RGBA) to an RGB uint8 array."""
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    return np.asarray(img)


def _gray(arr: np.ndarray) -> np.ndarray:
    """RGB(A) uint8 -> 2-D float64 luma."""
    a = arr[:, :, :3].astype(np.float64)
    return a @ np.array([0.299, 0.587, 0.114])


def _red_mask(arr: np.ndarray) -> np.ndarray:
    """Zero out everything that is not the vendor's flat red (#d02113-ish).

    Shumei icon_select icons are always this red; the background gradient and
    other-color distractors drop to black, leaving red silhouettes on both the
    fg bar and the bg field (same preprocessing as OpenCV_IconSelect's HSV
    red mask, done in plain RGB math)."""
    a = arr[:, :, :3].astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    keep = (r > 140) & (r - np.maximum(g, b) > 60)
    out = arr[:, :, :3].copy()
    out[~keep] = 0
    return out


# --------------------------------------------------------------------------
# NCC icon matching (icon_select)
# --------------------------------------------------------------------------

def _bg_spectrum(img: np.ndarray, pad_y: int = 0, pad_x: int = 0) -> Tuple[np.ndarray, Tuple[int, int]]:
    """Precompute the padded FFT of the search image (reused across templates).

    `pad_y/pad_x` must cover the largest template the caller will slide
    (rotated bounding box included) so every per-template FFT shares shape."""
    fh, fw = img.shape[0] + pad_y, img.shape[1] + pad_x
    return np.fft.rfft2(img, (fh, fw)), (fh, fw)


def ncc_map(img: np.ndarray, tpl: np.ndarray,
            spec: Optional[Tuple[np.ndarray, Tuple[int, int]]] = None) -> Optional[np.ndarray]:
    """Exact normalized cross-correlation of `tpl` over `img` (zero-mean,
    unit-variance per window; Pearson r). Returns an (H-h+1, W-w+1) map of
    correlation coefficients, or None when either side is degenerate."""
    H, W = img.shape
    h, w = tpl.shape
    if h > H or w > W or h < 1 or w < 1:
        return None
    t = tpl - tpl.mean()
    tsum = float((t * t).sum())
    if tsum <= 1e-9:
        return None
    if spec is None:
        FI, (fh, fw) = _bg_spectrum(img, h - 1, w - 1)
    else:
        FI, (fh, fw) = spec
    FK = np.fft.rfft2(t[::-1, ::-1], (fh, fw))
    corr = np.fft.irfft2(FI * FK, (fh, fw))[h - 1:H, w - 1:W]
    # integral images -> per-window sum and sum of squares
    ii = np.zeros((H + 1, W + 1))
    ii[1:, 1:] = img.cumsum(0).cumsum(1)
    i2 = np.zeros((H + 1, W + 1))
    i2[1:, 1:] = (img * img).cumsum(0).cumsum(1)
    s1 = ii[h:H + 1, w:W + 1] - ii[:-h, w:W + 1] - ii[h:H + 1, :-w] + ii[:-h, :-w]
    s2 = i2[h:H + 1, w:W + 1] - i2[:-h, w:W + 1] - i2[h:H + 1, :-w] + i2[:-h, :-w]
    var = s2 - s1 * s1 / (h * w)
    var[var < 1e-9] = 1e-9
    return corr / np.sqrt(var * tsum)


def split_fg_templates(fg_rgba: np.ndarray) -> List[np.ndarray]:
    """Split the icon_select order bar into per-slot RGB templates.

    The bar (e.g. 148x40) has N red icons on transparency; alpha column runs
    delimit the slots (no fixed grid assumption). Each template is the RGB
    crop with non-icon pixels zeroed."""
    if fg_rgba.shape[2] < 4:
        raise ValueError("fg order bar must be RGBA")
    alpha = fg_rgba[:, :, 3] > 0
    cols = alpha.any(axis=0)
    runs: List[Tuple[int, int]] = []
    start = None
    gap = 0
    for x, v in enumerate(cols):
        if v:
            if start is None:
                start = x
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 3:  # merge runs separated by <=3 px (antialiasing)
                runs.append((start, x - gap))
                start, gap = None, 0
    if start is not None:
        runs.append((start, len(cols) - 1))
    templates = []
    for x0, x1 in runs:
        cell = fg_rgba[:, x0:x1 + 1]
        rgb = cell[:, :, :3].copy()
        rgb[cell[:, :, 3] == 0] = 0
        templates.append(rgb)
    log.info("shumei: fg bar split into %d templates", len(templates))
    return templates


def _top_peaks(score: np.ndarray, count: int, radius: int) -> List[Tuple[float, int, int]]:
    """Iterative argmax with neighborhood suppression -> [(score, y, x), ...]."""
    m = score.copy()
    peaks = []
    for _ in range(count):
        y, x = np.unravel_index(int(np.argmax(m)), m.shape)
        peaks.append((float(m[y, x]), int(y), int(x)))
        y0, y1 = max(0, y - radius), min(m.shape[0], y + radius + 1)
        x0, x1 = max(0, x - radius), min(m.shape[1], x + radius + 1)
        m[y0:y1, x0:x1] = -2.0
    return peaks


def match_pieces(bg_rgb: np.ndarray, templates: Sequence[np.ndarray],
                 scales: Sequence[float] = (1.4, 1.6, 1.8, 2.0, 2.2),
                 coarse_step: int = 30, refine_span: int = 20, refine_step: int = 5,
                 min_dist: int = 40, score_floor: float = 0.45) -> List[Dict]:
    """Locate each icon template in the background; results keep template order
    (= click order). Returns [{"x", "y", "score", "scale", "angle"}, ...] with
    (x, y) the icon center in bg pixels.

    Search: red-mask both sides, sweep rotation x scale with exact FFT-NCC
    (bg spectrum computed once), refine the best coarse angle/scale, then
    greedily assign each template its best peak that no earlier template
    already claimed (within `min_dist`)."""
    if not templates:
        return []
    bgm = _gray(_red_mask(bg_rgb))
    # pad so even the biggest scaled+rotated template shares one FFT shape
    _tw = max(t.shape[1] for t in templates) * max(scales)
    _th = max(t.shape[0] for t in templates) * max(scales)
    _pad = int(np.hypot(_tw, _th)) + 2
    spec = _bg_spectrum(bgm, _pad, _pad)
    H, W = bgm.shape

    def search(tpl_rgb: np.ndarray) -> List[Tuple[float, int, int, Tuple[float, int]]]:
        """All scored candidates for one template across the scale/angle grid."""
        base = Image.fromarray(_red_mask(tpl_rgb))
        cands: List[Tuple[float, int, int, Tuple[float, int]]] = []
        for scale in scales:
            tw = max(4, int(round(tpl_rgb.shape[1] * scale)))
            th = max(4, int(round(tpl_rgb.shape[0] * scale)))
            if tw >= W or th >= H:
                continue
            scaled = base.resize((tw, th), Image.LANCZOS)
            for angle in range(-180, 180, coarse_step):
                rot = scaled if angle == 0 else scaled.rotate(angle, expand=True, fillcolor=(0, 0, 0))
                sc = ncc_map(bgm, _gray(np.asarray(rot.convert("RGB"))), spec)
                if sc is None:
                    continue
                for s, y, x in _top_peaks(sc, 3, min(tw, th) // 2):
                    cands.append((s, x + rot.width // 2, y + rot.height // 2, (scale, angle)))
        if not cands:
            return []
        cands.sort(key=lambda c: -c[0])
        best_scale, best_angle = cands[0][3]
        base_w, base_h = tpl_rgb.shape[1], tpl_rgb.shape[0]
        # refine: sweep angles within ±refine_span of the coarse winner at
        # refine_step resolution, plus scale neighbors at the winning angle
        scaled = base.resize((max(4, int(round(base_w * best_scale))),
                              max(4, int(round(base_h * best_scale)))), Image.LANCZOS)
        for angle in range(best_angle - refine_span, best_angle + refine_span + 1, refine_step):
            rot = scaled.rotate(angle, expand=True, fillcolor=(0, 0, 0))
            sc = ncc_map(bgm, _gray(np.asarray(rot.convert("RGB"))), spec)
            if sc is None:
                continue
            y, x = np.unravel_index(int(np.argmax(sc)), sc.shape)
            cands.append((float(sc[y, x]), int(x + rot.width // 2), int(y + rot.height // 2),
                          (best_scale, angle)))
        step = scales[1] - scales[0] if len(scales) > 1 else 0.2
        for scale in scales:
            if abs(scale - best_scale) > step + 1e-9:
                continue
            scaled = base.resize((max(4, int(round(base_w * scale))),
                                  max(4, int(round(base_h * scale)))), Image.LANCZOS)
            rot = scaled.rotate(best_angle, expand=True, fillcolor=(0, 0, 0))
            sc = ncc_map(bgm, _gray(np.asarray(rot.convert("RGB"))), spec)
            if sc is None:
                continue
            y, x = np.unravel_index(int(np.argmax(sc)), sc.shape)
            cands.append((float(sc[y, x]), int(x + rot.width // 2), int(y + rot.height // 2),
                          (scale, best_angle)))
        cands.sort(key=lambda c: -c[0])
        return cands

    taken: List[Tuple[int, int]] = []
    results: List[Dict] = []
    for idx, tpl in enumerate(templates):
        cands = search(tpl)
        pick = None
        for s, cx, cy, meta in cands:
            if all((cx - tx) ** 2 + (cy - ty) ** 2 >= min_dist * min_dist for tx, ty in taken):
                pick = (s, cx, cy, meta)
                break
        if pick is None or pick[0] < score_floor:
            log.warning("shumei: template %d/%d not matched (best %.3f)",
                        idx + 1, len(templates), cands[0][0] if cands else -1.0)
            results.append({"x": -1, "y": -1, "score": pick[0] if pick else 0.0,
                            "scale": 0.0, "angle": 0})
            continue
        s, cx, cy, (scale, angle) = pick
        taken.append((cx, cy))
        results.append({"x": cx, "y": cy, "score": round(s, 4),
                        "scale": scale, "angle": angle})
        log.info("shumei: slot %d -> (%d,%d) score %.3f scale %.1f angle %d",
                 idx + 1, cx, cy, s, scale, angle)
    return results


# --------------------------------------------------------------------------
# geometric-shape detection (spatial_select) — cv2 port of auto_detector.py
# --------------------------------------------------------------------------

# OpenCV HSV hue bands: (Chinese name, hue center, half width)
COLOR_BANDS = [
    ("红色", 0, 14),
    ("黄色", 22, 10),
    ("绿色", 60, 25),
    ("蓝色", 108, 20),
    ("紫色", 150, 16),
]
MIN_AREA = 700
MIN_SIZE = 14

FAMILY_MAP = {
    "长方体": ["棱柱", "长方体", "六棱柱", "五棱柱", "立方体", "正方体"],
    "正方体": ["棱柱", "长方体", "六棱柱", "五棱柱", "立方体", "正方体"],
    "六棱柱": ["棱柱", "六棱柱", "长方体", "五棱柱"],
    "五棱柱": ["棱柱", "五棱柱", "长方体", "六棱柱"],
    "棱柱": ["棱柱", "长方体", "六棱柱", "五棱柱", "立方体", "正方体"],
    "圆锥": ["圆锥", "三棱锥"],
    "三棱锥": ["三棱锥", "圆锥"],
    "棱锥": ["三棱锥", "圆锥"],
    "球体": ["球体", "圆球", "球"],
    "圆形": ["球体", "圆球", "球"],
    "圆柱体": ["圆柱体", "圆柱", "圆棍"],
}


def shape_family(shape: Optional[str]) -> Optional[set]:
    return set(FAMILY_MAP.get(shape, [shape])) if shape else None


def parse_instruction(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """'点击图中最小的黄色六棱柱' -> ('黄色', '六棱柱', '最小')."""
    color = next((c for c in ("红色", "黄色", "绿色", "蓝色", "紫色") if c in text), None)
    shape = next((s for s in ("长方体", "正方体", "圆柱体", "六棱柱", "五棱柱",
                              "三棱锥", "圆锥", "球体", "棱柱", "棱锥", "圆形", "三角形")
                  if s in text), None)
    size = "最小" if "最小" in text else ("最大" if "最大" in text else None)
    return color, shape, size


def select_target(objects: List[Dict], instruction: str) -> Dict:
    """Pick the object matching color -> shape family -> size constraint.
    Falls back to same-color candidates when shape classification is unsure."""
    color, shape, size = parse_instruction(instruction)
    fam = shape_family(shape)
    color_objs = [o for o in objects if not color or o["color"] == color]
    candidates = [o for o in color_objs if not fam or o["type"] in fam]
    if not candidates and color_objs:
        log.info("shumei: no %s in %s objects, falling back to color only", shape, color)
        candidates = color_objs
    if not candidates:
        raise ValueError(f"no object matches instruction: {instruction}")
    if size == "最小":
        return min(candidates, key=lambda o: o["area"])
    if size == "最大":
        return max(candidates, key=lambda o: o["area"])
    return candidates[0]


def _hue_dist(h: np.ndarray, center: int) -> np.ndarray:
    d = np.abs(h - center)
    return np.minimum(d, 180 - d)


def _profile_features(mask_bool: np.ndarray, V: np.ndarray) -> Optional[Dict]:
    """Width-profile / circularity features of one object mask (port)."""
    rows = np.where(mask_bool.any(axis=1))[0]
    if len(rows) == 0:
        return None
    top, bot = rows[0], rows[-1]
    cols = np.where(mask_bool.any(axis=0))[0]
    lft, rgt = cols[0], cols[-1]
    span = max(rgt - lft, 1)
    hgt = bot - top + 1

    prof = []
    for f in range(1, 21):
        yy = top + hgt * f // 20
        row = mask_bool[yy, :]
        idx = np.where(row)[0]
        prof.append((idx[-1] - idx[0] + 1) / span if len(idx) else 0.0)
    topw = prof[0]
    maxw = max(prof)
    peak = max(range(20), key=lambda i: prof[i])

    flat = cur = 0
    for v in prof:
        if v >= 0.88 * maxw:
            cur += 1
            flat = max(flat, cur)
        else:
            cur = 0
    flat_ratio = flat / 20.0
    tw5 = sum(prof[:5]) / 5.0 / maxw

    mask_u8 = mask_bool.astype(np.uint8) * 255
    cnts, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnt = max(cnts, key=cv2.contourArea)
    area = cv2.contourArea(cnt)
    perim = cv2.arcLength(cnt, True)
    circ = 4 * np.pi * area / (perim * perim + 1e-6)
    hull = cv2.convexHull(cnt)
    solid = area / (cv2.contourArea(hull) + 1e-6)

    vv = V[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1][mask_bool[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]]
    if len(vv) > 20:
        hist, _ = np.histogram(vv, bins=14, range=(vv.min(), vv.max() + 1))
        sm = cv2.GaussianBlur(hist.astype(np.float32).reshape(-1, 1), (0, 0), 1.5).ravel()
        vpk = int((sm > sm.max() * 0.4).sum())
    else:
        vpk = 1

    return dict(topw=topw, maxw=maxw, peak=peak, flat=flat_ratio, tw5=tw5,
                circ=circ, solid=solid, vpk=vpk, span=span, hgt=hgt, prof=prof)


def classify_shape(feat: Dict) -> str:
    """Width-profile rules -> shape family (port of auto_detector.classify_shape)."""
    prof, maxw, tw5, flat, peak = feat["prof"], feat["maxw"], feat["tw5"], feat["flat"], feat["peak"]
    circ, w, h, topw = feat["circ"], feat["span"], feat["hgt"], feat["topw"]
    ar = w / h

    if topw <= 0.32 and flat < 0.35:            # triangle family
        return "圆锥" if peak >= 13 else "三棱锥"
    if (circ > 0.72 and 0.8 <= ar <= 1.3 and 0.3 <= topw <= 0.85
            and flat < 0.6 and prof[17] <= 0.85):  # sphere
        return "球体"
    if flat >= 0.4:                              # cylinder/prism family
        if tw5 >= 0.6 and topw >= 0.4:
            return "圆柱体"
        return "棱柱"
    if topw <= 0.32:
        return "圆锥" if peak >= 13 else "三棱锥"
    if flat >= 0.25:
        return "圆柱体" if tw5 >= 0.6 and topw >= 0.4 else "棱柱"
    if circ > 0.6 and ar <= 1.3:
        return "球体"
    return "棱柱"


def detect_objects(img_bgr: np.ndarray) -> List[Dict]:
    """Detect all colored geometric objects in a BGR image (cv2 required).
    Returns [{'color', 'type', 'center', 'area'}, ...] sorted by color/row."""
    if not _CV2:
        raise RuntimeError("spatial_select detection needs opencv-python-headless")
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)

    objects: List[Dict] = []
    for color_name, center, half_w in COLOR_BANDS:
        mask = ((_hue_dist(H, center) <= half_w) & (S > 25)).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        ncc, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, ncc):
            x, y, w, h, area = stats[i]
            if area < MIN_AREA or w < MIN_SIZE or h < MIN_SIZE:
                continue
            feat = _profile_features(labels == i, V)
            if feat is None:
                continue
            objects.append({
                "type": classify_shape(feat),
                "color": color_name,
                "center": (x + w // 2, y + h // 2),
                "area": int(area),
            })
    objects.sort(key=lambda o: (o["color"], o["center"][1]))
    return objects
