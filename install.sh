#!/usr/bin/env bash
# CF-Solver — full installer. Installs EVERYTHING the 50 types need.
#   python deps · chromium · xvfb · node sidecar · playwright · optional arkose models
# Idempotent: safe to re-run. Env knobs: PYTHON, SKIP_SYS, WITH_ARKOSE=1, YES=1
set -uo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; OK=$'\033[32m'; WARN=$'\033[33m'; ERR=$'\033[31m'; RST=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✔%s %s\n' "$OK" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$WARN" "$RST" "$*"; }
die()  { printf '%s✘%s %s\n' "$ERR" "$RST" "$*" >&2; exit 1; }
hr()   { printf '%s\n' "${DIM}────────────────────────────────────────────${RST}"; }

YES="${YES:-0}"; SKIP_SYS="${SKIP_SYS:-0}"; WITH_ARKOSE="${WITH_ARKOSE:-0}"
ask() { # ask "<prompt>" -> 0 yes / 1 no
  [ "$YES" = "1" ] && return 0
  local a; read -r -t 20 -p "$1 [y/N] " a || a="n"
  [[ "${a:-n}" =~ ^[yY]$ ]]
}
SUDO=""; [ "$(id -u)" != "0" ] && command -v sudo >/dev/null && SUDO="sudo"

hr; say "${BOLD}CF-Solver installer${RST}  ·  python + chromium + xvfb + node + playwright"; hr

# ── 1. python ────────────────────────────────────────────────────────────
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || die "python3 not found — install python 3.11+ first"
PYV="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
"$PY" -c 'import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)' \
  || die "python $PYV too old — need 3.11+"
ok "python $PYV"

# ── 2. system packages (chromium + xvfb + node) ───────────────────────────
need_sys=0
command -v node >/dev/null || need_sys=1
command -v Xvfb >/dev/null || need_sys=1
command -v chromium >/dev/null || command -v chromium-browser >/dev/null || command -v google-chrome >/dev/null || need_sys=1

if [ "$SKIP_SYS" = "1" ]; then
  warn "SKIP_SYS=1 — not touching system packages"
elif [ "$need_sys" = "1" ]; then
  if command -v apt-get >/dev/null; then
    say "[*] installing system deps (chromium, xvfb, nodejs, fonts) — needs root"
    $SUDO apt-get update -qq || warn "apt update failed (continuing)"
    $SUDO apt-get install -y -qq \
      chromium xvfb xauth nodejs npm \
      fonts-liberation fonts-dejavu-core \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
      libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libpango-1.0-0 libcairo2 libasound2 2>/dev/null \
      || warn "some packages failed — check output above"
  elif command -v dnf >/dev/null; then
    $SUDO dnf install -y -q chromium xvfb-run nodejs npm liberation-fonts dejavu-sans-fonts || warn "dnf install incomplete"
  elif command -v apk >/dev/null; then
    $SUDO apk add --no-cache chromium xvfb-run nodejs npm font-dejavu || warn "apk add incomplete"
  else
    warn "unknown distro — install chromium + xvfb + nodejs manually"
  fi
fi
for b in node Xvfb; do command -v "$b" >/dev/null && ok "$b $(command -v $b)" || warn "$b missing (browser/imperva types will fail)"; done
if command -v chromium >/dev/null; then CHROME_BIN="$(command -v chromium)"
elif command -v chromium-browser >/dev/null; then CHROME_BIN="$(command -v chromium-browser)"
elif command -v google-chrome >/dev/null; then CHROME_BIN="$(command -v google-chrome)"
else CHROME_BIN=""; fi
[ -n "$CHROME_BIN" ] && ok "chromium $CHROME_BIN" || warn "no chromium binary found"

# ── 3. python venv + deps ────────────────────────────────────────────────
if [ ! -d .venv ]; then "$PY" -m venv .venv && ok "venv created"; fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip wheel >/dev/null 2>&1 || true

say "[*] installing python deps (a few minutes)…"
if pip install -q -r requirements.txt 2>/dev/null; then
  ok "python deps"
else
  warn "full requirements failed (geetest_v3 needs a Rust build on py<3.12)"
  # Install everything except the Rust binding first, so the server is usable.
  grep -v "bili_ticket_gt_python" requirements.txt > /tmp/cf_req.txt
  pip install -q -r /tmp/cf_req.txt && ok "python deps (geetest_v3 pending)"
  if ask "build the geetest_v3 binding now (needs Rust, ~2 min, no root)?"; then
    if bash scripts/build_geetest_v3.sh "$(command -v python)"; then
      ok "python deps (incl. geetest_v3)"
    else
      warn "geetest_v3 binding build failed — that type fails fast, everything else works"
    fi
  else
    warn "geetest_v3 skipped — that type fails fast"
  fi
