# Prompt VLM untuk grid hCaptcha

Tujuan: satu call per grid, output yang bisa di-parse tanpa toleransi.

## Prompt yang dipakai

```
CAPTCHA grid, {N} tiles in a {COLS}-column layout, numbered LEFT-TO-RIGHT then
TOP-TO-BOTTOM from index 0.
Task: "{PROMPT}"
If a reference/sample image is present, it is NOT a selectable tile - match
tiles to its category.
Return ONLY a JSON array of matching tile indexes, e.g. [0,3,7].
Output must start with "[" and end with "]". No words, no explanation.
```

Parameter: `max_tokens: 40`, `temperature: 0`.

## Kenapa dirumuskan begitu

| Bagian | Alasan |
|---|---|
| "numbered LEFT-TO-RIGHT then TOP-TO-BOTTOM from index 0" | model tidak tahu penomoran kita; tanpa ini dia menebak urutan |
| "reference image is NOT a selectable tile" | pada task `ref=true`, model sering ikut memilih gambar referensi |
| 'Output must start with "["' | memotong jawaban prosa |
| `temperature: 0` | jawaban harus deterministik antar retry |
| `max_tokens: 40` | cukup untuk array, mencegah esai |

## Penanganan output

Jangan percaya format. Urutan fallback:

```js
let picks = [];
const m = content.match(/\[[\s\d,\s]*\]/);
if (m) { try { picks = JSON.parse(m[0]); } catch { picks = []; } }
else picks = (content.match(/\b\d{1,2}\b/g) || []).map(Number);

picks = [...new Set(picks)].filter(i => Number.isInteger(i) && i >= 0 && i < n);
if (picks.length >= n) picks = picks.slice(0, 3);   // model salah paham
```

Kasus yang benar-benar terjadi:

- `[3, 5, 7]` — normal
- `[1,2,5,6,8]` — tanpa spasi, tetap valid
- `The reference animal is a sea turtle, which l...` — prosa; fallback regex
  menyelamatkan sebagian, tapi lebih baik perbaiki prompt
- `[0,1,2,3,4,5,6,7,8]` — semua tile; hampir selalu salah

## Rate limit

Endpoint vision sering membalas 500 kalau dipanggil paralel 6 sekaligus.
Gunakan paralel 2–3 per grid, atau serial. Retry dengan backoff:

```js
if ((res.status >= 500 || res.status === 429) && attempt < 4) {
  await sleep(700 * attempt);
  return callVlm(messages, maxTokens, attempt + 1);
}
```

## SSE vs JSON

Sebagian endpoint mengembalikan `text/event-stream`, sebagian JSON biasa.
Parser harus menangani keduanya — akumulasi `data:` lines, dan kalau hasilnya
kosong coba `JSON.parse(seluruh body)`.

## Biaya waktu

Mengirim 9–10 tile satu per satu: ~47 detik per ronde.
Mengirim grid utuh dalam satu gambar: ~10 detik per ronde.
Untuk grid yang butuh 2 page, selisihnya jadi ~90 detik per solve.
