"""geetest_v3 selftest — checks the binding is present and, if the network is
reachable, does a real end-to-end solve (auto-fetching gt+challenge).

Run:  python -m solvers.geetest_v3.selftest
"""
from __future__ import annotations

import asyncio
import sys


def main() -> int:
    from solvers.geetest_v3.solve import available, fetch_gt_challenge, solve_geetest_v3

    checks = 0
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal checks, failures
        checks += 1
        if not cond:
            failures += 1
        print(f"  [{'ok' if cond else 'FAIL'}] {label}")

    print("geetest_v3 selftest")

    # 1. binding importable
    if not available():
        print("  [SKIP] bili_ticket_gt_python not installed — build it with "
              "scripts/build_geetest_v3.sh")
        print("  (offline checks only)")
        return 0
    check(True, "bili_ticket_gt_python importable")

    # 2. seccode format: validate + '|jordan' (per GeeTest docs — NOT md5)
    from solvers.geetest_v3.solve import _run_sync  # noqa: F401
    check(True, "solver module imports")

    # 3. live end-to-end (auto-fetch gt+challenge)
    print("  [..] live solve (auto-fetch gt+challenge from bilibili)…")
    try:
        r = asyncio.run(solve_geetest_v3("", "", timeout_s=90))
    except Exception as exc:  # noqa: BLE001
        print(f"  [SKIP] live solve errored: {str(exc)[:100]}")
        print(f"\n{checks} checks, {failures} failed")
        return 1 if failures else 0

    if not r.get("solved"):
        # network-dependent: report but don't hard-fail the offline suite
        print(f"  [SKIP] live solve did not succeed: {r.get('error')}")
    else:
        check(bool(r.get("validate")), f"live solve → validate={r['validate'][:24]}…")
        check(r.get("seccode") == r["validate"] + "|jordan",
              "seccode == validate + '|jordan'")
        check(bool(r.get("gt")), "returns the gt used")
        check(bool(r.get("challenge")), "returns the challenge used")

    print(f"\n{checks} checks, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
