"""Flow adapters: walk the host page until the TCaptcha widget attaches.

A flow is the only site-specific part of the tool. It must:

    1. reach the point where the host page requests a captcha,
    2. return the widget's frame once prehandle has answered.

Register a new flow with @register("name") and run it with --flow name.
Credentials come from Config (env/CLI) - never from this file.
"""
from __future__ import annotations

import re
from typing import Callable

from .config import Config
from .log import log
from .network import NetworkTap

_REGISTRY: dict[str, Callable] = {}


def register(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def get(name: str) -> Callable:
    if name not in _REGISTRY:
        raise KeyError(f"unknown flow {name!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def available() -> list[str]:
    return sorted(_REGISTRY)


def wait_for_widget(page, cap: dict, timeout_s: float, tag: str):
    """Poll for the captcha frame AND its prehandle response.

    Observed timings: the frame attaches ~6-21s after the final Continue, and
    the prehandle response can land a beat after the frame - wait for both.
    """
    frame = None
    for _ in range(int(timeout_s / 0.5)):
        page.wait_for_timeout(500)
        found = next((f for f in page.frames
                      if "captcha" in (f.url or "") or "gtimg" in (f.url or "")), None)
        if found:
            frame = found
        if frame and cap["prehandle_resp"]:
            break
    if not frame:
        log("captcha", f"IFRAME NOT FOUND [{tag}] frames={[f.url for f in page.frames]}")
        return None
    for _ in range(30):                     # prehandle can trail the frame
        if cap["prehandle_resp"]:
            break
        page.wait_for_timeout(300)
    if not cap["prehandle_resp"]:
        log("captcha", f"frame found but no prehandle response [{tag}]")
        return None
    log("captcha", f"frame ok [{tag}] prehandle={len(cap['prehandle'])}")
    return frame


# --------------------------------------------------------------------------
@register("minimax-login")
def minimax_login(page, cfg: Config, cap: dict, tag: str = "initial"):
    """MiniMax unified-login: e-mail -> TOS -> Continue -> password -> Continue.

    This is the flow the whole pipeline was measured on. The captcha appears
    ~6-21s after the last Continue.
    """
    cfg.require_credentials()
    NetworkTap.reset(cap)
    page.goto("https://account.minimax.io/unified-login",
              wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("input[type=email]", timeout=60000)
    page.wait_for_timeout(800)
    page.fill("input[type=email]", cfg.email)

    btn = page.evaluate("""() => {
        const p = [...document.querySelectorAll('p')]
            .find(e => (e.innerText || '').includes('I have read and agree'));
        const sib = p && p.parentElement.querySelector('button');
        if (!sib) return false;
        sib.click();
        return true;
    }""")
    log("tos", f"button clicked={btn}")
    page.wait_for_timeout(400)

    page.get_by_role("button", name=re.compile("^continue$", re.I)).first.click()
    page.wait_for_selector("input[type=password]", timeout=30000)
    page.fill("input[type=password]", cfg.password)
    page.get_by_role("button", name=re.compile("^continue$", re.I)).first.click()

    return wait_for_widget(page, cap, cfg.frame_timeout, tag)
