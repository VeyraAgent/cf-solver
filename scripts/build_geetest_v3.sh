#!/usr/bin/env bash
# Build the geetest_v3 (bili_ticket_gt_python) Rust binding without root.
#
# Why this exists: bili_ticket_gt_python ships no cp311 manylinux wheel and its
# source build needs OpenSSL headers (libssl-dev). On hosts without root we can
# extract libssl-dev from the .deb into a local prefix and point OPENSSL_DIR at
# it. Produces a working `import bili_ticket_gt_python` for the current venv.
#
# Usage:  ./scripts/build_geetest_v3.sh [python]
#   python defaults to $VIRTUAL_ENV/bin/python3 or python3
set -euo pipefail

PY="${1:-${VIRTUAL_ENV:-}/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"
echo "[*] python: $PY"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1"; exit 1; }; }
need cargo; need curl

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. fetch + extract libssl-dev headers (no root)
echo "[*] fetching libssl-dev headers"
( cd "$WORK" && apt-get download libssl-dev >/dev/null 2>&1 || \
  curl -sL -o libssl-dev.deb "$(apt-get download --print-uris libssl-dev 2>/dev/null | cut -d\' -f2 | head -1)" )
( cd "$WORK" && dpkg-deb -x libssl-dev_*.deb root )

PREFIX="$WORK/ssl"
mkdir -p "$PREFIX/lib" "$PREFIX/include"
cp -r "$WORK"/root/usr/include/openssl "$PREFIX/include/"
[ -d "$WORK/root/usr/include/x86_64-linux-gnu/openssl" ] && \
  cp -r "$WORK"/root/usr/include/x86_64-linux-gnu/openssl/* "$PREFIX/include/openssl/"
cp -a "$WORK"/root/usr/lib/x86_64-linux-gnu/libssl.so "$PREFIX/lib/" 2>/dev/null || true
cp -a "$WORK"/root/usr/lib/x86_64-linux-gnu/libcrypto.so "$PREFIX/lib/" 2>/dev/null || true
# the dev symlinks point at libssl.so.3 / libcrypto.so.3, which live in the
# system runtime dir — link those in so the linker can resolve -lssl/-lcrypto.
for lib in ssl crypto; do
  [ -e "$PREFIX/lib/lib$lib.so.3" ] || \
    ln -sf "$(ldconfig -p | grep -m1 "lib$lib.so.3 " | awk '{print $NF}')" "$PREFIX/lib/lib$lib.so.3"
done

# 2. fetch + build the crate
echo "[*] building bili_ticket_gt_python (this takes ~2 min)"
( cd "$WORK" && "$PY" -m pip download bili_ticket_gt_python -d src --no-binary :all: --no-deps -q )
( cd "$WORK/src" && tar xzf bili_ticket_gt_python-*.tar.gz )

SRC="$(ls -d "$WORK"/src/bili_ticket_gt_python-*/ | head -1)"
( cd "$SRC" && env -u OPENSSL_INCLUDE_DIR -u OPENSSL_LIB_DIR OPENSSL_DIR="$PREFIX" \
    cargo build --release -q )

# 3. install as a package into the venv site-packages
SP="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"
SUF="$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
rm -rf "$SP/bili_ticket_gt_python"
mkdir -p "$SP/bili_ticket_gt_python"
cp "$SRC/bili_ticket_gt_python/__init__.py" "$SP/bili_ticket_gt_python/"
cp "$SRC/target/release/libbili_ticket_gt_python.so" \
   "$SP/bili_ticket_gt_python/bili_ticket_gt_python$SUF"

echo "[*] verifying"
"$PY" -c "import bili_ticket_gt_python as m; print('[ok] geetest_v3 binding ready:', [a for a in dir(m) if a.endswith('Py')])"
