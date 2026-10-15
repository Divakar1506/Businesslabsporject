"""Polite, resumable crawler.

- robots.txt is checked before every URL.
- At most CONCURRENCY requests in flight and DELAY_SECONDS between request starts.
- Temporary failures (timeouts, 429, 5xx) retry with exponential backoff; 4xx do not.
- Each page's state is saved as soon as it finishes, so a stopped run resumes where it left off.
- One failing page never stops the run.
"""
import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.robotparser import RobotFileParser

import httpx

from .sitemap import article_id
from .store import KBStore

RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
GONE = {404, 410}


@dataclass
class CrawlConfig:
    user_agent: str
    concurrency: int = 2
    delay_seconds: float = 1.5
    timeout_seconds: float = 20.0
    max_attempts: int = 3
    backoff_base: float = 2.0


class _Pacer:
    """Spaces request starts at least `delay` seconds apart across all workers."""

    def __init__(self, delay: float):
        self.delay, self._next, self._lock = delay, 0.0, asyncio.Lock()

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await asyncio.sleep(self._next - now)
            self._next = max(now, self._next) + self.delay


async def load_robots(client: httpx.AsyncClient, base: str, user_agent: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        r = await client.get(f"{base}/robots.txt")
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        rp.parse([])  # robots.txt unreachable: be conservative, still pace requests
    return rp


async def crawl(store: KBStore, urls: list[str], process: Callable[[str, str], Awaitable[str]],
                cfg: CrawlConfig, base: str, client: httpx.AsyncClient | None = None) -> dict:
    """Fetch each URL and hand the HTML to `process`, which returns added | updated | unchanged."""
    report = {"added": 0, "updated": 0, "unchanged": 0, "failed": 0, "removed": 0, "blocked_by_robots": 0}
    own_client = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": cfg.user_agent}, timeout=cfg.timeout_seconds,
                                         follow_redirects=True)
    pacer, sem = _Pacer(cfg.delay_seconds), asyncio.Semaphore(cfg.concurrency)
    robots = await load_robots(client, base, cfg.user_agent)

    async def one(url: str):
        if not robots.can_fetch(cfg.user_agent, url):
            store.mark_page(url, state="failed", error="disallowed by robots.txt")
            report["blocked_by_robots"] += 1
            return
        async with sem:
            while True:
                attempt = store.bump_attempt(url)
                await pacer.wait()
                try:
                    r = await client.get(url)
                except httpx.HTTPError as e:
                    status, err = None, f"{type(e).__name__}: {e}"
                else:
                    status, err = r.status_code, None
                    if status == 200:
                        try:
                            outcome = await process(url, r.text)
                        except Exception as e:  # parse/index failure: isolate this page
                            store.mark_page(url, state="failed", http_status=status, error=f"process: {e}"[:500])
                            report["failed"] += 1
                            return
                        store.mark_page(url, state="done", http_status=status, fetched_at=time.time(), error=None,
                                        content_hash=hashlib.sha256(r.text.encode()).hexdigest()[:16])
                        report[outcome] += 1
                        return
                    if status in GONE:
                        store.mark_page(url, state="removed", http_status=status)
                        store.deactivate_article(article_id(url) or "")
                        report["removed"] += 1
                        return
                retry = status is None or status in RETRYABLE
                if not retry or attempt >= cfg.max_attempts:
                    store.mark_page(url, state="failed", http_status=status, error=err or f"HTTP {status}")
                    report["failed"] += 1
                    return
                await asyncio.sleep(cfg.backoff_base ** attempt)

    try:
        await asyncio.gather(*(one(u) for u in urls))
    finally:
        if own_client:
            await client.aclose()
    return report
