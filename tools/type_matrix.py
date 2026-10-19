#!/usr/bin/env python3
"""Type coverage matrix for the cf-solver engine + the VeyraCaptcha API.

For every advertised captcha type it fires one real request and classifies the
outcome:

  SOLVED     - a real token/cookie came back (live E2E proof)
  DISPATCHED - the request reached the right solver, but the target is missing
               (no real sitekey / URL / image / clean IP) -> the wiring is fine
  REJECTED   - the route validated the payload and refused it (400) -> wiring fine
  BROKEN     - 5xx or an unhandled error -> real bug, needs a fix

Phase 1 hits the engine directly (native payload contract).
Phase 2 hits the VeyraCaptcha API with the 2captcha-style task names, which is
the path a customer actually uses.

Usage:
  python3 tools/type_matrix.py --engine https://... --api https://... --out matrix.md
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ENGINE = "https://tumbalkuncuy--cf-solver-solver.us-east.modal.run"
API = "https://tumbalkuncuy--veyracaptcha-api.modal.run"
TOKEN = "sol_a83770ad98467ce83ac6559604e36bc0"
API_KEY = "veyra_test_key"

# public test keys / demo targets
TS_KEY = "1x00000000000000000000AA"          # Cloudflare Turnstile "always pass"
HC_KEY = "10000000-ffff-ffff-ffff-000000000001"  # hCaptcha test key
RC_KEY = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"  # reCAPTCHA v2 test key
PNG_1PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
           "IQAAAABJRU5ErkJggg==")

TURNSTILE = {"sitekey": TS_KEY, "url": "https://demo.turnstile.workers.dev"}
HCAPTCHA = {"sitekey": HC_KEY, "url": "https://accounts.hcaptcha.com/demo"}
RECAPTCHA = {"version": "v2", "sitekey": RC_KEY,
              "url": "https://recaptcha-demo.appspot.com/recaptcha-v2-checkbox"}
IMAGE = {"image_b64": PNG_1PX, "body": PNG_1PX}
IMAGE_ACT = dict(IMAGE, action="click the cat")

# native payload per type (best effort: public key where one exists)
PAYLOADS: dict[str, dict] = {
    "turnstile": dict(TURNSTILE),
    "recaptcha": dict(RECAPTCHA),
    "hcaptcha": dict(HCAPTCHA),
    "hcaptcha_enterprise": dict(HCAPTCHA),
    "cloudflare": {"url": "https://nowsecure.nl"},
    "cloudflare_challenge": {"url": "https://nowsecure.nl"},
    "awswaf": {"url": "https://example.com"},
    "datadome": {"url": "https://example.com", "captcha_data": ""},
    "perimeterx": {"url": "https://example.com"},
    "akamai": {"url": "https://example.com"},
    "botguard": {"url": "https://accounts.google.com", "email": "t@example.com"},
    "geetest": {"gt": "", "challenge": "", "url": "https://example.com"},
    "geetest_v3": {"url": "https://www.bilibili.com"},
    "tencent": {"appid": "2040039444", "url": "https://example.com"},
    "mtcaptcha": {"sitekey": "MT_PUBLIC", "url": "https://www.mtcaptcha.com/"},
    "altcha": {"challenge": "00", "salt": "00", "algorithm": "SHA-256", "maxnumber": 1000},
    "friendly": {"sitekey": "FCMGEMUD2M567T8G", "url": "https://friendlycaptcha.com"},
    "arkose": {"public_key": "", "url": "https://example.com"},
    "kasada": {"page_url": "https://example.com"},
    "imperva": {"url": "https://example.com"},
    "x5sec": {"url": "https://example.com"},
    "yandex": {"sitekey": "", "url": "https://example.com"},
    "image_to_text": dict(IMAGE),
    "recaptcha_audio": {"audio_url": "https://example.com/a.mp3"},
    "steam": dict(IMAGE),
    "vk": dict(IMAGE),
    "zhihu": dict(IMAGE),
    "binance": {"biz_id": "", "url": "https://example.com"},
    "basilisk": {"site_key": "", "site_domain": "example.com"},
    "captchafox": {"body": "", "image_url": "", "website_key": ""},
    "tspd": {"url": "https://example.com"},
    "yidun": {"captchaId": "", "url": "https://dun.163.com/demo"},
    "dingxiang": {"app_id": "", "url": "https://example.com"},
    "shumei": {"org": "", "url": "https://example.com"},
    "douyin": {"url": "https://www.douyin.com"},
    "vaptcha": {"vid": "", "url": "https://example.com"},
    "procaptcha": {"url": "https://example.com", "sitekey": ""},
    "cybersiara": {"masterurl_id": "", "url": "https://example.com"},
    "aliyun": {"scene_id": "", "prefix": "", "url": "https://example.com"},
    "cap": {"challenge": "", "url": "https://example.com"},
    "mcaptcha": {"challenge_json": {}},
    "anubis": {"challenge": "", "url": "https://example.com"},
    "goaway": {"challenge": "", "url": "https://example.com"},
    "cerberus": {"challenge": "", "url": "https://example.com"},
    "rotate": dict(IMAGE),
    "grid": dict(IMAGE_ACT),
    "coordinates": dict(IMAGE_ACT),
    "draw_around": dict(IMAGE_ACT),
    "drag_drop": dict(IMAGE_ACT),
    "bounding_box": dict(IMAGE_ACT),
}

# 2captcha task name per native type. Loaded from the API source when available
# (so the matrix always uses the names the API actually registers).
def _load_task_names() -> dict[str, str]:
    import re
    src = Path(__file__).resolve().parent.parent.parent / "veyracaptcha" / "api" / "api_server.py"
    if not src.exists():
        return dict(TASK_NAMES)
    text = src.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for names, cf in re.findall(r'_reg\(\s*"([^"]+)"\s*,\s*"([^"]+)"', text):
        for n in names.split():
            out.setdefault(cf, n)
    return out


TASK_NAMES = {
    "turnstile": "TurnstileTaskProxyless",
    "recaptcha": "RecaptchaV2TaskProxyless",
    "hcaptcha": "HCaptchaTaskProxyless",
    "hcaptcha_enterprise": "HCaptchaEnterpriseTaskProxyless",
    "cloudflare": "CloudFlareTurnstileTaskProxyless",
    "cloudflare_challenge": "AntiCloudflareTaskProxyless",
    "awswaf": "AwsWafTaskProxyless",
    "datadome": "DataDomeTaskProxyless",
    "geetest": "GeeTestTaskProxyless",
    "image_to_text": "ImageToTextTask",
    "recaptcha_audio": "RecaptchaAudioTask",
    "arkose": "ArkoseFunCaptchaTaskProxyless",
    "friendly": "FriendlyCaptchaTaskProxyless",
    "mtcaptcha": "MTCaptchaTaskProxyless",
    "altcha": "AltchaTaskProxyless",
    "procaptcha": "ProsopoTaskProxyless",
    "cybersiara": "CyberSiARATaskProxyless",
    "imperva": "ImpervaTaskProxyless",
    "tencent": "TencentTaskProxyless",
    "yandex": "YandexSmartCaptchaTaskProxyless",
    "vk": "VKTaskProxyless",
    "binance": "BinanceTaskProxyless",
    "basilisk": "BasiliskTaskProxyless",
    "captchafox": "CaptchaFoxTaskProxyless",
    "tspd": "TsApiTask",
    "steam": "SteamTask",
    "yidun": "YidunTaskProxyless",
    "dingxiang": "DingxiangTaskProxyless",
    "shumei": "ShumeiTaskProxyless",
    "douyin": "DouyinTaskProxyless",
    "vaptcha": "VaptchaTaskProxyless",
    "zhihu": "ZhihuTaskProxyless",
    "aliyun": "AliyunTaskProxyless",
    "botguard": "BotGuardTaskProxyless",
    "cap": "CapTaskProxyless",
    "mcaptcha": "McaptchaTaskProxyless",
    "anubis": "AnubisTaskProxyless",
    "goaway": "GoAwayTaskProxyless",
    "cerberus": "CerberusTaskProxyless",
    "perimeterx": "PerimeterXTaskProxyless",
    "akamai": "AkamaiTaskProxyless",
    "kasada": "KasadaTaskProxyless",
    "x5sec": "X5SecTaskProxyless",
    "grid": "GridTaskProxyless",
    "coordinates": "CoordinatesTaskProxyless",
    "draw_around": "DrawAroundTaskProxyless",
    "drag_drop": "DragDropTaskProxyless",
    "bounding_box": "BoundingBoxTaskProxyless",
    "rotate": "RotateTaskProxyless",
}


def post(url: str, payload: dict, headers: dict, timeout: int = 120):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:300]}
    except Exception as exc:  # timeout / connection reset
        return 0, {"detail": f"{type(exc).__name__}: {exc}"}


def classify(status: int, data: dict) -> tuple[str, str]:
    if status == 0:
        return "TIMEOUT", str(data.get("detail", ""))[:120]
    if status >= 500:
        return "BROKEN", str(data.get("detail", data))[:120]
    if status == 400:
        return "REJECTED", str(data.get("detail", ""))[:120]
    if status == 200:
        if data.get("solved") is True and (data.get("token") or data.get("success")):
            return "SOLVED", str(data.get("method") or "")[:60]
        if data.get("solved") is False or data.get("error"):
            return "DISPATCHED", str(data.get("error") or "")[:120]
        return "DISPATCHED", str(data.get("token") or data.get("method") or "")[:80]
    return f"HTTP{status}", str(data)[:120]


def api_payload(native: str) -> dict:
    """Build a 2captcha-style task body for the API."""
    task = {"type": TASK_NAMES.get(native, native)}
    base = PAYLOADS.get(native, {})
    for key in ("sitekey", "url", "websiteKey", "websiteURL", "body", "image_url",
                "image_b64", "action", "version", "email", "appid", "app_id",
                "captchaId", "vid", "org", "biz_id", "public_key", "site_key",
                "site_domain", "masterurl_id", "scene_id", "prefix", "captcha_data",
                "audio_url", "gt", "challenge", "salt", "algorithm", "maxnumber",
                "challenge_json", "page_url"):
        if key in base:
            task[key] = base[key]
    # aliases the API understands
    if "sitekey" in base:
        task["websiteKey"] = base["sitekey"]
    if "url" in base:
        task["websiteURL"] = base["url"]
    if "image_b64" in base:
        task["body"] = base["image_b64"]
    # the API reads the instruction under "comment" for the recognition family
    if "action" in base:
        task["comment"] = base["action"]
    return task


def main() -> int:
    global TASK_NAMES
    TASK_NAMES = _load_task_names()
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=ENGINE)
    ap.add_argument("--api", default=API)
    ap.add_argument("--out", default="type-matrix.md")
    ap.add_argument("--only-engine", action="store_true")
    ap.add_argument("--only-api", action="store_true")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    types = list(PAYLOADS.keys())
    rows: list[tuple[str, str, str, str, str]] = []

    if not args.only_api:
        for t in types:
            payload = dict(PAYLOADS[t], type=t)
            status, data = post(f"{args.engine}/solve", payload,
                                {"Authorization": f"Bearer {TOKEN}"}, timeout=args.timeout)
            verdict, note = classify(status, data)
            rows.append((t, "engine", verdict, note, f"HTTP {status}"))
            print(f"[engine] {t:22} {verdict:11} {note[:80]}", flush=True)
            time.sleep(1)

    if not args.only_engine:
        for t in types:
            body = {"clientKey": API_KEY, "task": api_payload(t)}
            status, data = post(f"{args.api}/createTask", body, {}, timeout=60)
            if status != 200 or not data.get("taskId"):
                rows.append((t, "api", "REJECTED", str(data.get("errorDescription") or data)[:100], f"HTTP {status}"))
                print(f"[api]    {t:22} REJECTED      {str(data)[:80]}", flush=True)
                continue
            task_id = data["taskId"]
            verdict, note = "PENDING", ""
            for _ in range(25):
                time.sleep(4)
                st, res = post(f"{args.api}/getTaskResult?clientKey={API_KEY}&taskId={task_id}",
                               {}, {}, timeout=60) if False else _get(args.api, task_id)
                if res.get("status") == "ready":
                    sol = res.get("solution", {})
                    verdict = "SOLVED" if sol.get("token") or sol.get("gRecaptchaResponse") else "DISPATCHED"
                    note = str(sol.get("token") or sol.get("gRecaptchaResponse") or "")[:60]
                    break
                if res.get("errorId") == 1:
                    verdict = "REJECTED"
                    note = str(res.get("errorDescription"))[:100]
                    break
            else:
                verdict = "TIMEOUT"
            rows.append((t, "api", verdict, note, f"task {task_id}"))
            print(f"[api]    {t:22} {verdict:11} {note[:80]}", flush=True)

    # ---- report ----------------------------------------------------------
    tally: dict[str, int] = {}
    for _, _, verdict, _, _ in rows:
        tally[verdict] = tally.get(verdict, 0) + 1
    lines = ["# cf-solver type coverage matrix", "",
             f"engine: `{args.engine}`  ", f"api: `{args.api}`", "",
             "| type | layer | verdict | note |", "|---|---|---|---|"]
    for t, layer, verdict, note, extra in rows:
        lines.append(f"| {t} | {layer} | **{verdict}** | {note.replace('|', '/')} |")
    lines += ["", "## tally", ""]
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        lines.append(f"- {k}: {v}")
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines[-8:]))
    print(f"\nwrote {args.out}")
    return 0


def _get(api: str, task_id: str) -> tuple[int, dict]:
    url = f"{api}/getTaskResult?clientKey={API_KEY}&taskId={task_id}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except Exception as exc:
        return 0, {"detail": str(exc)}


if __name__ == "__main__":
    raise SystemExit(main())
