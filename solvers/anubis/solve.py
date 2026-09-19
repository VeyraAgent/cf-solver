"""Anubis PoW solver — pure-HTTP (classic) + browser harvest (v1.28+).

Anubis (https://anubis.techaro.lol) is the proof-of-work anti-bot gate used by
kernel.org, GNOME, Forgejo, distro infra and many more.

Two protocol generations:

* CLASSIC (<= v1.17 — embedded hex challenge or make-challenge API):
    GET page → <script id="anubis_challenge"> {"rules":{"algorithm":
    "fast"|"preact","difficulty":N},"challenge":"<hex>"} (old builds wrap the
    challenge as {id,challenge}), or GET /.within.website/x/cmd/anubis/api/
    make-challenge → {challenge:{id,challenge}, rules}.
    Proof: sha256(challenge + str(nonce)) with `difficulty` leading HEX zeros
    ("fast") — or challenge hashed first then nonce appended ("preact",
    midstate). pow-buster compute_mask_anubis == difficulty*4 zero bits.
    Redeem: GET /.within.website/x/cmd/anubis/api/pass-challenge
    ?response=<hash>&nonce=<n>&redir=<url>&elapsedTime=<ms> → 302 +
    Set-Cookie within.website-x-cmd-anubis-cookie.

* v1.28+ (verified live 2026-09): the descriptor's challenge object carries
  `randomData` and the client redeems with a single `challenge=<proof>`
  parameter (no response/nonce) — an iterated proof not yet ported here.
  Those deployments are solved by letting the REAL embedded client run its
  ~5s PoW in our stealth browser and harvesting the auth cookie (same
  page-level pattern as the cloudflare/awswaf solvers).

Credits: TecharoHQ/anubis, sleeyax/anubis-solver (Go),
999Diogo/anubis_challenge_pow_solver, eternal-flame-AD/pow-buster (Rust).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlencode

log = logging.getLogger("anubis")


# ── PoW cores ─────────────────────────────────────────────────────────
def solve_pow(challenge: str, difficulty: int, algorithm: str = "sha256",
              max_nonce: int = 100_000_000) -> tuple[int, str]:
    """Return (nonce, hash_hex) for a CLASSIC Anubis challenge.

    NEW (algorithm="sha256"): difficulty = leading zero BITS.
    OLD (algorithm="fast"/"preact"): difficulty = leading zero HEX chars
    (== difficulty*4 zero bits — pow-buster compute_mask_anubis equivalence).
    """
    if algorithm == "preact":
        base = hashlib.sha256(challenge.encode())  # midstate source
        target = "0" * difficulty
        nonce = 0
        while nonce < max_nonce:
            h = base.copy()
            h.update(str(nonce).encode())
            digest = h.hexdigest()
            if digest.startswith(target):
                return nonce, digest
            nonce += 1
        raise RuntimeError(f"anubis: nonce space exhausted (difficulty={difficulty})")

    if algorithm == "sha256":
        nonce = 0
        while nonce < max_nonce:
            d = hashlib.sha256((challenge + str(nonce)).encode()).digest()
            if int.from_bytes(d, "big") >> (256 - difficulty) == 0:
                return nonce, d.hex()
            nonce += 1
        raise RuntimeError(f"anubis: nonce space exhausted (difficulty={difficulty})")

    # fast — hex-zero prefix
    target = "0" * difficulty
    nonce = 0
    while nonce < max_nonce:
        digest = hashlib.sha256((challenge + str(nonce)).encode()).hexdigest()
        if digest.startswith(target):
            return nonce, digest
        nonce += 1
    raise RuntimeError(f"anubis: nonce space exhausted (difficulty={difficulty})")


_CHALLENGE_SCRIPT = re.compile(
    r'<script[^>]+id="anubis_challenge"[^>]*>(.*?)</script>', re.S)


def extract_embedded_challenge(html: str) -> Optional[dict]:
    m = _CHALLENGE_SCRIPT.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return None


# ── browser harvest (v1.28+) ──────────────────────────────────────────
async def _browser_harvest(url: str, timeout_s: int) -> dict:
    """Let the REAL embedded client run its PoW and harvest the auth cookie."""
    import cloakbrowser
    t0 = time.monotonic()
    try:
        async with await cloakbrowser.launch_async(headless=True, humanize=True) as browser:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(url, wait_until="load", timeout=45000)
            auth = None
            deadline = time.monotonic() + max(timeout_s - 5, 15)
            while time.monotonic() < deadline:
                for c in await ctx.cookies():
                    if "anubis-auth" in (c.get("name") or ""):
                        auth = c
                        break
                if auth or "anubis" not in (page.url or ""):
                    break
                await asyncio.sleep(1)
            if not auth:
                return {"solved": False,
                        "error": "anubis: browser harvest — no auth cookie before deadline"}
            return {"solved": True, "token": auth["value"],
                    "cookie_name": auth["name"], "method": "browser-harvest",
                    "elapsed": round(time.monotonic() - t0, 1)}
    except Exception as e:
        return {"solved": False,
                "error": f"anubis: browser harvest failed: {type(e).__name__}: {str(e)[:120]}"}


# ── main entry ────────────────────────────────────────────────────────
async def solve_anubis(url: Optional[str] = None,
                       challenge: Optional[str] = None,
                       difficulty: Optional[int] = None,
                       algorithm: str = "sha256",
                       proxy: Optional[str] = None,
                       timeout_s: int = 90,
                       allow_browser: bool = True) -> dict:
    """Solve an Anubis gate.

    Pass `url` (protected site — fetch challenge, solve, redeem, return the
    auth cookie) or explicit `challenge` + `difficulty` (+ algorithm) for a
    bare classic proof. v1.28+ deployments fall back to browser harvest
    (allow_browser, default on).
    """
    t0 = time.monotonic()
    result: dict[str, Any] = {"type": "anubis", "solved": False, "token": "",
                              "method": "sha256-pow", "error": None}

    import httpx

    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) "
                             "Gecko/20100101 Firefox/132.0"}

    # ── 0. v1.28+ probe → browser harvest ────────────────────────────
    if url and allow_browser and not challenge:
        try:
            probe = await httpx.AsyncClient(timeout=20, proxy=proxy,
                                            headers=headers).get(url)
            emb = extract_embedded_challenge(probe.text) or {}
            ch_obj = emb.get("challenge") or {}
            if isinstance(ch_obj, dict) and ch_obj.get("randomData"):
                log.info("anubis: v1.28+ protocol detected → browser harvest")
                r = await _browser_harvest(url, timeout_s)
                r.setdefault("type", "anubis")
                return r
        except Exception as e:
            log.info("anubis: probe failed (%s) — continuing classic path", str(e)[:60])

    challenge_id: Optional[str] = None

    async with httpx.AsyncClient(timeout=25, proxy=proxy, headers=headers,
                                 follow_redirects=False) as cli:
        # ── 1. fetch challenge (page embed or API) ────────────────────
        if url and (not challenge or difficulty is None):
            try:
                resp = await cli.get(url)
                html = resp.text
            except Exception as e:
                result["error"] = f"anubis: page fetch failed: {type(e).__name__}: {str(e)[:120]}"
                return result

            embedded = extract_embedded_challenge(html)
            if embedded:
                rules = embedded.get("rules", {})
                ch = embedded.get("challenge", "")
                if isinstance(ch, dict):  # id-wrapped classic build
                    challenge_id = ch.get("id")
                    ch = (ch.get("challenge") or ch.get("randomData") or "")
                challenge = ch
                difficulty = int(rules.get("difficulty", 4))
                algorithm = rules.get("algorithm", "fast")
            else:
                api = url.rstrip("/") + "/.within.website/x/cmd/anubis/api/make-challenge"
                try:
                    resp = await cli.get(api, params={"url": url})
                    body = resp.json()
                except Exception as e:
                    result["error"] = f"anubis: make-challenge failed: {type(e).__name__}: {str(e)[:120]}"
                    return result
                ch = body.get("challenge", {})
                challenge = ch.get("challenge", "")
                challenge_id = ch.get("id")
                rules = body.get("rules", {})
                difficulty = int(rules.get("difficulty", 4))
                algorithm = rules.get("algorithm", "fast")

            if not challenge or not difficulty:
                result["error"] = "anubis: no challenge found on page (already passed?)"
                return result

        if not challenge or not difficulty:
            result["error"] = "anubis: pass url or challenge+difficulty"
            return result

        log.info("anubis: challenge=%s… difficulty=%d algorithm=%s",
                 str(challenge)[:16], difficulty, algorithm)

        # ── 2. solve PoW ─────────────────────────────────────────────
        try:
            nonce, digest = await asyncio.wait_for(
                asyncio.to_thread(solve_pow, challenge, difficulty, algorithm),
                timeout=max(timeout_s - 5, 10))
        except RuntimeError as e:
            result["error"] = str(e)
            return result
        except asyncio.TimeoutError:
            result["error"] = f"anubis: PoW exceeded {timeout_s}s (difficulty={difficulty})"
            return result
        log.info("anubis: nonce=%d hash=%s…", nonce, digest[:16])

        result["nonce"] = nonce
        result["hash"] = digest

        # ── 3. redeem (pass-challenge) ───────────────────────────────
        if url:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            base = url.rstrip("/")
            qs = urlencode({"response": digest, "nonce": nonce,
                            "redir": url, "elapsedTime": elapsed_ms})
            if challenge_id:
                qs = f"id={challenge_id}&{qs}"
            redir = f"{base}/.within.website/x/cmd/anubis/api/pass-challenge?{qs}"
            try:
                resp = await cli.get(redir)
                cookies = {c.name: c.value for c in resp.cookies.jar}
                auth = (cookies.get("within.website-x-cmd-anubis-cookie")
                        or ("anubis-auth" in resp.headers.get("set-cookie", "")))
                if resp.status_code in (200, 302) and auth:
                    result["solved"] = True
                    result["token"] = auth if isinstance(auth, str) else cookies
                    result["method"] = "sha256-pow+pass-challenge"
                    result["cookies"] = cookies
                elif allow_browser:
                    # classic redeem rejected — likely a newer deployment; harvest
                    log.info("anubis: classic redeem rejected (%s) → browser harvest",
                             resp.status_code)
                    r = await _browser_harvest(url, timeout_s)
                    r.setdefault("type", "anubis")
                    return r
                else:
                    result["error"] = (f"pass-challenge HTTP {resp.status_code} — "
                                       f"site may use a challenge variant we don't cover")
                    result["token"] = nonce
            except Exception as e:
                result["error"] = f"pass-challenge failed: {type(e).__name__}: {str(e)[:120]}"
                result["token"] = nonce
        else:
            result["solved"] = True
            result["token"] = nonce
            result["method"] = "sha256-pow"

        result["elapsed"] = round(time.monotonic() - t0, 1)
        return result
