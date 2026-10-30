#!/usr/bin/env python3
"""Klien solver API eksternal (gaya 2captcha-compatible). TANPA KREDENSIAL.

Semua rahasia dari environment — tidak ada nilai default, tidak di-echo:
  CAPTCHA_API_KEY    (wajib)  API key operator
  CAPTCHA_API_URL    (opsional) default https://2captcha.com
  CAPTCHA_PAGE_URL   (wajib untuk tipe interaktif)
  CAPTCHA_SITEKEY    (wajib untuk tipe interaktif)

Pakai:
  export CAPTCHA_API_KEY=...
  python solve_api.py --image captcha.png                    # captcha gambar
  python solve_api.py --type recaptcha_v2 --sitekey 0x... --page-url https://TARGET/
Dependencies: requests.
"""
import argparse
import base64
import os
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("[!] requests tidak terpasang: pip install requests")

DEFAULT_TIMEOUT = 120
POLL_INTERVAL = 5
BACKOFF_MAX = 30


def require_env(name: str, cli_value: str | None = None) -> str:
    val = cli_value or os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"[!] {name} belum di-set" + (" (via flag atau env)" if cli_value is None else ""))
    return val


def masked(key: str) -> str:
    """Representasi aman untuk log — JANGAN pernah print key asli."""
    return f"<key len={len(key)}>"


class Client:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/")
        self.key = key
        self.s = requests.Session()

    def _get(self, path: str, **params) -> dict:
        params["key"] = self.key
        r = self.s.get(f"{self.base}/{path}", params=params, timeout=30)
        r.raise_for_status()
        txt = r.text.strip()
        if txt.startswith("ERROR"):
            raise RuntimeError(txt)
        if "|" in txt:
            rid, extra = txt.split("|", 1)
            return {"id": rid, "text": extra}
        return {"text": txt}

    def submit_image(self, b64: str, soft_id: int = 0) -> str:
        resp = self._get("in.php", method="base64", body=b64,
                         json=1, soft_id=soft_id)
        return resp["id"]

    def submit_interactive(self, sitekey: str, page_url: str) -> str:
        method = {"recaptcha_v2": "userrecaptcha",
                  "recaptcha_v3": "userrecaptcha",
                  "hcaptcha": "hcaptcha",
                  "turnstile": "turnstile"}[ARGS.type]
        extra = {}
        if ARGS.type == "recaptcha_v3":
            extra = {"version": "v3", "action": ARGS.action, "min_score": ARGS.min_score}
        if ARGS.type == "turnstile":
            extra = {"data-sitekey": sitekey, "domain": page_url}
        resp = self._get("in.php", method=method, googlekey=sitekey,
                         pageurl=page_url, json=1, **extra)
        return resp["id"]

    def poll(self, rid: str, timeout: int) -> str:
        deadline = time.time() + timeout
        delay = POLL_INTERVAL
        while time.time() < deadline:
            time.sleep(delay)
            delay = min(delay * 1.5, BACKOFF_MAX)
            try:
                resp = self._get("res.php", action="get", id=rid, json=1)
            except RuntimeError as e:
                if str(e) == "ERROR_CAPTCHA_UNSOLVABLE":
                    raise
                if str(e) == "ERROR_NO_SLOT_AVAILABLE":
                    continue  # backoff sudah naik
                raise
            if resp.get("text") == "CAPCHA_NOT_READY":
                continue
            return resp["text"]
        raise TimeoutError(f"token {rid} belum selesai dalam {timeout}s")


def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="path captcha gambar")
    ap.add_argument("--type", default="image",
                    choices=["image", "recaptcha_v2", "recaptcha_v3", "hcaptcha", "turnstile"])
    ap.add_argument("--sitekey", default=None)
    ap.add_argument("--page-url", default=None)
    ap.add_argument("--action", default="verify", help="reCAPTCHA v3 action")
    ap.add_argument("--min-score", type=float, default=0.3)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ARGS = ap.parse_args()

    key = require_env("CAPTCHA_API_KEY")
    base = os.environ.get("CAPTCHA_API_URL", "https://2captcha.com")
    client = Client(base, key)
    print(f"[*] endpoint={base} auth={masked(key)}", file=sys.stderr)

    try:
        if ARGS.type == "image":
            if not ARGS.image:
                sys.exit("[!] --image wajib untuk tipe image")
            b64 = base64.b64encode(open(ARGS.image, "rb").read()).decode()
            rid = client.submit_image(b64)
        else:
            sitekey = require_env("CAPTCHA_SITEKEY", ARGS.sitekey)
            page_url = require_env("CAPTCHA_PAGE_URL", ARGS.page_url)
            rid = client.submit_interactive(sitekey, page_url)
        print(f"[*] submitted id={rid}", file=sys.stderr)
        token = client.poll(rid, ARGS.timeout)
    except (RuntimeError, TimeoutError) as e:
        sys.exit(f"[!] solver gagal: {e}")

    print(token)  # hanya token hasil solve — bukan kredensial


if __name__ == "__main__":
    main()
