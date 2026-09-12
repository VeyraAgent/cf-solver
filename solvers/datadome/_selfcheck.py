"""Offline runnable check for the DataDome cookie parser: python -m datadome._selfcheck

Exercises _parse_dd_cookie against the real api-js.datadome.co response shape
({"status":200,"cookie":"datadome=...; Max-Age=...; Domain=.github.com; ..."}) —
no network.
"""
from solvers.datadome.solve import _parse_dd_cookie

_OK = ('{"status":200,"cookie":"datadome=hd4Wnz4AH6C2Vx3czLr47hzd~qNJutXMMt4; '
       'Max-Age=31536000; Domain=.github.com; Path=/; Secure; SameSite=None"}')
_CHALLENGE = '{"status":403,"url":"https://geo.captcha-delivery.com/captcha/?initialCid=x"}'
_GARBAGE = "not json at all"

if __name__ == "__main__":
    ok = _parse_dd_cookie(_OK)
    assert ok["value"] == "hd4Wnz4AH6C2Vx3czLr47hzd~qNJutXMMt4", ok
    assert ok["domain"] == ".github.com", ok
    assert ok["max_age"] == 31536000, ok
    assert ok["status"] == 200, ok

    # A challenge response has no datadome cookie -> no value, status surfaced.
    ch = _parse_dd_cookie(_CHALLENGE)
    assert "value" not in ch, ch
    assert ch.get("status") == 403, ch

    # Non-JSON degrades to {}.
    assert _parse_dd_cookie(_GARBAGE) == {}, "garbage must yield {}"

    print("ok")
