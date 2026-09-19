# reCAPTCHA v2 audio transcriber — standalone ASR engine (fallback path)

Pure transcription engine for the reCAPTCHA v2 **audio challenge**: feed it the
challenge MP3 (`audio_url` or `audio_b64`) and get the transcript + the
digit answer back. No browser, no widget — the CALLER owns the browser part.

> **Honest boundary.** The in-page audio fallback of reCAPTCHA v2 is reliably
> IP-blocked from datacenter/automation egress ("Your computer or network may be
> sending automated queries") — that is why the widget-level audio flow was
> removed from `solvers/recaptcha`. This module does NOT open the widget. It
> transcribes whatever MP3 you hand it — harvested from a clean/residential-IP
> session, an existing capture, or any other source. If your egress can still
> get the audio button to play, this solver completes the path; if Google
> denies the audio challenge for your IP, no transcriber helps.

## Division of labor (caller contract)

```text
THIS SOLVER                                    CALLER (browser side)
────────────────────────────────────────      ─────────────────────────────
1. harvest audio URL from the bframe    ───►  2. solve_recaptcha_audio(...)
   iframe (#audio-source src, signed          3. read result["digits"]
   /api2/audioupdate?... MP3)            ◄───  4. type digits into #audio-response
                                              5. click #recaptcha-verify-button
6. re-transcribe + loop on reject             6. read #g-recaptcha-response (token)
```

## Usage

```python
from solvers.recaptcha_audio import solve_recaptcha_audio

result = await solve_recaptcha_audio(
    audio_url="https://www.google.com/recaptcha/api2/audioupdate?k=...&...",  # or
    audio_b64="...",                       # raw/data-URI base64 of the MP3/WAV
    provider="whisper",                    # auto backend | faster-whisper | openai-whisper
    model="tiny",                          # or set $RECAPTCHA_AUDIO_MODEL
    proxy=None,                            # for audio_url fetch — use the SAME
    timeout_s=90,                          # IP that minted the signed URL
)
# result["token"]     = raw transcript  ("1, 2, 3, 4, 5, 6")
# result["digits"]    = "123456"        ← what you type into the widget
```
Result contract: `{"solved": bool, "type": "recaptcha_audio",
"token": str, "transcript": str, "digits": str, "method": "whisper",
"backend": "faster-whisper"|"openai-whisper", "model": str,
"duration_s": float, "elapsed": float, "error": str|None, "replay": str}`
— never raises; all failures return the dict with `solved: false`.
`solved` means *transcription succeeded*; if the transcript contained no
digits at all, `digits` is `""` + a `warning` is attached (empty transcript →
`solved: false` with the backend/model in the error extras).

### Parameters

| Param | Default | Notes |
| --- | --- | --- |
| `audio_url` | — | signed audioupdate MP3 URL; fetched with curl_cffi Chrome impersonation |
| `audio_b64` | — | base64 / `data:audio/...;base64,` of MP3 or WAV; takes precedence over `audio_url` |
| `provider` | `"whisper"` | `whisper` = prefer faster-whisper, fall back to openai-whisper; explicit values force one backend |
| `timeout_s` | `90` | overall budget, incl. first-use model download |
| `proxy` | `None` | only used for the `audio_url` fetch — Google binds the signed URL to the session IP |
| `model` | `$RECAPTCHA_AUDIO_MODEL` or `"tiny"` | Whisper size: `tiny`/`base`/`small`/... |

## Digit normalization

reCAPTCHA expects **only digits**; Whisper returns words. `to_digits()` maps
`oh→0, one/won→1, to/too→2, for/fore→4, ate→8`, expands merged groups
(`"34"` → 3,4) and splits separator-hyphenated groups (`"307-819"` — what
Whisper emits under noise) per character. Everything else is dropped.

## Model requirement

| Backend | Install | Notes |
| --- | --- | --- |
| **faster-whisper** (recommended) | `pip install faster-whisper` | CTranslate2 int8 CPU, decodes via PyAV — **no ffmpeg CLI needed** |
| openai-whisper | `pip install openai-whisper` | needs the `ffmpeg` binary on PATH |

Whisper **weights** are NOT shipped: the backend auto-downloads the selected
model from Hugging Face on first use (cached under `~/.cache/huggingface`;
tiny ≈ 75 MB). If neither package is installed, the solver returns a clear
error dict instead of raising. Default `tiny` was chosen for download size +
CPU speed; pass `model="base"`/`"small"` if accuracy degrades on very garbled
audio.

## Self-test

```bash
python3 -m solvers.recaptcha_audio.selftest
```

Covers: digit-normalization vectors (words, merged groups, hyphenated groups,
punctuation), the uniform error-path contract (no input / bad base64 /
non-audio payload / unknown provider — all fail fast before any network or
model work), and an end-to-end transcribe of a synthetic WAV (contract +
no-crash; the tone audio is non-speech by design).

## Validation evidence (owner notes, 2026-09-13)

- faster-whisper **tiny** + **base**, real TTS speech (`"1 2 3 4 5 6"`):
  `digits == "123456"` both — tiny 1.5 s, base 2.4 s (int8 CPU, warm model).
- **Noise robustness** (recaptcha-style noise bursts, digits `3 0 7 8 1 9`,
  tiny): correct `307819` at 10 dB, 3 dB **and 0 dB** SNR. The 3 dB / 0 dB
  transcripts came back hyphenated (`"307-819"`) — that exact case is pinned
  in the self-test vectors.
- Synthetic tone WAV: contract holds, no crash; empty transcript → graceful
  `solved: false` (not silently "solved with garbage").

## Credits

| Source | What was taken |
| --- | --- |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Primary ASR backend (CTranslate2 port of Whisper) |
| [openai/whisper](https://github.com/openai/whisper) | Fallback ASR backend |
| [mikeyy/nonoCAPTCHA](https://github.com/mikeyy/nonoCAPTCHA) | Audio-path reference: harvest → transcribe → type digits → read token loop |
| [RektCaptcha](https://github.com/d686e6/RektCaptcha) | Whisper-on-recaptcha-audio approach (digits corpus behaves well on tiny/base) |

## Files

```text
solvers/recaptcha_audio/
├── solve.py      entry point (solve_recaptcha_audio), to_digits, backend loader, fetch
├── selftest.py   offline unit tests (vectors + contract + synthetic WAV)
└── README.md
```
