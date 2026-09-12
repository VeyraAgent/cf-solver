#!/usr/bin/env bash
set -e
echo "╔══════════════════════════════════════╗"
echo "║   CF-Solver — one-shot installer     ║"
echo "╚══════════════════════════════════════╝"

# 0. check python
PY=python3
if ! command -v $PY &>/dev/null; then echo "❌ python3 not found"; exit 1; fi
PYV=$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ python $PYV"

# 1. venv
if [ ! -d .venv ]; then
  $PY -m venv .venv && echo "✅ venv created"
fi
source .venv/bin/activate

# 2. deps
echo "[*] installing python deps (this may take a few minutes)…"
pip install -q --upgrade pip
if pip install -q -r requirements.txt 2>/dev/null; then
  echo "✅ python deps"
else
  # py<3.12: wheel linux tidak ada — perlu Rust build. tawarkan install rust.
  read -t 10 -p "python <3.12 detected: build geetest_v3 binding needs Rust (~5 min)? [y/N]: " B || B="n"
  B=${B:-n}
  if [[ "$B" == "y" || "$B" == "Y" ]]; then
    if ! command -v cargo &>/dev/null; then
      curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y -q
      source $HOME/.cargo/env
    fi
    (apt-get install -y -qq libssl-dev pkg-config 2>/dev/null ||      echo "⚠️  install libssl-dev + pkg-config manually (openssl-sys needs it)") &&     pip install -q -r requirements.txt && echo "✅ python deps (incl. geetest_v3 binding)"
  else
    grep -v "bili_ticket_gt_python" requirements.txt > /tmp/req.txt
    pip install -q -r /tmp/req.txt
    echo "⏭️  geetest_v3 binding skipped (no Rust) — type will fail-fast"
  fi
fi

# 3. imperva node sidecar
if ! command -v node &>/dev/null; then
  echo "[*] installing node.js (imperva sidecar needs it)…"
  (apt-get update -qq && apt-get install -y -qq nodejs npm) 2>/dev/null || \
    echo "⚠️  install node manually: https://nodejs.org — imperva type will fail-fast"
fi
if command -v node &>/dev/null && [ -d solvers/imperva/node ]; then
  cd solvers/imperva/node && npm install --ignore-scripts --silent && cd ../..
  echo "✅ imperva node sidecar"
fi

# 4. optional: arkose models (~1.4GB — user's choice)
read -t 10 -p "download arkose ONNX models (~1.4GB)? [y/N] (auto-skip in 10s): " A || A="n"
A=${A:-n}
if [[ "$A" == "y" || "$A" == "Y" ]]; then
  mkdir -p solvers/arkose/models && cd solvers/arkose/models
  for f in 3d_rollball_objects_cv 3d_rollball_objects_v2 BrokenJigsawbrokenjigsaw_swap \
           card cardistance conveyor coordinatesmatch coordinatesmatch_cv counting \
           dicematch dice_pair frankenhead hand_number_puzzle hopscotch_highsec \
           knotsCrossesCircle penguin penguins-icon penguins rockstack rockstack_v2 \
           shadows train_coordinates train_coordinates_cv unbentobjects; do
    curl -fLO "https://funcaptchamodel.unix.do/$f.onnx" 2>/dev/null || \
    curl -fLO "https://www.tbooks.com.cn/funcaptcha_model/$f.onnx"
  done
  cd ../..
  echo "✅ arkose models"
else
  echo "⏭️  arkose models skipped (arkose type will fail-fast until models are in solvers/arkose/models/)"
fi

# 5. done
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   ✅ install selesai                  "
echo "║   start:  ./run.sh                    "
echo "║   health: curl :8877/health           "
echo "║   docs:   :8877/docs                  "
echo "╚══════════════════════════════════════╝"
