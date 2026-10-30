"""The solve loop: flow -> capture -> detect -> decide -> drag -> verify.

Retry policy (all of it measured):

    placeholder prehandle  -> stop immediately: nothing will ever be verified
                              (attempting it only deepens the risk hold)
    errorCode 12           -> sleep ratelimit_backoff, re-enter the flow
    errorCode 50           -> wrong drag: re-enter the flow for a new challenge
    no verify response     -> the widget never submitted: classify the
                              prehandle, then refresh or stop
    corrupt image bytes    -> drop artifacts, re-enter the flow

IMPORTANT: the widget's own refresh button does NOT mint a new challenge -
probing it showed image fetches doubling while the prehandle count stayed at
1, i.e. it re-serves the same session. Re-entering the host flow is the only
way to get a genuinely new puzzle.
"""
from __future__ import annotations

import io
import json
import os
import time

from . import drag, flows, risk
from .browser import Session, select_healthy_proxy
from .config import Config
from .decision import decide
from .detect import detect
from .log import log, set_sink
from .network import NetworkTap


def _annotate(out_dir: str, bg_bytes: bytes, det: dict, piece: dict) -> None:
    """Evidence picture: piece box + every detector's guess + an x ruler."""
    try:
        from PIL import Image, ImageDraw
        im = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
        d = ImageDraw.Draw(im)
        pw, ph = piece["size_2d"]
        ix, iy = piece["init_pos"]
        d.rectangle([ix, iy, ix + pw, iy + ph], outline=(255, 0, 0), width=3)
        colors = {"ncc": (0, 255, 0), "canny": (0, 160, 255), "contour": (255, 0, 255),
                  "dark": (255, 140, 0), "hole": (255, 255, 255)}
        for key, col in colors.items():
            entry = det.get(key) or {}
            if entry.get("x") is not None:
                ey = entry.get("y", iy)
                d.rectangle([entry["x"], ey, entry["x"] + pw, ey + ph], outline=col, width=3)
        for x in range(0, im.size[0], 50):
            d.line([x, im.size[1] - 22, x, im.size[1]], fill=(255, 255, 0), width=1)
            d.text((x + 2, im.size[1] - 21), str(x), fill=(255, 255, 0))
        im.save(f"{out_dir}/annotated.png")
    except Exception as exc:  # noqa: BLE001 - annotation is best-effort evidence
        log("annotate", exc)


_SCRUB_ON_START = ("outcome.json", "capture.json", "verify_resp.json",
                   "decision.json", "candidates.json")


