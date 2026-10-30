"""Logging that can never leak a proxy credential - in any form.

Two rules (both learned the hard way, see the skill references):
  * redact `scheme://user:pass@host` embedded anywhere in a line, AND
    bare `user:pass@host:port` (urlsplit-based redaction misses it),
  * persist every emitted line to a sink file - a failure whose only
    trace was one stdout line is undiagnosable after the process exits.
"""
from __future__ import annotations

import sys
import time

from .config import BARE_CRED_RE, redact

_SINK = None


def set_sink(path: str) -> None:
    """Append every emitted (already redacted) line to `path`.

    The engine opens this at the start of each run, so a crashed run
    leaves its full transcript behind instead of nothing.
    """
    global _SINK
    try:
        _SINK = open(path, "a", encoding="utf-8")
    except OSError:
        _SINK = None


def _scrub(text: str) -> str:
    """Redact credentials in free text: scheme'd urls AND bare proxies."""
    if "://" in text and "@" in text:
        # redact every scheme://user:pass@host token in the message
        out, i = [], 0
        while True:
            s = text.find("://", i)
            if s == -1:
                out.append(text[i:])
                break
            at = text.find("@", s)
            out.append(text[i:s + 3])
            if at != -1 and at - s < 128 and " " not in text[s + 3:at]:
                out.append("***:***@")
                i = at + 1
            else:
                i = s + 3
        text = "".join(out)
    # bare `user:pass@host` (no scheme) - the form urlsplit-based
    # redaction used to miss entirely
    return BARE_CRED_RE.sub("***:***@", text)


def log(stage: str, msg: str = "") -> None:
    """Print a stage-tagged line, redacting anything that looks like a cred."""
    text = _scrub(str(msg))
    print(f"[{stage}] {text}", flush=True)
    if _SINK:
        try:
            _SINK.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [{stage}] {text}\n")
            _SINK.flush()
        except OSError:
            pass


def safe(url: str) -> str:
    return redact(url)


def die(msg: str, code: int = 2) -> None:
    print(f"[fatal] {_scrub(str(msg))}", file=sys.stderr, flush=True)
    raise SystemExit(code)
