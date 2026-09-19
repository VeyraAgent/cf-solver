"""Proxy pool with health checking and round-robin rotation.

Shared by every browser/HTTP solver through the server layer: a request with
`proxy: "pool"` acquires the next healthy proxy; failures put it on a cooldown
ladder (60s -> 120s -> 300s -> dead), successes reset the streak. State is
in-memory (restart = re-check); the proxy list itself persists to proxies.txt
next to server.py and auto-reloads on change.

IP-bound cookie notes (caller must replay from the same IP):
    cloudflare / datadome / akamai / imperva / awswaf / perimeterx / x5sec
are cookie-bound to the session IP+UA — when solved through a pool proxy the
server echoes `proxy_used` (host:port) in the result so the caller pins it.
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("proxypool")

# ── Config ────────────────────────────────────────────────────────────
_POOL_FILE = Path(os.getenv("SOLVER_POOL_FILE",
                            Path(__file__).resolve().parent.parent.parent / "proxies.txt"))
_CHECK_INTERVAL = int(os.getenv("SOLVER_POOL_CHECK_INTERVAL", "300"))  # 0 = background off
_TCP_TIMEOUT = float(os.getenv("SOLVER_POOL_TCP_TIMEOUT", "4"))
_HTTP_TIMEOUT = float(os.getenv("SOLVER_POOL_HTTP_TIMEOUT", "12"))
# Cooldown ladder after consecutive failures; index = fail streak - 1.
_COOLDOWN_S = (60, 120, 300)
_DEAD_AFTER = len(_COOLDOWN_S) + 1        # 4th consecutive failure = dead
_HEALTHY_URL = os.getenv("SOLVER_POOL_HEALTHY_URL", "https://www.gstatic.com/generate_204")

# ── State ─────────────────────────────────────────────────────────────
class _Proxy:
    __slots__ = ("url", "hostport", "fails", "uses", "ok_until", "last_latency",
                 "last_error", "dead", "added_at")

    def __init__(self, url: str):
        self.url = url                      # full scheme://user:pass@host:port
        self.hostport = _hostport(url)
        self.fails = 0                      # consecutive failures
        self.uses = 0
        self.ok_until = 0.0                 # TCP-pass cache (monotonic)
        self.last_latency: float | None = None
        self.last_error: str | None = None
        self.dead = False
        self.added_at = time.time()


class ProxyPool:
    def __init__(self, path: Path = _POOL_FILE):
        self.path = path
        self._items: dict[str, _Proxy] = {}
        self._rr = 0                        # round-robin cursor
        self._mtime = 0.0
        self._load()

    # ── persistence ───────────────────────────────────────────────────
    def _load(self):
        """Load proxies.txt (one URL per line, # comments). Keeps health stats
        of proxies that survive a rewrite."""
        try:
            if not self.path.exists():
                self._mtime = 0.0
                return
            mtime = self.path.stat().st_mtime
            if mtime == self._mtime:
                return
            self._mtime = mtime
            fresh: dict[str, _Proxy] = {}
            for line in self.path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                url = _normalize(line)
                if url and url not in fresh:
                    keep = self._items.get(url)
                    fresh[url] = keep if keep else _Proxy(url)
            self._items = fresh
            log.info("pool: loaded %d proxies from %s", len(self._items), self.path.name)
        except Exception as e:  # unreadable file must never kill the server
            log.error("pool: load failed: %s", e)

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(p.url for p in self._items.values()) + "\n")
            self._mtime = self.path.stat().st_mtime
        except Exception as e:
            log.error("pool: save failed: %s", e)

    # ── CRUD (server endpoints) ───────────────────────────────────────
    def add(self, urls: list[str]) -> tuple[int, int]:
        """Add proxies (deduped, normalized). Returns (added, skipped)."""
        added = skipped = 0
        for raw in urls:
            url = _normalize(raw)
            if not url:
                skipped += 1
                continue
            if url in self._items:
                skipped += 1
                continue
            self._items[url] = _Proxy(url)
            added += 1
        if added:
            self._save()
        return added, skipped

    def remove(self, hostport_or_url: str) -> bool:
        target = _normalize(hostport_or_url) or hostport_or_url.strip()
        for key in (target, f"http://{target}", f"socks5://{target}"):
            if key in self._items:
                del self._items[key]
                self._save()
                return True
        # fuzzy: match by hostport
        for key in list(self._items):
            if self._items[key].hostport == _hostport(hostport_or_url):
                del self._items[key]
                self._save()
                return True
        return False

    def clear(self) -> int:
        n = len(self._items)
        self._items.clear()
        self._save()
        return n

    # ── rotation ──────────────────────────────────────────────────────
    def acquire(self, exclude_hostport: str | None = None) -> _Proxy:
        """Next healthy proxy, round-robin. Raises KeyError when empty/exhausted."""
        self._load()
        if not self._items:
            raise KeyError("pool is empty — add proxies via POST /pool")
        now = time.monotonic()
        items = list(self._items.values())
        for i in range(len(items)):
            p = items[(self._rr + i) % len(items)]
            if p.dead or exclude_hostport and p.hostport == exclude_hostport:
                continue
            if self._cooling(p, now):
                continue
            self._rr = (self._rr + i + 1) % len(items)
            p.uses += 1
            return p
        raise KeyError(f"no healthy proxy available ({len(items)} in pool, all cooling/dead)")

    def report(self, url: str, ok: bool, error: str | None = None):
        """Feed a solve outcome back into the health bookkeeping."""
        p = self._items.get(url)
        if not p:
            return
        if ok:
            p.fails = 0
            p.last_error = None
            p.dead = False
            p.ok_until = time.monotonic() + 60      # short trust window after real traffic
        else:
            self.mark_failure(url, error or "solve failed")

    # ── health ────────────────────────────────────────────────────────
    def _cooling(self, p: _Proxy, now: float) -> bool:
        """True while the proxy is on a failure cooldown (still revivable)."""
        if p.fails == 0:
            return False
        return now < p.ok_until  # ok_until doubles as the cooldown deadline after fails

    def mark_failure(self, url: str, error: str):
        """Cooldown ladder on consecutive failures: 60s -> 120s -> 300s -> dead."""
        p = self._items.get(url)
        if not p:
            return
        p.fails += 1
        p.last_error = (error or "unknown")[:120]
        idx = min(p.fails - 1, len(_COOLDOWN_S) - 1)
        p.ok_until = time.monotonic() + _COOLDOWN_S[idx]
        if p.fails >= _DEAD_AFTER:
            p.dead = True
            log.warning("pool: %s marked DEAD after %d fails", p.hostport, p.fails)

    async def check(self, url: str, deep: bool = False) -> dict:
        """Health-check one proxy: TCP dial (cheap) + optional HTTP fetch (deep)."""
        p = self._items.get(url)
        if not p:
            return {"proxy": _mask(url), "exists": False}
        t0 = time.monotonic()
        tcp_ok = await _tcp_ok(p.hostport)
        latency = time.monotonic() - t0
        if not tcp_ok:
            self.mark_failure(url, "tcp connect failed")
            return {"proxy": _mask(url), "healthy": False, "latency": round(latency, 3),
                    "error": "tcp", "fails": p.fails, "dead": p.dead}
        p.last_latency = round(latency, 3)
        if deep:
            http_ok, err = await _http_ok(url)
            if not http_ok:
                self.mark_failure(url, err or "http check failed")
                return {"proxy": _mask(url), "healthy": False, "latency": p.last_latency,
                        "error": err, "fails": p.fails, "dead": p.dead}
        # success: revive
        p.fails = 0
        p.last_error = None
        p.dead = False
        p.ok_until = time.monotonic() + 120
        return {"proxy": _mask(url), "healthy": True, "latency": p.last_latency,
                "deep": deep, "fails": 0}

    async def check_all(self, deep: bool = False, batch: int = 16) -> list[dict]:
        self._load()
        sem = asyncio.Semaphore(batch)

        async def one(u: str):
            async with sem:
                return await self.check(u, deep=deep)

        return await asyncio.gather(*(one(u) for u in self._items))

    # ── introspection ─────────────────────────────────────────────────
    def status(self) -> dict:
        self._load()
        now = time.monotonic()
        out = []
        for p in self._items.values():
            cooling = self._cooling(p, now) and p.fails > 0
            out.append({
                "proxy": _mask(p.url),
                "healthy": not p.dead and not cooling,
                "dead": p.dead,
                "fails": p.fails,
                "uses": p.uses,
                "latency": p.last_latency,
                "error": p.last_error,
            })
        healthy = sum(1 for x in out if x["healthy"])
        return {"size": len(out), "healthy": healthy,
                "file": str(self.path), "proxies": out}


# ── helpers ───────────────────────────────────────────────────────────
def _normalize(raw: str) -> str | None:
    """Accept scheme://user:pass@host:port, host:port, user:pass@host:port → URL."""
    raw = (raw or "").strip()
    if not raw or raw.startswith("#"):
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        u = urlparse(raw)
        if not u.hostname or not u.port:
            return None
        auth = f"{u.username}:{u.password}@" if u.username else ""
        return f"{u.scheme}://{auth}{u.hostname}:{u.port}"
    except ValueError:
        return None


def _hostport(url: str) -> str:
    try:
        u = urlparse(url)
        return f"{u.hostname}:{u.port}"
    except ValueError:
        return url


def _mask(url: str) -> str:
    """scheme://user:***@host:port — never echo credentials to the API."""
    try:
        u = urlparse(url)
        auth = f"{u.username}:***@" if u.username else ""
        return f"{u.scheme}://{auth}{u.hostname}:{u.port}"
    except ValueError:
        return "opaque"


async def _tcp_ok(hostport: str) -> bool:
    try:
        host, port = hostport.rsplit(":", 1)
        fut = asyncio.open_connection(host, int(port))
        reader, writer = await asyncio.wait_for(fut, timeout=_TCP_TIMEOUT)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _http_ok(proxy_url: str) -> tuple[bool, str | None]:
    """HTTP check through the proxy with chrome TLS impersonation (curl_cffi)."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return True, None  # deep check unavailable → TCP result stands
    try:
        async with AsyncSession(impersonate="chrome", proxy=proxy_url,
                                timeout=_HTTP_TIMEOUT) as s:
            r = await s.get(_HEALTHY_URL)
            if r.status_code < 400:
                return True, None
            return False, f"http {r.status_code}"
    except Exception as e:
        return False, type(e).__name__


# ── module singleton + background checker ─────────────────────────────
_pool: ProxyPool | None = None


def get_pool() -> ProxyPool:
    global _pool
    if _pool is None:
        _pool = ProxyPool()
    return _pool


async def background_checker():
    """Periodic deep health sweep; started from server lifespan (0 = off)."""
    if _CHECK_INTERVAL <= 0:
        return
    pool = get_pool()
    while True:
        try:
            await asyncio.sleep(_CHECK_INTERVAL)
            if not pool._items:
                continue
            results = await pool.check_all(deep=True)
            bad = sum(1 for r in results if not r.get("healthy"))
            log.info("pool: background check — %d/%d healthy", len(results) - bad, len(results))
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.error("pool: background check error: %s", e)
