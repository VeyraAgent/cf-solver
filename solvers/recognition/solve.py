"""Universal vision-based recognition solver — handles all generic interaction types.

Powers: grid, coordinates, draw_around, drag_drop, bounding_box.
Uses Mistral KeyPool (from common/apikey.txt) to analyze images and extract
structured answers (cell indices, coordinate pairs, bounding boxes, drag targets).

Same approach as hcaptcha image_solve.py grid overlay, generalized for any
recognition task. 2captcha and CapSolver both offer these as paid services;
we solve locally via a vision model at zero cost.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import time
from typing import Any, Optional

from PIL import Image

log = logging.getLogger("recognition")

_KEYFILE = __import__("pathlib").Path(__file__).parent.parent / "common" / "apikey.txt"
_keypool = None


def _get_keypool():
    global _keypool
    if _keypool is None:
        from solvers.common.mistral import KeyPool
        _keypool = KeyPool(str(_KEYFILE), model="mistral-medium-latest")
    return _keypool


# ── Prompt builders per mode ──────────────────────────────────────

def _prompt_grid(task: str, grid: int) -> str:
    n = grid * grid
    return (
        f"This image has a {grid}x{grid} numbered grid (0..{n - 1}, yellow label "
        f"top-left of each cell). Task: \"{task}\". "
        f"Reply ONLY the cell number(s) that satisfy the task, comma-separated "
        f"(e.g. `3` or `1,4,9`), or `none`."
    )


def _prompt_coordinates(task: str, w: int, h: int) -> str:
    return (
        f"Image is {w}x{h} pixels. Task: \"{task}\". "
        f"Reply ONLY the (x, y) pixel coordinates to click, one per line as `x,y`. "
        f"If multiple points, list each on its own line. Reply `none` if nothing matches."
    )


def _prompt_draw_around(task: str, w: int, h: int) -> str:
    return (
        f"Image is {w}x{h} pixels. Task: \"{task}\". "
        f"Draw a tight bounding box around the object. "
        f"Reply ONLY as `x1,y1,x2,y2` (top-left and bottom-right pixel coords). "
        f"If multiple objects, one box per line."
    )


def _prompt_drag_drop(task: str, w: int, h: int) -> str:
    return (
        f"Image is {w}x{h} pixels. Task: \"{task}\". "
        f"Identify the element to drag (source) and where to drop it (target). "
        f"Reply ONLY as `sx,sy,tx,ty` (source x,y then target x,y)."
    )


def _prompt_bounding_box(task: str, w: int, h: int) -> str:
    return (
        f"Image is {w}x{h} pixels. Task: \"{task}\". "
        f"Return ALL bounding boxes of objects matching the task as `x1,y1,x2,y2` "
        f"(top-left and bottom-right), one per line. Reply `none` if nothing found."
    )


# ── Image helpers ─────────────────────────────────────────────────

def _image_size(b64: str) -> tuple[int, int]:
    """Return (w, h) of a base64-encoded image."""
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    return img.size


def _grid_overlay(b64: str, grid: int = 3) -> str:
    """Overlay numbered grid on image, return base64 PNG."""
    from PIL import ImageDraw
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    w, h = img.size
    cw, ch = w // grid, h // grid
    draw = ImageDraw.Draw(img)
    n = 0
    for r in range(grid):
        for c in range(grid):
            x0, y0 = c * cw, r * ch
            draw.rectangle([x0, y0, x0 + cw, y0 + ch], outline="red", width=3)
            tx, ty = x0 + 6, y0 + 6
            draw.rectangle([tx - 2, ty - 2, tx + 38, ty + 26], fill="yellow")
            draw.text((tx, ty), str(n), fill="black")
            n += 1
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── Response parsers ──────────────────────────────────────────────

def _parse_cells(text: str, max_cells: int = 16) -> list[int]:
    """Extract cell indices from vision response."""
    nums = []
    for m in re.finditer(r"\d+", text):
        v = int(m.group())
        if 0 <= v < max_cells:
            nums.append(v)
    seen = set()
    return [x for x in nums if not (x in seen or seen.add(x))]


def _parse_coordinates(text: str) -> list[tuple[int, int]]:
    """Extract (x, y) coordinate pairs."""
    coords = []
    for m in re.finditer(r"(\d+)\s*,\s*(\d+)", text):
        x, y = int(m.group(1)), int(m.group(2))
        if 0 <= x <= 10000 and 0 <= y <= 10000:
            coords.append((x, y))
    return coords


def _parse_bboxes(text: str) -> list[tuple[int, int, int, int]]:
    """Extract bounding boxes as (x1, y1, x2, y2)."""
    boxes = []
    for m in re.finditer(r"(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text):
        x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if x1 < x2 and y1 < y2:
            boxes.append((x1, y1, x2, y2))
    return boxes


def _parse_drag(text: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Extract source + target for drag-and-drop."""
    m = re.search(r"(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text)
    if m:
        return (int(m.group(1)), int(m.group(2))), (int(m.group(3)), int(m.group(4)))
    return None


# ── Main solver ───────────────────────────────────────────────────

async def solve_recognition(
    image_b64: Optional[str] = None,
    task: str = "",
    mode: str = "coordinates",
    grid_size: int = 3,
    image_url: Optional[str] = None,
    proxy: Optional[str] = None,
    timeout_s: int = 60,
) -> dict:
    """Solve a generic vision-based recognition captcha.

    Modes:
      - "grid": click cells in a numbered grid (returns cell_indices)
      - "coordinates": click at (x,y) positions (returns coords)
      - "draw_around": draw bounding box around object (returns bboxes)
      - "drag_drop": drag source to target (returns source + target)
      - "bounding_box": return object bounding boxes (returns bboxes)

    Args:
      image_b64: base64-encoded captcha image
      task: text description of what to find/do
      mode: recognition mode
      grid_size: grid dimensions (for grid mode, default 3x3)
      image_url: alternative to image_b64 (will be downloaded)
    """
    t0 = time.monotonic()
    result: dict[str, Any] = {
        "type": mode, "solved": False, "token": "",
        "method": "vision-recognition", "elapsed": 0, "error": None,
    }

    if not image_b64 and image_url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15, verify=False) as c:
                resp = await c.get(image_url)
                image_b64 = base64.b64encode(resp.content).decode()
        except Exception as e:
            result["error"] = f"recognition: download failed: {e}"
            return result

    if not image_b64:
        result["error"] = "recognition: pass image_b64 or image_url"
        return result
    if not task:
        result["error"] = "recognition: pass task (text description)"
        return result

    keypool = _get_keypool()
    try:
        w, h = _image_size(image_b64)
    except Exception:
        w, h = 0, 0

    # Build prompt + prepare image based on mode
    if mode == "grid":
        gridded = _grid_overlay(image_b64, grid_size)
        prompt = _prompt_grid(task, grid_size)
        send_b64 = gridded
    elif mode == "coordinates":
        prompt = _prompt_coordinates(task, w, h)
        send_b64 = image_b64
    elif mode == "draw_around":
        prompt = _prompt_draw_around(task, w, h)
        send_b64 = image_b64
    elif mode == "drag_drop":
        prompt = _prompt_drag_drop(task, w, h)
        send_b64 = image_b64
    elif mode == "bounding_box":
        prompt = _prompt_bounding_box(task, w, h)
        send_b64 = image_b64
    else:
        result["error"] = f"recognition: unknown mode '{mode}'"
        return result

    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(keypool.ask, send_b64, prompt, 16, 45),
            timeout=max(timeout_s - 2, 10),
        )
    except asyncio.TimeoutError:
        result["error"] = f"recognition: vision call timed out"
        return result
    except Exception as e:
        result["error"] = f"recognition: vision error: {e}"
        return result

    log.info("recognition[%s] task=%r → %s", mode, task[:60], text[:120])

    # Parse based on mode
    if mode == "grid":
        cells = _parse_cells(text, grid_size * grid_size)
        result["solved"] = bool(cells)
        result["token"] = ",".join(str(c) for c in cells)
        result["cell_indices"] = cells
        result["grid_size"] = grid_size
    elif mode == "coordinates":
        coords = _parse_coordinates(text)
        result["solved"] = bool(coords)
        result["token"] = ";".join(f"{x},{y}" for x, y in coords)
        result["coords"] = [{"x": x, "y": y} for x, y in coords]
    elif mode in ("draw_around", "bounding_box"):
        bboxes = _parse_bboxes(text)
        result["solved"] = bool(bboxes)
        result["token"] = ";".join(f"{x1},{y1},{x2},{y2}" for x1, y1, x2, y2 in bboxes)
        result["bboxes"] = [{"x1": x1, "y1": y1, "x2": x2, "y2": y2} for x1, y1, x2, y2 in bboxes]
    elif mode == "drag_drop":
        drag = _parse_drag(text)
        result["solved"] = drag is not None
        if drag:
            src, tgt = drag
            result["token"] = f"{src[0]},{src[1]}→{tgt[0]},{tgt[1]}"
            result["source"] = {"x": src[0], "y": src[1]}
            result["target"] = {"x": tgt[0], "y": tgt[1]}

    result["elapsed"] = round(time.monotonic() - t0, 2)
    if not result["solved"]:
        result["error"] = f"recognition: no valid result from vision (raw: {text[:80]})"
    return result
