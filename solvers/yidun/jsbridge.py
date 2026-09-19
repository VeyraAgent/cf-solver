"""Node subprocess bridge for the vendored Yidun crypto JS (js/bridge.js).

The /v3/get + /v3/check crypto params (cb, data, encryptValidate) and the fp
device fingerprint are implemented in the vendor's obfuscated JavaScript —
porting them to Python is out of scope, so we run the exact proven code via a
one-shot `node` process per call and parse the sentinel-prefixed JSON result.

Vendored JS: CodeEmpower/yidun-silder (encrypt.js + fp.js + webpack.js).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger("yidun.bridge")

_BRIDGE = Path(__file__).parent / "js" / "bridge.js"
_SENTINEL = "__YIDUN_BRIDGE__"


def available() -> bool:
    return shutil.which("node") is not None and _BRIDGE.exists()


async def call(op: str, args: dict | None = None, timeout_s: float = 20) -> dict:
    """Run one bridge op, return its JSON result dict (raises on failure)."""
    proc = await asyncio.create_subprocess_exec(
        "node", str(_BRIDGE), op, json.dumps(args or {}),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        raise TimeoutError(f"node bridge op={op} timed out after {timeout_s}s")

    text = out.decode(errors="replace")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith(_SENTINEL):
            result = json.loads(line[len(_SENTINEL):])
            if "error" in result:
                raise RuntimeError(f"node bridge op={op}: {result['error'][:300]}")
            return result
    raise RuntimeError(
        f"node bridge op={op} produced no result (rc={proc.returncode}): "
        f"{err.decode(errors='replace')[:200]}")
