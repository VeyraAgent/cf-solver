"""Prosoco Procaptcha PoW solver — pure-HTTP (twickets.live proven protocol).

Procaptcha (prosopo.io, prosopo/captcha) is a Polkadot-signed PoW captcha.
Flow (ported 1:1 from chris-period/procaptcha-pow — protocol RE'd on
twickets.live, MIT-referenced):

  1. GET page_url                          → html → page_tags fingerprint (128-bit
                                             bloom-ish bits via computeThing)
  2. POST {node}.prosopo.io/v1/prosopo/provider/client/captcha/frictionless
     {token(RSA+AES encrypted), headHash(encrypted), dapp=site_key, user,
      mode:"visible", currentUrl}          → {sessionId, captchaType:"pow"}
  3. POST .../provider/client/captcha/pow  {dapp, sessionId, user}
                                           → {challenge, difficulty, timestamp,
                                              signature{provider{challenge}}, status:"ok"}
  4. solve: sha256(str(nonce) + challenge) hex starts with `difficulty` zeros
     (session.Pow.checkPrefix — note the nonce is PREPENDED, unlike anubis)
  5. sign(challenge.timestamp) with an SR25519 keypair derived from
     blake2b(visitor_id,16) → BIP39 mnemonic → mini-secret → keypair
  6. POST .../provider/client/pow/solution {challenge, difficulty, nonce,
     salt(click-positions hex), behavioralData(RSA+AES), signature{user{timestamp:0x..},
     provider{challenge}}, user, dapp}
  7. build the final widget token: 0x-encoded compact-int struct (encode_solution)

Requires: substrate-interface (SR25519 Keypair), pycryptodome, bip39, mnemonic,
curl_cffi. Credits: chris-period/procaptcha-pow, xKiian/Prosopo, prosopo/captcha.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import re
import secrets
import time
from typing import Any, Optional

log = logging.getLogger("procaptcha")

# ── page fingerprint (page_tags.py port) ──────────────────────────────
def _page_tags(html: str) -> list[str]:
    onum: list[str] = []
    r = re.sub(r"\s+", " ", html).strip()
    tag_re = re.compile(r"<(\w+)([^>]*)>")
    attr_re = re.compile(r'(\w+)=["\']([^"\']+)["\']')
    for f in tag_re.finditer(r):
        s = f.group(1).lower()
        if s not in ["meta", "link", "script"]:
            onum.append(f"tag:{s}")
        for i in attr_re.finditer(f.group(2)):
            k = i.group(1).lower()
            val = i.group(2)
            onum.append(f"attr:{k}")
            if k in ["charset", "name", "property", "rel", "type", "content", "href", "src"]:
                onum.append(f"{k}:{val}")
                if k in ["href", "src"]:
                    onum.extend([f"{k}:{val}"] * 2)
    for a in re.finditer(r">([^<]+)<", r):
        s = a.group(1).strip()
        if 0 < len(s) < 200:
            for w in re.split(r"\s+", s):
                if len(w) > 2:
                    onum.extend([f"word:{w.lower()}"] * 6)
    tags = [t.lower() for t in re.findall(r"<(\w+)", r)]
    for i in range(len(tags) - 1):
        onum.append(f"2gram:{tags[i]},{tags[i + 1]}")
    return onum


def _u0(s: str) -> int:
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def compute_page_bits(html: str, bits: int = 128) -> str:
    """computeThing — 128-char 0/1 fingerprint of the page's tag/attr/text features."""
    c = _page_tags(html)
    if not c:
        return "0" * bits
    f = [0] * bits
    for a in c:
        d = _u0(a)
        for s in range(bits):
            bit = (_u0(f"{d}_{s}") >> (s % 32)) & 1
            f[s] += 1 if bit == 1 else -1
    return "".join("1" if x >= 0 else "0" for x in f)


