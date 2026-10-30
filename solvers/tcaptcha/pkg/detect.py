"""Gap detection: five independent detectors, then a consensus.

Every detector below was validated against real challenges; the notes record
what each one is actually good at, because they fail in *correlated* ways.

    ncc      template match of the piece's opaque RGB onto the background.
    canny    Canny edges x piece contour (CaptchaCracker style).
    contour  Otsu + dilate + area/size/row filtered blobs.
    dark     piece shape x relative-darkness map, ranked by surviving interior
             texture: a real hole keeps the scene running through it, a solid
             decoy is flat and dark.
    hole     interior/ring brightness ratio at outline peaks.

Consensus rules (measured, not guessed):
  * ncc/canny/dark are all shape-or-darkness matchers and can lock onto the
    SAME decoy together - a pair is necessary but not sufficient, dark must
    also agree within 8px.
  * the hole detector reads the interior and therefore sees through the decoy,
    but it was trialled as an override and lost 5/5 (errorCode 50) while 3/3
    runs without an override passed - so it stays EVIDENCE ONLY.
  * with no cluster and no credible hole there is no supported coordinate;
    guessing costs the challenge, so the caller must refresh instead.
"""
from __future__ import annotations

import io
from typing import Any

import cv2
import numpy as np
from PIL import Image

CLUSTER_PX = 8


def crop_piece(fg_bytes: bytes, piece: dict) -> np.ndarray:
    """RGBA crop of the slider piece out of the foreground sprite."""
    fg = np.array(Image.open(io.BytesIO(fg_bytes)).convert("RGBA"))
    px, py = piece["sprite_pos"]
    pw, ph = piece["size_2d"]
    return fg[py:py + ph, px:px + pw]


