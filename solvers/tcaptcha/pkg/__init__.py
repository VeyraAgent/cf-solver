"""tcaptcha - Tencent TCaptcha (slider) solver toolkit.

Built from an observed, measured solving pipeline:

    flow (site adapter) -> TCaptcha widget attaches -> capture prehandle
    -> pull bg/fg challenge images -> 5 independent detectors -> consensus
    -> arbiter picks a legal action (LLM or heuristic) -> human-like drag
    -> read verify response -> risk-control aware retry policy.

Design rules learned the hard way (see README "Hard-won lessons"):
  * credentials NEVER live in source - env/CLI only,
  * Chromium drops concurrent proxy CONNECTs, so captcha static assets are
    pre-fetched out-of-band and served from memory,
  * the widget's own refresh button does not mint a new challenge - re-enter
    the host flow instead,
  * a placeholder prehandle (empty ticket) means Tencent risk-control: the
    widget will never submit a verify, so stop instead of burning attempts,
  * errorCode=12 is IP/fingerprint rate-limit, not a wrong drag - back off.
"""

__version__ = "1.1.0"
__all__ = ["__version__"]