# ── RSA+AES encrypted payloads (gen_token.py port) ────────────────────
_PUB = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA6H8lw79+zANM3BGqMFgN"
    "L7ZaBpOVJAC+8UTpbmrX5+xA7fgGjiDfnO5wKfxUfxMDTA0bvO7MDI0V1l6zOGQO"
    "YDi4FDy+4FZO+UPVz7rJ85ecJTkAW3E3ZImnOFlN2ZBuOuZobAbMIRyCcXDmXRgI"
    "HGGX2nEgcx53oPKv/rQkiCbaHOjycBP1KofSZ/7JaZdBxoSGEuozQefEeE3YfiOx"
    "0M0rOXEXICOCG3xLvFy5gPlKSioPIEYhqHASF9CtU4RasrhFCUbCThaz+Bh8m+ZP"
    "LJ7LIpbK9iOZb4tzsldY0LZ+z5VW+ESKtB4fkbIb1Aemkb/Ta3uKTHsC3qgrWR1/"
    "GQIDAQAB"
)


def _encrypt_text(plain: str) -> str:
    from Crypto.Cipher import AES, PKCS1_OAEP
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA
    from Crypto.Random import get_random_bytes

    rsa_key = RSA.import_key(base64.b64decode(_PUB))
    aes_key = get_random_bytes(32)
    cipher_aes = AES.new(aes_key, AES.MODE_GCM, nonce=get_random_bytes(12))
    ciphertext, tag = cipher_aes.encrypt_and_digest(plain.encode())
    encrypted_key = PKCS1_OAEP.new(rsa_key, hashAlgo=SHA256).encrypt(aes_key)
    return json.dumps({
        "key": base64.b64encode(encrypted_key).decode(),
        "data": base64.b64encode(ciphertext + tag).decode(),
        "iv": base64.b64encode(cipher_aes.nonce).decode(),
    })


def generate_behavior_data() -> str:
    """Mouse collector payload (behaviorData.py — 3 moves + click burst)."""
    base_ts = int(time.time() * 1000)
    c1 = []
    px = random.uniform(500, 800)
    py = random.uniform(300, 400)
    ts = base_ts
    dx = random.choice([-1, 1]) * random.uniform(3, 8)
    dy = random.choice([-1, 1]) * random.uniform(3, 8)
    for _ in range(3):
        px += dx + random.gauss(0, 2)
        py += dy + random.gauss(0, 2)
        ts += random.randint(30, 120)
        c1.append({"x": round(px), "y": round(py), "timestamp": ts,
                   "eventType": "mousemove"})
    cx = 0 if random.random() < 0.3 else round(random.uniform(500, 800))
    cy = round(random.uniform(300, 400))
    ct = base_ts - random.randint(3000, 5000)
    c3 = []
    for ev, d in (("mousedown", 0), ("mouseup", random.randint(50, 150)),
                  ("click", random.randint(50, 150))):
        c3.append({"x": cx, "y": cy, "timestamp": ct + d, "eventType": ev,
                   "button": 0, "targetElement": "TWICKETS-BUY-OVERVIEW",
                   "ctrlKey": False, "shiftKey": False, "altKey": False})
    return _encrypt_text(json.dumps({
        "collector1": c1, "collector2": [], "collector3": c3,
        "deviceCapability": "desktop"}))


def generate_salt() -> str:
    """Click-position hex blob (891,616 = procaptcha box click coords)."""
    def hash_hex(r: str, t: list[int]) -> str:
        e = list(r.removeprefix("0x"))
        n, s = 2, len(e) - 1
        e[:2] = f"{len(t):02x}"
        pos, size, used = [], [], 0
        for x in t:
            h = f"{x:x}"
            d = s - len(h) + 1
            e[d:d + len(h)] = h
            pos.append(d); size.append(len(h)); s -= len(h); used += len(h)
        for p, l in zip(pos, size):
            used += 4
            if used > len(e):
                raise ValueError("Hex data exceeds string length")
            e[n:n + 4] = f"{p:02x}{l:02x}"
            n += 4
        return "0x" + "".join(e)
    return hash_hex(secrets.token_hex(14), [891, 616])


def _hash_ua(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:32]


