"""Risk-control interpretation: the two failure modes that are NOT bad drags.

1) errorCode=12 - Tencent IP/fingerprint rolling window (rate limit).
   Sources: github.com/reserver/tds ("连续快速请求会触发 errorCode=12（限流）")
   and the pj-one blog ("verify IP/指纹级滚动窗口 ... 自动退避 65s").
   Retrying instantly keeps you inside the window, so back off.

2) placeholder prehandle - response has `"ticket":""` and
   `src_2=template/new_placeholder.html`. The widget renders but NEVER posts a
   verify: there is nothing to solve. Attempting a drag here wastes a slot and
   deepens the hold. Observed to be sticky across IPs, accounts and UA tweaks,
   i.e. it only clears with time - so classify early and stop.

errorCode=50 is a genuinely wrong drag: refresh and re-solve.
"""
from __future__ import annotations

import json
import re

JSONP_RE = re.compile(r"_aq_\d+\((.*)\)\s*$", re.S)

RATE_LIMITED = "12"
WRONG_DRAG = "50"

KIND_CHALLENGE = "challenge"     # ticket issued, widget will verify
KIND_PLACEHOLDER = "placeholder" # risk hold, widget will never verify
KIND_UNKNOWN = "unknown"


def parse_prehandle(text: str) -> dict | None:
    """Unwrap the JSONP envelope of a prehandle response (or None)."""
    if not text:
        return None
    m = JSONP_RE.search(text.strip())
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return None


def classify_prehandle(text: str) -> str:
    """challenge | placeholder | unknown"""
    data = parse_prehandle(text)
    if not data:
        return KIND_UNKNOWN
    try:
        payload = data["data"] if isinstance(data, dict) and "data" in data else data
    except Exception:  # noqa: BLE001
        payload = data
    ticket = ""
    src2 = ""
    try:
        ticket = payload.get("ticket", "") or ""
        dyn = payload.get("dyn_show_info") or {}
        src2 = str(dyn.get("src_2", "") or "")
    except Exception:  # noqa: BLE001
        pass
    if "placeholder" in src2:
        return KIND_PLACEHOLDER
    if not ticket:
        # no src_2 but no ticket either: the widget has nothing to verify against
        return KIND_PLACEHOLDER
    return KIND_CHALLENGE


def parse_verify(text: str) -> str | None:
    """errorCode as a string, or None when no verify response arrived."""
    if not text:
        return None
    try:
        value = json.loads(text).get("errorCode")
    except Exception:  # noqa: BLE001
        m = re.search(r'"errorCode"\s*:\s*"?(\d+)"?', text)
        return m.group(1) if m else None
    return None if value is None else str(value)


def backoff_seconds(error_code: str | None, cfg) -> float | None:
    """Seconds to sleep for a failure code, or None when no backoff applies."""
    if error_code == RATE_LIMITED:
        return float(cfg.ratelimit_backoff)
    return None


def challenge_geometry(prehandle_resp: str) -> tuple[dict, dict]:
    """Return (piece_elem, logical_bg) from a challenge prehandle.

    Raises ValueError when the response is not a solvable challenge.
    """
    data = parse_prehandle(prehandle_resp)
    if not data:
        raise ValueError("prehandle parse failed")
    dyn = data["data"]["dyn_show_info"]
    piece = next((e for e in dyn["fg_elem_list"]
                  if "DynAnswerType_POS" in ((e.get("move_cfg") or {}).get("data_type") or [])),
                 None)
    if piece is None:
        raise ValueError("no POS piece element in prehandle")
    return piece, dyn["bg_elem_cfg"]["size_2d"]
