---
name: aliyun-nocaptcha-traceless
description: Use when solving Aliyun NoCaptcha (TRACELESS mode) — the checkbox "确认您不是机器人" that never shows a puzzle. Covers why emulated browsers fail, the Camoufox recipe that works, and the HTTP/2 pitfall that silently kills navigation.
version: 1.0.0
license: MIT
metadata:
  synapse:
    tags: [captcha, aliyun, nocaptcha, traceless, camoufox, anti-detect, red-team]
    related_skills: [alcaptcha-adslab, captcha-solving]
---

## When to Use

Target memakai **Aliyun NoCaptcha** dan widget-nya cuma berupa checkbox
`确认您不是机器人` — tanpa slider, tanpa puzzle gambar, tanpa grid. Ciri di DOM:

- `#aliyunCaptcha-window-popup`, `#aliyunCaptcha-checkbox-body`, `#aliyunCaptcha-checkbox-text`
- `window.initAliyunCaptcha`, `window.AliyunCaptchaConfig`
- XHR ke `https://<prefix>.captcha-open.aliyuncs.com/` dengan `Action=InitCaptchaV3`
- Respons init berisi `"CaptchaType": "TRACELESS"` ← **penentu segalanya**

Kalau `CaptchaType` = `SLIDING` / `CHECK_BOX`, ini bukan skill ini (ada puzzle
visual yang bisa di-vision). `TRACELESS` berarti tidak ada apa pun untuk
di-"jawab" secara visual: skor datang 100% dari telemetri device + perilaku.

## Aturan pertama: jangan buang waktu bikin solver visual

Pada saat popup muncul dan checkbox terlihat, sudah tidak ada captcha image.
Yang diverifikasi adalah fingerprint runtime. Urutan kejadian sebenarnya:

1. Submit form → SDK kirim `InitCaptchaV3` dengan blob `DeviceData` (base64)
2. Server balas `CaptchaType: TRACELESS` + `StaticPath` (bundle challenge)
3. Bundle `cx.<NNN>.<hash>.js` (~450 KB) di-inject → bangun widget + telemetri
4. Klik checkbox → event telemetri dikirim → server putuskan lolos/gagal
5. Kalau lolos: checkbox jadi `验证通过!`, popup `window-hidden`, session token kembali

Gagal (F001) hampir selalu terjadi di langkah 1–3, **sebelum** klik pernah dinilai.

## Resep yang bekerja: Camoufox

Jangan pakai emulator DOM (Obscura, jsdom, custom Rust engine) — detail kenapa
ada di `references/why-emulators-fail.md`. Pakai browser asli dengan anti-detect.

### 1. Setup binary + model (wajib, sekali per host)

```bash
pip install "camoufox[geoip]"

# Binary TIDAK lewat `camoufox fetch` bila release-assets.githubusercontent.com diblok.
# Ambil URL asset dari API (api.github.com biasanya tetap reachable):
curl -s "https://api.github.com/repos/daijro/camoufox/releases/latest" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['tag_name']);[print(a['name'],a.get('digest')) for a in d['assets'] if 'lin.x86_64' in a['name']]"

# Download lewat github.com/.../releases/download (bukan release-assets), lalu verifikasi sha256:
curl -sL -o cf.zip "https://github.com/daijro/camoufox/releases/download/<TAG>/camoufox-<TAG>-lin.x86_64.zip"
echo "<sha256>  cf.zip" | sha256sum -c -
mkdir -p /root/camoufox-bin && unzip -q cf.zip -d /root/camoufox-bin && chmod -R 755 /root/camoufox-bin

# Model fingerprint (fpgen, 1.5 MB) — URL-nya juga di github.com, bukan release-assets:
curl -sL -o model.zip "https://github.com/scrapfly/fingerprint-generator/releases/download/model-2/2026/model-release.zip"
echo "6530b8322cdaa4ec042921c8d9a0369a0e6e0269ba636c01a7203e4a2f109936  model.zip" | sha256sum -c -
unzip -q model.zip -d "$(python3 -c 'from camoufox.fpgen_model import fpgen_data_dir;print(fpgen_data_dir())')"
```