def generate_token(user_addr: str, user_agent: str) -> str:
    rand_float = min(random.random() * 0.3, 1)
    details = (f"{user_addr}|{0.4294846358501722 * rand_float**2 + 3.0 * rand_float + 1.0}"
               f"|{_hash_ua(user_agent)}|0|0|0000000000")
    a = secrets.randbits(16) % 2001
    token = json.dumps([int(time.time() * 1000), details, a],
                       indent=None, separators=(",", ":"))
    return _encrypt_text(token)


def generate_html_hash(contents: str) -> str:
    return _encrypt_text(contents)


# ── solution struct (gen_solution.py port) ────────────────────────────
def _encode_compact_int(n: int) -> bytes:
    if n < (1 << 6):
        return ((n << 2) | 0).to_bytes(1, "little")
    if n < (1 << 14):
        return ((n << 2) | 1).to_bytes(2, "little")
    if n < (1 << 30):
        return ((n << 2) | 2).to_bytes(4, "little")
    b = n.to_bytes((n.bit_length() + 7) // 8, "little")
    return bytes([((len(b) - 4) << 2) | 3]) + b


def _encode_str(s: str) -> bytes:
    b = s.encode()
    return _encode_compact_int(len(b)) + b


def encode_solution(prosopo_url: str, site_key: str, user_key: str,
                    challenge_str: str, provider: str, signature: str,
                    timestamp: str, nonce: int) -> str:
    enc = b""
    enc += b"\x00"                                   # commitment_id = None
    enc += b"\x01" + _encode_str(prosopo_url)
    enc += _encode_str(site_key)
    enc += _encode_str(user_key)
    enc += b"\x01" + _encode_str(challenge_str)
    enc += b"\x01" + nonce.to_bytes(4, "little")
    enc += _encode_str(timestamp)
    sig = b""
    sig += b"\x01" + _encode_str(provider)
    sig += b"\x00"                                   # provider_request_hash = None
    sig += b"\x01" + _encode_str(signature)
    sig += b"\x00"                                   # user_request_hash = None
    return "0x" + (enc + sig).hex()


# ── SR25519 keypair from visitor_id (polka.py port) ───────────────────
class Polka:
    def __init__(self, visitor_id: str):
        self.visitor_id = visitor_id
        self.keypair = None

    def create_account(self):
        from substrateinterface import Keypair
        import bip39 as _bip39
        import mnemonic as _mnemonic

        digest = hashlib.blake2b(self.visitor_id.encode(),
                                 digest_size=16, key=b"").hexdigest().encode()
        seed_phrase = _mnemonic.Mnemonic(language="english").to_mnemonic(digest)
        mini_secret = _bip39.bip39_to_mini_secret(seed_phrase, "")
        self.keypair = Keypair.create_from_seed(
            seed_hex=mini_secret.hex(), crypto_type=1)   # SR25519

    def address(self) -> str:
        return self.keypair.ss58_address

    def sign(self, timestamp: str) -> str:
        return bytes(self.keypair.sign(timestamp.encode())).hex()


# ── PoW (session.Pow.checkPrefix — nonce PREPENDED to challenge) ──────
def solve_pow(challenge: str, difficulty: int,
              max_nonce: int = 100_000_000) -> tuple[int, str]:
    target = "0" * difficulty
    nonce = 0
    while nonce < max_nonce:
        digest = hashlib.sha256((str(nonce) + challenge).encode()).hexdigest()
        if digest.startswith(target):
            return nonce, digest
        nonce += 1
    raise RuntimeError(f"procaptcha: nonce space exhausted (difficulty={difficulty})")


# ── main entry ────────────────────────────────────────────────────────
async def solve_procaptcha(page_url: str, site_key: str,
                           visitor_id: Optional[str] = None,
                           node: str = "pronode7",
                           proxy: Optional[str] = None,
                           timeout_s: int = 90,
                           user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                                             "Chrome/143.0.0.0 Safari/537.36") -> dict:
    """Full frictionless PoW solve for a Procaptcha-protected page.

    Returns {solved, type:"procaptcha", token (0x solution struct), method,
    elapsed, error, nonce, session_id}.
    """
    t0 = time.monotonic()
    result: dict[str, Any] = {"type": "procaptcha", "solved": False, "token": "",
                              "method": "pow+sr25519", "error": None}

    if not page_url or not site_key:
        result["error"] = "procaptcha: page_url and site_key are required"
        return result

    visitor_id = visitor_id or ("%020x" % random.randrange(16 ** 20))
    base_url = f"https://{node}.prosopo.io/v1/prosopo"

    def _headers() -> dict:
        return {
            "accept": "*/*", "content-type": "application/json",
            "origin": page_url.split("/")[0] + "//" + page_url.split("/")[2]
                      if page_url.startswith("http") else "https://www.twickets.live",
            "referer": page_url,
            "user-agent": user_agent,
        }

    def _run() -> dict:
        from curl_cffi import requests as cr

        polka = Polka(visitor_id)
        polka.create_account()
        user_key = polka.address()

        s = cr.Session(impersonate="chrome124", proxy=proxy)

        # 1. page contents → fingerprint
        r = s.get(page_url, headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "user-agent": user_agent}, timeout=25)
        if r.status_code != 200:
            return {"solved": False, "error": f"page HTTP {r.status_code}"}
        page_bits = compute_page_bits(r.text)

        # 2. frictionless session
        r = s.post(f"{base_url}/provider/client/captcha/frictionless", headers=_headers(),
                   json={"token": generate_token(user_key, user_agent),
                         "headHash": generate_html_hash(page_bits),
                         "dapp": site_key, "user": user_key,
                         "mode": "visible", "currentUrl": page_url}, timeout=25)
        body = r.json()
        if body.get("captchaType") != "pow":
            return {"solved": False, "error": f"unexpected captchaType: {str(body)[:140]}"}
        session_id = body.get("sessionId")

        # 3. challenge
        r = s.post(f"{base_url}/provider/client/captcha/pow", headers=_headers(),
                   json={"dapp": site_key, "sessionId": session_id, "user": user_key},
                   timeout=25)
        chal = r.json()
        if chal.get("status") != "ok":
            return {"solved": False, "error": f"bad challenge: {str(chal)[:140]}"}

        # 4. PoW
        nonce, digest = solve_pow(chal["challenge"], int(chal["difficulty"]))
        log.info("procaptcha: nonce=%d hash=%s…", nonce, digest[:16])

        # 5. sign timestamp
        signature = polka.sign(chal["timestamp"])

        # 6. submit solution
        r = s.post(f"{base_url}/provider/client/pow/solution", headers=_headers(),
                   json={"challenge": chal["challenge"], "difficulty": chal["difficulty"],
                         "signature": {"user": {"timestamp": "0x" + signature},
                                       "provider": {"challenge": chal["signature"]["provider"]["challenge"]}},
                         "user": user_key, "dapp": site_key, "nonce": nonce,
                         "salt": generate_salt(), "behavioralData": generate_behavior_data()},
                   timeout=25)
        out = r.json() if r.status_code == 200 else {}
        if not (out.get("status") == "ok" or r.status_code == 200):
            return {"solved": False, "error": f"solution HTTP {r.status_code}: {r.text[:140]}"}

        # 7. widget token struct
        solution = encode_solution(
            prosopo_url=f"https://{base_url.split('/')[2]}",
            site_key=site_key, user_key=user_key,
            challenge_str=chal["challenge"],
            provider=chal["signature"]["provider"]["challenge"],
            signature=signature, timestamp=chal["timestamp"], nonce=nonce)
        return {"solved": True, "token": solution, "nonce": nonce,
                "session_id": session_id, "verify": out}

    try:
        out = await asyncio.wait_for(asyncio.to_thread(_run),
                                     timeout=max(timeout_s, 30))
    except asyncio.TimeoutError:
        out = {"solved": False, "error": f"procaptcha: exceeded {timeout_s}s"}
    except Exception as e:
        out = {"solved": False, "error": f"procaptcha: {type(e).__name__}: {str(e)[:140]}"}

    result.update(out)
    result["elapsed"] = round(time.monotonic() - t0, 1)
    return result
