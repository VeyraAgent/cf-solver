"""Friendly Captcha v1 solver — pure-Python PoW via hashlib blake2b (no browser).

Spec reverse-verified from FriendlyCaptcha/friendly-pow + friendly-challenge:

  1. GET {puzzle_endpoint}?sitekey=SK  (header x-frc-client: js-0.9.20)
     → {data: {puzzle: "SIGNATURE.BUFFERB64"}}
  2. buffer = b64decode(BUFFERB64)  (32 bytes)
     numPuzzles = buffer[14], difficulty = buffer[15]
     threshold = int(2 ** ((255.999 - difficulty) / 8))   (u32)
  3. For puzzle i in 0..numPuzzles-1: input = 128 bytes
     [0:32]=buffer, zeros, input[120]=i, then iterate byte123=b + nonce u32
     (input[124:128] LE) until blake2b(input, digest_size=32) first-u32-LE
     < threshold. Solution_i = input[120:128] (8 bytes).
  4. token = signature + "." + BUFFERB64 + "." + b64(concat solutions) +
     "." + b64(diagnostics: u8 solverID=1 + u16 totalHashes)

Browser-widget fallback path retained for deployments that prefer it.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import cloakbrowser
from solvers.common.browser import browser_kwargs

log = logging.getLogger(__name__)

PUZZLE_ENDPOINT = "https://api.friendlycaptcha.com/api/v1/puzzle"
_FRC_HEADER = {"x-frc-client": "js-0.9.20"}


def _difficulty_to_threshold(value: int) -> int:
    value = max(0, min(255, value))
    return int(2 ** ((255.999 - value) / 8.0)) & 0xFFFFFFFF


def _solve_one(input_128: bytearray, threshold: int, deadline: float) -> bytes | None:
    nonce = 0
    while nonce < 0xFFFFFFFF:
        input_128[124:128] = struct.pack("<I", nonce)
        h = hashlib.blake2b(bytes(input_128), digest_size=32).digest()
        if struct.unpack("<I", h[:4])[0] < threshold:
            return bytes(input_128[120:128])
        nonce += 1
        if time.monotonic() > deadline and nonce > 100000:
            return None
    return None


def _fail(error: str, t0: float) -> dict:
    """Uniform failure dict — module-level so every path (PoW + browser) can use it."""
    return {
        "solved": False, "type": "friendly", "token": "",
        "method": "pow-blake2b", "elapsed": round(time.monotonic() - t0, 1),
        "error": error,
    }


def _solve_puzzle(sitekey: str, timeout_s: int) -> dict:
    t0 = time.monotonic()

    r = requests.get(PUZZLE_ENDPOINT, params={"sitekey": sitekey},
                     headers=_FRC_HEADER, timeout=15)
    data = r.json()
    if not data.get("success", True) or "data" not in data:
        return _fail(f"puzzle fetch failed: {json.dumps(data)[:150]}", t0)

    puzzle_str = data["data"]["puzzle"]
    parts = puzzle_str.split(".")
    signature, buffer_b64 = parts[0], parts[1]
    buf = base64.b64decode(buffer_b64)
    num_puzzles = buf[14]
    threshold = _difficulty_to_threshold(buf[15])
    log.info("friendly: %d puzzles, difficulty %d, threshold %d",
             num_puzzles, buf[15], threshold)

    solutions = bytearray()
    hashes = 0
    deadline = time.monotonic() + max(10, int(timeout_s))
    _pack, _unpack = struct.pack, struct.unpack
    for i in range(num_puzzles):
        input_128 = bytearray(128)
        input_128[0:32] = buf
        input_128[120] = i
        # Reuse one blake2b prefix state across nonces (prefix = input[:123]).
        # Verified to produce byte-identical digests; ~1.8x the per-hash rate.
        base = hashlib.blake2b(bytes(input_128[:123]), digest_size=32)
        found = False
        for b in range(256):
            bb = bytes([b])
            for nonce in range(0, 0xFFFFFFFF):
                h = base.copy()
                h.update(bb)
                h.update(_pack("<I", nonce))
                hashes += 1
                if _unpack("<I", h.digest()[:4])[0] < threshold:
                    solutions += bytes([i, 0, 0, b]) + _pack("<I", nonce)
                    found = True
                    break
                # Deadline is checked in batches, not per hash (monotonic() was
                # ~30% of the loop cost); 4096 hashes is <2 ms of drift.
                if (nonce & 0xFFF) == 0xFFF and time.monotonic() > deadline:
                    return _fail(f"PoW timeout: solved {i}/{num_puzzles} puzzles", t0)
                if nonce >= 400000:  # safety valve per (b, nonce) sweep
                    break
            if found:
                break
        if not found:
            return _fail(f"puzzle {i} unsolvable within limits", t0)

    diagnostics = struct.pack("<BH", 1, min(hashes, 0xFFFF))
    token = (signature + "." + buffer_b64 + "." +
             base64.b64encode(bytes(solutions)).decode() + "." +
             base64.b64encode(diagnostics).decode())
    return {
        "solved": True, "type": "friendly", "token": token,
        "payload": token, "method": "pow-blake2b",
        "elapsed": round(time.monotonic() - t0, 1), "error": None,
        "puzzles": num_puzzles, "total_hashes": hashes,
        "warning": "Friendly tokens are short-lived — submit immediately.",
    }


async def solve_friendly(url: str = None, sitekey: str = None,
                          proxy: str = None, timeout_s: int = 90) -> dict:
    """Solve Friendly Captcha: pure-Python PoW via sitekey (primary),
    browser-widget fallback (loads the page and reads the solution input)."""
    if not sitekey and not url:
        return {
            "solved": False, "type": "friendly", "token": "",
            "method": "pow-blake2b", "elapsed": 0.0,
            "error": "sitekey (for PoW) or url (page hosting the widget) is required",
        }

    t0 = time.monotonic()

    # Path 1: pure-PoW via the puzzle API (needs a valid sitekey)
    if sitekey:
        def _pow() -> dict:
            return _solve_puzzle(sitekey, timeout_s)
        try:
            r = await asyncio.wait_for(asyncio.to_thread(_pow), timeout=max(timeout_s, 30))
            if r.get("solved"):
                return r
            log.info("friendly PoW path failed (%s) — trying browser widget", r.get("error"))
        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            log.warning("friendly PoW path failed: %s", exc)

    # Path 2: browser widget fallback (works when `url` hosts the widget)
    async def _drive():
        async with await cloakbrowser.launch_async(
                **browser_kwargs("TURNSTILE", proxy=proxy)) as browser:
            page = await browser.new_page()
            try:
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                else:
                    import base64 as _b64
                    snippet = (f'<html><head><script type="module" src='
                               f'"https://cdn.jsdelivr.net/npm/friendly-challenge@0.9.20/'
                               f'widget.min.js" async></script></head><body><form>'
                               f'<div class="frc-captcha" data-sitekey="{sitekey}" '
                               f'data-start="auto" data-lang="en"></div></form></body></html>')
                    data_url = ("data:text/html;base64," +
                                _b64.b64encode(snippet.encode()).decode())
                    await page.goto(data_url, wait_until="domcontentloaded", timeout=45000)
                deadline = time.monotonic() + max(15, int(timeout_s))
                while time.monotonic() < deadline:
                    for sel in ("input.frc-captcha-solution",
                                'input[name="frc-captcha-solution"]'):
                        try:
                            val = await page.eval_on_selector(sel, "el => el.value || ''")
                            if val and len(val) > 32:
                                return val
                        except Exception:
                            pass
                    await asyncio.sleep(1.0)
                return None
            finally:
                await page.close()

    async def _browser_run() -> dict:
        val = await _drive()
        if val:
            return {
                "solved": True, "type": "friendly", "token": val,
                "method": "browser-widget", "elapsed": round(time.monotonic() - t0, 1),
                "error": None,
            }
        return _fail("browser widget did not produce a solution", t0)

    try:
        return await asyncio.wait_for(_browser_run(), timeout=max(timeout_s, 40))
    except asyncio.TimeoutError:
        return _fail(f"friendly solve timed out after {timeout_s}s", t0)
    except Exception as exc:
        log.warning("friendly browser path failed: %s", exc)
        return _fail(str(exc).splitlines()[0][:200], t0)
