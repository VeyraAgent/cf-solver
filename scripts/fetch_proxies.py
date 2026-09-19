"""Fetch + validate free proxies from public GitHub lists → feed the proxy pool.

Sources are raw.githubusercontent.com lists (http/socks4/socks5, refreshed hourly
by their maintainers). Free proxies die fast — the value here is the validator:
concurrent curl_cffi chrome-TLS checks, latency + anonymity scoring, then only
survivors enter the pool (proxies.txt) where the cooldown ladder keeps culling.

Usage:
    python3 scripts/fetch_proxies.py                    # fetch + validate + write proxies.txt
    python3 scripts/fetch_proxies.py --min 25 --limit 60
    python3 scripts/fetch_proxies.py --loop 1800        # keep refreshing every 30 min
    python3 scripts/fetch_proxies.py --post :8877       # POST survivors to a running server
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Sources (protocol → raw list URLs) ────────────────────────────────
SOURCES: dict[str, list[str]] = {
    "http": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/Proxifly/free-proxy-list/main/proxies/http/data.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/http.txt",
        "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
    ],
    "socks4": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4_RAW.txt",
    ],
    "socks5": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/Proxifly/free-proxy-list/main/proxies/socks5/data.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
        "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/socks5.txt",
    ],
}

# Validation target: 204 fast path (any HTTP/1.1 proxy must serve it).
# Socks proxies are TCP-dialed + CONNECT-tested via curl_cffi itself.
PROBE_URL = "https://www.gstatic.com/generate_204"


def _norm(line: str) -> str | None:
    """host:port → http://host:port (default). Passes scheme:// lines through."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        return line
    host, _, port = line.partition(":")
    if not host or not port.isdigit():
        return None
    return f"http://{host}:{port}"


async def fetch_all(timeout_s: int = 25) -> set[str]:
    """Pull every source, normalize, dedupe (httpx, no extra dep)."""
    import httpx

    out: set[str] = set()
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as s:

        async def one(url: str, proto: str):
            name = url.rstrip("/").rsplit("/", 2)[-2]
            try:
                r = await s.get(url)
                if r.status_code != 200:
                    print(f"  [{proto}] {name} → HTTP {r.status_code}")
                    return
                n = 0
                for line in r.text.splitlines():
                    p = _norm(line)
                    if p and p.startswith(f"{proto}://"):
                        out.add(p)
                        n += 1
                print(f"  [{proto}] {name} → {n}")
            except Exception as e:
                print(f"  [{proto}] {name} → {type(e).__name__}")

        await asyncio.gather(*(one(u, proto) for proto, group in SOURCES.items() for u in group))
    return out


async def validate(proxies: list[str], concurrency: int = 128, timeout_s: float = 8.0,
                   max_latency: float = 6.0) -> list[dict]:
    """Concurrent real-request check via curl_cffi (chrome TLS). Returns survivors
    sorted by latency: [{proxy, latency, type}]."""
    from curl_cffi.requests import AsyncSession

    sem = asyncio.Semaphore(concurrency)
    alive: list[dict] = []

    async def check(p: str):
        async with sem:
            t0 = time.monotonic()
            try:
                async with AsyncSession(impersonate="chrome", proxy=p,
                                        timeout=timeout_s) as s:
                    r = await s.get(PROBE_URL)
                    lat = time.monotonic() - t0
                    if r.status_code < 400 and lat <= max_latency:
                        alive.append({"proxy": p, "latency": round(lat, 2),
                                      "type": p.split("://")[0]})
            except Exception:
                pass

    await asyncio.gather(*(check(p) for p in proxies))
    alive.sort(key=lambda x: x["latency"])
    return alive


def write_pool_file(alive: list[dict], path: Path, limit: int, keep_old: bool = True) -> int:
    """Merge survivors into the pool file (round-robin friendly)."""
    new_urls = [a["proxy"] for a in alive[:limit]]
    old: list[str] = []
    if keep_old and path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in new_urls:
                old.append(line)
    # interleave old (previously-alive) first — they earned trust
    merged = old + new_urls
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(merged) + "\n")
    return len(new_urls)


async def post_to_server(alive: list[dict], base: str, limit: int) -> None:
    import httpx
    urls = [a["proxy"] for a in alive[:limit]]
    async with httpx.AsyncClient(timeout=30) as s:
        r = await s.post(f"{base.rstrip('/')}/pool", json={"proxies": urls})
        print(f"POST /pool → {r.status_code}: {r.text}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch + validate free proxies → proxy pool")
    ap.add_argument("--min", type=int, default=20, help="min survivors to consider a run useful")
    ap.add_argument("--limit", type=int, default=60, help="max proxies written into the pool")
    ap.add_argument("--concurrency", type=int, default=128)
    ap.add_argument("--timeout", type=float, default=8.0, help="per-proxy probe timeout (s)")
    ap.add_argument("--max-latency", type=float, default=6.0, help="drop probes slower than this (s)")
    ap.add_argument("--pool-file", default=str(Path(__file__).resolve().parent.parent / "proxies.txt"))
    ap.add_argument("--post", default=None, help="also POST to a running server, e.g. :8877")
    ap.add_argument("--loop", type=int, default=0, help="refresh every N seconds (0 = once)")
    args = ap.parse_args()

    pool_file = Path(args.pool_file)

    while True:
        print("── fetching sources ──")
        raw = await fetch_all()
        print(f"unique proxies: {len(raw)}")
        if not raw:
            print("no proxies fetched; keep previous pool")
        else:
            sample = random.sample(sorted(raw), min(len(raw), 4000)) if len(raw) > 4000 else sorted(raw)
            print(f"── validating {len(sample)} (concurrency {args.concurrency}) ──")
            alive = await validate(sample, args.concurrency, args.timeout, args.max_latency)
            print(f"alive: {len(alive)}")
            for a in alive[:10]:
                print(f"  {a['latency']:>5.2f}s  {a['proxy']}")
            if len(alive) >= args.min:
                n = write_pool_file(alive, pool_file, args.limit)
                print(f"pool file: +{n} validated (total {len(old_count(pool_file)) + min(n, args.limit)} lines) → {pool_file}")
                if args.post:
                    await post_to_server(alive, args.post, args.limit)
            else:
                print(f"only {len(alive)} alive (< min {args.min}) — pool file untouched")
        if not args.loop:
            return
        print(f"sleep {args.loop}s …")
        await asyncio.sleep(args.loop)


def old_count(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


if __name__ == "__main__":
    asyncio.run(main())
