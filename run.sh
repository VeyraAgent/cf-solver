#!/usr/bin/env bash
# CF-Solver — run the gateway. Handles venv, xvfb (headless), port, and health.
#   ./run.sh                 # xvfb auto, port 8877
#   PORT=9000 ./run.sh       # custom port
#   HEADED=1 ./run.sh        # force a real display (no xvfb)
#   ./run.sh --reload        # dev auto-reload
# Env: PORT VENV HEADED HOST SOLVER_TOKEN BROWSER_HEADLESS SKIP_XVFB
set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; OK=$'\033[32m'; WARN=$'\033[33m'; ERR=$'\033[31m'; RST=$'\033[0m'
ok()   { printf '%s✔%s %s\n' "$OK" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$WARN" "$RST" "$*"; }
die()  { printf '%s✘%s %s\n' "$ERR" "$RST" "$*" >&2; exit 1; }

PORT="${PORT:-8877}"
HOST="${HOST:-0.0.0.0}"
VENV="${VENV:-.venv}"

# ── venv ─────────────────────────────────────────────────────────────────
if [ -f "$VENV/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  ok "venv $VENV"
elif [ -d "$VENV" ]; then
  die "$VENV has no bin/activate — delete it and re-run ./install.sh"
else
  warn "no venv at $VENV — run ./install.sh first (continuing with system python)"
fi
PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || die "python3 not found"
"$PY" -c 'import fastapi,uvicorn' 2>/dev/null || die "deps missing — run ./install.sh"

# ── port free? ───────────────────────────────────────────────────────────
if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
  die "port ${PORT} already in use — set PORT=<other> ./run.sh"
fi

# ── display: xvfb when headless, unless told otherwise ───────────────────
# NOTE: prefer xvfb-run whenever it exists. On WSL, $DISPLAY is set by WSLg but
# chromium launched as a subprocess fails X auth on it ("Authorization required,
# but no authorization protocol specified") — xvfb-run overrides DISPLAY cleanly.
CMD=("$PY" server.py "$@")
USE_XVFB=0
if [ "${HEADED:-0}" != "1" ] && [ "${SKIP_XVFB:-0}" != "1" ]; then
  if command -v xvfb-run >/dev/null; then
    USE_XVFB=1
  elif [ -n "${DISPLAY:-}" ]; then
    ok "no xvfb-run — falling back to DISPLAY=$DISPLAY"
  else
    warn "no DISPLAY and no xvfb-run — browser types will fail. ./install.sh installs xvfb."
  fi
fi

export PORT HOST
[ "$USE_XVFB" = "1" ] && export BROWSER_HEADLESS="${BROWSER_HEADLESS:-0}"

# ── banner + health wait ─────────────────────────────────────────────────
printf '%s\n' "${DIM}────────────────────────────────────────────${RST}"
printf '%sCF-Solver%s  port %s%s%s  %s\n' "$BOLD" "$RST" "$BOLD" "$PORT" "$RST" \
  "$([ "$USE_XVFB" = "1" ] && echo '· xvfb (headless)' || echo '· direct display')"
printf '  health  %shttp://localhost:%s/health%s\n' "$DIM" "$PORT" "$RST"
printf '  docs    %shttp://localhost:%s/docs%s\n' "$DIM" "$PORT" "$RST"
printf '%s\n' "${DIM}────────────────────────────────────────────${RST}"

if [ "$USE_XVFB" = "1" ]; then
  exec xvfb-run -a -s "-screen 0 1920x1080x24" "${CMD[@]}"
else
  exec "${CMD[@]}"
fi
