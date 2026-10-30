# aliyun-nocaptcha-traceless v1.0.0

Skill untuk menyelesaikan **Aliyun NoCaptcha mode TRACELESS** — captcha yang
hanya berupa checkbox `确认您不是机器人` tanpa puzzle, slider, atau grid.

## Isi paket

```
aliyun-nocaptcha-traceless/
├── SKILL.md                          # instruksi utama + resep lengkap
├── scripts/
│   └── aliyun_traceless_solver.py    # solver siap pakai (CLI)
└── references/
    ├── why-emulators-fail.md         # kenapa engine emulasi gagal
    ├── http2-pitfall.md              # timeout yang menyesatkan (h2 vs h1.1)
    └── wreq-js-combo.md              # jalur cadangan: Camoufox + wreq-js
```

## Instalasi skill

**Hermes / Cybermes** — ekstrak ke direktori skills:

```bash
unzip aliyun-nocaptcha-traceless-v1.0.0.zip -d ~/Cybermes/.hermes/skills/captcha/
```

Skill akan terdeteksi otomatis (kategori `captcha`).

**Klien lain** — `SKILL.md` adalah Markdown mandiri. Taruh di mana pun agent
membaca instruksi, atau baca manual dan jalankan script-nya langsung.

## Prasyarat

```bash
pip install "camoufox[geoip]"
```

Binary Camoufox + model fingerprint perlu dipasang sekali. Langkah lengkapnya
ada di `SKILL.md` bagian **"1. Setup binary + model"**.

Ringkas: unduh dari `github.com/.../releases/download` (bukan
`release-assets.githubusercontent.com`), verifikasi sha256, ekstrak ke
`/root/camoufox-bin`, lalu:

```bash
export CAMOUFOX_EXECUTABLE_PATH=/root/camoufox-bin/camoufox-bin
```

## Pemakaian

```bash
export CAMOUFOX_EXECUTABLE_PATH=/root/camoufox-bin/camoufox-bin

python3 scripts/aliyun_traceless_solver.py \
  --url https://TARGET/login \
  --runs 1 \
  --out evidence
```

Exit code `0` = captcha lolos (`验证通过!` terdeteksi).
Exit code `1` = tidak lolos — periksa `evidence/summary.json`.

### Opsi penting

| Flag | Default | Keterangan |
|---|---|---|
| `--url` | wajib | halaman login target |
| `--user-selector` | `#normal_login_account, input[type=text]` | sesuaikan per target |
| `--pass-selector` | `input[type=password]` | sesuaikan per target |
| `--submit-selector` | `button:has-text("登录")` | sesuaikan per target |
| `--runs` | `1` | jumlah percobaan |
| `--delay` | `30` | jeda antar run (detik) — jangan dinaikkan rate-nya |
| `--warmup-ms` | `0` | gerak mouse sebelum submit |
| `--locale` | `zh-CN` | samakan dengan IP |
| `--headful` | off | tampilkan jendela untuk debug |

Kredensial default adalah dummy — hanya untuk memicu munculnya captcha.

## Ekspektasi hasil

Diukur pada target uji (2026-10-28): **4 dari 5 percobaan berhasil (80%)**,
durasi 34–39 detik per solve. Percobaan yang gagal berhenti di
`确认您不是机器人`, biasanya karena throttle dari IP yang sama setelah beberapa
percobaan beruntun.

**Ini bukan solver deterministik.** Untuk menaikkan tingkat keberhasilan:
beri jeda 30–60 detik antar percobaan, rotasi IP, atau tambahkan warm-up.

## Batasan

- Hanya untuk `CaptchaType: TRACELESS`. Kalau target memakai `SLIDING` atau
  `CHECK_BOX`, ada puzzle visual yang perlu pendekatan berbeda.
- Memerlukan browser asli. Emulator DOM (jsdom, engine Rust kustom) tidak akan
  berhasil — alasannya dibahas di `references/why-emulators-fail.md`.
- Bukan alat untuk melewati autentikasi. Kredensial tetap harus sah; skill ini
  hanya menyelesaikan lapisan captcha.

## Lisensi

MIT