**PITFALL:** library menghapus `/root/.cache/camoufox` setiap import bila
`COMPAT_FLAG` tidak ada (`camoufox_path()` → `shutil.rmtree`). Karena itu binary
dipasang di luar cache dan selalu dijalankan dengan:

```bash
export CAMOUFOX_EXECUTABLE_PATH=/root/camoufox-bin/camoufox-bin
```

### 2. Jalankan solver

`scripts/aliyun_traceless_solver.py` — isi `TARGET_URL`, `USER_FIELD`,
`PASS_FIELD`, kredensial dummy, lalu:

```bash
CAMOUFOX_EXECUTABLE_PATH=/root/camoufox-bin/camoufox-bin \
  python3 aliyun_traceless_solver.py --url https://TARGET/login --runs 1
```

Exit 0 = `验证通过!` terdeteksi. Exit 1 = gagal (lihat `final_chk` di JSON).

Opsi penting:
- `geoip=False` — MMDB diunduh dari release-assets (diblok); tidak diperlukan untuk captcha
- `humanize=True` — **wajib**, ini yang memberi gerakan mouse manusiawi
- `locale=["zh-CN","en-US"]`, `window=(1920,1080)` — selaras dengan target CN

### 3. Klik checkbox dengan mouse asli

Pakai `page.mouse.move(...)` bertahap lalu `down/up`. `element.click()` lewat JS
sering tidak menghasilkan telemetri yang dinilai. Lihat `human_click()` di script.

## Ekspektasi hasil — jujur

Diukur pada `sso.openxlab.org.cn` (2026-10-28): **4/5 run sukses (80%)**,
masing-masing 34–39 dtk. Run yang gagal berhenti di `确认您不是机器人`
(challenge direject sebelum sempat dinilai), dan itu run dengan durasi terpanjang
(45.9 dtk) — indikasi throttle, bukan kesalahan klik.

**Jangan klaim deterministik.** Untuk menaikkan rate:
- beri jeda 30–60 dtk antar run (hindari pola burst dari IP yang sama)
- rotasi IP/proxy per run
- warm-up: gerakkan mouse 1–2 dtk sebelum submit

## Pitfall HTTP/2 — paling sering bikin bingung

Server di balik `istio-envoy` (mis. openxlab) bisa **menolak HTTP/2 dari client
non-whitelist**: semua yang ALPN-negosiasi `h2` menggantung sampai timeout,
sementara klien yang jatuh ke HTTP/1.1 dapat 200. Gejalanya menyesatkan —
`navigation exceeded 30000ms deadline`, `error decoding response body`,
`unexpected EOF` — dan mudah disalahartikan sebagai TLS fingerprint salah.

Cara mendiagnosa cepat:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://TARGET/login            # h2  -> timeout
curl -s --http1.1 -o /dev/null -w "%{http_code}\n" https://TARGET/login  # h1  -> 200
```

Kalau pola itu muncul, pakai klien yang bisa dipaksa h1.1. Camoufox/Firefox
sudah aman; wreq-js perlu `http1: true` untuk profil `chrome_149`.

## Kalau target memblokir Camoufox juga

Kombo yang terbukti bisa dipakai: **Camoufox untuk generasi telemetri + wreq-js
untuk transport**. Rujukan API dan fungsi yang dipakai ada di
`references/wreq-js-combo.md`. Perlu diingat wreq-js berhenti di layer transport
— ia tidak menjalankan JS, jadi ia tidak bisa menggantikan browser untuk
captcha yang butuh telemetri runtime.

## Debug cepat

| Gejala | Artinya |
|---|---|
| `btn=校验中` selamanya, `canvases: 0` | bundle `cx.*.js` gagal dieksekusi — engine tidak sanggup |
| checkbox balik ke `确认您不是机器人` | challenge direject di init, bukan salah klik |
| `#aliyunCaptcha-errorCode` terisi | baca isinya, biasanya kode F0xx |
| `getLocationInfo` → `overseas: true` | IP di luar CN; samakan `timezone`/`locale` dengan IP |
| popup `window-hidden` tapi chk bukan `验证通过!` | user menutup manual, bukan lolos |

Selalu simpan artefak: `page.screenshot()` + log XHR + `final_chk`. Solve tanpa
`验证通过!` bukan solve.
