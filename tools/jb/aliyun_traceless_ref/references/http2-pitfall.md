# Pitfall HTTP/2: timeout yang menyerupai blokir fingerprint

## Gejala

Berbagai bentuk, semuanya menyesatkan:

- `navigation exceeded 30000ms deadline`
- `error decoding response body`
- `error sending request for url … unexpected EOF`
- handshake Python `ssl` menggantung lalu `TimeoutError`

Semuanya terlihat seperti "IP diblokir" atau "JA3 ditolak". Biasanya bukan.

## Diagnosa 3 baris

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://TARGET/login            # ALPN h2
curl -s --http1.1 -o /dev/null -w "%{http_code}\n" https://TARGET/login   # dipaksa h1.1
```

Kalau baris pertama timeout dan baris kedua `200`, penyebabnya HTTP/2.

## Matriks uji (openxlab, 2026-10-28)

| Klien | Protokol | Hasil |
|---|---|---|
| curl default | h2 | timeout |
| curl `--http1.1` | h1.1 | **200** |
| Python `ssl` (ALPN h2) | h2 | handshake timeout |
| Obscura `fetch` | h2 | timeout |
| wreq-js `chrome_149` | h2 | timeout (`unexpected EOF`) |
| wreq-js `chrome_149` + `http1:true` | h1.1 | **200** (~5.3 s) |
| wreq-js `chrome_133` | h1.1 | **200** (~2.4 s) |
| wreq-js `firefox_135` | h1.1 | **200** (~1.5 s) |

Server berada di balik `istio-envoy` dan tampaknya hanya menerima h2 dari klien
yang dikenal. Klien lain menggantung sampai deadline.

## Konsekuensi praktis

- Jangan buang waktu mengatur ulang TLS profile / cipher order. Masalahnya di
  pemilihan protokol, bukan di JA3.
- Kerusakan yang paling menipu adalah `error decoding response body` — terlihat
  seperti kompresi rusak, padahal stream h2 diputus di tengah body.
- Camoufox (Firefox) tidak terpengaruh. Untuk wreq-js, set `http1: true`
  pada profil `chrome_149`, atau pakai profil yang memang jatuh ke h1.1.
- Saat memakai engine sendiri dengan kontrol ALPN (rustls, wreq, hyper), sediakan
  cara memaksa `http/1.1` di ALPN list — tanpa itu target seperti ini tidak
  akan pernah bisa dinavigasi.
