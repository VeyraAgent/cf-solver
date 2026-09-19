"""Yidun ir-sdk request-body builder — Python port of the self-contained half of
CodeEmpower/yidun-silder's encrypt.js (build_request_body path only).

The /v3/get + /v3/check crypto params (cb / data / encryptValidate) depend on
the obfuscated vendor webpack chunk and are NOT ported here — they run verbatim
through jsbridge.py. This module ports everything encrypt.js implements in the
clear: CRC32, S-box substitution, the operation-sequence byte permutations,
the custom Base64 alphabet, the SHA-256-style padding and the TLV device
fingerprint encoding used to build the irToken upload body
(POST https://ir-sdk.dun.163.com/v4/j/up -> data.tk).

Ported 1:1 from encrypt.js by CodeEmpower/yidun-silder (protocol research of
NetEase Yidun captcha 2.28.5) — see README for credits. Deterministic under an
injected random.Random, which makes the byte core unit-testable against node
vectors.
"""
from __future__ import annotations

import random
import struct
import time

# ── Constants (verbatim from encrypt.js) ─────────────────────────────

ENCRYPT_KEY_HEX = "fd6a43ae25f74398b61c03c83be37449"

SBOX_HEX = (
    "a7be3f3933fa8c5fcf86c4b6908b569ba1e26c1a6d7cfbf6"
    "0ae4b00e074a194dac4b73e7f898541159a39d08183b76eedee3ed341e6685d2"
    "357440158394b1ff03a9004cbbb5ca7dcb7f41489a16e03dcc9c71eb3c979668"
    "5b1d01b4d56193a6e1f1a2470445c191ae49c5d82765dc82c350f263387a24a5"
    "02fcbf442e2dddaad0e936d9ea22b89275307b42518fbc3a626ba806d4ecd6d7"
    "25f50cc8c72fefa4551ccd6fc9b2b7ab954f815c7264c6e51f4eaf99885a7989"
    "2b1b60a0b3526e57ba5d178d370958847eb9fd28f9ce0bc023f4148a2adfe632"
    "126769057043d3bd8eda0df7872629f3809ef05310e83113216afe202c460fc2"
    "3e789f77d1addb5e"
)

OP_SEQUENCE = "037606da0296055c"

CUSTOM_B64_ALPHABET = "MB.CfHUzEeJpsuGkgNwhqiSaI4Fd9L6jYKZAxn1/Vml0c5rbXRP+8tD3QTO2vWyo"
CUSTOM_B64_PADDING = "7"

_UUID_PATTERN = "xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx"

_DEFAULT_SDK_VERSION = "2.0.13_yanzhengma"
_DEFAULT_VERSION_KEY = "d44593ca"

_SBOX = list(bytes.fromhex(SBOX_HEX))
_CRC32_TABLE: list[int] = []
for _i in range(256):
    _c = _i
    for _j in range(8):
        _c = (0xEDB88320 ^ (_c >> 1)) if (_c & 1) else (_c >> 1)
    _CRC32_TABLE.append(_c)


# ── Byte helpers ─────────────────────────────────────────────────────

def _clamp_byte(n: int) -> int:
    return n & 0xFF


