"""Runtime configuration. No secrets are stored here or anywhere in source.

Resolution order (last wins): built-in defaults -> environment -> CLI flags.

Environment variables:
    TCAPTCHA_EMAIL       host account e-mail          (required by login flows)
    TCAPTCHA_PASSWORD    host account password        (required by login flows)
    TCAPTCHA_PROXY       single proxy url             http://user:pass@host:port
    TCAPTCHA_PROXY_FILE  file with one proxy url/line (up to 4 candidates,
                         each health-checked before the browser spawns)
    TCAPTCHA_OUT         artifact directory           (default /tmp/tcaptcha)
"""
from __future__ import annotations

import os
import random
import re
import urllib.parse
from dataclasses import dataclass, field

EMAIL_ENV = "TCAPTCHA_EMAIL"
PASSWORD_ENV = "TCAPTCHA_PASSWORD"
PROXY_ENV = "TCAPTCHA_PROXY"
PROXY_FILE_ENV = "TCAPTCHA_PROXY_FILE"
OUT_ENV = "TCAPTCHA_OUT"

DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


# Matches `user:pass@host` even with no scheme: urlsplit() would read `user`
# as the scheme and hand the credentials back untouched. The lookbehind plus
# the excluded characters keep `/`, `:` and `*` out of the user part, so an
# already-redacted `scheme://***:***@host` (or a bare `***:***@host`) can
# never re-match - redaction must be idempotent on BOTH forms, and the star
# of a previous redaction was the re-match hook (`http://****:***@...`).
BARE_CRED_RE = re.compile(r"(?<![\w:/])([^\s:/@*]{1,64}):([^\s/@]{1,128})@")


def redact(value: str) -> str:
    """Strip user:password from a url (or a bare proxy) before it hits a log.

        http://user:pass@1.2.3.4:3129  ->  http://***:***@1.2.3.4:3129
        user:pass@1.2.3.4:3129         ->  ***:***@1.2.3.4:3129
    Idempotent - redacting twice is a no-op.
    """
    if not value:
        return value
    out = value
    try:
        parts = urllib.parse.urlsplit(value)
        if parts.hostname and parts.username is not None:
            host = parts.hostname + (f":{parts.port}" if parts.port else "")
            out = urllib.parse.urlunsplit(
                (parts.scheme, f"***:***@{host}", parts.path, parts.query, parts.fragment))
    except Exception:
        pass
    return BARE_CRED_RE.sub("***:***@", out)


def parse_proxy(line: str) -> dict | None:
    """Turn a proxy url into a Playwright proxy dict, or None if unusable.

    Accepts with or without a scheme; never echoes credentials back.
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    if "://" not in line:
        line = "http://" + line
    parts = urllib.parse.urlsplit(line)
    if not parts.hostname or not parts.port:
        return None
    cfg = {"server": f"{parts.scheme or 'http'}://{parts.hostname}:{parts.port}"}
    if parts.username:
        cfg["username"] = urllib.parse.unquote(parts.username)
    if parts.password:
        cfg["password"] = urllib.parse.unquote(parts.password)
    return cfg


def load_proxy_candidates(proxy: str | None = None,
                          proxy_file: str | None = None) -> list[str]:
    """All usable proxy candidates: explicit flag > env var > shuffled file.

    The engine health-checks up to 4 of them before the browser spawns
    (skill lesson: a dead proxy used to consume an entire run).
    """
    if proxy:
        return [proxy.strip()]
    env = os.environ.get(PROXY_ENV, "").strip()
    if env:
        return [env]
    path = proxy_file or os.environ.get(PROXY_FILE_ENV, "").strip()
    if path:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh
                     if ln.strip() and not ln.lstrip().startswith("#")]
        if not lines:
            raise ConfigError(f"proxy file {path!r} has no usable lines")
        random.shuffle(lines)     # rotate which candidates the ladder sees first
        return lines
    return []


def load_proxy_url(proxy: str | None = None, proxy_file: str | None = None) -> str | None:
    """First candidate (compat helper for callers that only want one url)."""
    candidates = load_proxy_candidates(proxy, proxy_file)
    return candidates[0] if candidates else None


@dataclass
class Config:
    """Everything the engine needs, already validated."""
    email: str | None = None
    password: str | None = None
    proxy: str | None = None            # raw url (redact before logging!)
    proxy_candidates: list[str] = field(default_factory=list)
    flow: str = "minimax-login"
    headless: bool = True
    arbiter: str = "jev"                # "jev" | "heuristic"
    min_probability: float = 0.55
    max_attempts: int = 4
    ratelimit_backoff: float = 70.0     # seconds, errorCode=12
    placeholder_backoff: float = 0.0    # seconds, empty-ticket risk hold
    frame_timeout: float = 40.0         # seconds to wait for the widget
    inter_run_sleep: float = 60.0       # seconds between separate solves
    out_dir: str = "/tmp/tcaptcha"
    user_agent: str = DEFAULT_UA
    viewport: tuple[int, int] = (1280, 900)
    extra: dict = field(default_factory=dict)

    # -- derived helpers -------------------------------------------------
    @property
    def proxy_dict(self) -> dict | None:
        return parse_proxy(self.proxy) if self.proxy else None

    @property
    def proxy_label(self) -> str:
        """Host:port only - safe for logs."""
        if not self.proxy:
            return "direct"
        try:
            parts = urllib.parse.urlsplit(self.proxy)
            return f"{parts.hostname}:{parts.port}" if parts.port else (parts.hostname or "?")
        except Exception:
            return "?"

    def require_credentials(self) -> None:
        if not self.email or not self.password:
            raise ConfigError(
                "this flow needs an account: set "
                f"{EMAIL_ENV} and {PASSWORD_ENV} (or pass --email/--password). "
                "No credentials are ever stored in the source tree.")

    @classmethod
    def build(cls, args=None) -> "Config":
        """Build from an argparse namespace (or from the environment alone)."""
        g = (lambda name, default=None: getattr(args, name, default) if args else default)
        # Legacy fallback: this environment already ships JEV_EMAIL/JEV_PWD in its
        # .env for the sibling jev tooling; reuse them only when TCAPTCHA_* is unset.
        email = (g("email") or os.environ.get(EMAIL_ENV)
                 or os.environ.get("JEV_EMAIL") or None)
        password = (g("password") or os.environ.get(PASSWORD_ENV)
                    or os.environ.get("JEV_PWD") or None)
        candidates = load_proxy_candidates(g("proxy"), g("proxy_file"))
        proxy = candidates[0] if candidates else None
        out = g("out") or os.environ.get(OUT_ENV) or "/tmp/tcaptcha"
        cfg = cls(
            email=email,
            password=password,
            proxy=proxy,
            proxy_candidates=candidates,
            flow=g("flow") or "minimax-login",
            headless=not g("headed", False),
            arbiter=g("arbiter") or "jev",
            min_probability=float(g("min_probability") or 0.55),
            max_attempts=int(g("max_attempts") or 4),
            ratelimit_backoff=float(g("backoff") or 70.0),
            out_dir=out,
        )
        os.makedirs(cfg.out_dir, exist_ok=True)
        return cfg
