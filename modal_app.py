#!/usr/bin/env python3
"""Deploy the 50-type cf-solver engine to Modal — FULL LOCAL (no Capsolver/2captcha).

Source tree: ~/projects/cf-solver-push  (44 dispatch branches / ~50 advertised types,
`from solvers.<type>.solve import ...` layout, WORKDIR /app).

Solve path = CloakBrowser Chromium + ONNX models, all inside the Modal container.
The engine is a plain FastAPI app:
    GET  /health                 -> liveness + supported types
    POST /solve                  -> synchronous solve   {"type","sitekey","url",...}
    POST /solve/async            -> {"job_id"}          (poll GET /solve/job/{id})
    GET  /status | /logs | /pool -> introspection

Auth: bearer `SOLVER_TOKEN` (Modal secret `solver-token`) is required on everything
except /health, /docs, /redoc, /openapi.json, / — the veyracaptcha-api sends it.

Usage:
  cd ~/projects/cf-solver-push
  modal deploy modal_app.py
  # smoke:
  curl -s https://tumbalkuncuy--cf-solver-solver.us-east.modal.run/health
"""
from __future__ import annotations

import os

import modal

APP_NAME = "cf-solver"

app = modal.App(APP_NAME)

# Cloak free Chromium (same version as the WSL cloakenv + the VPS image)
_CLOAK_VER = "146.0.7680.177.5"
_CLOAK_TGZ = (
    f"https://cloakbrowser.dev/chromium-v{_CLOAK_VER}/cloakbrowser-linux-x64.tar.gz"
)
_CLOAK_DIR = f"/root/.cloakbrowser/chromium-{_CLOAK_VER}"

# Shared async job state — Dict is multi-container safe (files on a Volume need commit)
jobs_dict = modal.Dict.from_name("cf-solver-jobs-dict", create_if_missing=True)
jobs_vol = modal.Volume.from_name("cf-solver-jobs", create_if_missing=True)
_JOBS_MOUNT = "/root/solver-jobs"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "libnss3",
        "libnspr4",
        "libatk1.0-0",
        "libatk-bridge2.0-0",
        "libcups2",
        "libdrm2",
        "libxkbcommon0",
        "libxcomposite1",
        "libxdamage1",
        "libxfixes3",
        "libxrandr2",
        "libgbm1",
        "libasound2",
        "libpango-1.0-0",
        "libcairo2",
        "libatspi2.0-0",
        "libx11-6",
        "libx11-xcb1",
        "libxcb1",
        "libxext6",
        "libxfixes3",
        "libxshmfence1",
        "fonts-liberation",
        "fonts-noto-color-emoji",
        "ca-certificates",
        "curl",
        "xvfb",
        "procps",
        # imperva solver spawns a Node sidecar
        "nodejs",
        "npm",
    )
    .pip_install(
        "fastapi",
        "uvicorn[standard]",
        "pydantic",
        # PIN: 0.5.3 is the tested engine — 0.5.10+ routes .click() through a
        # humanized selector resolver that breaks frame_locator chains.
        "cloakbrowser==0.5.3",
        "wre-client-akamai",
        "wre-client-kasada",
        "altcha",
        "blake3",
        "pycryptodome",
        "ddddocr",
        "requests",
        "httpx",
        "curl_cffi",
        "pillow",
        "onnxruntime",
        "opencv-python-headless",
        "numpy",
        "substrate-interface",
        "bip39",
        "mnemonic",
    )
    .run_commands(
        f"mkdir -p /root/.cloakbrowser && "
        f"curl -fsSL '{_CLOAK_TGZ}' -o /tmp/cloak.tgz && "
        f"mkdir -p '{_CLOAK_DIR}' && "
        f"tar -xzf /tmp/cloak.tgz -C '{_CLOAK_DIR}' --strip-components=0 && "
        f"rm -f /tmp/cloak.tgz && "
        f"ls '{_CLOAK_DIR}' | head"
    )
    .add_local_dir(
        "/home/kupraa/projects/cf-solver-push",
        remote_path="/app",
        copy=True,
        ignore=[
            "**/.git/**",
            "**/__pycache__/**",
            "**/*.pyc",
            "**/artifacts/**",
        ],
    )
    # geetest_v3 binding: no cp311 wheel -> build from the sdist (mirrors
    # scripts/build_geetest_v3.sh, but with root so libssl-dev installs directly)
    .run_commands(
        # fetch the sdist straight from PyPI (pip download would try to build
        # the metadata, which needs the rust toolchain already present)
        # imperva: refresh the node deps for the installed node (ffi-napi must not build)
        "cd /app/solvers/imperva/node && "
        "(npm install --ignore-scripts --no-audit --no-fund 2>&1 | tail -3 || true) && "
        "(node -e 'require(\"./runner.js\");console.log(\"[ok] imperva node sidecar\")' 2>&1 | tail -2 || true)",
    )
)


def _install_auth(fastapi_app):
    """Require Bearer on everything but the public docs/health routes."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    token = os.environ.get("SOLVER_TOKEN", "").strip()

    @fastapi_app.middleware("http")
    async def _auth(request: Request, call_next):
        path = request.url.path
        if path in ("/health", "/docs", "/redoc", "/openapi.json", "/"):
            return await call_next(request)
        if not token:
            return await call_next(request)
        auth = request.headers.get("authorization") or ""
        if auth == f"Bearer {token}" or auth == token:
            return await call_next(request)
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    return fastapi_app


@app.function(
    image=image,
    timeout=600,
    memory=4096,
    cpu=2.0,
    min_containers=0,
    max_containers=2,
    scaledown_window=180,
    secrets=[modal.Secret.from_name("solver-token")],
    volumes={_JOBS_MOUNT: jobs_vol},
)
@modal.concurrent(max_inputs=3)
@modal.asgi_app()
def solver():
    import subprocess
    import sys
    import time as _t
    from pathlib import Path

    os.environ.setdefault("PORT", "8877")
    os.environ.setdefault("HOME", "/root")
    os.environ.setdefault("CLOAKBROWSER_CACHE", _CLOAK_DIR)
    os.environ.setdefault("CLOAKBROWSER_CACHE_DIR", _CLOAK_DIR)
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("ORT_NUM_THREADS", "2")
    os.environ["SOLVER_JOB_DIR"] = _JOBS_MOUNT
    Path(_JOBS_MOUNT).mkdir(parents=True, exist_ok=True)
    # Never set CAPSOLVER_* — full local only
    for k in list(os.environ):
        if k.startswith(("CAPSOLVER", "TWOCAPTCHA", "TWO_CAPTCHA")):
            del os.environ[k]

    try:
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            if os.path.exists("/tmp/.X99-lock") or os.path.exists("/tmp/.X11-unix/X99"):
                break
            _t.sleep(0.1)
        os.environ["DISPLAY"] = ":99"
        os.environ["BROWSER_HEADLESS"] = "0"
    except Exception:
        os.environ.setdefault("BROWSER_HEADLESS", "1")

    sys.path.insert(0, "/app")
    os.chdir("/app")

    import server as solver_server  # noqa: E402

    try:
        solver_server.configure_job_store(jobs_dict)
        print("[modal] job store = modal.Dict cf-solver-jobs-dict", flush=True)
    except Exception as exc:
        print(f"[modal] job store Dict failed: {exc}", flush=True)

    return _install_auth(solver_server.app)
