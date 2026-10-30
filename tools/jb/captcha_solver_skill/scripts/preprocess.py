#!/usr/bin/env python3
"""Pra-proses & segmentasi captcha gambar. Tanpa kredensial.

Pakai:
  python preprocess.py INPUT.png --seg-dir out/seg        # simpan potongan karakter
  python preprocess.py INPUT.png --chars 5                # hanya hasil binarize
Dependencies: opencv-python, numpy, (opsional) scikit-image.
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np


def load_gray(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        sys.exit(f"[!] gagal membaca gambar: {path}")
    return img


def deskew(gray: np.ndarray) -> np.ndarray:
    coords = np.column_stack(np.where(gray < 128))
    if len(coords) < 10:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.5 or abs(angle) > 15:
        return gray
    h, w = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def binarize(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    th = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 10)
    # buang noise kecil
    num, _, stats, _ = cv2.connectedComponentsWithStats(th, 8)
    cleaned = np.zeros_like(th)
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] >= 12:
            cleaned[th == i] = 255
    return cleaned


def segment(binary: np.ndarray, n_chars: int = 0) -> list:
    """Potong kolom-kolom karakter via proyeksi vertikal."""
    col = binary.sum(axis=0)
    gaps, runs, start = [], [], None
    for x, v in enumerate(col):
        if v > 0 and start is None:
            start = x
        elif v == 0 and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, len(col)))

    # gabung run yang terlalu sempit (noise) atau terpisah <3px (satu karakter)
    merged = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= 3:
            merged[-1] = (merged[-1][0], e)
        elif e - s >= 3:
            merged.append((s, e))

    if n_chars and len(merged) > n_chars:
        merged.sort(key=lambda r: (r[1] - r[0]), reverse=True)
        merged = sorted(merged[:n_chars])
    return merged


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--seg-dir", help="folder output potongan karakter")
    ap.add_argument("--chars", type=int, default=0, help="panjang captcha jika diketahui")
    args = ap.parse_args()

    gray = load_gray(args.image)
    gray = deskew(gray)
    binary = binarize(gray)

    if not args.seg_dir:
        out = Path(args.image).with_suffix(".bin.png")
        cv2.imwrite(str(out), binary)
        print(out)
        return

    seg_dir = Path(args.seg_dir)
    seg_dir.mkdir(parents=True, exist_ok=True)
    runs = segment(binary, args.chars)
    h = binary.shape[0]
    for i, (s, e) in enumerate(runs):
        crop = binary[int(h * 0.05):int(h * 0.95), s:e]
        crop = cv2.resize(crop, (28, 28), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(seg_dir / f"{i:02d}.png"), crop)
    print(f"[+] {len(runs)} karakter disimpan di {seg_dir}")


if __name__ == "__main__":
    main()
