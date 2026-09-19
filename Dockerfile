# syntax=docker/dockerfile:1
#
# CF-Solver container image — self-hosted captcha sidecar.
#
#   docker build -t cf-solver .
#   docker run --rm -p 8877:8877 cf-solver
#   curl localhost:8877/health
#
# Notes:
#  * The browser engine is Chromium, so the image carries its runtime libs +
#    Xvfb. Solver processes run under xvfb-run (headless boxes have no display).
#  * CLOAKBROWSER_LICENSE_KEY is read at RUNTIME (env), never baked into a layer.
#    Get a free key: https://cloakbrowser.dev/free
#  * SKIP_BROWSER_DOWNLOAD=1 builds a smaller image for pure-HTTP/PoW types only
#    (cloudflare/turnstile/recaptcha/hcaptcha will fail-fast without it).

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    # keep the ~760 MB Chromium download out of the repo tree
    CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser \
    PORT=8877

# ---------------------------------------------------------------------------
# 1. Chromium runtime libs + Xvfb (the browser needs a display server)
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xvfb xauth \
        fonts-liberation fonts-dejavu-core \
        libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
        libcairo2 libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 \
        libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
        libxdamage1 libxext6 libxfixes3 libxi6 libxkbcommon0 libxrandr2 \
        libxrender1 libxshmfence1 libxss1 libxtst6 \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------------------------------------------------------------------------
# 2. Python deps (lean set — see requirements-docker.txt)
# ---------------------------------------------------------------------------
COPY requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

# ---------------------------------------------------------------------------
# 3. App source
# ---------------------------------------------------------------------------
COPY . .

# ---------------------------------------------------------------------------
# 4. Browser binary — baked in so the first /solve is fast.
#    SKIP_BROWSER_DOWNLOAD=1 to omit it (pure-HTTP/PoW types still work).
# ---------------------------------------------------------------------------
ARG SKIP_BROWSER_DOWNLOAD=0
RUN if [ "$SKIP_BROWSER_DOWNLOAD" != "1" ]; then \
        python -m cloakbrowser install; \
    else \
        echo "browser download skipped (pure-HTTP/PoW types only)"; \
    fi

# drop privileges — never run the solver as root
RUN useradd -m -u 10001 solver && chown -R solver:solver /app /opt/cloakbrowser 2>/dev/null || true
USER solver

EXPOSE 8877

# Xvfb-backed start; PORT is honoured by server.py via os.getenv("PORT").
CMD ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", "python3", "server.py"]
