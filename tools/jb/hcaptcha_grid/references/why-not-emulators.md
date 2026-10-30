# Kenapa emulator gagal untuk hCaptcha

Catatan dari percobaan langsung, bukan teori. Dua engine diuji dan dua-duanya
gagal dengan mode kegagalan yang berbeda.

## Obscura (headless browser Rust)

Obscura tampak menjanjikan: ringan, CDP-compatible, cepat start. Masalahnya ada
di lapisan evaluasi JavaScript.

### `Runtime.evaluate` mengembalikan `undefined` untuk apa pun yang berisi `;`

Terukur, satu sesi CDP, target `about:blank`:

| Ekspresi | Hasil |
|---|---|
| `1+1` | `2` ✅ |
| `"hello"` | `"hello"` ✅ |
| `Math.max(3,9)` | `9` ✅ |
| `console.log("x"); "x"` | `undefined` ❌ |
| `(()=>{ globalThis.__x = 42; return 42; })()` | `undefined` ❌ |
| `setTimeout(()=>{...}, 100)` sebagai statement | `undefined` ❌ |

Konsekuensinya:

- menulis state ke `window.*` tidak pernah berhasil
- timer tidak pernah terpasang, jadi polling tidak mungkin
- promise tidak pernah settle
- seluruh telemetri hCaptcha (yang bergantung pada timer + state) mati

Bahkan `Runtime.awaitPromise` dengan `awaitPromise: true` mengembalikan
`undefined`. Tidak ada workaround: ini bukan soal selector atau timing.

### Kerusakan frame tree

Pada halaman dengan iframe bersarang, `Page.getFrameTree` hanya mengembalikan
frame root, sementara DOM di halaman induk menunjukkan iframe hCaptcha ada.
`contentFrame()` mengembalikan `null`, dan `page.$()` menggantung sampai
`Runtime.callFunctionOn timed out`.

### Klik menggantung

`elementHandle.click()` menunggu "element is visible, enabled and stable" lalu
tidak pernah selesai — 30 detik timeout, berulang. Ini efek samping dari
evaluasi JS yang rusak.

## hsw.js di Node `vm`

Pendekatan "jalankan proof-of-work di Node saja, tanpa browser" juga gagal.

`checksiteconfig` mengembalikan `c.type = "hsw"` dan `c.req` berupa JWT. Field
`l` di payload JWT menunjuk ke path bundle (relatif, perlu di-prefix
`https://newassets.hcaptcha.com`). Bundle `hsw.js` ~1.5 MB, berisi WASM.

Dimuat ke `vm.createContext` dengan sandbox lengkap (`navigator`, `screen`,
`crypto`, `performance`, `atob`/`btoa`, dst):

```
loaded, hsw type = function
wasm://wasm/003efeb6:1
RuntimeError: unreachable
    at wasm://wasm/003efeb6:wasm-function[606]:0x76917
    ...
    at Rj (evalmachine.<anonymous>:2:1418039)
```

WASM trap `unreachable` — bundle mendeteksi environment yang tidak sesuai dan
sengaja abort. Menambah stub `navigator`/`screen` tidak menolong; bundle memeriksa
hal-hal yang hanya konsisten di browser asli.

## Kesimpulan

Untuk hCaptcha, pakai browser asli dengan WebGL. Transport-level client
(wreq-js, curl-impersonate, dsb.) bagus untuk recon dan verifikasi token, tapi
tidak bisa menggantikan browser karena:

1. challenge butuh eksekusi JS + WebGL untuk merender tile
2. server menilai telemetri runtime (mouse, timing, device signals)
3. proof-of-work bundle `hsw.js` melakukan anti-tamper terhadap environment

Pembagian tugas yang bekerja: **wreq-js** untuk HTTP yang butuh fingerprint TLS
Chrome, **Camoufox** untuk semua yang butuh runtime browser, **VLM** untuk
membaca grid.
