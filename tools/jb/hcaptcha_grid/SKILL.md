---
name: hcaptcha-grid-solver
description: Use when solving hCaptcha image-grid challenges with a real anti-detect browser plus a vision model. Covers the Camoufox + wreq-js recipe, the WebGL trap that silently blanks every tile, iframe click coordinate pitfalls, the 2-page challenge rule, and proxy health-check rotation. Includes a working Node solver.
version: 1.0.0
license: MIT
metadata:
  synapse:
    tags: [captcha, hcaptcha, camoufox, playwright, vision, anti-detect, wreq-js, red-team]
    related_skills: [aliyun-nocaptcha-traceless, captcha-solving]
---

## When to Use

Target memakai **hCaptcha** dengan challenge gambar (grid 3x3 / 4x4, "Tap on
items used in a gym", "Click on things you can build with the item shown", dst).

Ciri di DOM (di dalam iframe `newassets.hcaptcha.com`):

- `iframe[src*="hcaptcha"]` di halaman induk
- `textarea[name="h-captcha-response"]` ← token `P1_...` mendarat di sini
- frame `#frame=checkbox` berisi `#checkbox` ("I am human")
- frame `#frame=challenge` berisi `.task[role="button"]` (tile) + `#verify`

Kalau target memakai **Aliyun NoCaptcha TRACELESS** (checkbox `确认您不是机器人`
tanpa puzzle), itu skill lain — lihat `aliyun-nocaptcha-traceless`.

## Aturan pertama: token adalah satu-satunya bukti solve

Solve = string `P1_...` masuk ke `textarea[name="h-captcha-response"]`.
Screenshot yang "kelihatan benar" bukan bukti. Kalau tidak ada token, belum solve.

Token **sekali pakai**, umur ~120 detik, terikat ke `sitekey` + `host`.

## Jangan buang waktu pada engine emulator

Jangan pakai Obscura, jsdom, atau engine headless buatan sendiri untuk hCaptcha.
Dua kegagalan yang terbukti di lapangan:

1. `Runtime.evaluate` di Obscura mengembalikan `undefined` untuk ekspresi apa pun
   yang mengandung `;`, IIFE, atau `setTimeout` — jadi telemetri dan timer mati.
2. Rendering challenge butuh WebGL; engine tanpa GL menghasilkan tile kosong.

Pakai browser asli: **Camoufox** (Firefox anti-detect) atau Chromium asli.
Detail di `references/why-not-emulators.md`.

## Pitfall #1 — WebGL mati = semua tile kosong

Gejala paling menipu. Semua tile tampak placeholder abu-abu, klik menggantung
30 detik, dan vision model menjawab ngawur karena gambarnya kosong. Di console:

```
Failed to create WebGL context: WebGL creation failed:
* tryNativeGL ()
* Exhausted GL driver options. (FEATURE_FAILURE_WEBGL_EXHAUSTED_DRIVERS)
```

Fix wajib (Camoufox / Firefox):

```js
firefox_user_prefs: {
  'webgl.force-enabled': true,
  'webgl.disabled': false,
  'webgl.enable-webgl2': true,
  'gfx.webrender.software': true,
  'layers.acceleration.disabled': true,
  'gfx.canvas.accelerated': false,
},
args: ['-allow-software-gl', '--use-gl=swiftshader'],
```

Chromium: `--use-gl=swiftshader --enable-unsafe-swiftshader`.

Verifikasi cepat sebelum debug lebih jauh — buka frame challenge dan cek:

```js
[...document.querySelectorAll('.task[role="button"]')].map(t => ({
  bg: getComputedStyle(t.querySelector('div')).backgroundImage.slice(0, 40),
  loading: !!t.querySelector('.loading-indicator'),
}))
```

Kalau semua kosong dan `.loading-indicator` masih ada, WebGL/asset yang salah —
bukan vision model-nya.

## Pitfall #2 — boundingBox() di iframe bukan koordinat viewport

`elementHandle.boundingBox()` di dalam iframe mengembalikan koordinat **relatif
frame**. Kalau dipakai dengan `page.mouse.move(b.x, b.y)`, klik mendarat di tempat
kosong dan `aria-pressed` tetap `0` — padahal log bilang "picked 4 tiles".

Selalu klik lewat handle (Playwright menghitung offset iframe sendiri):

```js
for (const i of picks) await handles[i].click({ delay: 35 });
```

State "terpilih" ada di **parent**, bukan di `.task-image`:

```js
document.querySelectorAll('.task[aria-pressed="true"]').length
```

## Pitfall #3 — `.display-error` selalu ada di DOM

Jangan baca `document.body.innerText` untuk mencari error. hCaptcha menaruh
`<div class="display-error" aria-hidden="true" style="opacity: 0">` sejak awal.
Kalau asal grep "try again" dari `innerText`, setiap challenge terlihat gagal
padahal tidak ada error sama sekali.

Baca style-nya:

```js
const box = document.querySelector('.display-error');
const visible = box && getComputedStyle(box).opacity !== '0'
                   && getComputedStyle(box).display !== 'none';
```

## Pitfall #4 — satu challenge bisa minta 2+ page

`aria-label="Skip Challenge, page 1 of 2"` bukan hiasan. Setelah verify page 1
lolos, prompt dan tile **diganti** di frame yang sama, dan token baru keluar
setelah page 2 dijawab. Kalau solver cuma menangani satu page, token tidak pernah
muncul.

Loop di dalam frame yang sama sampai token ada (lihat `solveChallengeLoop` di
script).

## Recipe

### 1. Dependensi

```bash
npm install camoufox-js playwright-core wreq-js
```

Camoufox mengunduh binary Firefox (~660 MB) saat pertama dipanggil.

### 2. Reconfigure target

Edit konstanta di `scripts/hcaptcha_solver.mjs`:

- `TARGET_URL` — halaman yang memuat widget
- `SITEKEY_FALLBACK` — sitekey target (kosongkan untuk auto-parse)

### 3. Environment (semua opsional kecuali vision)

```bash
export VL_URL="https://your-vlm.example/v1/chat/completions"
export VL_KEY="<token>"
export VL_MODEL="your-vision-model"
export PROXY_LIST="./proxies_ok.txt"   # host:port:user:pass per baris
```

Tidak ada kredensial yang di-hardcode. Tanpa `VL_URL`, solver jatuh ke mode
heuristik dan hampir pasti gagal — vision model wajib.

### 4. Health-check proxy dulu (jangan rotate buta)

Rotate tanpa health-check menghasilkan `NS_ERROR_NET_TIMEOUT` acak. Saring pool
lebih dulu — di pengukuran, 43 dari 60 proxy hidup:

```bash
while IFS=: read -r H P U W; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 \
    -x "http://$U:$W@$H:$P" https://api.ipify.org)
  [ "$code" = "200" ] && echo "$H:$P:$U:$W" >> proxies_ok.txt
done < proxies_raw.txt
```

### 5. Jalankan

```bash
node scripts/hcaptcha_solver.mjs
```

Exit 0 = token didapat.

## Arsitektur solver

Tiga lapis, masing-masing punya satu tugas:

| Lapis | Tool | Tugas |
|---|---|---|
| Transport | `wreq-js` | recon halaman + `siteverify` dengan TLS fingerprint Chrome |
| Browser | `Camoufox` | render widget, telemetri, klik mouse |
| Vision | VLM apa pun | jawab grid: 1 call per grid |

**Satu call VLM per grid**, bukan per tile. Mengirim 9–10 tile satu per satu
memakan ~47 detik per ronde; grid utuh dalam satu gambar turun ke ~10 detik.
Prompt yang dipakai ada di `references/vision-prompt.md`.

## Ekspektasi hasil — jujur

Diukur pada demo publik `nopecha.com/demo/hcaptcha` (2026-10-29), 3x batch 5 run:
**4/5, lalu 5/5, lalu 5/5**. Median 87–99 detik, maksimum 183 detik.

Angka itu bukan jaminan untuk target lain. Yang menaikkan rate:

- health-check proxy sebelum rotate (ini yang mengubah 4/5 jadi 5/5)
- jeda 12–20 dtk antar run
- `humanize: true` dan gerakan mouse sebelum submit
- viewport konsisten 1280x800 dengan `locale` cocok region IP

## Debug cepat

| Gejala | Artinya |
|---|---|
| semua tile abu-abu, `loading-indicator` ada | WebGL mati (Pitfall #1) |
| log "picked 4" tapi `aria-pressed=0` | klik pakai koordinat viewport (Pitfall #2) |
| "Please try again" di setiap challenge | kemungkinan elemen hidden (Pitfall #3) |
| verify sukses tapi token kosong | challenge minta page berikutnya (Pitfall #4) |
| `NS_ERROR_NET_TIMEOUT` acak | proxy mati di pool, belum di-health-check |
| VLM jawab `[0..8]` semua | prompt kurang tegas; lihat `vision-prompt.md` |

Selalu simpan artefak: screenshot grid, log `[vlm] picks`, isi
`tokens.log`. Solve tanpa token bukan solve.
