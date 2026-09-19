"""Selftest for the Yandex SmartCaptcha solver.

Offline (default — no network, no browser):

    python3 -m solvers.yandex.selftest

Live smoke (browser launch + navigation to the demo page; per task spec):

    python3 -m solvers.yandex.selftest --live

The live mode verifies browser launch + widget presence on the demo page — it does
NOT run a full solve (keep CI smoke fast and independent of Yandex risk scoring).
"""
import asyncio
import base64
import sys

from solvers.yandex.solve import (
    _looks_like_challenge,
    solve_yandex,
)

# A real-shaped token (captured live 2026-09-13, already expired — safe as fixture).
_FIXTURE_TOKEN = ("dD0xNzg5MjgzOTUzO2k9MmEwOTpiYWM1OjNhM2E6MjVhZjo6M2MxOjQwO0Q9MTQ2OTk1Mjk4MEZBQkRENTdDQTE1QTQxQ0UzMjQxODA4OTIyMzYxNUNBQTY4RTY1MjMwM0YxMDgyMDJFQjJFRDA0QUJGRTk1MkNGMUJFN0Q1QTlDMEQ1QTU5MzQwQ0Y4NUQ0MTU4QThEMEFCMEFGQUMxNTlDQ0Q2MzdGMjU0ODVBNEFCRTMyQTlDN0NBMzZFQ0YxMDQxRjAwMjJBNDMwRjBERTZCRDlBMjM0RDhEQjU7dT0xNzg5MjgzOTUzNTEzODEyOTcyO2g9Y2ZkODhhYzUwYjUxNDJhMzMyYTNjZmRlMDg0ZmZhMjk=")


def _decode_token(token: str) -> str:
    s = token.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8", "replace")


def _check_contract(result: dict):
    """Uniform result contract — no exceptions, ever."""
    for key in ("solved", "type", "token", "method", "elapsed", "error"):
        assert key in result, f"missing contract key {key!r} in {result}"
    assert result["type"] == "yandex", result["type"]
    assert isinstance(result["solved"], bool), "solved must be bool"
    assert isinstance(result["elapsed"], (int, float)), "elapsed must be numeric"
    assert result["token"] is None or isinstance(result["token"], str), "token type"
    assert result["error"] is None or isinstance(result["error"], str), "error type"
    if not result["solved"]:
        assert result["error"], "failed solve must carry an error string"


def main_offline():
    # 1. Token decoder: real-shaped fixture decodes to the documented structure.
    decoded = _decode_token(_FIXTURE_TOKEN)
    assert decoded.startswith("t="), decoded[:40]
    assert ";i=" in decoded and ";D=" in decoded and ";u=" in decoded and ";h=" in decoded, decoded[:120]
    assert decoded.count(";") >= 4, decoded[:120]

    # 2. Challenge text matcher: markers match, benign text does not.
    assert _looks_like_challenge("Перетащите ползунок до конца")
    assert _looks_like_challenge("Select all images with traffic lights")
    assert _looks_like_challenge("slide to verify")
    assert not _looks_like_challenge("Я не робот\nНажмите, чтобы продолжить")
    assert not _looks_like_challenge("")
    assert not _looks_like_challenge(None)

    # 3. Argument validation + contract without launching anything.
    r = asyncio.run(solve_yandex(url=None))
    _check_contract(r)
    assert not r["solved"] and "url is required" in r["error"], r

    print("offline selftest ok")


async def main_live():
    import cloakbrowser

    from solvers.common.browser import browser_kwargs
    from solvers.yandex.solve import _collect, _find_checkbox_frame

    demo = "https://metropol-moscow.ru/booking/"
    async with await cloakbrowser.launch_async(**browser_kwargs("TURNSTILE")) as browser:
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(demo, wait_until="domcontentloaded", timeout=45000)
        # The demo page lazy-loads captcha.js ~1.5 s after window load.
        loaded = False
        for _ in range(20):
            await asyncio.sleep(1)
            loaded = await page.evaluate(
                "() => !!(window.smartCaptcha && window.smartCaptcha.render)")
            if loaded:
                break
        assert loaded, "smartCaptcha JS API did not appear on the demo page"
        agg = await _collect(page)
        cb_frame, _ = await _find_checkbox_frame(page)
        state = "checkbox" if cb_frame else "invisible-only"
        assert isinstance(agg.get("tokens"), list)
        print(f"live smoke ok: demo page loaded, widget mode={state}, "
              f"events={agg['events']}")


if __name__ == "__main__":
    if "--live" in sys.argv:
        asyncio.run(main_live())
    else:
        main_offline()