def detect(bg_bytes: bytes, fg_bytes: bytes, piece: dict) -> dict[str, Any]:
    """Run all detectors and return their evidence (host-owned, no LLM)."""
    bg = np.array(Image.open(io.BytesIO(bg_bytes)).convert("RGB"))
    pw, ph = piece["size_2d"]
    init_x, init_y = piece["init_pos"]
    h, w = bg.shape[:2]
    out: dict[str, Any] = {}

    crop = crop_piece(fg_bytes, piece)
    alpha = crop[:, :, 3]
    mask = alpha > 128

    # --- detector 1: NCC over the piece's opaque pixels -----------------
    rgb = crop[:, :, :3].astype(np.float32)[mask]
    pc = rgb - rgb.mean()
    pn = float(np.sqrt((pc ** 2).sum())) + 1e-8
    bgf = bg[:, :, :3].astype(np.float32)
    best = (-2.0, init_x, init_y)
    for y in range(max(0, init_y - 6), min(h - ph, init_y + 6) + 1):
        for x in range(0, w - pw + 1, 3):
            patch = bgf[y:y + ph, x:x + pw][mask]
            if patch.size != pc.size or patch.size == 0:
                continue
            fc = patch - patch.mean()
            fn = float(np.sqrt((fc ** 2).sum())) + 1e-8
            score = float((fc * pc).sum() / (fn * pn))
            if score > best[0]:
                best = (score, x, y)
    out["ncc"] = {"x": best[1], "y": best[2], "score": round(best[0], 4)}

    # --- detector 2: Canny edges x piece contour ------------------------
    _, binm = cv2.threshold(alpha, 128, 255, cv2.THRESH_BINARY)
    cs, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ci = np.zeros_like(binm)
    cv2.drawContours(ci, cs, -1, 255, 2)
    gray = cv2.cvtColor(cv2.GaussianBlur(bg, (3, 3), 0), cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 150, 250)
    res = cv2.matchTemplate(edges, ci, cv2.TM_CCORR)
    _, mx, _, loc = cv2.minMaxLoc(res)
    out["canny"] = {"x": int(loc[0]), "y": int(loc[1]), "score": round(float(mx), 1)}

    # --- detector 3: threshold + contour area filter --------------------
    piece_area = pw * ph
    gray2 = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray2, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    edges2 = cv2.Canny(th, 25, 75)
    edges2 = cv2.dilate(edges2, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges2, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    votes = []
    for c in contours:
        a = cv2.contourArea(c)
        if not (0.35 * piece_area < a < 1.6 * piece_area):
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        if abs(ch - ph) > 26 or abs(cw - pw) > 34:
            continue
        if abs(y - init_y) > 22:          # the gap lives on the piece's row
            continue
        votes.append((x, y))
    # A scattered vote means the filter caught something other than the gap.
    if votes:
        xs = sorted(v[0] for v in votes)
        spread = xs[-1] - xs[0]
        if spread <= 14:
            out["contour"] = {"x": int(np.median(xs)),
                              "y": int(np.median([v[1] for v in votes])),
                              "score": len(votes)}
        else:
            out["contour"] = {"x": None, "y": None, "score": 0,
                              "rejected": f"scattered votes {xs} (spread {spread}px)"}
    else:
        out["contour"] = {"x": None, "y": None, "score": 0}
    out["contour_votes"] = votes

    # --- detector 4: shape x relative darkness, ranked by texture -------
    shape = (alpha > 128).astype(np.float32)
    blur = cv2.GaussianBlur(gray2.astype(np.float32), (0, 0), 25)
    dmap = np.clip(blur - gray2.astype(np.float32), 0, 255).astype(np.uint8)
    tmpl = (shape * 255).astype(np.uint8)
    corr2d = cv2.matchTemplate(dmap, tmpl, cv2.TM_CCOEFF_NORMED)
    y_lo = max(0, init_y - 6)
    y_hi = min(corr2d.shape[0], init_y + 7)
    corr = corr2d[y_lo:y_hi].max(axis=0)
    ins_edge: dict[int, float] = {}
    for x in range(0, w - pw + 1):
        win = gray2[init_y:init_y + ph, x:x + pw].astype(np.float32)
        if win.shape != (ph, pw):
            continue
        gx = cv2.Sobel(win, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(win, cv2.CV_32F, 0, 1)
        ins_edge[x] = float(np.hypot(gx, gy)[alpha > 128].mean())
    peaks = []
    for i in range(1, len(corr) - 1):
        if corr[i] >= corr[i - 1] and corr[i] > corr[i + 1] and corr[i] > 0.35:
            x = i
            peaks.append((float(corr[i]) * ins_edge.get(x, 0.0), x,
                          float(corr[i]), ins_edge.get(x, 0.0)))
    peaks.sort(reverse=True)
    out["dark_peaks"] = [{"x": int(p[1]), "corr": round(p[2], 3),
                          "interior_edge": round(p[3], 1), "score": round(p[0], 1)}
                         for p in peaks[:5]]
    out["dark"] = ({"x": int(peaks[0][1]), "score": round(peaks[0][0], 1),
                    "corr": round(peaks[0][2], 3),
                    "interior_edge": round(peaks[0][3], 1)}
                   if peaks else {"x": None, "score": 0})

    # --- detector 5: hole vs opaque decoy (interior/ring ratio) ---------
    # Both the hole and a decoy carry a bright rim, so outline strength alone
    # picks the decoy (measured 45.3M vs 31.5M). The interior separates them:
    # a hole keeps the background running through it (ratio >= ~0.9), a decoy
    # is opaque and much darker (ratio ~0.5).
    edges_map = cv2.Canny(gray2.astype(np.uint8), 120, 220)
    mm = (alpha > 128).astype(np.uint8)
    o_out = cv2.dilate(mm, np.ones((5, 5), np.uint8)) > 0
    o_in = cv2.erode(mm, np.ones((5, 5), np.uint8)) > 0
    outline_tmpl = ((o_out & ~o_in).astype(np.uint8)) * 255
    band2d = cv2.matchTemplate(edges_map, outline_tmpl, cv2.TM_CCORR)
    yl = max(0, init_y - 6)
    yh = min(band2d.shape[0], init_y + 7)
    band = band2d[yl:yh].max(axis=0)
    hp = []
    bmax = float(band.max()) if band.size else 0.0
    for i in range(1, len(band) - 1):
        if band[i] >= band[i - 1] and band[i] > band[i + 1] and bmax and band[i] > 0.45 * bmax:
            x = i
            win = gray2[init_y:init_y + ph, x:x + pw].astype(np.float32)
            if win.shape != (ph, pw):
                continue
            inside = float(win[alpha > 128].mean())
            ring = float(win[~(alpha > 128)].mean())
            hp.append({"x": int(x), "ratio": round(inside / max(ring, 1e-6), 3),
                       "band": round(float(band[i])), "inside": round(inside, 1)})
    hp.sort(key=lambda d: -d["ratio"])
    # Ranking on ratio alone is a trap: plain background also yields ratio ~1,
    # which once picked x=74 right next to the piece start. Filter on ratio
    # first, then take the STRONGEST outline among survivors.
    good = [d for d in hp if d["ratio"] >= 0.90 and d["band"] >= 0.45 * bmax]
    good.sort(key=lambda d: -d["band"])
    out["hole"] = ({"x": good[0]["x"], "ratio": good[0]["ratio"],
                    "band": good[0]["band"], "inside": good[0]["inside"]}
                   if good else {"x": None, "ratio": 0, "band": 0})
    out["hole_peaks"] = hp[:6]

    out["bg_size"] = [int(w), int(h)]
    out["init_pos"] = [int(init_x), int(init_y)]
    return out


def cluster(det: dict) -> dict:
    """Cluster detector x-coordinates (8px) and derive the consensus verdict."""
    present = {k: det[k]["x"] for k in ("ncc", "canny", "contour", "dark")
               if det[k]["x"] is not None}
    votes_by_x: dict[int, list] = {}
    for k, x in present.items():
        votes_by_x.setdefault(x, []).append(k)
    clusters = []
    for x in sorted(votes_by_x):
        if clusters and x - clusters[-1]["x"] <= CLUSTER_PX:
            prev = clusters[-1]
            prev["x"] = (prev["x"] * prev["n"] + x * len(votes_by_x[x])) // (prev["n"] + len(votes_by_x[x]))
            prev["n"] += len(votes_by_x[x])
            prev["src"] += votes_by_x[x]
        else:
            clusters.append({"x": x, "n": len(votes_by_x[x]), "src": list(votes_by_x[x])})
    clusters.sort(key=lambda c: -c["n"])
    top = clusters[0] if clusters else None

    dark_x = det["dark"]["x"]
    hole_x = (det.get("hole") or {}).get("x")
    pair_ok = bool(top and top["n"] >= 2)
    dark_ok = dark_x is None or (top and abs(dark_x - top["x"]) <= CLUSTER_PX)
    agree = bool(pair_ok and dark_ok)

    target_x = top["x"] if agree else None
    y_vote = None
    if top:
        y_vote = next((det[k]["y"] for k in top["src"] if det[k].get("y") is not None), None)
    if y_vote is None:
        y_vote = det["ncc"]["y"]

    blocker = None
    if not agree:
        if not pair_ok:
            blocker = "no ncc/canny pair cluster"
        else:
            blocker = f"detectors disagree (dark={dark_x}, hole={hole_x}, cluster={top['x']})"

    return {"clusters": clusters, "top": top, "pair_ok": pair_ok, "dark_ok": bool(dark_ok),
            "agree": agree, "target_x": target_x, "y_vote": y_vote,
            "dark_x": dark_x, "hole_x": hole_x, "blocker": blocker,
            "init_x": det["init_pos"][0]}


def candidates(det: dict) -> dict:
    """Legal drag candidates keyed by action name (only when evidence supports)."""
    verdict = cluster(det)
    init_x = verdict["init_x"]
    cands: dict[str, dict] = {}

    def add(key, x, y, origin, evidence):
        cands[key] = {"dx": int(x - init_x), "x": int(x), "y": int(y),
                      "origin": origin, "evidence": evidence}

    if not verdict["agree"]:
        return cands

    top = verdict["top"]
    add("drag_target", verdict["target_x"], verdict["y_vote"],
        f"Cluster of {top['n']}/4 detectors: {','.join(top['src'])}",
        f"clustered at x={verdict['target_x']}")

    ncc, canny, cont, dark = det["ncc"], det["canny"], det["contour"], det["dark"]
    add("drag_ncc", ncc["x"], ncc["y"], "NCC template match on piece alpha",
        f"similarity={ncc['score']} at x={ncc['x']}")
    if canny["x"] is not None:
        add("drag_canny", canny["x"], canny["y"], "Canny edge x piece-contour correlation",
            f"score={canny['score']} at x={canny['x']}")
    if cont["x"] is not None:
        add("drag_contour", cont["x"], cont["y"], "Threshold + contour area filter",
            f"{cont['score']} contour votes at x={cont['x']}")
    if dark["x"] is not None:
        add("drag_dark", dark["x"], verdict["y_vote"],
            "Shape x relative-darkness match, ranked by surviving interior texture",
            f"corr={dark.get('corr')} interior_edge={dark.get('interior_edge')} at x={dark['x']}")
    for i, pk in enumerate(det.get("dark_peaks", [])[:2]):
        if pk["x"] in (dark["x"], verdict["target_x"]):
            continue
        add(f"drag_dark_alt{i + 1}", pk["x"], verdict["y_vote"],
            "Secondary dark blob (possible decoy)",
            f"corr={pk['corr']} interior_edge={pk['interior_edge']} at x={pk['x']}")
    return cands
