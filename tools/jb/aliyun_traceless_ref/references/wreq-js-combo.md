# Kombo Camoufox + wreq-js

Camoufox melakukan bagian yang butuh runtime browser; wreq-js melakukan bagian
yang butuh transport dengan fingerprint TLS browser. Dipakai bila target juga
menolak transport Camoufox, atau bila replay perlu dikendalikan byte-per-byte.

## Batas kemampuan wreq-js (baca dulu)

wreq-js adalah HTTP client Node.js di atas Rust `wreq` + BoringSSL. Ia menyamakan
**JA3/JA4, HTTP/2 SETTINGS, dan urutan pseudo-header**, lalu berhenti di situ.
Ia **tidak** menjalankan JavaScript, **tidak** mengambil screenshot, **tidak**
membangun DOM. Untuk captcha yang menilai telemetri runtime, ia tidak bisa
berdiri sendiri — ia hanya menggantikan lapisan transport.

## Pembagian peran

| Bagian | Dikerjakan oleh |
|---|---|
| Muat halaman, jalankan bundle `cx.*.js`, bangun widget | Camoufox |
| Kumpulkan WebGL / canvas / font / audio / gerak mouse | Camoufox |
| Kirim `InitCaptchaV3` dengan JA3/JA4 Chrome yang rapi | wreq-js (`chrome_149`) |
| Replay request yang butuh header/urutan persis | wreq-js |

## API wreq-js yang dipakai

```js
import { fetch, createSession } from 'wreq-js';

// Satu request
const res = await fetch(url, { browser: 'chrome_149', os: 'windows' });

// Sesi: cookie jar + connection pool + TLS session cache tetap hidup
const session = await createSession({ browser: 'chrome_149', os: 'windows' });
await session.fetch(url, { method: 'POST', body: new URLSearchParams({ ... }) });
```

Profil yang tersedia antara lain `chrome_149`, `chrome_133`, `firefox_135`.
`http1: true` memaksa HTTP/1.1 — **wajib** untuk target yang menolak h2
(lihat `references/http2-pitfall.md`).

## Jebakan yang sudah terbukti

1. **Node tidak punya DOM.** `screen`, `navigator`, `document` tidak ada.
   Fingerprint harus diambil dari browser (Camoufox) lalu dibaca wreq-js dari
   file — jangan dikarang di Node.
2. **`InitCaptchaV3` ditandatangani HMAC-SHA1.** Field `SignatureNonce` dan
   `Signature` wajib ada; tanpa itu server membalas `404 Whitelabel Error Page`.
   Kuncinya di-obfuscate di bundle SDK (`KEY_SECRET: ye(nr[re(390)+"EC"])`) dan
   tidak tersedia sebagai literal maupun lewat scan global runtime. Artinya:
   request ini tidak bisa direkonstruksi dari luar — ia hanya bisa dipantau
   saat SDK sendiri yang mengirim (hook XHR di dalam page).
3. **Jangan mengarang `DeviceData`.** Server membandingkannya dengan sinyal
   transport. Blob base64 buatan sendiri akan ditolak walaupun bentuknya benar.
   Yang benar: ambil dari SDK yang berjalan di browser sungguhan.

## Kesimpulan praktis

Kalau Camoufox sudah lolos sendirian, kombo ini tidak perlu. Simpan sebagai
cadangan untuk kasus transport diblokir, bukan sebagai jalur utama.
