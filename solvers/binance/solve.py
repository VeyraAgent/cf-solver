"""Binance slide captcha solver — port of xKiian/binance-captcha-solver.

Solves the Binance antibot SLIDE captcha by:
1. Splitting the combined image (puzzle piece left 60px | background right)
2. Canny edge detection on both pieces
3. cv2.matchTemplate TM_CCOEFF_NORMED to find the X offset
4. BinanceCrypto XOR-based encrypt/decrypt for anti-bot payload
5. Fingerprint + biometric mouse-movement generation

Core image solver (Canny + matchTemplate) is pure cv2/numpy — no model needed.

Reference: https://github.com/xKiian/binance-captcha-solver (92 stars, MIT)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from math import hypot

import cv2
import httpx
import numpy as np

log = logging.getLogger("binance")

# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _result(**kw) -> dict:
    base = {"solved": False, "type": "binance", "token": "", "method": "",
            "elapsed": 0.0, "error": None}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Slide solver — Canny edge + cv2.matchTemplate (core engine)
# ---------------------------------------------------------------------------

_PIECE_WIDTH = 60          # left 60px of combined image = puzzle piece
_ADJUSTMENT = 31           # empirical offset correction from reference
_CANNY_LOW = 100
_CANNY_HIGH = 200


def solve_slide(image: np.ndarray) -> int:
    """Detect puzzle-piece X offset in a combined Binance slide image.

    The image layout is [puzzle_piece | background] side by side.
    Returns the pixel X offset (with empirical adjustment) where the piece
    fits on the background.
    """
    height, width = image.shape[:2]
    if width <= _PIECE_WIDTH:
        raise ValueError(f"image too narrow ({width}px) — expected piece+bg")

    piece = image[0:height, 0:_PIECE_WIDTH]
    background = image[0:height, _PIECE_WIDTH:width]

    edge_piece = cv2.Canny(piece, _CANNY_LOW, _CANNY_HIGH)
    edge_bg = cv2.Canny(background, _CANNY_LOW, _CANNY_HIGH)

    # matchTemplate needs same dtype/channels — convert to RGB
    edge_piece_rgb = cv2.cvtColor(edge_piece, cv2.COLOR_GRAY2RGB)
    edge_bg_rgb = cv2.cvtColor(edge_bg, cv2.COLOR_GRAY2RGB)

    res = cv2.matchTemplate(edge_bg_rgb, edge_piece_rgb,
                            cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(res)

    center_x = max_loc[0] + edge_piece.shape[1] // 2
    return center_x - _ADJUSTMENT


def solve_slide_from_bytes(data: bytes) -> int:
    """Decode image bytes and solve the slide offset."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_ANYCOLOR)
    if img is None:
        raise ValueError("failed to decode image")
    return solve_slide(img)


def solve_slide_from_b64(b64: str) -> int:
    """Decode base64 image and solve the slide offset."""
    return solve_slide_from_bytes(base64.b64decode(b64))


# ---------------------------------------------------------------------------
# BinanceCrypto — XOR-based encrypt/decrypt with key derivation
# ---------------------------------------------------------------------------
# Ported verbatim from binance/crypto.py (xKiian). NOT AES — custom XOR
# with UTF-8/UTF-16 encoding and custom base64-like encoding.

