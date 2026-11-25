#!/usr/bin/env python3
"""camoufox_worker.py — optional reCAPTCHA v2 audio worker (Camoufox + Whisper).

Why a separate process: on flagged datacenter egress a plain Chromium gets
"Your computer or network may be sending automated queries" on the audio
fallback, while the Camoufox fingerprint often passes. cf-solver therefore
shells out to this worker and only parses its JSON.

Deps (optional, only for the audio path):
    pip install camoufox faster-whisper numpy
    python -m camoufox fetch          # once, downloads the browser
    ffmpeg must be on PATH (or FFMPEG env)

Usage:
    python camoufox_worker.py --url https://target/page [--sitekey 6Lc...] [--proxy http://...]

Prints one JSON line: {"token": "...", "method": "audio", "rounds": N} or {"error": "..."}
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

FFMPEG = os.getenv("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
_MODELS = {}


def _model(size="small.en"):
    if size not in _MODELS:
        from faster_whisper import WhisperModel
        _MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8", num_workers=1)
    return _MODELS[size]


def mp3_to_pcm(data: bytes, rate=16000):
    import numpy as np
    p = subprocess.run([FFMPEG, "-v", "quiet", "-i", "pipe:0", "-f", "s16le",
                        "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(rate), "pipe:1"],
                       input=data, capture_output=True, timeout=60)
    if not p.stdout:
        raise RuntimeError("ffmpeg decode failed")
    return np.frombuffer(p.stdout, dtype=np.int16).astype("float32") / 32768.0


def transcribe(data: bytes, size="small.en") -> str:
    segs, _ = _model(size).transcribe(mp3_to_pcm(data), language="en", beam_size=5,
                                      condition_on_previous_text=False, vad_filter=True)
    return " ".join(s.text for s in segs).strip()


def norm(t: str) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def token_of(page) -> str:
    for js in ("()=>{const t=document.querySelector('#g-recaptcha-response');return t?t.value:'';}",
               "()=>{const t=document.querySelector('textarea[name=\"g-recaptcha-response\"]');"
               "return t?t.value:'';}"):
        try:
            v = page.evaluate(js)
            if v and len(v) > 20:
                return v
        except Exception:
            pass
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--sitekey", default="")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--model", default=os.getenv("RECAPTCHA_AUDIO_MODEL", "small.en"))
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--headful", action="store_true")
    a = ap.parse_args()

    from camoufox.sync_api import Camoufox

    kw = {"proxy": {"server": a.proxy}} if a.proxy else {}
    with Camoufox(headless=not a.headful, humanize=True, i_know_what_im_doing=True, **kw) as b:
        page = b.new_page(viewport={"width": 1200, "height": 900})
        page.goto(a.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        has_widget = any("anchor" in (f.url or "") for f in page.frames)
        if not has_widget and a.sitekey:
            page.evaluate("""(k)=>{const d=document.createElement('div');d.id='rcbox';
                document.body.appendChild(d);
                const s=document.createElement('script');
                s.src='https://www.google.com/recaptcha/api.js?render=explicit&onload=__rc';
                window.__rc=()=>{window.__wid=grecaptcha.render('rcbox',{sitekey:k});};
                document.head.appendChild(s);}""", a.sitekey)
            page.wait_for_timeout(3000)
        try:
            page.frame_locator("iframe[src*='anchor']").first.locator(
                "#recaptcha-anchor").click(timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        tok = token_of(page)
        rounds = 0
        if not tok:
            for i in range(a.rounds):
                fr = next((f for f in page.frames if "bframe" in (f.url or "")), None)
                if not fr:
                    tok = token_of(page)
                    if tok:
                        break
                    page.wait_for_timeout(1200)
                    continue
                if not fr.evaluate("()=>!!document.querySelector('#audio-source')"):
                    fr.evaluate("()=>{const b=document.querySelector('#recaptcha-audio-button');"
                                "if(b)b.click();}")
                    page.wait_for_timeout(2200)
                src = fr.evaluate("()=>{const a=document.querySelector('#audio-source');"
                                  "return a?a.src:null;}")
                if not src:
                    fr.evaluate("()=>{const b=document.querySelector('#recaptcha-reload-button');"
                                "if(b)b.click();}")
                    page.wait_for_timeout(1800)
                    continue
                try:
                    data = page.request.get(src, timeout=30000).body()
                    ans = norm(transcribe(data, a.model))
                except Exception:
                    ans = ""
                rounds = i + 1
                if not ans:
                    fr.evaluate("()=>{const b=document.querySelector('#recaptcha-reload-button');"
                                "if(b)b.click();}")
                    page.wait_for_timeout(1800)
                    continue
                fr.evaluate("""(t)=>{const i=document.querySelector('#audio-response');if(i){
                    i.value=t;i.dispatchEvent(new Event('input',{bubbles:true}));}}""", ans)
                page.wait_for_timeout(600)
                fr.evaluate("()=>{const b=document.querySelector('#recaptcha-verify-button');"
                            "if(b)b.click();}")
                page.wait_for_timeout(3200)
                tok = token_of(page)
                if tok:
                    break
            page.wait_for_timeout(1000)
            tok = tok or token_of(page)

    print(json.dumps({"token": tok, "method": "audio", "rounds": rounds} if tok
                     else {"error": "no token"}))


if __name__ == "__main__":
    main()
