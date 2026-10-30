"""Friendly Captcha **v2** solver — pure-HTTP protocol replay + BLAKE2s PoW (no browser).

v1 lives in `solvers/friendly/solve.py` (api.friendlycaptcha.com/api/v1/puzzle).
v2 is a different protocol: global.frcapi.com, compressed telemetry `signals`,
an activate → quote → (PoW) → redeem handshake, and 36 sub-puzzles hashed by the
widget's own permutation (not plain blake2b of the puzzle buffer).

Flow (ported from the @Real_zezomod v2 reference implementation):

  1. GET  {base}/api/v2/captcha/agent?origin=..&sess_id=..&agent_id=..
     → HTML carrying window.SOLVE_CONTEXT_BASE64 / _NONCE / _TIMESTAMP
  2. GET  {base}/api/v2/captcha/widget?..&sitekey=SK
  3. POST {base}/api/v2/captcha/activate   {sitekey, widget_id, ts, signals(i=2), solve_context}
  4. POST {base}/api/v2/captcha/quote      {…, activation}    → quote + cost
  5. node pow_engine.js  cost/solveCtxTs/nonce → "puzzle.solutions.diag"
  6. POST {base}/api/v2/captcha/redeem     {…, payment.entries[0].submissions[]}
     → data.redeem_token  == the value the host page puts in `frc-captcha-response`.

Difficulty is server-assigned from IP reputation: a clean IP gets cost 5M–20M
(1–3 s on all cores), a hammered IP gets 900M (~noninteractive mode). Pass a
`proxy` to keep the cost tier low; without one, repeated runs on the same IP
escalate.

Signals are the widget's own telemetry JSON, gzip+base64url packed, with the
sequence counter `root.i` advancing 2 → 4 and `pnow`/`ts` kept monotonic.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import os
import random
import re
import string
import time
from pathlib import Path

log = logging.getLogger(__name__)

BASE_URL = "https://global.frcapi.com"
DEMO_SITEKEY = "FCMGEMUD2LR7ICLH"
_HERE = Path(__file__).resolve().parent
_SIGNALS = _HERE / "signals_template.json"
_POW = _HERE / "pow_engine.js"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _rand(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def _pack(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(gzip.compress(raw)).decode()


def _session(proxy: str | None, timeout: float):
    from curl_cffi.requests import AsyncSession

    kwargs: dict = {"impersonate": "chrome", "timeout": timeout}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return AsyncSession(**kwargs)


def _fail(error: str, t0: float, **extra) -> dict:
    return {"solved": False, "token": "", "error": str(error)[:300],
            "method": "frc-v2-pow", "elapsed": round(time.monotonic() - t0, 1),
            **extra}


async def _pow(cost: int, solve_ctx_ts: int, nonce: int, solve_mode: int,
               timeout_s: float) -> dict:
    """Run the node multi-core BLAKE2s engine; returns its JSON (submissionString)."""
    payload = json.dumps({"cost": cost, "solveCtxTs": solve_ctx_ts,
                          "nonce": nonce, "solveMode": solve_mode}).encode()
    proc = await asyncio.create_subprocess_exec(
        "node", str(_POW), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"PoW engine exceeded {timeout_s:.0f}s (cost={cost})")
    if proc.returncode != 0:
        raise RuntimeError(f"pow_engine.js rc={proc.returncode}: "
                           f"{(err or b'').decode(errors='replace')[:160]}")
    return json.loads((out or b"").decode())


async def solve_friendly_v2(sitekey: str = DEMO_SITEKEY, origin: str | None = None,
                            proxy: str | None = None, timeout_s: int = 90,
                            base_url: str = BASE_URL,
                            submit_to_demo: bool = False) -> dict:
    """Solve Friendly Captcha v2 for `sitekey`; `origin` = page origin (defaults to API host)."""
    t0 = time.monotonic()
    timeout_s = max(15, int(timeout_s or 90))
    sitekey = (sitekey or DEMO_SITEKEY).strip()
    base = (base_url or BASE_URL).rstrip("/")
    page_origin = (origin or base).rstrip("/")
    try:
        from urllib.parse import quote as _q
    except Exception:                                            # pragma: no cover
        _q = lambda s: s

    def _hdr(extra: dict | None = None) -> dict:
        h = {"User-Agent": _UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
             "Origin": page_origin, "Referer": page_origin + "/"}
        if extra:
            h.update(extra)
        return h

    sess_id = _rand(12)
    agent_id = "a_" + _rand(12)
    widget_id = "w_" + _rand(12)
    pto_agent = int(time.time() * 1000)
    pto_widget = pto_agent + random.randint(30, 80)
    pnow0 = 105.0

    try:
        template = json.loads(_SIGNALS.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(f"signals template unreadable: {e}", t0)

    async with _session(proxy, min(60, timeout_s)) as cli:
        try:
            # 1. agent iframe (carries the solve context + nonce)
            agent_url = (f"{base}/api/v2/captcha/agent?origin={_q(page_origin)}"
                         f"&sess_id={sess_id}&sess_c=1&comm_id={agent_id}&sdk_v=1.1.1&v=1"
                         f"&agent_id={agent_id}&ts={pto_agent + int(pnow0)}")
            r = await cli.get(agent_url, headers=_hdr())
            if r.status_code != 200:
                return _fail(f"agent iframe HTTP {r.status_code}: {r.text[:120]}", t0)
            html = r.text

            # 2. widget iframe
            widget_url = (f"{base}/api/v2/captcha/widget?origin={_q(page_origin)}"
                          f"&sess_id={sess_id}&sess_c=2&comm_id={widget_id}&sdk_v=1.1.1&v=1"
                          f"&agent_id={agent_id}&lang=en&sitekey={sitekey}"
                          f"&ts={pto_agent + int(pnow0) + 5}")
            rw = await cli.get(widget_url, headers=_hdr())
            if rw.status_code != 200:
                return _fail(f"widget iframe HTTP {rw.status_code}: {rw.text[:120]}", t0)

            sc_m = re.search(r'window\.SOLVE_CONTEXT_BASE64\s*=\s*"([^"]+)"', html)
            nonce_m = re.search(r"window\.SOLVE_CONTEXT_NONCE\s*=\s*([0-9]+)", html)
            ts_m = re.search(r"window\.SOLVE_CONTEXT_TIMESTAMP\s*=\s*([0-9]+)", html)
            if not (sc_m and nonce_m and ts_m):
                return _fail("solve context missing from agent iframe "
                             "(sitekey invalid / blocked / origin rejected)", t0)
            solve_context, nonce, ts_ctx = sc_m.group(1), int(nonce_m.group(1)), int(ts_m.group(1))

            sig = json.loads(json.dumps(template))
            sig["nonce"] = nonce
            sig["root"]["sid"] = sess_id
            sig["root"]["wid"] = widget_id
            sig["widget"]["wid"] = widget_id

            def _sync(i: int, pnow: float, solve_duration_ms: int = 0) -> str:
                """Advance the telemetry clock to sequence i and pack it."""
                ts = pto_agent + int(pnow)
                sig["root"]["i"] = i
                sig["root"]["t"]["pto"] = pto_agent
                sig["root"]["t"]["pnow"] = int(pnow)
                sig["root"]["t"]["ts"] = ts
                if "mm" in sig["root"]:
                    sig["root"]["mm"]["ts"] = int(pnow - 80 if i == 2 else pnow - 40)
                sig["widget"]["t"]["pto"] = pto_widget
                sig["widget"]["t"]["pnow"] = int(pnow - (pto_widget - pto_agent))
                sig["widget"]["t"]["ts"] = sig["widget"]["t"]["pto"] + sig["widget"]["t"]["pnow"]
                if "mm" in sig["widget"]:
                    sig["widget"]["mm"]["ts"] = int(sig["widget"]["t"]["pnow"] - 50)
                if sig.get("trigger"):
                    sig["trigger"]["a_pnow"] = int(pnow)
                    sig["trigger"]["t_pnow"] = int(pnow)
                return _pack(sig)

            # 3. activate (i = 2)
            act_pnow = 1250.0
            act_ts = pto_agent + int(act_pnow)
            r = await cli.post(f"{base}/api/v2/captcha/activate",
                               json={"sitekey": sitekey, "widget_id": widget_id,
                                     "widget_announce": pto_agent + 100, "ts": act_ts,
                                     "signals": _sync(2, act_pnow), "solve_context": solve_context,
                                     "guard_context": ""},
                               headers=_hdr({"Content-Type": "application/json"}))
            body = r.json() if r.status_code == 200 else {}
            if not body.get("success"):
                return _fail(f"activate rejected HTTP {r.status_code}: {r.text[:160]}", t0)
            activation = body["data"]["activation"]

            # 4. quote
            q_pnow = act_pnow + 350.0
            q_ts = pto_agent + int(q_pnow)
            r = await cli.post(f"{base}/api/v2/captcha/quote",
                               json={"sitekey": sitekey, "widget_id": widget_id,
                                     "widget_announce": pto_agent + 100, "ts": q_ts,
                                     "signals": _sync(2, q_pnow), "solve_context": solve_context,
                                     "activation": activation},
                               headers=_hdr({"Content-Type": "application/json"}))
            body = r.json() if r.status_code == 200 else {}
            if not body.get("success"):
                return _fail(f"quote rejected HTTP {r.status_code}: {r.text[:160]}", t0)
            quote = body["data"]["quote"]
            details = body["data"].get("details") or {}
            cost = int(details.get("cost") or 0)
            solve_mode = int(details.get("solveMode") or details.get("mode") or 2)

            # 5. PoW
            pow_budget = max(5.0, timeout_s - (time.monotonic() - t0) - 10)
            pow_out = await _pow(cost, ts_ctx, nonce, solve_mode, pow_budget)
            submission = pow_out["submissionString"]
            solve_ms = int(pow_out.get("solveDuration") or 0)

            # 6. redeem (i = 4)
            rd_pnow = q_pnow + solve_ms + 50.0
            rd_ts = pto_agent + int(rd_pnow)
            r = await cli.post(f"{base}/api/v2/captcha/redeem",
                               json={"sitekey": sitekey, "solve_context": solve_context,
                                     "activation": activation, "quote": quote,
                                     "payment": {"entries": [{"solve_context": solve_context,
                                                              "submissions": [submission]}]},
                                     "signals": _sync(4, rd_pnow), "widget_id": widget_id,
                                     "solve_duration_wall": solve_ms,
                                     "solve_duration_accumulated": solve_ms,
                                     "ts": rd_ts},
                               headers=_hdr({"Content-Type": "application/json"}))
            body = r.json() if r.status_code == 200 else {}
            if not body.get("success"):
                return _fail(f"redeem rejected HTTP {r.status_code}: {r.text[:160]}", t0,
                             cost=cost)
            token = (body.get("data") or {}).get("redeem_token") or ""
            if not token:
                return _fail("redeem ok but no redeem_token", t0, cost=cost)

            out = {"solved": True, "token": token, "method": "frc-v2-pow",
                   "elapsed": round(time.monotonic() - t0, 1), "cost": cost,
                   "solve_ms": solve_ms, "sitekey": sitekey}

            if submit_to_demo:
                rd = await cli.post(f"{base}/api/v2/captcha/demo",
                                    data={"comment": "solver",
                                          "frc-captcha-response": token},
                                    headers=_hdr({"Content-Type":
                                                  "application/x-www-form-urlencoded"}))
                txt = rd.text or ""
                out["demo_verified"] = ("Verification successful" in txt) or ("✅" in txt)
            return out
        except Exception as e:                                   # never raise out
            log.warning("friendly_v2 failed: %s", e)
            return _fail(f"{type(e).__name__}: {e}", t0, cost=locals().get("cost") or 0)


async def solve(*args, **kwargs) -> dict:  # pragma: no cover - dispatch alias
    return await solve_friendly_v2(*args, **kwargs)


if __name__ == "__main__":  # quick manual run: python -m solvers.friendly_v2.solve
    import sys

    sk = sys.argv[1] if len(sys.argv) > 1 else DEMO_SITEKEY
    print(asyncio.run(solve_friendly_v2(sk, submit_to_demo=(sk == DEMO_SITEKEY))))
