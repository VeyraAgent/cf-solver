"""Local engine sweep: POST every advertised type to a running cf-solver and
classify the outcome.

    SOLVED      a real token/cookie came back
    DISPATCHED  reached the right solver, target input missing (no sitekey/URL/image)
    REJECTED    payload validation refused it (400) — wiring fine
    TIMEOUT     408 from the server deadline
    BROKEN      5xx or transport error → a real bug

Usage:
    python tools/engine_sweep.py                       # all types
    python tools/engine_sweep.py recaptcha hcaptcha    # a subset
Env:
    SOLVER_URL   default http://127.0.0.1:8877

Exit 0 when nothing is BROKEN. Result also written to /tmp/engine_sweep.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.type_matrix import PAYLOADS  # noqa: E402

BASE = os.getenv("SOLVER_URL", "http://127.0.0.1:8877")
PNG_1PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
           "IQAAAABJRU5ErkJggg==")

# types added after the original matrix (2026-10-31 jb drop)
EXTRA = {
    "friendly_v2": {"sitekey": "FCMGEMUD2LR7ICLH"},
    "awswaf_js": {"url": "https://www.turnitin.com/login_page.asp?lang=en_us"},
    "ocr": {"image_b64": PNG_1PX},
    "aliyun_traceless": {"url": "https://example.com/login"},
    "tcaptcha": {"mode": "detect", "challenge_json": {}},
}


def classify(status: int, data: dict) -> str:
    if status >= 500:
        return "BROKEN"
    if status == 408:
        return "TIMEOUT"
    if status == 400:
        return "REJECTED"
    if data.get("solved") or data.get("token") or data.get("success"):
        return "SOLVED"
    return "DISPATCHED"


def main() -> int:
    types = json.loads(urllib.request.urlopen(BASE + "/health", timeout=30).read())["supported_types"]
    only = sys.argv[1:]
    rows = []
    for t in types:
        if only and t not in only:
            continue
        payload = dict(PAYLOADS.get(t) or EXTRA.get(t) or {})
        payload["type"] = t
        payload.setdefault("timeout_s", 20)
        data, status, err = {}, 0, None
        t0 = time.time()
        try:
            req = urllib.request.Request(BASE + "/solve", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=75) as r:
                status = r.status
                data = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                data = json.loads(e.read() or b"{}")
            except Exception:  # noqa: BLE001
                data = {}
        except Exception as e:  # noqa: BLE001
            status, err = -1, f"{type(e).__name__}: {e}"
        verdict = classify(status, data) if status > 0 else "BROKEN"
        rows.append({"type": t, "verdict": verdict, "status": status,
                     "elapsed": round(time.time() - t0, 1),
                     "error": err or (data.get("error") if isinstance(data, dict) else None)})
        print(f"{verdict:11} {t:22} {status:4} {rows[-1]['elapsed']:6.1f}s  "
              f"{str(rows[-1]['error'])[:90]}", flush=True)

    bad = [r for r in rows if r["verdict"] == "BROKEN"]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("\n=== summary ===", json.dumps(counts, sort_keys=True))
    if bad:
        print("BROKEN:", [(r["type"], r["status"], str(r["error"])[:120]) for r in bad])
    json.dump(rows, open("/tmp/engine_sweep.json", "w"), indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
