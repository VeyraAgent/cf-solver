"""reCAPTCHA v2 audio-challenge transcriber — pure ASR engine (fallback path).

reCAPTCHA v2 audio challenges are spoken-digit MP3s served from a signed
`/recaptcha/api2/audioupdate` URL. Since 2026 the in-page audio fallback is
reliably IP-blocked from datacenter/automation egress ("Your computer or network
may be sending automated queries"), which is why the widget-level audio flow was
removed from `solvers/recaptcha`. This module is the complementary, standalone
**transcription engine**: the caller harvests the MP3 (audio_url or audio_b64 —
e.g. from a clean-IP session or an existing capture) and gets the transcript
back. The CALLER replays the answer — type `digits` into the bframe
`#audio-response` input, click `#recaptcha-verify-button`, then read the final
token from `#g-recaptcha-response`.

Two ASR backends, resolved by `provider`:
  - "whisper"        → prefer faster-whisper (CTranslate2, int8, no ffmpeg),
                       fall back to openai-whisper (needs the ffmpeg CLI).
  - "faster-whisper" → faster-whisper only.
  - "openai-whisper" → openai-whisper only.

The Whisper model weights auto-download from Hugging Face on first use
(cached under ~/.cache/huggingface); `model` / `RECAPTCHA_AUDIO_MODEL` selects
the size ("tiny" default — see README for the accuracy/speed tradeoff).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
import tempfile
import threading
import time

log = logging.getLogger(__name__)

_DEFAULT_MODEL = os.getenv("RECAPTCHA_AUDIO_MODEL", "tiny")
_FETCH_TIMEOUT = 30.0

# reCAPTCHA audio speaks one digit at a time, so map the words Whisper commonly
# emits back to digits (oh→0, ate→8, ...). Non-digit filler (noise artifacts
# between digits) is dropped. Multi-digit tokens ("12") expand per character.
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2",
    "three": "3",
    "four": "4", "for": "4", "fore": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9",
}


def to_digits(transcript: str) -> str:
    """Extract the digit sequence reCAPTCHA expects from a Whisper transcript."""
    out: list[str] = []
    for tok in transcript.lower().replace(",", " ").split():
        tok = tok.strip(".,;:!?\"'()[]")
        if not tok:
            continue
        if tok in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[tok])
            continue
        # Whisper merges adjacent digits into one group and often hyphenates
        # it under noise ("307-819") — split groups on separators, then expand
        # each multi-digit part per character ("34" → 3,4).
        for part in re.split(r"[-–—/_.]+", tok):
            if part.isdigit():
                out.extend(part)
            elif part in _DIGIT_WORDS:
                out.append(_DIGIT_WORDS[part])
    return "".join(out)




def available() -> bool:
    """True when at least one ASR backend is importable."""
    for mod in ("faster_whisper", "whisper"):
        try:
            __import__(mod)
            return True
        except Exception:
            continue
    return False


# ── model loading (cached, thread-safe) ───────────────────────────────

_MODEL_CACHE: dict = {}
_MODEL_LOCK = threading.Lock()


def _load_model(backend: str, model: str):
    with _MODEL_LOCK:
        key = (backend, model)
        if key not in _MODEL_CACHE:
            t0 = time.monotonic()
            if backend == "faster-whisper":
                from faster_whisper import WhisperModel
                _MODEL_CACHE[key] = WhisperModel(
                    model, device="cpu", compute_type="int8")
            else:  # openai-whisper
                import whisper
                _MODEL_CACHE[key] = whisper.load_model(model)
            log.info("loaded %s model=%s in %.1fs", backend, model,
                     time.monotonic() - t0)
        return _MODEL_CACHE[key]


def _resolve_backend(provider: str) -> str:
    """Map the requested provider to an importable backend."""
    if provider in ("faster-whisper", "faster"):
        try:
            import faster_whisper  # noqa: F401
            return "faster-whisper"
        except Exception:
            raise RuntimeError(
                "provider='faster-whisper' but the package is not installed "
                "(pip install faster-whisper)")
    if provider in ("openai-whisper", "openai"):
        try:
            import whisper  # noqa: F401
            return "openai-whisper"
        except Exception:
            raise RuntimeError(
                "provider='openai-whisper' but the package is not installed "
                "(pip install openai-whisper; also requires the ffmpeg CLI)")
    if provider != "whisper":
        raise ValueError(f"unknown provider {provider!r} "
                         "(whisper | faster-whisper | openai-whisper)")
    # auto: prefer faster-whisper (no ffmpeg needed), fall back to openai-whisper
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except Exception:
        pass
    try:
        import whisper  # noqa: F401
        return "openai-whisper"
    except Exception:
        pass
    raise RuntimeError(
        "no ASR backend available — pip install faster-whisper "
        "(recommended, no ffmpeg) or openai-whisper")


def _transcribe(path: str, backend: str, model: str) -> tuple[str, float]:
    """Run ASR on an audio file → (transcript, audio_duration_s)."""
    t0 = time.monotonic()
    m = _load_model(backend, model)
    if backend == "faster-whisper":
        # vad_filter=False is deliberate: reCAPTCHA noise-garbles the gaps
        # between digits and VAD would eat real digits along with them.
        segments, info = m.transcribe(
            path, language="en", temperature=0.0, beam_size=5,
            condition_on_previous_text=False, vad_filter=False,
            without_timestamps=True)
        text = "".join(s.text for s in segments).strip()
        return text, float(info.duration)
    # openai-whisper: load_audio shells out to the ffmpeg CLI
    import whisper
    audio = whisper.load_audio(path)
    result = m.transcribe(
        audio, language="en", temperature=0.0,
        condition_on_previous_text=False)
    text = result.get("text", "").strip()
    return text, float(len(audio) / whisper.audio.SAMPLE_RATE)


# ── audio acquisition ─────────────────────────────────────────────────

def _fetch_audio_bytes(url: str, proxy: str | None) -> bytes:
    """Download the challenge MP3 with Chrome impersonation."""
    from curl_cffi import requests as creq

    kwargs: dict = {"impersonate": "chrome", "timeout": _FETCH_TIMEOUT}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    r = creq.get(url, **kwargs)
    r.raise_for_status()
    body = r.content
    if not body:
        raise RuntimeError("audio URL returned an empty body")
    ctype = (r.headers.get("content-type") or "").lower()
    if "text/" in ctype or "json" in ctype:
        raise RuntimeError(
            f"audio URL returned non-audio content ({ctype or 'unknown'}): "
            f"{body[:120]!r}")
    return body


def _decode_audio_b64(b64: str) -> bytes:
    s = b64.strip()
    if s.startswith("data:"):                   # data:audio/mpeg;base64,....
        s = s.split(",", 1)[-1]
    try:
        return base64.b64decode(s)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"audio_b64 is not valid base64: {exc}")


def _sniff_audio(body: bytes) -> str:
    """Best-effort container guess for the temp-file suffix (mp3/wav)."""
    if body[:3] == b"ID3" or (body[:1] == b"\xff" and
                              (body[1] & 0xE0) == 0xE0):
        return ".mp3"
    if body[:4] == b"RIFF" and body[8:12] == b"WAVE":
        return ".wav"
    raise RuntimeError(
        f"payload does not look like mp3/wav audio ({body[:16]!r})")


# ── entry point ───────────────────────────────────────────────────────

async def solve_recaptcha_audio(audio_url: str = None, audio_b64: str = None,
                                provider: str = "whisper", timeout_s: int = 90,
                                proxy: str = None, model: str = None) -> dict:
    """Transcribe a reCAPTCHA v2 audio challenge into the digit answer.

    Args:
        audio_url:  signed `/api2/audioupdate` MP3 URL from the challenge iframe
                    (fetched with Chrome impersonation; `proxy` honored).
        audio_b64:  base64 (or data-URI) of the MP3/WAV — takes precedence over
                    `audio_url` when both are given.
        provider:   "whisper" (auto backend) | "faster-whisper" | "openai-whisper".
        timeout_s:  overall budget incl. first-use model download.
        proxy:      optional proxy for the audio_url fetch (same IP as the
                    session that minted the URL — Google binds them).
        model:      Whisper model size (default: $RECAPTCHA_AUDIO_MODEL or "tiny").

    Returns the uniform contract — never raises:
        {"solved": bool, "type": "recaptcha_audio", "token": transcript,
         "transcript": str, "digits": str, "method": "whisper",
         "backend": str, "model": str, "duration_s": float, "elapsed": float,
         "error": str|None, ...}
    `token` is the raw transcript; `digits` is the reCAPTCHA-ready answer
    (word→digit normalized). The CALLER replays `digits` in the widget —
    this engine never opens a browser.
    """
    t0 = time.monotonic()
    model = model or _DEFAULT_MODEL

    def _fail(error: str, **extra) -> dict:
        return {
            "solved": False, "type": "recaptcha_audio", "token": "",
            "transcript": "", "digits": "", "method": "whisper",
            "elapsed": round(time.monotonic() - t0, 1), "error": error,
            **extra,
        }

    if not audio_url and not audio_b64:
        return _fail("audio_url or audio_b64 is required")

    def _sync() -> dict:
        # 1. resolve the backend first — fail fast on provider/model issues
        backend = _resolve_backend(provider)
        log.info("backend=%s model=%s", backend, model)

        # 2. acquire the audio bytes
        if audio_b64:
            body = _decode_audio_b64(audio_b64)
            log.info("audio from b64 (%d bytes)", len(body))
        else:
            log.info("fetching audio %s", audio_url)
            body = _fetch_audio_bytes(audio_url, proxy)
            log.info("fetched %d bytes", len(body))
        suffix = _sniff_audio(body)

        # 3. transcribe
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(body)
            path = tmp.name
        try:
            text, duration = _transcribe(path, backend, model)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if not text:
            return _fail(f"transcription produced no text ({backend}/{model})",
                         backend=backend, model=model, duration_s=round(duration, 2))
        log.info("transcript: %r", text)

        # 4. normalize to the digit answer
        digits = to_digits(text)
        out = {
            "solved": True, "type": "recaptcha_audio", "token": text,
            "transcript": text, "digits": digits, "method": "whisper",
            "backend": backend, "model": model,
            "duration_s": round(duration, 2),
            "elapsed": round(time.monotonic() - t0, 1),
            "error": None,
            "replay": ("Type `digits` into the challenge iframe's "
                       "#audio-response input, click #recaptcha-verify-button, "
                       "then read #g-recaptcha-response."),
        }
        if not digits:
            out["warning"] = ("no digits found in transcript — reCAPTCHA "
                              "expects only the digits; retry with a larger "
                              "model or cleaner audio")
        return out

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync),
                                      timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _fail(f"recaptcha_audio timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("recaptcha_audio failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200])
