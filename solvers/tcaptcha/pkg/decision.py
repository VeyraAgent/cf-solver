"""Arbiter: turn detector evidence into exactly one legal action.

Two arbiters are supported because both were part of the working pipeline:

    heuristic   pure local rule - cluster >= 2 and dark agrees -> drag_target,
                otherwise refresh. Deterministic, no network, no tokens.
    jev         LLM arbitration over *numbers only* (never pixels) via the
                `jev-decide` CLI, with a min-probability gate. Used when the
                rule is unsure but the evidence is rich.

Both arbiters are constrained to the same action set:
    drag_* | refresh | abort
and a drag is only ever offered when a detector cluster exists - a bare guess
costs the challenge (errorCode 50, observed repeatedly).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from .detect import candidates, cluster
from .log import log

ACTION_DRAG_PREFIX = "drag_"
ACTION_REFRESH = "refresh"
ACTION_ABORT = "abort"


@dataclass
class Decision:
    action: str
    status: str            # selected | needs_review | ...
    probability: float | None
    source: str            # heuristic | jev
    candidates: dict
    verdict: dict


def _default_action(verdict: dict) -> str:
    if verdict["agree"]:
        return "drag_target"
    return ACTION_REFRESH


def heuristic(det: dict) -> Decision:
    cands = candidates(det)
    verdict = cluster(det)
    action = _default_action(verdict) if cands else ACTION_REFRESH
    return Decision(action=action, status="selected", probability=1.0,
                    source="heuristic", candidates=cands, verdict=verdict)


def build_request(det: dict, snapshot_id: str) -> tuple[dict, dict]:
    """Shape the evidence payload the LLM arbiter consumes (jev-act format)."""
    verdict = cluster(det)
    cands = candidates(det)
    top = verdict["top"]
    hole = det.get("hole", {})
    dark_x = verdict["dark_x"]
    hole_x = verdict["hole_x"]

    if verdict["agree"]:
        note = ("A detector cluster exists, so drag_target is legal. The shape "
                "detectors can still be wrong together, so treat drag_target as "
                "supported-but-not-certain and use refresh if anything looks off.")
    else:
        note = ("NO coordinate is supported by evidence: no detector cluster. "
                "Submitting a guess risks errorCode 50 and kills the challenge. "
                "Only refresh or abort are legal here.")

    criteria = {k: f"Drag slider {v['dx']}px to land the piece at x={v['x']}, y={v['y']}. "
                   f"Source: {v['origin']}. Evidence: {v['evidence']}."
                for k, v in cands.items()}
    criteria[ACTION_REFRESH] = ("Ask the host to reload the captcha and re-observe. Choose "
                                "this whenever no supported drag coordinate exists, or the "
                                "evidence looks weak.")
    criteria[ACTION_ABORT] = "Stop instead of submitting an unsupported drag."

    state = {
        "snapshot_id": snapshot_id,
        "goal": "Drag the TCaptcha slider so the puzzle piece lands in the gap; "
                "the host executes the drag and the server returns pass/fail.",
        "surface": "TCaptcha slider (iframe embedded by the host page)",
        "detectors_agree": verdict["agree"],
        "consensus_x": verdict["target_x"],
        "pair_cluster_x": top["x"] if (top and verdict["pair_ok"]) else None,
        "dark_x": dark_x,
        "blocker": verdict["blocker"],
        "clusters": verdict["clusters"],
        "evidence": {k: det[k] for k in ("ncc", "canny", "contour", "dark", "dark_peaks",
                                         "hole", "hole_peaks", "init_pos", "bg_size")},
        "candidates": cands,
        "allowed_actions": list(criteria),
        "notes": ("Arbiter receives numeric detector evidence only; it cannot see the image. "
                  + note + " Context from prior trials: the real hole keeps scene texture "
                  "inside it (high interior_edge) while a decoy is a solid flat dark blob "
                  "(high corr but lower interior_edge). The hole detector is evidence-only: "
                  "its override lost 5/5 trials."),
    }
    request = {
        "state": state,
        "questions": {"action": {"type": "choice",
                                 "instructions": "Choose the single legal next action toward "
                                                 "the goal using only the observed snapshot "
                                                 "evidence.",
                                 "criteria": criteria}},
    }
    return request, cands


def jev(det: dict, snapshot_id: str, cfg) -> Decision:
    """LLM arbitration over detector numbers (requires the `jev-decide` CLI)."""
    verdict = cluster(det)
    request, cands = build_request(det, snapshot_id)
    binary = shutil.which("jev-decide")
    if not binary:
        log("arbiter", "jev-decide not on PATH; falling back to heuristic")
        return heuristic(det)
    path = f"{cfg.out_dir}/arbiter_request.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(request, fh, indent=1)
    try:
        proc = subprocess.run(
            ["jev-decide", "decide", path, "--provider", "typesafe",
             "--min-probability", str(cfg.min_probability)],
            capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        log("arbiter", f"jev-decide error: {exc}; falling back to heuristic")
        return heuristic(det)
    if proc.returncode == 1:
        log("arbiter", f"jev-decide declined: {proc.stderr.strip()[:200]}; heuristic")
        return heuristic(det)
    try:
        report = json.loads(proc.stdout)
        chosen = report["decisions"]["action"]
        action = chosen["value"]
        status = chosen.get("status", "selected")
        prob = chosen.get("probability")
    except Exception as exc:  # noqa: BLE001
        log("arbiter", f"unparsable jev output: {exc}; heuristic")
        return heuristic(det)
    with open(f"{cfg.out_dir}/arbiter_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    if action not in cands and action not in (ACTION_REFRESH, ACTION_ABORT):
        action = ACTION_REFRESH
    return Decision(action=action, status=status, probability=prob,
                    source="jev", candidates=cands, verdict=verdict)


def decide(det: dict, snapshot_id: str, cfg) -> Decision:
    if cfg.arbiter == "jev":
        return jev(det, snapshot_id, cfg)
    return heuristic(det)
