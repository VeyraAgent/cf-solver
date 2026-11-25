"""Local reCAPTCHA v2 audio fallback for cf-solver (no paid solver credits).

Runs inside cf-solver's browser page: switches the bframe to audio, pulls the MP3
with the page request context (bypasses CORS/Xray, keeps cookies+IP), shells the
file to the codex-farm venv for Whisper transcription, replays the answer and
only reports success when a real token appears.

Enable with RECAPTCHA_AUDIO_LOCAL=1 (default on). Tunables:
  RECAPTCHA_AUDIO_ASR_PY   python with faster-whisper (default codex-farm venv)
  RECAPTCHA_AUDIO_MODEL    whisper size (default small.en)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

ASR_PY = os.getenv("RECAPTCHA_AUDIO_ASR_PY", sys.executable)
ASR_SCRIPT = os.getenv("RECAPTCHA_AUDIO_ASR",
                       str(Path(__file__).with_name("asr_once.py")))
MODEL = os.getenv("RECAPTCHA_AUDIO_MODEL", "small.en")


def _norm(t: str) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


async def _transcribe(mp3: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3)
        path = f.name
    try:
        p = await asyncio.create_subprocess_exec(
            ASR_PY, ASR_SCRIPT, path, MODEL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), timeout=120)
        if p.returncode != 0:
            log.warning("asr failed: %s", (err or b"")[-200:])
        return _norm((out or b"").decode().strip())
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _frame(page):
    for f in page.frames:
        if "bframe" in (f.url or ""):
            return f
    return None


async def _ev(frame, js, *args):
    try:
        return await (frame.evaluate(js, *args) if args else frame.evaluate(js))
    except Exception:
        return None


async def _token(page) -> str:
    for js in (
        "() => document.querySelector('#g-recaptcha-response')?.value || ''",
        "() => document.querySelector('textarea[name=\"g-recaptcha-response\"]')?.value || ''",
        "() => window.__rc_token || ''",
    ):
        try:
            v = await page.evaluate(js)
            if v and len(v) > 20:
                return v
        except Exception:
            continue
    return ""


async def solve_audio_local(page, max_rounds: int = 8) -> dict:
    """Try the audio challenge on an open page. Returns {token|error, method, rounds}."""
    t0 = time.monotonic()
    heard = []
    for i in range(max_rounds):
        f = await _frame(page)
        if not f:
            tok = await _token(page)
            if tok:
                return {"token": tok, "method": "audio-local", "rounds": i,
                        "elapsed": round(time.monotonic() - t0, 1)}
            await asyncio.sleep(1.0)
            continue
        if not await _ev(f, "()=>!!document.querySelector('#audio-source')"):
            await _ev(f, "()=>{const b=document.querySelector('#recaptcha-audio-button');"
                         "if(b)b.click();}")
            await asyncio.sleep(2.0)
        src = await _ev(f, "()=>{const a=document.querySelector('#audio-source');"
                           "return a?a.src:null;}")
        if not src:
            await _ev(f, "()=>{const b=document.querySelector('#recaptcha-reload-button');"
                         "if(b)b.click();}")
            await asyncio.sleep(1.8)
            continue
        try:
            resp = await page.request.get(src, timeout=30000)
            data = await resp.body()
        except Exception as e:
            log.warning("audio fetch: %s", str(e)[:80])
            await asyncio.sleep(1.5)
            continue
        ans = await _transcribe(data)
        heard.append(ans)
        log.info("audio round %d heard=%r", i, ans)
        if not ans:
            await _ev(f, "()=>{const b=document.querySelector('#recaptcha-reload-button');"
                         "if(b)b.click();}")
            await asyncio.sleep(1.8)
            continue
        await _ev(f, "(t)=>{const i=document.querySelector('#audio-response');if(i){"
                     "i.value=t;i.dispatchEvent(new Event('input',{bubbles:true}));}}", ans)
        await asyncio.sleep(0.6)
        await _ev(f, "()=>{const b=document.querySelector('#recaptcha-verify-button');"
                     "if(b)b.click();}")
        await asyncio.sleep(3.2)
        tok = await _token(page)
        if tok:
            return {"token": tok, "method": "audio-local", "rounds": i + 1,
                    "heard": heard[-3:], "elapsed": round(time.monotonic() - t0, 1)}
        f2 = await _frame(page)
        if f2:
            await _ev(f2, "()=>{const b=document.querySelector('#recaptcha-reload-button');"
                          "if(b)b.click();}")
            await asyncio.sleep(1.8)
    return {"error": "audio-local failed", "heard": heard[-3:],
            "elapsed": round(time.monotonic() - t0, 1)}
