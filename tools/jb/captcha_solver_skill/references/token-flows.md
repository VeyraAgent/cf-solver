# Alur token captcha interaktif (untuk pengujian terotorisasi)

Paket ini tidak berisi kredensial. Semua nilai (`sitekey`, `pageurl`, API key)
diambil dari CLI flag atau env: `CAPTCHA_SITEKEY`, `CAPTCHA_PAGE_URL`, `CAPTCHA_API_KEY`.

## Pola umum (semua tipe)

```
[Widget] --render--> sitekey (terlihat di DOM/JS)
   |
   v
solve_api.py --type <tipe> --sitekey <K> --page-url <URL>
   |
   v  (submit -> poll -> token)
[Target form] <- tempel token di parameter responsnya
```

1. **Ambil sitekey** dari DOM:
   - reCAPTCHA: `document.querySelector('[data-sitekey]')?.dataset.sitekey` atau regex `6L[0-9A-Za-z_-]{38}` pada HTML.
   - hCaptcha: `[data-sitekey]` atau regex `10000000-ffff-ffff-ffff-000000000001` (sitekey test publik).
   - Turnstile: `div[data-sitekey]` atau param `sitekey=` di script loader.
2. **pageurl** = URL halaman persis tempat widget dirender (ikut query string).
3. Submit + poll → token (berlaku ±120 detik, sekali pakai).

## Parameter respons yang menerima token

| Tipe        | Nama parameter                    | Header alternatif              |
|-------------|-----------------------------------|--------------------------------|
| reCAPTCHA v2/v3 | `g-recaptcha-response`        | `X-Recaptcha-Token`            |
| hCaptcha    | `h-captcha-response`              | `X-Hcaptcha-Token`             |
| Turnstile   | `cf-turnstile-response`           | `CF-Chl-Resp-Token`            |

reCAPTCHA v3 tambahan: `g-recaptcha-response` + field `action` & `page` yang
harus sama dengan yang dikirim saat solve (`--action`, halaman identik), serta
skor minimal sesuai threshold server.

## Tempel ke request (contoh curl)

```bash
TOKEN=$(python scripts/solve_api.py --type recaptcha_v2 \
        --sitekey "$CAPTCHA_SITEKEY" --page-url "$CAPTCHA_PAGE_URL")

curl -sS -X POST "$TARGET/submit" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "g-recaptcha-response=$TOKEN" \
  --data-urlencode "field1=VALUE"
```

## Endpoint SPA / API headless
Widget bisa dirender via API (`?render=sitekey` pada reCAPTCHA v3, atau
callback Turnstile). Token dari solver tetap dipakai apa adanya — tidak perlu
menjalankan browser sama sekunya.

## Batasan
- Hanya untuk target yang Anda miliki / diuji dengan izin tertulis.
- Token sekali pakai & kedaluwarsa cepat; jangan di-cache lintas sesi.
- Jangan jalankan banyak worker paralel dengan satu API key (kenali
  `ERROR_BAD_DUPLICATES` → tunggu, jangan retry cepat).
