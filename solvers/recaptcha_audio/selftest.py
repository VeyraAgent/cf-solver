"""Self-test for the recaptcha_audio solver — offline, no network.

Run: python3 -m solvers.recaptcha_audio.selftest

Covers:
  1. digit normalization vectors (word map, merged numbers, punctuation)
  2. uniform-contract error paths (no input, bad base64, bogus bytes)
  3. end-to-end transcribe of a synthetic WAV (numpy tone+noise) — asserts the
     contract holds and no crash; the transcript of non-speech audio is
     meaningless by design, accuracy is validated separately with real speech.
"""
import asyncio
import base64
import io
import sys
import wave

import numpy as np

from .solve import solve_recaptcha_audio, to_digits, available

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name} {detail}")


def test_digits():
    print("[1] to_digits vectors")
    vectors = [
        ("one two three four five six", "123456"),
        ("12 34 56", "123456"),
        ("oh for five six", "0456"),
        ("7, ate, 9.", "789"),
        ("5", "5"),
        ("three eight nine won two", "38912"),
        ("garbage noise words", ""),
        ("307-819.", "307819"),      # whisper hyphenates digit groups under noise
        ("307/819", "307819"),
        ("", ""),
    ]
    for src, want in vectors:
        got = to_digits(src)
        check(f"{src!r} -> {want!r}", got == want, f"(got {got!r})")


def _fail_contract_ok(res: dict, name: str):
    check(f"{name}: contract keys", all(
        k in res for k in ("solved", "type", "token", "method", "elapsed", "error")))
    check(f"{name}: solved False", res["solved"] is False)
    check(f"{name}: error set", bool(res["error"]))
    check(f"{name}: no raise, type ok", res["type"] == "recaptcha_audio")


def test_contract_errors():
    print("[2] error-path contract (never raises)")
    r1 = asyncio.run(solve_recaptcha_audio())
    _fail_contract_ok(r1, "no input")
    r2 = asyncio.run(solve_recaptcha_audio(audio_b64="not@@base64!!"))
    _fail_contract_ok(r2, "bad base64")
    r3 = asyncio.run(solve_recaptcha_audio(audio_b64="aGVsbG8="))  # "hello" bytes
    _fail_contract_ok(r3, "non-audio payload")
    r4 = asyncio.run(solve_recaptcha_audio(provider="nope", audio_b64="aGVsbG8="))
    _fail_contract_ok(r4, "unknown provider fails before audio sniff")


def _synth_wav_b64(seconds: float = 2.0, freq: float = 440.0) -> str:
    sr = 16000
    t = np.arange(int(sr * seconds)) / sr
    sig = (0.3 * np.sin(2 * np.pi * freq * t)
           + 0.05 * np.random.randn(len(t)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((sig * 32767).astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def test_synth_transcribe():
    print("[3] synthetic WAV end-to-end (requires an ASR backend)")
    if not available():
        print("  SKIP  no ASR backend installed "
              "(pip install faster-whisper)")
        return
    r = asyncio.run(solve_recaptcha_audio(audio_b64=_synth_wav_b64(),
                                          timeout_s=180))
    check("solved dict returned", isinstance(r, dict))
    check("token is str", isinstance(r.get("token"), str))
    check("elapsed float", isinstance(r.get("elapsed"), float))
    check("model/backend extras", bool(r.get("backend")) and bool(r.get("model")))
    if r["solved"]:
        print(f"  note: synthetic transcript {r.get('transcript')!r} "
              f"-> digits {r.get('digits')!r} "
              f"(tone audio is non-speech; empty digits + warning is OK)")
    else:
        # silence-only audio legitimately yields no text in some backends
        check("graceful no-text failure", bool(r.get("error")))


if __name__ == "__main__":
    test_digits()
    test_contract_errors()
    test_synth_transcribe()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