def _scrub_stale(cfg: Config) -> None:
    """Fail closed on artifact freshness (skill lesson: a stale PASS-looking
    outcome.json from a previous run must never outlive the run that wrote it
    - without an unlink-before-run, an aborted run leaves the last verdict
    sitting in out_dir and the next harness read scores a false PASS)."""
    for name in _SCRUB_ON_START:
        try:
            os.remove(f"{cfg.out_dir}/{name}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            log("artifacts", f"could not remove stale {name}: {exc}")


def solve(cfg: Config) -> dict:
    """Run one solve. Returns the outcome dict (also written to out_dir)."""
    flow = flows.get(cfg.flow)
    os.makedirs(cfg.out_dir, exist_ok=True)
    # Full redacted transcript survives the process (skill: a failure whose
    # only trace was one stdout line is undiagnosable afterwards).
    set_sink(f"{cfg.out_dir}/run.log")
    log("run", f"flow={cfg.flow} arbiter={cfg.arbiter} out={cfg.out_dir} "
               f"attempts={cfg.max_attempts}")
    _scrub_stale(cfg)

    # Proxy ladder: screen candidates before any browser time. Raises
    # ConfigError ("no healthy proxy candidate (N tried, ...)") when every
    # candidate is dead, instead of a misleading IFRAME NOT FOUND at minute 4.
    select_healthy_proxy(cfg)

    outcome: dict = {"ok": False, "attempts": 0, "history": [], "risk_hold": False,
                     "flow": cfg.flow, "proxy": cfg.proxy_label, "arbiter": cfg.arbiter}
    started = time.time()

    with Session(cfg) as session:
        page, ctx = session.page, session.ctx
        cap = NetworkTap.blank()
        NetworkTap.attach(page, cap)

        frame = flow(page, cfg, cap, "initial")
        if not frame:
            try:
                page.screenshot(path=f"{cfg.out_dir}/fail.png")
            except Exception:  # noqa: BLE001
                pass
            outcome["error"] = "widget did not attach"
            _finalize(cfg, outcome, cap, None, None)
            return outcome

        def reenter(tag: str) -> bool:
            nonlocal frame
            nf = flow(page, cfg, cap, tag)
            if nf is None:
                return False
            frame = nf
            return True

        for attempt in range(1, cfg.max_attempts + 1):
            outcome["attempts"] = attempt
            cap["verify_resp"] = ""
            cap["verify"] = []

            # ---- risk gate: is this even a solvable challenge? ----------
            kind = risk.classify_prehandle(cap["prehandle_resp"])
            log("risk", f"prehandle kind={kind}")
            if kind == risk.KIND_PLACEHOLDER:
                outcome["risk_hold"] = True
                outcome["history"].append({"attempt": attempt, "error": "placeholder prehandle"})
                log("risk", "placeholder (empty ticket): widget will never submit a verify - "
                            "stopping instead of burning attempts")
                if cfg.placeholder_backoff > 0:
                    log("risk", f"sleeping {cfg.placeholder_backoff:.0f}s and retrying once")
                    page.wait_for_timeout(int(cfg.placeholder_backoff * 1000))
                    outcome["risk_hold"] = False
                    if not reenter("risk-wait"):
                        break
                    continue
                break

            NetworkTap.wait_for_images(ctx, page, cap, timeout_ms=20000)
            log("round", f"{attempt}: resp={'Y' if cap['prehandle_resp'] else 'n'} "
                         f"images={sorted(cap['images'])}")

            try:
                piece, logical = risk.challenge_geometry(cap["prehandle_resp"])
            except ValueError as exc:
                log("prehandle", str(exc))
                outcome["history"].append({"attempt": attempt, "error": str(exc)})
                if not reenter("reparse"):
                    break
                continue
            log("piece", f"id={piece.get('id')} size={piece['size_2d']} "
                         f"init={piece['init_pos']} logical={logical}")

            bg_b, fg_b = cap["images"].get("bg"), cap["images"].get("fg")
            if not bg_b or not fg_b:
                outcome["history"].append({"attempt": attempt, "error": "images missing"})
                if not reenter("no-images"):
                    break
                continue

            try:
                det = detect(bg_b, fg_b, piece)
            except Exception as exc:  # noqa: BLE001 - corrupt/truncated bytes
                log("detect", f"image decode failed: {exc!r}; refreshing")
                outcome["history"].append({"attempt": attempt, "error": f"bad images: {exc!r}"})
                cap["images"] = {}
                cap["prehandle_resp"] = ""
                if not reenter("bad-images"):
                    break
                continue

            open(f"{cfg.out_dir}/bg.png", "wb").write(bg_b)
            open(f"{cfg.out_dir}/fg.png", "wb").write(fg_b)
            json.dump(det, open(f"{cfg.out_dir}/detect.json", "w"), indent=1)
            json.dump({"piece": piece, "logical_bg": logical, "attempt": attempt},
                      open(f"{cfg.out_dir}/geometry.json", "w"), indent=1)
            _annotate(cfg.out_dir, bg_b, det, piece)
            log("detect", json.dumps(det)[:1200])

            decision = decide(det, f"snap-{attempt}-{int(time.time())}", cfg)
            chosen = decision.action
            log("arbiter", f"attempt={attempt} value={chosen} status={decision.status} "
                           f"p={decision.probability} via={decision.source}")
            outcome["history"].append({"attempt": attempt, "action": chosen,
                                       "status": decision.status,
                                       "p": decision.probability, "det": det})
            _write_outcome(cfg, outcome)

            if chosen == "abort":
                log("stop", "arbiter chose abort")
                break
            if chosen == "refresh":
                log("refresh", "arbiter asked for a new challenge")
                if not reenter(f"refresh-{attempt}"):
                    break
                page.wait_for_timeout(1200)
                continue

            candidate = dict(decision.candidates.get(chosen) or {})
            if not candidate:
                log("drag", f"action {chosen} has no candidate; refreshing")
                if not reenter(f"nocand-{attempt}"):
                    break
                continue
            candidate["_label"] = chosen

            result = drag.execute(page, frame, logical, candidate)
            if result is None:
                if not reenter(f"drag-fail-{attempt}"):
                    break
                continue

            for _ in range(40):                 # wait for the verify POST
                page.wait_for_timeout(500)
                if cap["verify_resp"]:
                    break
            raw = cap.get("verify_resp", "")
            log("verify-resp", repr(raw)[:400])
            with open(f"{cfg.out_dir}/verify_resp.json", "w", encoding="utf-8") as fh:
                fh.write(raw)

            error_code = risk.parse_verify(raw)
            outcome["history"][-1]["errorCode"] = error_code
            _write_outcome(cfg, outcome)

            if error_code == "0":
                outcome["ok"] = True
                log("SUCCESS", f"errorCode=0 on attempt {attempt}")
                break

            wait = risk.backoff_seconds(error_code, cfg)
            if wait:
                outcome["history"][-1]["ratelimited"] = True
                log("ratelimit", f"errorCode={error_code} (IP/fingerprint risk); "
                                 f"backing off {wait:.0f}s")
                page.wait_for_timeout(int(wait * 1000))
                if not reenter("after-backoff"):
                    break
                page.wait_for_timeout(1500)
                continue

            log("fail", f"errorCode={error_code}; refreshing for a new challenge")
            if not reenter(f"retry-{attempt}"):
                break
            page.wait_for_timeout(1200)

        outcome["duration_s"] = round(time.time() - started, 1)
        try:
            page.screenshot(path=f"{cfg.out_dir}/after.png")
        except Exception:  # noqa: BLE001
            pass

    _finalize(cfg, outcome, cap, None, None)
    return outcome


def _write_outcome(cfg: Config, outcome: dict) -> None:
    json.dump(outcome, open(f"{cfg.out_dir}/outcome.json", "w"), indent=1)


def _finalize(cfg: Config, outcome: dict, cap: dict, _pg, _b) -> None:
    _write_outcome(cfg, outcome)
    slim = {k: v for k, v in cap.items() if k != "images"}
    json.dump(slim, open(f"{cfg.out_dir}/capture.json", "w"), indent=1)
    log("outcome", f"ok={outcome['ok']} attempts={outcome['attempts']} "
                   f"risk_hold={outcome.get('risk_hold')} dir={cfg.out_dir}")