def _int_to_4bytes_be(n: int) -> list[int]:
    return [(n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF]


def _int_to_2bytes_be(n: int) -> list[int]:
    return [(n >> 8) & 0xFF, n & 0xFF]


def _right_align(value_bytes: list[int], max_len: int) -> list[int]:
    return value_bytes[-max_len:]


# ── CRC32 ────────────────────────────────────────────────────────────

def crc32_hex(data) -> str:
    """Reflected CRC32 (poly 0xEDB88320) over bytes, returned as 8 hex chars."""
    crc = 0xFFFFFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC32_TABLE[(crc ^ b) & 0xFF]
    crc = 0xFFFFFFFF ^ crc
    return "".join(f"{b:02x}" for b in _int_to_4bytes_be(crc))


# ── S-box / operation sequence ───────────────────────────────────────

def sbox_substitute(data: list[int]) -> list[int]:
    return [_SBOX[16 * ((b >> 4) & 0x0F) + (b & 0x0F)] for b in data]


def _op_xor_increment(data: list[int], start: int) -> list[int]:
    out, t = [], _clamp_byte(start)
    for b in data:
        out.append(_clamp_byte(b) ^ t)
        t = (t + 1) & 0xFF
    return out


def _op_add_decrement(data: list[int], start: int) -> list[int]:
    out, t = [], _clamp_byte(start)
    for b in data:
        out.append((b + t) & 0xFF)
        t = (t - 1) & 0xFF
    return out


def _op_add_constant(data: list[int], constant: int) -> list[int]:
    t = _clamp_byte(constant)
    return [(b + t) & 0xFF for b in data]


def _op_xor_decrement(data: list[int], start: int) -> list[int]:
    out, t = [], _clamp_byte(start)
    for b in data:
        out.append(_clamp_byte(b) ^ t)
        t = (t - 1) & 0xFF
    return out


_OP_FUNCS = {
    0x03: _op_xor_increment,
    0x06: _op_add_decrement,
    0x02: _op_add_constant,
    0x05: _op_xor_decrement,
}


def apply_operations(data: list[int]) -> list[int]:
    result = list(data)
    for i in range(0, len(OP_SEQUENCE), 4):
        func_idx = int(OP_SEQUENCE[i:i + 2], 16)
        operand = int(OP_SEQUENCE[i + 2:i + 4], 16)
        func = _OP_FUNCS.get(func_idx)
        if func:
            result = func(result, operand)
    return result


# ── Array mixing / key expansion ─────────────────────────────────────

def _xor_arrays(a: list[int], b: list[int]) -> list[int]:
    if not a:
        return []
    if not b:
        return list(a)
    return [_clamp_byte(a[i]) ^ _clamp_byte(b[i % len(b)]) for i in range(len(a))]


def _add_mod256_arrays(a: list[int], b: list[int]) -> list[int]:
    if not a:
        return []
    if not b:
        return list(a)
    return [(a[i] + b[i % len(b)]) & 0xFF for i in range(len(a))]


def _expand_to_64(key: list[int]) -> list[int]:
    if not key:
        return [0] * 64
    if len(key) >= 64:
        return list(key[:64])
    return [key[j % len(key)] for j in range(64)]


# ── Custom Base64 ────────────────────────────────────────────────────

def custom_b64_encode(data) -> str:
    """Custom-alphabet Base64 with '7' padding (encrypt.js custom_base64_encode)."""
    if not data:
        return ""
    alphabet, pad = CUSTOM_B64_ALPHABET, CUSTOM_B64_PADDING
    out: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        a = data[i]
        b = data[i + 1] if i + 1 < n else 0
        c = data[i + 2] if i + 2 < n else 0
        chunk_len = min(3, n - i)
        if chunk_len == 3:
            out.append(alphabet[(a >> 2) & 0x3F])
            out.append(alphabet[((a << 4) & 0x30) | ((b >> 4) & 0x0F)])
            out.append(alphabet[((b << 2) & 0x3C) | ((c >> 6) & 0x03)])
            out.append(alphabet[c & 0x3F])
        elif chunk_len == 2:
            out.append(alphabet[(a >> 2) & 0x3F])
            out.append(alphabet[((a << 4) & 0x30) | ((b >> 4) & 0x0F)])
            out.append(alphabet[(b << 2) & 0x3C])
            out.append(pad)
        else:  # 1
            out.append(alphabet[(a >> 2) & 0x3F])
            out.append(alphabet[(a << 4) & 0x30])
            out.append(pad)
            out.append(pad)
        i += 3
    return "".join(out)


# ── SHA-256-style padding (encrypt.js sha256_pad) ────────────────────

def _sha256_pad(data: list[int]) -> list[int]:
    length = len(data)
    padded = list(data)
    if length % 64 <= 60:
        pad_count = 64 - length % 64 - 4
    else:
        pad_count = 128 - length % 64 - 4
    padded.extend([0] * pad_count)
    padded.extend(_int_to_4bytes_be(length))
    return padded


# ── Core encrypt_d ───────────────────────────────────────────────────

def encrypt_d(input_bytes, random_bytes: list[int] | None = None,
              rng: random.Random | None = None) -> str:
    """Port of encrypt.js encrypt_d — the ir-sdk body `d` field.

    Pass `random_bytes` (4 bytes) for a fully deterministic output; otherwise
    4 bytes are drawn from `rng` (or OS randomness).
    """
    rng = rng or random.SystemRandom()
    if random_bytes is None:
        random_bytes = [rng.randrange(256) for _ in range(4)]

    key_bytes = [ord(c) for c in ENCRYPT_KEY_HEX]
    expanded_key = _expand_to_64(key_bytes)
    expanded_random = _expand_to_64(list(random_bytes))
    derived_key = _expand_to_64(_xor_arrays(expanded_key, expanded_random))

    crc = crc32_hex(input_bytes)
    crc_bytes = [ord(c) for c in crc]

    data_with_crc = list(input_bytes) + crc_bytes
    padded = _sha256_pad(data_with_crc)
    blocks = [padded[i:i + 64] for i in range(0, len(padded), 64)]

    output = list(random_bytes)
    state = list(derived_key)

    for block in blocks:
        transformed = apply_operations(block)
        xored = _xor_arrays(transformed, derived_key)
        added = _add_mod256_arrays(xored, state)
        combined = _xor_arrays(added, state)
        state = sbox_substitute(sbox_substitute(combined))
        output.extend(state)

    return custom_b64_encode(output)


# ── TLV encoding ─────────────────────────────────────────────────────

def _tlv_encode_string(tag: int, value: str, max_len: int) -> list[int]:
    value_bytes = _right_align([ord(c) for c in value], max_len)
    return _int_to_2bytes_be(tag) + _int_to_2bytes_be(len(value_bytes)) + value_bytes


def _tlv_encode_int(tag: int, value: int, max_len: int) -> list[int]:
    value_bytes = _right_align(_int_to_4bytes_be(value), max_len)
    return _int_to_2bytes_be(tag) + _int_to_2bytes_be(len(value_bytes)) + value_bytes


def _tlv_encode_bool(tag: int, value: bool, max_len: int = 1) -> list[int]:
    value_bytes = _right_align(_int_to_4bytes_be(1 if value else 2), max_len)
    return _int_to_2bytes_be(tag) + _int_to_2bytes_be(len(value_bytes)) + value_bytes


def _tlv_encode_hex(tag: int, hex_str: str, max_len: int) -> list[int]:
    value_bytes = _right_align(list(bytes.fromhex(hex_str)), max_len)
    return _int_to_2bytes_be(tag) + _int_to_2bytes_be(len(value_bytes)) + value_bytes


def _tlv_encode_array(tag: int, values: list[int], lengths: list[int]) -> list[int]:
    result: list[int] = []
    for i, v in enumerate(values):
        result.extend(_right_align(_int_to_4bytes_be(v), lengths[i]))
    return _int_to_2bytes_be(tag) + _int_to_2bytes_be(len(result)) + result


# ── Fingerprint data (verbatim entries from encrypt.js) ──────────────

def generate_uuid(rng: random.Random | None = None) -> str:
    rng = rng or random.SystemRandom()
    out = []
    for c in _UUID_PATTERN:
        if c == "x":
            out.append(f"{rng.randrange(16):x}")
        elif c == "y":
            out.append(f"{(rng.randrange(16) & 0x3) | 0x8:x}")
        else:
            out.append(c)
    return "".join(out)


def _random_hex(length: int, rng: random.Random) -> str:
    return "".join(f"{rng.randrange(256):02x}" for _ in range(length))


def build_fingerprint_data(app_id: str, rng: random.Random,
                           sdk_version: str = _DEFAULT_SDK_VERSION,
                           version_key: str = _DEFAULT_VERSION_KEY,
                           session_id: str | None = None,
                           access_info: str = "init:1-gts:1",
                           online_times: int = 1,
                           enc_device_id: str | None = None,
                           enc_device_status: int = 200,
                           collect_duration: int | None = None,
                           visit_duration: int | None = None,
                           intranet_ip: str | None = None,
                           storage_usage_kb: int | None = None,
                           behavior_counts: dict | None = None,
                           now_ms: int | None = None) -> list[int]:
    """Port of encrypt.js build_fingerprint_data (same tags, same order)."""
    behavior_counts = behavior_counts or {}
    session_id = session_id or generate_uuid(rng)
    enc_device_id = enc_device_id or generate_uuid(rng)
    collect_duration = collect_duration if collect_duration is not None else rng.randrange(100, 3000)
    visit_duration = visit_duration if visit_duration is not None else rng.randrange(1000, 30000)
    if not intranet_ip:
        intranet_ip = f"192.168.{rng.randrange(1, 255)}.{rng.randrange(1, 255)}"
    storage_usage_kb = storage_usage_kb if storage_usage_kb is not None else rng.randrange(100, 50000)

    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
    lang = "zh-CN"
    platform_str = "Win32"
    timestamp_ms = str(now_ms if now_ms is not None else int(time.time() * 1000))
    canvas_hash = _random_hex(16, rng)
    webgl_hash = _random_hex(16, rng)
    webgl_debug = "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"
    font_list = ("Arial,Calibri,Cambria,Consolas,Courier New,Georgia,Impact,"
                 "Tahoma,Times New Roman,Trebuchet MS,Verdana")
    audio_fp = "124.04347527516074,-100"
    webgl_vendor = "Google Inc. (NVIDIA)"
    webgl_renderer = "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER)"
    timezone = "Asia/Shanghai"
    conn_info = "4g"
    permissions_data = "geolocation=granted,notifications=granted"
    screen_data = "1920,1080,1920,1040,24"
    murmur_hash = _random_hex(16, rng)

    entries: list[list[int]] = []

    # Navigator / browser attributes
    entries.append(_tlv_encode_string(200, ua, 400))
    entries.append(_tlv_encode_string(201, lang, 20))
    entries.append(_tlv_encode_int(202, 5, 1))
    entries.append(_tlv_encode_int(203, 8, 1))
    entries.append(_tlv_encode_int(206, 0, 1))
    entries.append(_tlv_encode_bool(207, True))
    entries.append(_tlv_encode_bool(208, True))
    entries.append(_tlv_encode_bool(209, True))
    entries.append(_tlv_encode_bool(210, True))
    entries.append(_tlv_encode_bool(211, True))
    # Platform / hardware
    entries.append(_tlv_encode_string(213, platform_str, 10))
    entries.append(_tlv_encode_string(214, "8", 15))
    # Canvas / WebGL fingerprint hashes
    entries.append(_tlv_encode_hex(216, canvas_hash, 16))
    entries.append(_tlv_encode_hex(217, webgl_hash, 16))
    # Capability detection
    entries.append(_tlv_encode_bool(218, True))
    entries.append(_tlv_encode_bool(223, True))
    entries.append(_tlv_encode_int(225, 0, 1))
    entries.append(_tlv_encode_bool(228, True))
    entries.append(_tlv_encode_bool(229, True))
    # Fonts / connection / canvas data
    entries.append(_tlv_encode_string(233, font_list, 400))
    entries.append(_tlv_encode_string(234, conn_info, 64))
    entries.append(_tlv_encode_string(238, "", 40))
    entries.append(_tlv_encode_string(239, timezone, 20))
    # Screen info
    entries.append(_tlv_encode_array(242, [1920, 1080, 1920, 1040], [2, 2, 2, 2]))
    entries.append(_tlv_encode_int(243, 24, 1))
    # Other browser attributes
    entries.append(_tlv_encode_bool(250, False))
    entries.append(_tlv_encode_int(251, 0, 1))
    entries.append(_tlv_encode_string(252, "Google Inc.", 100))
    entries.append(_tlv_encode_string(253, "5.0 (Windows)", 30))
    entries.append(_tlv_encode_int(254, 1, 1))
    entries.append(_tlv_encode_string(255, webgl_vendor, 20))
    entries.append(_tlv_encode_string(257, webgl_renderer, 20))
    entries.append(_tlv_encode_int(258, 20030107, 1))
    entries.append(_tlv_encode_int(260, 0, 4))
    entries.append(_tlv_encode_int(261, 0, 1))
    entries.append(_tlv_encode_string(262, "Google Inc.", 30))
    entries.append(_tlv_encode_array(263, [1] * 8, [1] * 8))
    entries.append(_tlv_encode_int(264, 0, 1))
    # Audio fingerprint
    entries.append(_tlv_encode_string(265, audio_fp, 400))
    entries.append(_tlv_encode_int(267, 0, 1))
    # Hash fields
    entries.append(_tlv_encode_hex(273, _random_hex(16, rng), 16))
    entries.append(_tlv_encode_string(279, "0", 5))
    entries.append(_tlv_encode_int(280, 0, 1))
    # WebGL debug
    entries.append(_tlv_encode_string(283, webgl_debug, 500))
    entries.append(_tlv_encode_string(284, audio_fp, 400))
    # Window / screen
    entries.append(_tlv_encode_string(500, screen_data, 100))
    entries.append(_tlv_encode_int(501, 0, 1))
    entries.append(_tlv_encode_array(502, [1, 1, 1920, 1080], [1, 1, 4, 4]))
    entries.append(_tlv_encode_string(503, "", 32))
    entries.append(_tlv_encode_string(505, "1", 3))
    entries.append(_tlv_encode_bool(506, True))
    entries.append(_tlv_encode_array(508, [0, 0], [4, 4]))
    entries.append(_tlv_encode_string(509, "5.0 (Windows)", 30))
    entries.append(_tlv_encode_string(510, "Win32", 15))
    entries.append(_tlv_encode_string(511, "", 32))
    entries.append(_tlv_encode_bool(512, True))
    entries.append(_tlv_encode_string(513, "", 100))
    # Permissions
    entries.append(_tlv_encode_string(700, permissions_data, 200))
    entries.append(_tlv_encode_array(713, [0, 0], [4, 4]))
    # Window dimension features
    entries.append(_tlv_encode_string(800, "1920", 8))
    entries.append(_tlv_encode_string(801, "1080", 8))
    entries.append(_tlv_encode_string(802, "1920", 8))
    entries.append(_tlv_encode_string(803, "1040", 8))
    entries.append(_tlv_encode_string(804, "2", 8))
    # Hash / special fields
    entries.append(_tlv_encode_hex(902, _random_hex(16, rng), 16))
    entries.append(_tlv_encode_hex(904, _random_hex(16, rng), 16))
    entries.append(_tlv_encode_hex(900, murmur_hash, 16))
    entries.append(_tlv_encode_string(901, "", 200))
    entries.append(_tlv_encode_bool(911, True))
    entries.append(_tlv_encode_int(912, 0, 4))
    entries.append(_tlv_encode_int(913, 0, 4))
    entries.append(_tlv_encode_string(914, "", 100))
    entries.append(_tlv_encode_string(922, "", 100))
    entries.append(_tlv_encode_string(963, "", 400))
    entries.append(_tlv_encode_int(964, 0, 1))

    # Vr collection (14 entries)
    entries.append(_tlv_encode_string(2, app_id, 32))
    entries.append(_tlv_encode_string(3, "", 32))
    entries.append(_tlv_encode_string(4, sdk_version, 20))
    entries.append(_tlv_encode_string(5, session_id, 32))
    entries.append(_tlv_encode_string(6, timestamp_ms, 16))
    entries.append(_tlv_encode_int(515, collect_duration, 4))
    entries.append(_tlv_encode_int(516, visit_duration, 4))
    entries.append(_tlv_encode_string(121, access_info, 32))
    entries.append(_tlv_encode_string(910, intranet_ip, 400))
    entries.append(_tlv_encode_int(278, storage_usage_kb, 4))
    entries.append(_tlv_encode_string(3006, enc_device_id, 400))
    entries.append(_tlv_encode_string(3007, session_id, 400))
    entries.append(_tlv_encode_int(971, enc_device_status, 4))
    entries.append(_tlv_encode_int(972, online_times, 4))

    # Wr collection (13 entries)
    entries.append(_tlv_encode_int(110, behavior_counts.get("click", 0), 2))
    entries.append(_tlv_encode_int(111, behavior_counts.get("keydown", 0), 2))
    entries.append(_tlv_encode_int(112, behavior_counts.get("touchstart", 0), 2))
    entries.append(_tlv_encode_int(113, behavior_counts.get("touchmove", 0), 2))
    entries.append(_tlv_encode_int(114, behavior_counts.get("touchend", 0), 2))
    entries.append(_tlv_encode_int(115, behavior_counts.get("mousedown", 0), 2))
    entries.append(_tlv_encode_int(116, behavior_counts.get("mouseup", 0), 2))
    entries.append(_tlv_encode_int(117, behavior_counts.get("wheel", 0), 2))
    entries.append(_tlv_encode_int(118, behavior_counts.get("scroll", 0), 2))
    entries.append(_tlv_encode_int(119, behavior_counts.get("pointerdown", 0), 2))
    entries.append(_tlv_encode_int(120, behavior_counts.get("pointerup", 0), 2))
    entries.append(_tlv_encode_int(967, behavior_counts.get("trusted_keydown", 0), 2))
    entries.append(_tlv_encode_int(968, behavior_counts.get("untrusted_keydown", 0), 2))

    # Fisher-Yates shuffle (same algorithm as encrypt.js)
    shuffled = list(entries)
    i = len(shuffled)
    while i > 1:
        r = rng.randrange(i)
        i -= 1
        shuffled[i], shuffled[r] = shuffled[r], shuffled[i]

    result: list[int] = []
    for entry in shuffled:
        result.extend(entry)
    return result


def build_request_body(app_id: str, rng: random.Random | None = None,
                       behavior_counts: dict | None = None,
                       now_ms: int | None = None) -> dict:
    """Port of encrypt.js build_request_body — the POST body for
    https://ir-sdk.dun.163.com/v4/j/up. Deterministic under `rng`."""
    rng = rng or random.SystemRandom()
    nonce = generate_uuid(rng)
    input_data = build_fingerprint_data(app_id, rng, behavior_counts=behavior_counts,
                                        now_ms=now_ms)
    d = encrypt_d(input_data, rng=rng)
    return {
        "p": app_id,
        "v": _DEFAULT_SDK_VERSION,
        "vk": _DEFAULT_VERSION_KEY,
        "n": nonce,
        "d": d,
    }
