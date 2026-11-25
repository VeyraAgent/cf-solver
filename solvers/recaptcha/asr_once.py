#!/usr/bin/env python3
"""asr_once.py — transcribe a single audio file to text (helper for audio_local).

Kept as a standalone script so the solver process does not need faster-whisper
installed: cf-solver shells out to a python that has it (RECAPTCHA_AUDIO_ASR_PY).

Usage: python asr_once.py /tmp/challenge.mp3 [model]
"""
import os
import shutil
import subprocess
import sys

import numpy as np

FFMPEG = os.getenv("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
_MODELS = {}


def main():
    path = sys.argv[1]
    size = sys.argv[2] if len(sys.argv) > 2 else "small.en"
    p = subprocess.run([FFMPEG, "-v", "quiet", "-i", path, "-f", "s16le", "-acodec",
                        "pcm_s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
                       capture_output=True, timeout=60)
    if not p.stdout:
        print("", flush=True)
        return
    if size not in _MODELS:
        from faster_whisper import WhisperModel
        _MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8", num_workers=1)
    arr = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    segs, _ = _MODELS[size].transcribe(arr, language="en", beam_size=5,
                                       condition_on_previous_text=False, vad_filter=True)
    print(" ".join(s.text for s in segs).strip(), flush=True)


if __name__ == "__main__":
    main()
