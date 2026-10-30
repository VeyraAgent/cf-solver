"""Command line interface.

    tcaptcha solve     run one solve (credentials via env/CLI, never source)
    tcaptcha doctor    environment / dependency / connectivity check
    tcaptcha detect    run the detectors offline on saved challenge images
    tcaptcha classify  classify a saved prehandle response
    tcaptcha env       print a ready-to-fill .env template (no secrets)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, flows
from .config import (EMAIL_ENV, OUT_ENV, PASSWORD_ENV, PROXY_ENV, PROXY_FILE_ENV,
                     Config, ConfigError, redact)
from .log import die, log


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--proxy", help=f"single proxy url (else ${PROXY_ENV})")
    p.add_argument("--proxy-file", help=f"file with one proxy per line (else ${PROXY_FILE_ENV})")
    p.add_argument("--out", help=f"artifact directory (else ${OUT_ENV})")
    p.add_argument("--headed", action="store_true", help="run with a visible browser")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tcaptcha",
        description=f"tcaptcha {__version__} - Tencent TCaptcha slider solver")
    ap.add_argument("--version", action="version", version=f"tcaptcha {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("solve", help="solve one captcha")
    _add_common(s)
    s.add_argument("--flow", default="minimax-login",
                   help=f"flow adapter: {', '.join(flows.available()) or 'minimax-login'}")
    s.add_argument("--email", help=f"account e-mail (else ${EMAIL_ENV})")
    s.add_argument("--password", help=f"account password (else ${PASSWORD_ENV})")
    s.add_argument("--arbiter", choices=("jev", "heuristic"), default="jev",
                   help="action arbiter: LLM evidence arbitration or pure rule")
    s.add_argument("--min-probability", type=float, default=0.55)
    s.add_argument("--max-attempts", type=int, default=4)
    s.add_argument("--backoff", type=float, default=70.0,
                   help="seconds to sleep on errorCode=12 (default 70)")
    s.add_argument("--placeholder-backoff", type=float, default=0.0,
                   help="seconds to wait when risk-control returns a placeholder "
                        "(0 = stop immediately, which is the safe default)")
    s.add_argument("--frame-timeout", type=float, default=40.0)

    d = sub.add_parser("doctor", help="check dependencies, config and connectivity")
    _add_common(d)

    t = sub.add_parser("detect", help="run detectors offline on saved images")
    t.add_argument("--bg", required=True, help="background image (png)")
    t.add_argument("--fg", required=True, help="foreground/piece sprite (png)")
    t.add_argument("--piece", required=True, help="json file with sprite_pos/size_2d/init_pos")
    t.add_argument("--json", action="store_true", help="print raw detector evidence as json")

    c = sub.add_parser("classify", help="classify a saved prehandle response")
    c.add_argument("--file", required=True)

    sub.add_parser("env", help="print a .env template")
    return ap


# ---------------------------------------------------------------- commands --
def cmd_solve(args) -> int:
    from .engine import solve
    cfg = Config.build(args)
    outcome = solve(cfg)
    print("=== OUTCOME ===")
    print(json.dumps(outcome, indent=1)[:4000])
    return 0 if outcome["ok"] else (3 if not outcome.get("risk_hold") else 4)


def cmd_doctor(args) -> int:
    cfg = Config.build(args)
    ok = True

    def check(label, fn):
        nonlocal ok
        try:
            detail = fn()
            log("doctor", f"OK   {label}: {detail}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            log("doctor", f"FAIL {label}: {exc}")

    check("python", lambda: sys.version.split()[0])
    check("playwright", lambda: __import__("importlib.metadata",
                                           fromlist=["version"]).version("playwright"))

    def browser_exe():
        # Provisions Chromium on demand (python -m playwright install chromium).
        from playwright.sync_api import sync_playwright as _sp
        pw = _sp().start()
        try:
            return __import__("tcaptcha.browser", fromlist=["x"]).ensure_chromium(pw)
        finally:
            pw.stop()

    check("browser", browser_exe)
    check("opencv", lambda: __import__("cv2").__version__)
    check("numpy", lambda: __import__("numpy").__version__)
    check("pillow", lambda: __import__("PIL").__version__)
    check("flows", lambda: ", ".join(flows.available()))

    def creds():
        have = [n for n, v in ((EMAIL_ENV, cfg.email), (PASSWORD_ENV, cfg.password)) if v]
        missing = [n for n in (EMAIL_ENV, PASSWORD_ENV)
                   if not ((EMAIL_ENV == n and cfg.email) or (PASSWORD_ENV == n and cfg.password))]
        if missing:
            raise ConfigError(f"missing {', '.join(missing)} (required by login flows)")
        return f"set ({', '.join(have)})"

    check("credentials", creds)
    log("doctor", f"INFO proxy: {cfg.proxy_label} (redacted={redact(cfg.proxy or '')})")
    log("doctor", f"INFO out:   {cfg.out_dir}")

    if cfg.proxy:
        def _health():
            from .browser import MAX_PROXY_CANDIDATES, probe_proxy
            tried = (cfg.proxy_candidates or [cfg.proxy])[:MAX_PROXY_CANDIDATES]
            fails = []
            for i, cand in enumerate(tried, 1):
                ok_p, detail = probe_proxy(cand)
                log("doctor", f"INFO proxy candidate {i}/{len(tried)} "
                              f"{redact(cand)}: {'OK' if ok_p else 'FAIL'} ({detail})")
                if not ok_p:
                    fails.append(f"{i}: {detail}")
            if fails and len(fails) == len(tried):
                raise ConfigError(f"no healthy proxy candidate ({len(tried)} tried)")
            return f"{len(tried) - len(fails)}/{len(tried)} healthy"
        check("proxy health", _health)
        check("proxy assets", lambda: f"{len(__import__('tcaptcha.browser', fromlist=['x'])
                                             .prefetch_assets(cfg.proxy))} assets cached")

    def arbiter():
        if cfg.arbiter != "jev":
            return f"{cfg.arbiter} (no external dependency)"
        import shutil
        if not shutil.which("jev-decide"):
            raise ConfigError("jev-decide not on PATH - use --arbiter heuristic")
        return "jev-decide on PATH"

    check("arbiter", arbiter)
    log("doctor", "READY" if ok else "NOT READY")
    return 0 if ok else 1


def cmd_detect(args) -> int:
    from .detect import cluster, detect
    with open(args.piece, "r", encoding="utf-8") as fh:
        piece = json.load(fh)
    det = detect(open(args.bg, "rb").read(), open(args.fg, "rb").read(), piece)
    verdict = cluster(det)
    if args.json:
        print(json.dumps(det, indent=1))
    else:
        for key in ("ncc", "canny", "contour", "dark", "hole"):
            print(f"  {key:9s} {json.dumps(det[key])}")
        print(f"  consensus agree={verdict['agree']} target_x={verdict['target_x']} "
              f"blocker={verdict['blocker']}")
    return 0 if verdict["agree"] else 5


def cmd_classify(args) -> int:
    from .risk import classify_prehandle, parse_prehandle
    text = open(args.file, "r", encoding="utf-8", errors="replace").read()
    kind = classify_prehandle(text)
    data = parse_prehandle(text)
    print(f"class={kind}")
    if data:
        try:
            inner = data.get("data", data)
            print(f"ticket_len={len(inner.get('ticket') or '')}")
        except Exception:  # noqa: BLE001
            pass
    return 0 if kind == "challenge" else 4


def cmd_env(_args) -> int:
    print(f"# tcaptcha environment template - fill in, do NOT commit")
    print(f"{EMAIL_ENV}=")
    print(f"{PASSWORD_ENV}=")
    print(f"{PROXY_ENV}=http://user:pass@host:port")
    print(f"# {PROXY_FILE_ENV}=/path/to/proxies.txt")
    print(f"{OUT_ENV}=/tmp/tcaptcha")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handler = {"solve": cmd_solve, "doctor": cmd_doctor, "detect": cmd_detect,
               "classify": cmd_classify, "env": cmd_env}[args.cmd]
    try:
        return handler(args)
    except ConfigError as exc:
        die(str(exc))
    except KeyboardInterrupt:
        die("interrupted", 130)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
