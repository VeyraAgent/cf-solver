#!/usr/bin/env bash
# Fetch the ONNX models cf-solver needs from Hugging Face.
# Models live outside the git repo to keep the clone light.
#   ./scripts/fetch_models.sh              # public repo, no token needed
#   HF_TOKEN=hf_xxx ./scripts/fetch_models.sh   # only if the HF repo is private
set -euo pipefail
cd "$(dirname "$0")/.."          # repo root

REPO="${HF_REPO:-VeyraAgent/cf-solver-models}"
BASE="https://huggingface.co/${REPO}/resolve/main"

FILES=(
  models/siamese.onnx
  models/yolov11n_captcha.onnx
  solvers/aliyun/best.onnx
  solvers/geetest/models/geetest_v4_icon.onnx
  solvers/recaptcha/models/recaptcha_cls_s.onnx
  solvers/rotate/rotate_model.onnx
  solvers/vk/captcha_model.onnx
  solvers/vk/ctc_model.onnx
)

AUTH=()
[ -n "${HF_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer ${HF_TOKEN}")

ok=0; skip=0; fail=0
for f in "${FILES[@]}"; do
  if [ -s "$f" ]; then printf '  skip %s (present)\n' "$f"; skip=$((skip+1)); continue; fi
  mkdir -p "$(dirname "$f")"
  printf '  get  %s … ' "$f"
  if curl -fsSL "${AUTH[@]}" -o "$f.part" "$BASE/$f" && mv "$f.part" "$f"; then
    printf 'ok (%s)\n' "$(du -h "$f" | cut -f1)"; ok=$((ok+1))
  else
    rm -f "$f.part"; printf 'FAILED\n'; fail=$((fail+1))
  fi
done

echo
echo "models: $ok downloaded, $skip already present, $fail failed"
echo "source: https://huggingface.co/${REPO}"
[ "$fail" = "0" ] || exit 1
