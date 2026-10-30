# Perluasan & alternatif tanpa TensorFlow

## 1. Jalur ringan: HOG + SVM (scikit-learn)
Bila TF tidak tersedia, ganti `train_ocr.py` dengan fitur HOG:

```python
from skimage.feature import hog
from sklearn.svm import LinearSVC
import joblib, cv2, numpy as np

def feats(img):  # img 28x28 biner
    return hog(img, orientations=9, pixels_per_cell=(4, 4),
               cells_per_block=(2, 2), block_norm="L2-Hys")

# X = np.array([feats(cv2.resize(cv2.imread(p, 0), (28, 28))) ... ])
# y = [...]
clf = LinearSVC(max_iter=5000).fit(X, y)
joblib.dump(clf, "model/hog_svm.joblib")
```
Akurasi tipikal ~85–92% pada captcha 4–5 karakter sederhana; di bawah CNN.

## 2. Captcha bergaya tulisan tangan / karakter menyatu → CRNN+CTC
- Label per gambar (bukan per karakter), loss `ctc_loss`.
- Arsitektur: Conv → BiLSTM(256) → Dense(vocab) → CTC decode.
- Cocok untuk [distort, connect, rotate] per-karakter (Animoji/GIF pecah dulu jadi frame: `cv2.VideoCapture` + ambil frame stabil).

## 3. Captcha audio
Sebagian widget menyediakan audio MP3. Jalur umum: unduh → VAD potong kata →
ASR lokal (whisper.cpp / vosk offline). Butuh decoder khusus untuk digit tunggal.

## 4. Augmentasi agar akurasi naik (≥90%)
- rotasi ±10°, shear ±5°, gaussian noise σ=8, blur 3x3, erode/dilate 1px
- pertahankan label; 3–4× lipat dataset asli
- split stratifikasi per kelas, cek confusion matrix → kelas mirip (O/0, I/1) bisa digabung jika font captcha memang ambigu

## 5. Debugging segmentasi gagal
- Kolom kosong terlalu banyak → turunkan threshold adaptive (`blockSize` naik).
- Karakter menyatu → gunakan distance transform + watershed, atau guru `--chars` lalu bagi rata berdasarkan lebar rata-rata glyph.
- Noise spot → naikkan ambang `CC_STAT_AREA` di `preprocess.py`.
