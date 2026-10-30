"""Passive capture of the TCaptcha conversation.

The solver only ever reads four things off the wire:

    GET  .../cap_union_prehandle  -> JSONP: challenge geometry + ticket
    GET  .../getcapbysig?img_index=1 -> background image
    GET  .../getcapbysig?...        -> foreground (piece) sprite
    POST .../cap_union_new_verify   -> {"errorCode": "0" | "12" | "50" | ...}

`resp.body()` is not reliable for every response, so image bytes are re-fetched
by URL through the page's own request context when the inline body fails.
"""
from __future__ import annotations

import urllib.parse
from typing import Any


class NetworkTap:
    """Accumulates prehandle / verify / image data into a plain dict."""

    KEYS = ("prehandle", "verify", "images", "image_urls", "prehandle_resp", "verify_resp")

    @staticmethod
    def blank() -> dict[str, Any]:
        return {"prehandle": [], "verify": [], "images": {}, "image_urls": {},
                "prehandle_resp": "", "verify_resp": ""}

    @staticmethod
    def reset(cap: dict) -> None:
        cap["prehandle"] = []
        cap["verify"] = []
        cap["images"] = {}
        cap["image_urls"] = {}
        cap["prehandle_resp"] = ""
        cap["verify_resp"] = ""

    @staticmethod
    def attach(page, cap: dict) -> None:
        def on_req(req):
            path = urllib.parse.urlparse(req.url).path
            if path.endswith("cap_union_prehandle"):
                cap["prehandle"].append({"url": req.url, "headers": dict(req.headers)})
            elif path.endswith("cap_union_new_verify"):
                cap["verify"].append({"url": req.url, "headers": dict(req.headers),
                                      "post": req.post_data})

        def on_resp(resp):
            try:
                url = resp.url
                path = urllib.parse.urlparse(url).path
                if "getcapbysig" in url:
                    key = "bg" if "img_index=1" in url else "fg"
                    cap["image_urls"][key] = url
                    try:
                        cap["images"][key] = resp.body()
                    except Exception as exc:  # noqa: BLE001 - body() flakiness is common
                        log_img(key, exc)
                elif path.endswith("cap_union_prehandle"):
                    cap["prehandle_resp"] = resp.text()
                elif path.endswith("cap_union_new_verify"):
                    cap["verify_resp"] = resp.text()
            except Exception as exc:  # noqa: BLE001
                from .log import log
                log("resp-err", exc)

        page.on("request", on_req)
        page.on("response", on_resp)

    @staticmethod
    def wait_for_images(ctx, page, cap: dict, timeout_ms: int = 20000) -> bool:
        """Block until bg+fg bytes exist, re-fetching by URL when needed."""
        steps = timeout_ms // 500
        for i in range(steps):
            if cap["prehandle_resp"] and cap["images"].get("bg") and cap["images"].get("fg"):
                return True
            if i % 4 == 3:  # every 2s, try the URL fallback
                for key in ("bg", "fg"):
                    if cap["images"].get(key):
                        continue
                    url = cap.get("image_urls", {}).get(key)
                    if not url:
                        continue
                    try:
                        rr = ctx.request.get(url, timeout=15000)
                        if rr.ok:
                            cap["images"][key] = rr.body()
                    except Exception as exc:  # noqa: BLE001
                        from .log import log
                        log("img-fetch", f"{key}: {exc}")
            page.wait_for_timeout(500)
        return bool(cap["images"].get("bg") and cap["images"].get("fg"))


def log_img(key: str, exc: Exception) -> None:
    from .log import log
    log("img-body", f"{key}: {exc}")
