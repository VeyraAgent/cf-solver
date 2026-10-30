# Kamus DOM hCaptcha

Semua selector di bawah berlaku di dalam iframe
`newassets.hcaptcha.com/.../static/hcaptcha.html#frame=...`, bukan di halaman induk.

## Struktur frame

| Frame | URL fragment | Isi |
|---|---|---|
| checkbox | `#frame=checkbox&...` | `#checkbox` ("I am human") |
| challenge | `#frame=challenge&...` | `.task[role="button"]`, `#verify` |

Halaman induk menyediakan `textarea[name="h-captcha-response"]` — di sinilah
token `P1_...` mendarat.

## Elemen penting

```
.task[role="button"][aria-label="Challenge Image N"]   tile, N mulai 1
.task[aria-pressed="true"]                             tile terpilih
.task-image                                            wrapper gambar (bukan state)
.loading-indicator                                     spinner, opacity:0 saat siap
#verify, .button-submit                                tombol verify / skip
.display-error[aria-hidden="true"]                     container error, SELALU ada
.error-text                                            isi pesan di dalam display-error
.prompt-text, .challenge-text                          teks instruksi
```

## Kesalahan baca yang mahal

### `.display-error` selalu ada

```html
<div class="display-error" aria-hidden="true" role="alert"
     style="opacity: 0; display: table; ...">
  <div class="error-text" style="display: table-cell">Please try again. ⚠️</div>
</div>
```

Membaca `document.body.innerText` akan selalu menemukan "Please try again"
meskipun tidak ada error. Ini sempat membuat sesi dianggap gagal padahal
normal. Selalu cek computed style:

```js
const box = document.querySelector('.display-error');
const visible = box
  && getComputedStyle(box).opacity !== '0'
  && getComputedStyle(box).display !== 'none';
```

### State terpilih ada di parent

`.task-image` tidak berubah saat dipilih. Yang berubah adalah
`.task[aria-pressed]`. Kalau mengecek className `.task-image`, hasilnya selalu
"0 terpilih" walaupun klik berhasil.

### `boundingBox()` relatif frame

Untuk iframe, `elementHandle.boundingBox()` mengembalikan koordinat dalam ruang
frame. `page.mouse.move(b.x, b.y)` dari konteks page akan meleset sebesar offset
iframe. Pakai `elementHandle.click()` / `.hover()` yang menghitung offset
sendiri.

## Cek kesiapan tile

```js
const els = [...document.querySelectorAll('.task[role="button"]')];
const ok = els.filter(el => {
  const img = el.querySelector('img');
  if (img && img.complete && img.naturalWidth > 20) return true;
  const canvas = el.querySelector('canvas');
  if (canvas && canvas.width > 20) return true;
  for (const n of el.querySelectorAll('div, span')) {
    const bg = getComputedStyle(n).backgroundImage;
    if (bg && bg !== 'none' && /url\(["']?https?:/.test(bg)) return true;
  }
  return false;
}).length;
const ready = ok === els.length;
```

Kalau `ready` tidak pernah tercapai, curigai WebGL (lihat SKILL.md pitfall #1).

## Indikator jumlah halaman

`#verify` punya `aria-label` / `title`:

```
"Skip Challenge, page 1 of 2"
```

Angka setelah "page" adalah total halaman dalam challenge ini. Token baru keluar
setelah halaman terakhir dijawab. Kalau solver hanya menangani satu halaman,
token tidak akan pernah muncul.
