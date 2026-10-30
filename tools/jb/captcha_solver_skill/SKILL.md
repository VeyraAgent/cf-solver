---
name: captcha-solver
description: "Paket solver captcha end-to-end tanpa kredensial di dalam paket — preprocessing gambar, OCR CNN lokal, klien solver API berbasis env var, dan catatan alur reCAPTCHA/hCaptcha/Turnstile. Pakai saat ada tugas selesaikan captcha, bypass captcha untuk pengujian, atau OCR captcha."
---

# captcha-solver

## Kapan dipakai
- Tugas meng-OCR captcha gambar (4–6 karakter, distorsi, noise).
- Butuh klien untuk layanan solver eksternal (2captcha-compatible) **tanpa menaruh API key di kode/riwayat**.
- Butuh pemetaan alur token (reCAPTCHA v2/v3, hCaptcha, Cloudflare Turnstile) untuk pengujian terotorisasi.

## Aturan kredensial (WAJIB)
- Semua rahasia dibaca dari env: `CAPTCHA_API_KEY`, `CAPTCHA_API_URL`, `CAPTCHA_PAGE_URL`, `CAPTCHA_SITEKEY`.
- Paket ini tidak pernah menyimpan, mencetak, atau mengirim kredensial. `solve_api.py` hanya membaca env dan tidak pernah meng-echo nilai key.
- Sebelum kirim hasil kerja ke user, strip baris env yang berisi nilai rahasia dari log/transkrip.

## Langkah kerja

### A. Captcha gambar → solusi lokal (offline, disarankan)
1. Siapkan dataset: `dataset/<KARAKTER>/*.png` (mis. `dataset/A/*.png`).
2. Pra-proses & segmentasi: `python scripts/preprocess.py INPUT.png --seg-dir out/seg`
3. Latih model: `python scripts/train_ocr.py --data dataset --out model/cnn.keras`
4. Selesaikan: `python scripts/solve_local.py INPUT.png --model model/cnn.keras`
5. Akurasi <90%? Naikkan jumlah sampel per kelas (≥500), augmentasi (rotasi ±10°, gaussian noise), tambah deskew.

### B. Captcha gambar → layanan eksternal (fallback)
1. `export CAPTCHA_API_KEY=xxx` (diisi operator, bukan di dalam paket)
2. `python scripts/solve_api.py --image INPUT.png`
3. Default timeout 120s, poll tiap 5s. Jangan poll agresif — kenali `ERROR_BAD_DUPLICATES` / `ERROR_NO_SLOT_AVAILABLE` dan backoff eksponensial.

### C. Captcha interaktif (reCAPTCHA / hCaptcha / Turnstile)
Baca `references/token-flows.md`. Ringkas: ambil `sitekey` + `pageurl`, kirim ke endpoint solver dengan tipe yang sesuai, polling token, lalu tempel token ke parameter form target (`g-recaptcha-response` / `h-captcha-response` / `cf-turnstile-response`) hanya pada target yang Anda uji sendiri/berizin.

## Jebakan
- Jangan pernah menulis API key ke file di dalam direktori skill ini.
- `solve_api.py` butuh internet; untuk sandbox offline gunakan jalur A.
- Captcha bergerak/animoji (GIF, rotasi per-karakter) → segmentasi gagal; pakai model sequence (CRNN/CTC) — lihat `references/extend.md`.
- Rate limit: 1 akun/key = 1 antrian; jangan fork banyak proses dengan key yang sama.

## Verifikasi
- Jalur A: `python scripts/solve_local.py samples/demo.png --model model/cnn.keras` → output string sepanjang panjang captcha + exit 0.
- Jalur B: tanpa env → error jelas "CAPTCHA_API_KEY belum di-set" (bukan stacktrace aneh).
- Cek kebocoran: `grep -ri "api_key\s*=\s*['\"]" scripts/ references/ SKILL.md` → nol hasil.