fi

# ── 4. playwright chromium (CloakBrowser browser path) ────────────────────
if python -c "import playwright" 2>/dev/null; then
  say "[*] ensuring playwright chromium…"
  if [ -n "$CHROME_BIN" ] && [ "${PLAYWRIGHT_DOWNLOAD:-0}" != "1" ]; then
    ok "using system chromium (set PLAYWRIGHT_DOWNLOAD=1 to fetch playwright's own)"
  else
    python -m playwright install chromium >/dev/null 2>&1 && ok "playwright chromium" || warn "playwright install failed"
  fi
  python -m playwright install-deps chromium >/dev/null 2>&1 || true
fi

# ── 5. ONNX models from Hugging Face (~100 MB) ───────────────────────────
if [ -x scripts/fetch_models.sh ]; then
  say "[*] fetching ONNX models from Hugging Face…"
  ./scripts/fetch_models.sh || warn "model fetch incomplete — re-run ./scripts/fetch_models.sh"
  # verify every required model is present so a missing one is obvious
  miss=""
  for f in models/siamese.onnx models/yolov11n_captcha.onnx solvers/aliyun/best.onnx \
           solvers/geetest/models/geetest_v4_icon.onnx solvers/recaptcha/models/recaptcha_cls_s.onnx \
           solvers/rotate/rotate_model.onnx solvers/vk/captcha_model.onnx solvers/vk/ctc_model.onnx; do
    [ -s "$f" ] || miss="$miss $f"
  done
  if [ -n "$miss" ]; then
    warn "MISSING models:$miss"
    warn "download them with ./scripts/fetch_models.sh (from huggingface.co/VeyraAgent/cf-solver-models)"
  else
    ok "all 8 ONNX models present"
  fi
fi

# ── 6. imperva node sidecar ──────────────────────────────────────────────
if command -v node >/dev/null && [ -d solvers/imperva/node ]; then
  say "[*] npm install for the imperva sidecar…"
  ( cd solvers/imperva/node && npm install --ignore-scripts --silent >/dev/null 2>&1 ) \
    && ok "imperva node sidecar" || warn "npm install failed — imperva/tspd fail fast"
fi

# ── 7. optional: arkose ONNX models (~1.4 GB) ────────────────────────────
if [ "$WITH_ARKOSE" = "1" ] || ask "download arkose ONNX models (~1.4GB)?"; then
  mkdir -p solvers/arkose/models
  n=0
  for f in 3d_rollball_objects_cv 3d_rollball_objects_v2 BrokenJigsawbrokenjigsaw_swap \
           card cardistance conveyor coordinatesmatch coordinatesmatch_cv counting \
           dicematch dice_pair frankenhead hand_number_puzzle hopscotch_highsec \
           knotsCrossesCircle penguin penguins-icon penguins rockstack rockstack_v2 \
           shadows train_coordinates train_coordinates_cv unbentobjects; do
    [ -s "solvers/arkose/models/$f.onnx" ] && { n=$((n+1)); continue; }
    ( cd solvers/arkose/models && { curl -fsSLO "https://funcaptchamodel.unix.do/$f.onnx" \
        || curl -fsSLO "https://www.tbooks.com.cn/funcaptcha_model/$f.onnx"; } 2>/dev/null ) && n=$((n+1))
  done
  ok "arkose models: $n/24"
else
  warn "arkose models skipped (that type fails fast until present)"
fi

# ── 8. verify + summary ──────────────────────────────────────────────────
hr
python - <<'PY' || true
import importlib.util
need = ["fastapi","uvicorn","playwright","cv2","numpy","PIL","onnxruntime","curl_cffi","httpx"]
miss = [m for m in need if not importlib.util.find_spec(m)]
print("imports ok" if not miss else "MISSING: " + ", ".join(miss))
PY
say ""
say "${BOLD}${OK}install complete${RST}"
say "  start   ${BOLD}./run.sh${RST}          (auto-starts under xvfb, port from \$PORT, default 8877)"
say "  health  curl http://localhost:8877/health"
say "  docs    http://localhost:8877/docs"
hr