class _BinanceCrypto:
    """XOR cipher used by Binance antibot payload encryption."""

    _ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    _KEY_CHARS = "abcdhijkxy"

    @staticmethod
    def _derive_extra(key: str, parts: int, salt: int = 0x1F) -> str:
        """Derive extra key characters from key string (mirrors JS I() function)."""
        if not key:
            return ""
        chunk = len(key) // parts
        out = []
        for i in range(parts):
            acc = 0
            start = i * chunk
            end = chunk + (len(key) % parts) if i == parts - 1 else chunk
            for j in range(end):
                w = start + j
                if w < len(key):
                    acc += ord(key[w])
            acc *= salt
            out.append(_BinanceCrypto._KEY_CHARS[acc % len(_BinanceCrypto._KEY_CHARS)])
        return "".join(out)

    @staticmethod
    def _to_utf16_units(s: str) -> list[int]:
        """Convert string to UTF-16 code units (matches JS z())."""
        result = []
        i = 0
        while i < len(s):
            c = ord(s[i])
            i += 1
            if 0xD800 <= c <= 0xDBFF and i < len(s):
                lo = ord(s[i])
                i += 1
                if (lo & 0xFC00) == 0xDC00:
                    result.append(((c & 0x3FF) << 10) + (lo & 0x3FF) + 0x10000)
                else:
                    result.append(c)
                    i -= 1
            else:
                result.append(c)
        return result

    @staticmethod
    def _encode_utf8(codepoints: list[int]) -> str:
        """Encode codepoints to UTF-8 bytes as a latin1 string (matches JS L_func)."""
        out = []
        for cp in codepoints:
            if (cp & 0xFF80) == 0 and ((cp >> 16) & 0xFFFF) == 0:
                out.append(chr(cp))
                continue
            if (cp & 0xF800) == 0 and ((cp >> 16) & 0xFFFF) == 0:
                out.append(chr((cp >> 6) & 0x1F | 0xC0))
            elif (cp & 0x0) == 0 and ((cp >> 16) & 0xFFFF) == 0x0:
                out.append(chr((cp >> 12) & 0x0F | 0xE0))
                out.append(chr((cp >> 6) & 0x3F | 0x80))
            elif (cp & 0x0) == 0 and ((cp >> 16) & 0xFFE0) == 0:
                out.append(chr(((cp >> 18) & 0x07) | 0xF0))
                out.append(chr(((cp >> 12) & 0x3F) | 0x80))
                out.append(chr(((cp >> 6) & 0x3F) | 0x80))
            out.append(chr((cp & 0x3F) | 0x80))
        return "".join(out)

    @staticmethod
    def _custom_b64_encode(s: str) -> str:
        """Custom base64-like encoding matching JS y() function."""
        alpha = _BinanceCrypto._ALPHABET
        n = len(s)
        # pack bytes into 24-bit groups
        arr = [0] * ((n + 3) // 4)
        for i in range(n):
            arr[i // 4] |= (ord(s[i]) & 0xFF) << (24 - (i % 4) * 8)

        out = []
        i = 0
        while i < n:
            b0 = (arr[i // 4] >> (24 - (i % 4) * 8)) & 0xFF
            b1 = (arr[(i + 1) // 4] >> (24 - ((i + 1) % 4) * 8)) & 0xFF if (i + 1) < n else 0
            b2 = (arr[(i + 2) // 4] >> (24 - ((i + 2) % 4) * 8)) & 0xFF if (i + 2) < n else 0
            triple = (b0 << 16) | (b1 << 8) | b2
            for j in range(4):
                if i + j * 0.75 < n:
                    out.append(alpha[(triple >> (6 * (3 - j))) & 0x3F])
            i += 3
        pad = (4 - len(out) % 4) % 4
        out.extend("=" * pad)
        return "".join(out)

    @staticmethod
    def _custom_b64_decode(s: str) -> str:
        """Decode custom base64 to latin1 string (mirrors rY)."""
        pad = len(s) % 4
        if pad:
            s += "=" * (4 - pad)
        return base64.b64decode(s).decode("latin1")

    @staticmethod
    def _utf8_decode(s: str) -> str:
        """Decode UTF-8 bytes stored as latin1 chars to unicode (mirrors rZ)."""
        out = []
        i = 0
        while i < len(s):
            c = ord(s[i])
            if c < 128:
                out.append(chr(c))
                i += 1
            elif c > 191 and c < 224:
                c2 = ord(s[i + 1])
                out.append(chr(((c & 31) << 6) | (c2 & 63)))
                i += 2
            else:
                c2 = ord(s[i + 1])
                c3 = ord(s[i + 2])
                out.append(chr(((c & 15) << 12) | ((c2 & 63) << 6) | (c3 & 63)))
                i += 3
        return "".join(out)

    @staticmethod
    def encrypt(plaintext: str, key: str = "cdababcddcba") -> str:
        """Encrypt plaintext with XOR cipher + key derivation."""
        rev = key[::-1]
        derived = rev + _BinanceCrypto._derive_extra(rev, 4)
        # z() → UTF-16 → UTF-8 → XOR → custom-b64
        codepoints = _BinanceCrypto._to_utf16_units(plaintext)
        utf8 = _BinanceCrypto._encode_utf8(codepoints)
        xored = []
        for i, ch in enumerate(utf8):
            xored.append(chr(ord(ch) ^ ord(derived[i % len(derived)])))
        return _BinanceCrypto._custom_b64_encode("".join(xored))

    @staticmethod
    def decrypt(cipher_b64: str, key: str = "cdababcddcba") -> str:
        """Decrypt ciphertext (reverse of encrypt)."""
        rev = key[::-1]
        derived = rev + _BinanceCrypto._derive_extra(rev, 4)
        decoded = _BinanceCrypto._custom_b64_decode(cipher_b64)
        xored = []
        for i, ch in enumerate(decoded):
            xored.append(chr(ord(ch) ^ ord(derived[i % len(derived)])))
        return _BinanceCrypto._utf8_decode("".join(xored))

    @staticmethod
    def calculate_s(s: str) -> int:
        """Simple char-code checksum (Binance doesn't verify it)."""
        return sum(ord(c) for c in s)


# ---------------------------------------------------------------------------
# Fingerprint / device info generation
# ---------------------------------------------------------------------------

def _unflagged() -> int:
    """Generate an unflagged (odd) random value for browser checks."""
    return int(random.random() * 32) * 2 + 1


def generate_ev() -> dict:
    """Generate event fingerprint data."""
    return {
        "wd": _unflagged(),
        "im": _unflagged(),
        "de": "",
        "prde": ",".join(str(_unflagged()) for _ in range(4)),
        "brla": _unflagged(),
        "pl": "Win32",
        "wiinhe": 945,
        "wiouhe": "1032",
    }


def generate_device_id(user_agent: str) -> str:
    """Generate base64-encoded device info header."""
    device = {
        "screen_resolution": "1920,1080",
        "available_screen_resolution": "1920,1032",
        "system_version": "unknown",
        "brand_model": "unknown",
        "timezone": "",
        "web_timezone": "Europe/Berlin",
        "timezoneOffset": -120,
        "user_agent": user_agent,
        "list_plugin": ("PDF Viewer,Chrome PDF Viewer,Chromium PDF Viewer,"
                        "Microsoft Edge PDF Viewer,WebKit built-in PDF"),
        "platform": "Win32",
        "webgl_vendor": "unknown",
        "webgl_renderer": "unknown",
    }
    return base64.b64encode(
        json.dumps(device, separators=(",", ":")).encode()
    ).decode()


# ---------------------------------------------------------------------------
# Biometric mouse-movement generation
# ---------------------------------------------------------------------------

class _Point:
    __slots__ = ("x", "y")
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y


def _bezier_path(p0: _Point, p1: _Point, steps: int = 60) -> list[tuple[int, int]]:
    """Quadratic Bezier curve with random perpendicular offset."""
    mid_x = (p0.x + p1.x) / 2
    mid_y = (p0.y + p1.y) / 2
    dx = p1.x - p0.x
    dy = p1.y - p0.y
    length = hypot(dx, dy) or 1
    perp_x = -dy / length
    perp_y = dx / length
    offset = random.uniform(-120, 120)
    ctrl_x = mid_x + perp_x * offset
    ctrl_y = mid_y + perp_y * offset

    path = []
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * p0.x + 2 * (1 - t) * t * ctrl_x + t ** 2 * p1.x
        y = (1 - t) ** 2 * p0.y + 2 * (1 - t) * t * ctrl_y + t ** 2 * p1.y
        x += random.uniform(-0.5, 0.5)
        y += random.uniform(-0.5, 0.5)
        pos = (round(x), round(y))
        if not path or pos != path[-1]:
            path.append(pos)
    return path


def generate_mouse_movement(slide_distance: int) -> dict:
    """Generate human-like mouse movement data for the slide action.

    Returns dict matching Binance biometric format: ec (event counts),
    el (event log), th (thumb data).
    """
    # 3 anchor points: start, drag-end, release
    start = _Point(
        312 + random.randint(-100, 100),
        307 + random.randint(-20, 20),
    )
    end = _Point(
        757 + random.randint(-10, 10),
        354 + random.randint(-20, 20),
    )
    release = _Point(
        806 + random.randint(-10, 10),
        349 + random.randint(-20, 20),
    )

    mm_events = []
    mm_count = 0

    # Path start → end
    for pos in _bezier_path(start, end):
        if mm_count > 150:
            break
        mm_count += 1
        delay = random.randint(2, 5) if random.randint(1, 5) % 2 == 0 else 1
        mm_events.append(f"|mm|{pos[0]},{pos[1]}|{delay}|1")

    # Path end → release
    for pos in _bezier_path(end, release):
        if mm_count > 150:
            break
        mm_count += 1
        delay = random.randint(2, 5) if random.randint(1, 5) % 2 == 0 else 1
        mm_events.append(f"|mm|{pos[0]},{pos[1]}|{delay}|1")

    # Thumb micro-movement
    th_path = _bezier_path(
        _Point(44 + random.randint(-2, 2), 16 + random.randint(-2, 2)),
        _Point(29 + random.randint(-2, 2), 18 + random.randint(-2, 2)),
    )

    return {
        "ec": {
            "mm": random.randint(700, 800),
            "md": 1,
            "mu": 1,
        },
        "el": mm_events,
        "th": {
            "el": [f"mm|{p[0]},{p[1]}" for p in th_path[:30]],
            "si": {"w": 44, "h": 44},
        },
    }


def build_captcha_data(distance: int) -> dict:
    """Build the full fingerprint payload for Binance captcha submission.

    Combines event fingerprint + biometric mouse data + slide distance.
    """
    return {
        "ev": generate_ev(),
        "be": generate_mouse_movement(distance),
        "dist": distance,
        "imageWidth": "310",
    }


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


async def _download(client: httpx.AsyncClient, url: str) -> bytes:
    """Download image bytes from URL."""
    r = await client.get(url)
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

async def solve_binance(
    image_b64: str | None = None,
    image_url: str | None = None,
    biz_id: str = "register",
    security_check_response_validate_id: str = "",
    proxy: str | None = None,
    timeout_s: float = 60,
) -> dict:
    """Solve a Binance slide captcha.

    Two modes:
    - **Local mode** (default): pass `image_b64` or `image_url` — the
      combined slide image (piece + background). Returns the pixel offset
      in ``token`` as a string (e.g. ``"127"``).  This is the pure image
      solver path — no network calls to Binance.

    - **Full mode**: if neither image_b64 nor image_url is provided AND
      ``biz_id`` + ``security_check_response_validate_id`` are set, the
      solver fetches the captcha from Binance's API, solves the slide,
      encrypts the payload, and submits the validation. Returns the
      Binance captcha token.  (Requires ``curl_cffi`` for TLS
      fingerprinting.)

    Parameters
    ----------
    image_b64 : str, optional
        Base64-encoded combined slide image (piece|background).
    image_url : str, optional
        URL to download the combined slide image.
    biz_id : str
        Binance business context (``"register"``, ``"login"``, etc.).
    security_check_response_validate_id : str
        Validate ID from Binance's precheck request.
    proxy : str, optional
        HTTP/SOCKS5 proxy URL.
    timeout_s : float
        Overall timeout in seconds.

    Returns
    -------
    dict
        Uniform result: ``{solved, type, token, method, elapsed, error}``.
        In local mode, ``token`` is the pixel offset as a string.
    """
    t0 = time.monotonic()

    async def _run() -> dict:
        # --- Local mode: solve image directly ---
        if image_b64 or image_url:
            raw: bytes | None = None
            if image_b64:
                raw = base64.b64decode(image_b64)
            elif image_url:
                async with httpx.AsyncClient(
                    proxy=proxy, timeout=20, headers={"User-Agent": _UA}
                ) as cli:
                    raw = await _download(cli, image_url)
            if raw is None:
                return _result(error="no image provided")

            distance = solve_slide_from_bytes(raw)
            log.info("slide solved: distance=%d px", distance)
            return _result(
                solved=True,
                token=str(distance),
                method="binance_slide_local",
            )

        # --- Full mode: fetch captcha from Binance, solve, submit ---
        if not biz_id or not security_check_response_validate_id:
            return _result(
                error="full mode requires biz_id + security_check_response_validate_id "
                      "when no image is provided"
            )

        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            return _result(error="curl_cffi not installed — needed for full Binance API mode")

        ua = _UA.replace("149.0.0.0", "135.0.0.0")
        device_hdr = generate_device_id(ua)

        session = cffi_requests.Session(
            impersonate="chrome116",
            default_headers=0,
            verify=False,
        )

        # 1. Get captcha
        session.headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "bnc-uuid": "xxx",
            "cache-control": "no-cache",
            "captcha-sdk-version": "1.0.0",
            "clienttype": "web",
            "content-type": "text/plain; charset=UTF-8",
            "device-info": device_hdr,
            "fvideo-id": "xxx",
            "origin": "https://accounts.binance.com",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": ua,
            "x-captcha-se": "true",
        }

        get_payload = {
            "bizId": biz_id,
            "sv": "20220906",
            "lang": "en",
            "securityCheckResponseValidateId": security_check_response_validate_id,
            "clientType": "web",
        }

        resp = session.post(
            "https://accounts.binance.com/bapi/composite/v1/public/antibot/getCaptcha",
            data=get_payload,
        )
        captcha = resp.json()

        if "data" not in captcha:
            return _result(error=f"getCaptcha failed: {captcha}")

        data = captcha["data"]
        captcha_type = data.get("captchaType", "")
        if captcha_type != "SLIDE":
            return _result(error=f"unsupported captcha type: {captcha_type} (only SLIDE)")

        # 2. Solve slide
        img_url = "https://bin.bnbstatic.com" + data["path2"]
        async with httpx.AsyncClient(
            proxy=proxy, timeout=20, headers={"User-Agent": ua}
        ) as cli:
            img_bytes = await _download(cli, img_url)

        distance = solve_slide_from_bytes(img_bytes)
        log.info("slide solved: distance=%d px", distance)

        # 3. Build + encrypt payload
        fp_data = build_captcha_data(distance)
        fp_json = json.dumps(fp_data, separators=(",", ":"))
        encrypted = _BinanceCrypto.encrypt(fp_json, data["ek"])

        # 4. Validate
        time.sleep(0.5)  # human-like delay
        sig = data["sig"]
        salt = data.get("salt", "")
        s_val = _BinanceCrypto.calculate_s(biz_id + sig + encrypted + salt)

        validate_payload = {
            "bizId": biz_id,
            "sv": "20220906",
            "lang": "en",
            "securityCheckResponseValidateId": security_check_response_validate_id,
            "clientType": "web",
            "data": encrypted,
            "s": str(s_val),
            "sig": sig,
        }

        resp2 = session.post(
            "https://accounts.binance.com/bapi/composite/v1/public/antibot/validateCaptcha",
            data=validate_payload,
        )
        result = resp2.json()

        if "data" in result and "token" in result["data"]:
            return _result(
                solved=True,
                token=result["data"]["token"],
                method="binance_slide_full",
            )
        return _result(error=f"validateCaptcha failed: {result}")

    try:
        out = await asyncio.wait_for(_run(), timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        return _result(
            method="binance",
            elapsed=round(time.monotonic() - t0, 1),
            error=f"binance solve timed out after {timeout_s}s",
        )
    except Exception as exc:
        log.warning("binance solver failed: %s", exc)
        return _result(
            method="binance",
            elapsed=round(time.monotonic() - t0, 1),
            error=str(exc).splitlines()[0][:200],
        )
    out["elapsed"] = round(time.monotonic() - t0, 1)
    return out
