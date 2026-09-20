"""GeeTest v3 solver — generic gt+challenge flow via the biliTicker_gt Rust/Python
binding (Amorter/biliTicker_gt, AGPL-3.0).

`ClickPy.simple_match(gt, challenge)` drives the full v3 protocol (get_c_s →
get_type → calculate_key → generate_w → verify) for the **click** variant, which
is what bilibili uses. The Siamese grid model is auto-downloaded on first run.

End-to-end usage:
  * pass `gt` + `challenge` you scraped from the target page, OR
  * pass `register_url` (the page's register endpoint) and let the binding fetch
    a fresh pair, OR
  * pass nothing and the solver pulls a live pair from bilibili's public
    passport endpoint.

Note: needs Python 3.12/3.13 on Linux (no cp311 manylinux wheel; build from
source requires cargo + libssl-dev). See scripts/build_geetest_v3.sh.
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)

# Public source that hands out a fresh (gt, challenge) pair — no key needed.
_BILI_REGISTER = "https://passport.bilibili.com/x/passport-login/captcha?source=main_web"


def available() -> bool:
    try:
        import bili_ticket_gt_python  # noqa: F401
        return True
    except Exception:
        return False


def fetch_gt_challenge(register_url: str = _BILI_REGISTER, timeout_s: int = 20) -> tuple[str, str]:
    """Fetch a live (gt, challenge) pair.

    Prefers the binding's own `register_test` (handles both the bilibili JSON
    shape and a plain {gt, challenge} response); falls back to a raw HTTP GET so
    this also works for a page's own register endpoint. Returns ("", "") on
    failure.
    """
    # 1) binding helper — understands the bilibili response shape
    try:
        from bili_ticket_gt_python import ClickPy
        gt, ch = ClickPy().register_test(register_url)
        if gt and ch:
            return gt, ch
    except Exception as exc:  # noqa: BLE001
        log.debug("geetest v3: register_test failed (%s): %s", register_url, str(exc)[:120])

    # 2) raw GET — works for a plain {gt, challenge} JSON endpoint
    import json
    import time as _t
    import urllib.request

    sep = "&" if "?" in register_url else "?"
    url = f"{register_url}{sep}t={int(_t.time() * 1000)}"
    hdr = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
        "Referer": "https://www.bilibili.com/",
    }
    try:
        data = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers=hdr), timeout=timeout_s).read())
        g = data.get("data", {}).get("geetest", data)  # bilibili nests it
        if g.get("gt") and g.get("challenge"):
            return g["gt"], g["challenge"]
    except Exception as exc:  # noqa: BLE001
        log.warning("geetest v3: gt fetch failed (%s): %s", url, str(exc)[:120])
    return "", ""


def _run_sync(gt: str, challenge: str) -> dict:
    from bili_ticket_gt_python import ClickPy

    click = ClickPy()
    # simple_match_retry retries internally on a stale/expired challenge; fall
    # back to simple_match if this binding build doesn't expose it.
    validate = ""
    try:
        validate = click.simple_match_retry(gt, challenge)
    except AttributeError:
        validate = click.simple_match(gt, challenge)
    if not validate:
        validate = click.simple_match(gt, challenge)
    solved = bool(validate)
    return {
        "solved": solved, "type": "geetest_v3", "token": validate or "",
        "gt": gt, "challenge": challenge, "validate": validate or "",
        "seccode": (validate + "|jordan") if validate else "",
        "method": "gt3-click", "elapsed": 0.0,
        "error": None if solved else "simple_match returned no validate",
    }


async def solve_geetest_v3(gt: str, challenge: str, timeout_s: int = 90,
                           register_url: "str | None" = None, attempts: int = 2) -> dict:
    t0 = time.monotonic()

    def _fail(error: str) -> dict:
        return {
            "solved": False, "type": "geetest_v3", "token": "",
            "method": "gt3-click", "elapsed": round(time.monotonic() - t0, 1),
            "error": error,
        }

    if not gt or not challenge:
        gt2, ch2 = await asyncio.to_thread(fetch_gt_challenge, register_url or _BILI_REGISTER)
        gt, challenge = gt or gt2, challenge or ch2
    if not gt or not challenge:
        return _fail("gt and challenge are required (and auto-fetch found none)")

    last = ""
    for i in range(max(1, attempts)):
        def _run() -> dict:
            r = _run_sync(gt, challenge)
            r["elapsed"] = round(time.monotonic() - t0, 1)
            return r

        try:
            r = await asyncio.wait_for(asyncio.to_thread(_run), timeout=max(timeout_s, 30))
        except asyncio.TimeoutError:
            return _fail(f"geetest v3 solve timed out after {timeout_s}s")
        except Exception as exc:  # noqa: BLE001
            last = str(exc).splitlines()[0][:200]
            log.warning("geetest v3 attempt %d failed: %s", i + 1, last)
            # a fresh challenge on the next attempt (the old one may be stale)
            if i + 1 < attempts:
                g2, c2 = await asyncio.to_thread(fetch_gt_challenge, register_url or _BILI_REGISTER)
                gt, challenge = (g2 or gt), (c2 or challenge)
            continue
        if r.get("solved"):
            return r
        last = r.get("error") or "no validate"
        if i + 1 < attempts:
            g2, c2 = await asyncio.to_thread(fetch_gt_challenge, register_url or _BILI_REGISTER)
            gt, challenge = (g2 or gt), (c2 or challenge)

    return _fail(last or "geetest v3 failed")
