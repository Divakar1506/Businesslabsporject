"""One ingestion run: sitemap -> manifest diff -> polite crawl -> parse -> chunk -> embed -> save.

Run it again at any time: unchanged pages are skipped, a stopped run resumes from the
pending pages, and removed pages are deactivated. The same function serves the first
full read and the daily refresh.
"""
import asyncio
from urllib.parse import urlparse

import httpx

import settings
from retrieval import embedder
from retrieval.chunker import chunk_article, embed_text

from .crawler import CrawlConfig, crawl
from .parser import parse_article
from .sitemap import parse_sitemap, sync_manifest
from .store import KBStore


def _process_factory(store: KBStore, lastmods: dict, embed):
    async def process(url: str, html: str) -> str:
        art = parse_article(url, html, lastmods.get(url))
        old = store.article(art["article_id"])
        if old and old["content_hash"] == art["content_hash"] and old["active"]:
            return "unchanged"  # skip chunking and embedding entirely
        chunks = chunk_article(art, settings.CHUNK_MAX_TOKENS, settings.CHUNK_OVERLAP_SENTENCES)
        texts = [embed_text(art["title"], c["heading"], c["text"]) for c in chunks]
        vecs = await asyncio.to_thread(embed, texts)  # keep the event loop free for other fetches
        for c, v in zip(chunks, vecs):
            c["embedding"] = v.tobytes()
        return store.save_article(art, chunks)
    return process


async def run(store: KBStore, limit: int | None = None, sitemap_xml: str | None = None,
              client: httpx.AsyncClient | None = None, embed=None) -> dict:
    """Returns a report: sitemap diff + crawl outcome counts."""
    run_id = store.start_run()
    host = urlparse(settings.SITEMAP_URL).netloc
    base = f"https://{host}"
    if sitemap_xml is None:
        async with httpx.AsyncClient(headers={"User-Agent": settings.USER_AGENT},
                                     timeout=settings.TIMEOUT_SECONDS) as c:
            r = await c.get(settings.SITEMAP_URL)
            r.raise_for_status()
            sitemap_xml = r.text
    entries = parse_sitemap(sitemap_xml, host, settings.ARTICLE_PATH)
    diff = sync_manifest(store, entries, limit)
    pending = [p["url"] for p in store.pages("pending")]
    embed = embed or embedder.get(settings.EMBED_MODEL).encode
    cfg = CrawlConfig(settings.USER_AGENT, settings.CONCURRENCY, settings.DELAY_SECONDS,
                      settings.TIMEOUT_SECONDS, settings.MAX_ATTEMPTS)
    outcome = await crawl(store, pending, _process_factory(store, dict(entries), embed), cfg, base, client)
    report = {"sitemap_articles": len(entries), "diff": diff, "crawl": outcome}
    store.finish_run(run_id, report)
    return report
