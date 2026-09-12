"""Solve the reCAPTCHA v2 IMAGE challenge with a vision model.

The audio fallback is IP-blocked, but the image grid opens normally. We screenshot
the grid, slice it into tiles, ask the Mistral/ONNX key-pool yes/no per tile, click the
positives, and submit. Handles the three layouts:

  - 3x3 / 4x4 static : classify every tile once, click matches, verify.
  - dynamic (3x3)    : after a match is clicked the tile reloads a new image, so we
                       loop — classify, click new matches, wait for reload — until a
                       round finds nothing new (or a round cap), then verify.

Vision-solve is never 100% (reCAPTCHA images are deliberately ambiguous); success is
judged by reCAPTCHA accepting the submit (checkbox -> checked / token appears), not by
our own confidence. Caller retries on failure.
"""
import asyncio
import base64
import io
import logging

from PIL import Image

log = logging.getLogger(__name__)

_BFRAME = "/bframe"
_VERIFY = "#recaptcha-verify-button"
_CLASSIFY_CONCURRENCY = 6  # more parallel when Mistral keys available
_MAX_DYNAMIC_ROUNDS = 8


async def _find_bframe(page):
    for fr in page.frames:
        if _BFRAME in (fr.url or ""):
            return fr
    return None


async def _challenge_meta(bf) -> dict:
    return await bf.evaluate("""() => {
        const t = document.querySelector('table');
        const desc = document.querySelector(
            '.rc-imageselect-desc-no-canonical, .rc-imageselect-desc');
        const strong = desc?.querySelector('strong')?.innerText;
        const rows = t ? t.rows.length : 0;
        const cols = t && t.rows[0] ? t.rows[0].cells.length : 0;
        return {
            target: strong || (desc ? desc.innerText.split('\\n')[0] : ''),
            rows, cols,
            dynamic: !!document.querySelector(
                '.rc-imageselect-dynamic-selected') ||
                /click verify once there are none/i.test(desc?.innerText || ''),
            has_table: !!t,
        };
    }""")


async def _classify_grid(bf, keypool, target: str, n: int) -> list:
    """Screenshot the grid, slice into n*n tiles, classify each concurrently.

    Returns the list of tile indices (row-major) classified as containing target.
    """
    table = await bf.query_selector("table")
    if not table:
        return []
    try:
        await asyncio.wait_for(table.wait_for_element_state("stable", timeout=3000),
                               timeout=5)
    except Exception:
        pass
    try:
        grid_png = await asyncio.wait_for(table.screenshot(), timeout=10)
    except asyncio.TimeoutError:
        log.warning("grid screenshot timed out, retrying with frame screenshot")
        try:
            grid_png = await asyncio.wait_for(bf.screenshot(), timeout=10)
        except Exception:
            log.warning("frame screenshot also failed")
            return []
    img = Image.open(io.BytesIO(grid_png)).convert("RGB")
    W, H = img.size
    tw, th = W // n, H // n

    sem = asyncio.Semaphore(_CLASSIFY_CONCURRENCY)

    async def judge(idx, r, c):
        tile = img.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th))
        buf = io.BytesIO(); tile.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        async with sem:
            yes = await asyncio.to_thread(keypool.classify, b64, target)
        return idx if yes else None

    tasks = [judge(r * n + c, r, c) for r in range(n) for c in range(n)]
    results = await asyncio.gather(*tasks)
    return [i for i in results if i is not None]


async def _tiles(bf):
    return await bf.query_selector_all("td[role=button], .rc-imageselect-tile")


async def solve_image_challenge(page, keypool, max_rounds: int = _MAX_DYNAMIC_ROUNDS) -> bool:
    """Run the image challenge to a submit. Returns True if we pressed verify
    without an obvious error (caller confirms via token/checkbox)."""
    bf = await _find_bframe(page)
    if not bf:
        return False
    meta = await _challenge_meta(bf)
    if not meta.get("has_table") or not meta.get("target"):
        await asyncio.sleep(1.5)
        bf = await _find_bframe(page) or bf
        meta = await _challenge_meta(bf)
        if not meta.get("has_table") or not meta.get("target"):
            log.info("no image grid present"); return False
    n = meta["rows"]
    if not n:
        await asyncio.sleep(1.5)
        meta = await _challenge_meta(bf)
        n = meta.get("rows") or 0
        if not n:
            log.warning("grid rows unreadable — skipping to avoid mis-slice")
            return False
    target = meta["target"]
    dynamic = bool(meta["dynamic"])
    log.info("image challenge: target=%r grid=%dx%d dynamic=%s",
             target, n, meta["cols"], dynamic)

    rounds = max_rounds if dynamic else 2
    clicked_any = False
    empty_streak = 0
    for rnd in range(rounds):
        positives = await _classify_grid(bf, keypool, target, n)
        if not positives and rnd == 0:
            await asyncio.sleep(1.5)
            positives = await _classify_grid(bf, keypool, target, n)
        log.info("round %d/%d: %d/%d tiles match %r -> %s",
                 rnd + 1, rounds, len(positives), n * n, target, positives)
        if not positives:
            empty_streak += 1
            # dynamic: one empty round can be mid-reload — recheck once
            if dynamic and empty_streak == 1 and clicked_any:
                await asyncio.sleep(2.0)
                positives = await _classify_grid(bf, keypool, target, n)
                if positives:
                    empty_streak = 0
                    log.info("round %d recheck: %s", rnd + 1, positives)
                else:
                    break
            else:
                break
        tiles = await _tiles(bf)
        for idx in sorted(positives):
            if idx < len(tiles):
                try:
                    await tiles[idx].click(timeout=3000)
                    clicked_any = True
                    await asyncio.sleep(0.25 if dynamic else 0.12)
                except Exception as e:
                    log.debug("tile %d click: %s", idx, str(e).splitlines()[0])
        if dynamic:
            await asyncio.sleep(3.0)  # tile reload settle
            bf = await _find_bframe(page) or bf
            continue
        if rnd == 0 and clicked_any:
            await asyncio.sleep(0.8)
            continue
        break

    if not clicked_any:
        log.info("no tiles matched %r", target)
        # still press verify — an all-correct 'none' answer is a valid solve
    for _vtry in range(3):
        try:
            await page.frame_locator(
                "iframe[title*='recaptcha challenge']").locator(
                _VERIFY).click(timeout=4000, force=True)
            break
        except Exception as e:
            log.warning("verify click attempt %d: %s", _vtry + 1, str(e).splitlines()[0])
            if _vtry == 2:
                try:
                    bf2 = await _find_bframe(page)
                    if bf2:
                        await bf2.evaluate(
                            "() => { const b=document.querySelector('"
                            "#recaptcha-verify-button'); if(b) b.click(); }")
                        log.info("verify click: JS fallback succeeded")
                        break
                except Exception as e2:
                    log.warning("verify JS fallback: %s", str(e2).splitlines()[0])
                return False
            await asyncio.sleep(1)
            bf = await _find_bframe(page) or bf
    await asyncio.sleep(2.5)
    return True
