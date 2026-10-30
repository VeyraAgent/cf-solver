# Kenapa emulator DOM gagal di Aliyun TRACELESS

Diukur pada `sso.openxlab.org.cn` dengan **Obscura** (headless engine Rust + V8).
Semua temuan di bawah punya bukti di direktori kerja `/root/openxlab-captcha/evidence/`.

## Ringkas: kegagalan terjadi sebelum penilaian

Obscura bisa navigate, submit, dan `InitCaptchaV3` balik `Success: true`. Tapi
`document.querySelectorAll('canvas').length === 0` dan tombol nyangkut di
`校验中` selamanya. Artinya bundle challenge (`cx.<NNN>.js`, ~450 KB) tidak
pernah selesai dieksekusi — jadi tidak ada widget, tidak ada telemetri, tidak
ada yang bisa dinilai.

## Enam hal yang bolong, berurutan seperti yang muncul

| # | Gejala di console | Penyebab | Tambalan |
|---|---|---|---|
| 1 | `Couldn't find a style target. This probably means that the value for the 'insert' parameter is invalid.` | Bundle memanggil `document.querySelector('base[href]')` untuk target style; Obscura tidak punya `<base>` | sisipkan `<base href>` via MutationObserver |
| 2 | `moveBy is not defined` | `window.moveBy/moveTo/resizeBy/resizeTo` tidak ada | stub no-op |
| 3 | `Location is not defined` | global `Location` tidak ada, SDK menghitung `.prototype` | stub constructor |
| 4 | `TypeError: Cannot read properties of undefined (reading 'prototype')` | core-js / SDK menyentuh prototype dari global yang tidak ada (`TouchEvent`, `DragEvent`, `PageTransitionEvent`, `MediaSource`, `SourceBuffer`, …) | daftarkan stub untuk ~60 nama |
| 5 | `getContext('webgl') === null` | WebGL sengaja di-stub. Source `crates/obscura-js/js/bootstrap.js:14999` — "Context creation is allowed to fail … until the renderer has a real WebGL backend" | implementasi objek WebGL1/2 sendiri + API `getSupportedExtensions` |
| 6 | `measureText` = `len * 6` flat | `bootstrap.js:14728` tidak pernah query font DB, jadi semua font memberi lebar identik → deteksi font kosong | timpa `measureText` per instance canvas |

## Setelah semua ditambal: fingerprint wajar, captcha tetap gagal

Hasil `evidence/patch-verify.json`:

```
webgl        : WebKit / ANGLE (NVIDIA GeForce GTX 1660 D3D11), 35 ext
fontCount    : 8  (Arial, Verdana, SimSun, Noto Sans, …)
measureText  : Arial 80.6 vs Courier 74.2   (beda, tidak flat lagi)
deviceMemory : 8   (sebelumnya 0.25)
timeZone     : Asia/Shanghai
language     : zh-CN
```

Semua red flag yang terukur sudah tertutup. Tetap `canvases: 0`, tetap
`校验中`. Jadi masalahnya bukan fingerprint — **runtime tidak sanggup
menjalankan bundle-nya**, dan itu bukan sesuatu yang bisa ditambal dari sisi
halaman.

## Kenapa bukan `--stealth`

Build stealth menggantung pada host target: navigasinya timeout total
(`navigation exceeded 30000ms deadline`) sementara build non-stealth sampai.
Penyebab sebenarnya ada di `references/http2-pitfall.md`, bukan di TLS
fingerprint — jangan salah simpulkan.

## Kesimpulan

Emulator DOM hanya layak untuk captcha yang bisa dijawab secara eksplisit
(puzzle, slider, grid). Untuk `TRACELESS` yang menilai koherensi runtime secara
menyeluruh, pakai browser asli. Semua investasi menambal emulator tidak
memindahkan titik kegagalan — hanya memindahkannya lebih dalam.
