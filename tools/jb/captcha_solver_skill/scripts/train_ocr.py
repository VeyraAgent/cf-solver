#!/usr/bin/env python3
"""Latih CNN klasifikator karakter captcha. Tanpa kredensial.

Struktur dataset:
  dataset/
    0/*.png 1/*.png ... 9/*.png A/*.png ... Z/*.png   (28x28 grayscale idealnya)

Pakai:
  python train_ocr.py --data dataset --out model/cnn.keras --epochs 25
Dependencies: tensorflow (keras), numpy, opencv-python.
Fallback tanpa TF: lihat references/extend.md (HOG + SVM).
"""
import argparse
import random
from pathlib import Path

import cv2
import numpy as np

IMG = 28
EXTS = {".png", ".jpg", ".jpeg", ".bmp"}


def load_dataset(root: Path):
    labels = sorted(p.name for p in root.iterdir() if p.is_dir())
    if not labels:
        raise SystemExit(f"[!] tidak ada folder kelas di {root}")
    x, y = [], []
    for idx, lab in enumerate(labels):
        for f in (root / lab).rglob("*"):
            if f.suffix.lower() not in EXTS:
                continue
            img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            img = cv2.resize(img, (IMG, IMG), interpolation=cv2.INTER_AREA)
            x.append(img.astype("float32") / 255.0)
            y.append(idx)
    return labels, np.array(x)[..., None], np.array(y)


def augment(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), random.uniform(-10, 10), 1.0)
    img = cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
    img = np.clip(img + np.random.normal(0, 0.04, img.shape), 0, 1)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="model/cnn.keras")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()

    try:
        from tensorflow import keras
    except ImportError:
        raise SystemExit("[!] tensorflow tidak terpasang — lihat references/extend.md untuk jalur HOG+SVM")

    labels, x, y = load_dataset(Path(args.data))
    print(f"[+] {len(x)} sampel, {len(labels)} kelas: {''.join(labels)}")

    # augmentasi ringan di memori
    x = np.concatenate([x] + [np.array([augment(im) for im in x]) for _ in range(2)])

    n = len(x)
    idx = np.random.permutation(n)
    x, y = x[idx], y[idx]
    split = int(n * 0.9)
    (xtr, ytr), (xva, yva) = (x[:split], y[:split]), (x[split:], y[split:])

    model = keras.Sequential([
        keras.layers.Input((IMG, IMG, 1)),
        keras.layers.Conv2D(32, 3, activation="relu", padding="same"),
        keras.layers.MaxPooling2D(),
        keras.layers.Conv2D(64, 3, activation="relu", padding="same"),
        keras.layers.MaxPooling2D(),
        keras.layers.Flatten(),
        keras.layers.Dropout(0.3),
        keras.layers.Dense(len(labels), activation="softmax"),
    ])
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    model.fit(xtr, ytr, validation_data=(xva, yva),
              epochs=args.epochs, batch_size=args.batch)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    model.save(args.out)
    meta = Path(args.out).with_suffix(".labels.txt")
    meta.write_text("\n".join(labels))
    print(f"[+] model: {args.out} | label: {meta}")


if __name__ == "__main__":
    main()
