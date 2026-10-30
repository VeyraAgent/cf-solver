#!/usr/bin/env python3
"""Selesaikan captcha gambar dengan model lokal (offline). Tanpa kredensial.

Pakai:
  python solve_local.py INPUT.png --model model/cnn.keras --chars 5
Dependencies: tensorflow (keras), opencv-python, numpy.
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from preprocess import binarize, deskew, segment  # noqa: E402

IMG = 28


def solve(path: str, model_path: str, n_chars: int = 0) -> str:
    from tensorflow import keras

    labels_file = Path(model_path).with_suffix(".labels.txt")
    if not labels_file.exists():
        sys.exit(f"[!] file label tidak ditemukan: {labels_file}")
    labels = labels_file.read_text().split()

    gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        sys.exit(f"[!] gagal membaca: {path}")
    binary = binarize(deskew(gray))
    runs = segment(binary, n_chars)
    if not runs:
        sys.exit("[!] segmentasi gagal — coba --chars atau periksa gambar")

    model = keras.models.load_model(model_path)
    h = binary.shape[0]
    out = []
    for s, e in runs:
        crop = binary[int(h * 0.05):int(h * 0.95), s:e]
        crop = cv2.resize(crop, (IMG, IMG), interpolation=cv2.INTER_AREA)
        x = (crop.astype("float32") / 255.0)[None, ..., None]
        out.append(labels[int(np.argmax(model.predict(x, verbose=0)))])
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--model", default="model/cnn.keras")
    ap.add_argument("--chars", type=int, default=0)
    args = ap.parse_args()
    print(solve(args.image, args.model, args.chars))


if __name__ == "__main__":
    main()
