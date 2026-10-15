"""Task 1 tests. Fully offline: a simulated help center (httpx MockTransport) and fake embeddings.

    cd task1-knowledge-brain && python -m unittest discover -s tests -t . -v
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

import httpx
import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
os.environ.setdefault("LLM_MODE", "offline")
os.environ["KB_DELAY_SECONDS"] = "0"

import settings  # noqa: E402

settings.DELAY_SECONDS = 0.0

from ingestion import pipeline  # noqa: E402
from ingestion.crawler import CrawlConfig, crawl  # noqa: E402
from ingestion.parser import parse_article  # noqa: E402
from ingestion.sitemap import parse_sitemap, spread, sync_manifest  # noqa: E402
from ingestion.store import KBStore  # noqa: E402
from retrieval.chunker import chunk_article  # noqa: E402
from retrieval.index import HybridIndex  # noqa: E402

HOST = "support.brilliantdirectories.com"
BASE = f"https://{HOST}"
A1 = f"{BASE}/support/solutions/articles/111-reset-password"
A2 = f"{BASE}/support/solutions/articles/222-refund-settings"


def sitemap(*entries):
    urls = "".join(f"<url><loc>{u}</loc><lastmod>{lm}</lastmod></url>" for u, lm in entries)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


def page(title, body):
    return (f'<html><body><nav>Menu Home Login</nav><div class="breadcrumb"><a href="/">Solution home</a>'
            f'<a href="/s">Section: Account</a><a href="/x">{title}</a></div>'
            f'<article class="article-body" id="article-body"><h1 class="post-title heading">{title}</h1>'
            f'<p class="the-article-link"><small>Link: <a href="#">x</a></small></p>{body}</article>'
            f'<footer>Related articles footer</footer></body></html>')


PAGES = {
    A1: page("How to Reset a Password",
             "<p>Members can reset their password from the login page.</p>"
             "<h2>Steps</h2><ol><li>Click Forgot Password.</li><li>Enter the account email.</li></ol>"
             "<img src='https://cdn.example.com/reset.png'><p>Check the inbox for the reset link.</p>"),
    A2: page("Refund Settings",
             "<p>Admins can issue refunds from Finance » Invoices.</p>"
             "<h2>Partial refunds</h2><table><tr><th>Plan</th><th>Refund</th></tr>"
             "<tr><td>Monthly</td><td>Prorated</td></tr></table>"),
}


def fake_embed(texts):
    """Deterministic bag-of-letters vectors: same text -> same vector, no model needed."""
    out = np.zeros((len(texts), 384), dtype=np.float32)
    for i, t in enumerate(texts):
        for ch in t.lower():
            out[i, ord(ch) % 384] += 1
        out[i] /= np.linalg.norm(out[i]) or 1
    return out


class SimSite:
    """A simulated help center. Counts requests so tests can prove nothing is re-fetched."""

    def __init__(self, pages):
        self.pages, self.hits, self.fail_once = dict(pages), [], set()

    def handler(self, req: httpx.Request):
        url = str(req.url)
        self.hits.append(url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /support/search\n")
        if url in self.fail_once:
            self.fail_once.discard(url)
            return httpx.Response(503)
        if url in self.pages:
            return httpx.Response(200, text=self.pages[url])
        return httpx.Response(404)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


class SitemapTests(unittest.TestCase):
    def test_parse_filters_and_dedupes(self):
        xml = sitemap((A1, "2026-01-01"), (A1 + "?x", "2026-01-02"), (f"{BASE}/support/home", "2026-01-01"),
                      ("https://evil.example.com/support/solutions/articles/9-x", "2026-01-01"))
        self.assertEqual(parse_sitemap(xml, HOST, "/support/solutions/articles/"), [(A1, "2026-01-01")])

    def test_spread_and_limits(self):
        self.assertEqual(spread(list(range(10)), 0), [])
        self.assertEqual(spread(list(range(10)), 5), [0, 2, 4, 6, 8])
        store = KBStore(":memory:")
        self.assertEqual(sync_manifest(store, [(A1, "1"), (A2, "1")], limit=1)["new"], 1)
        self.assertEqual(sync_manifest(store, [(A1, "1"), (A2, "1")], limit=1)["new"], 0)  # no crash at limit


class ParserChunkerTests(unittest.TestCase):
    def test_parser_keeps_only_article_body(self):
        a = parse_article(A1, PAGES[A1])
        self.assertEqual((a["article_id"], a["title"], a["breadcrumb"]), ("111", "How to Reset a Password", ["Section: Account"]))
        self.assertNotIn("Menu", a["body_text"])
        self.assertNotIn("footer", a["body_text"])
        self.assertNotIn("Link:", a["body_text"])
        self.assertEqual([s["heading"] for s in a["sections"]], ["", "Steps"])
        img = a["images"][0]
        self.assertEqual((img["heading"], img["before"], img["after"]),
                         ("Steps", "Enter the account email.", "Check the inbox for the reset link."))

    def test_table_rows_kept(self):
        a = parse_article(A2, PAGES[A2])
        self.assertIn("Monthly | Prorated", a["body_text"].replace("  ", " "))

    def test_chunks_respect_sections_and_size(self):
        art = parse_article(A1, PAGES[A1])
        art["sections"].append({"heading": "Long", "text": "\n".join(f"Sentence number {i} is here." for i in range(200)),
                                "images": []})
        chunks = chunk_article(art, max_tokens=60, overlap=1)
        self.assertTrue(all(c["tokens"] <= 60 for c in chunks))
        long = [c for c in chunks if c["heading"] == "Long"]
        self.assertGreater(len(long), 3)
        self.assertEqual(long[0]["text"].split("\n")[-1], long[1]["text"].split("\n")[0])  # overlap
        self.assertEqual(len({c["chunk_id"] for c in chunks}), len(chunks))
        steps = [c for c in chunks if c["heading"] == "Steps"][0]
        self.assertEqual(steps["image_refs"], ["https://cdn.example.com/reset.png"])


class CrawlerTests(unittest.TestCase):
    def _run(self, site, urls, store):
        async def process(url, html):
            return "added"
        cfg = CrawlConfig("test-agent", concurrency=2, delay_seconds=0, max_attempts=3, backoff_base=0)
        for u in urls:
            store.upsert_page(u, u.rsplit("/", 1)[-1].split("-")[0], "1", "pending")

        async def go():
            async with site.client() as c:
                return await crawl(store, urls, process, cfg, BASE, c)
        return asyncio.run(go())

    def test_retry_then_success_404_and_robots(self):
        site = SimSite(PAGES)
        site.fail_once.add(A1)
        store = KBStore(":memory:")
        blocked = f"{BASE}/support/search/123-x"
        gone = f"{BASE}/support/solutions/articles/333-gone"
        rep = self._run(site, [A1, gone, blocked], store)
        self.assertEqual((rep["added"], rep["removed"], rep["blocked_by_robots"]), (1, 1, 1))
        self.assertEqual(site.hits.count(A1), 2)            # one 503, one retry
        self.assertNotIn(blocked, site.hits)                # robots.txt respected
        states = {p["url"]: p["state"] for p in store.pages()}
        self.assertEqual((states[A1], states[gone]), ("done", "removed"))

    def test_permanent_failure_not_retried_forever(self):
        site = SimSite({})
        site.handler_status = 500
        store = KBStore(":memory:")
        url = f"{BASE}/support/solutions/articles/444-boom"
        site.pages = {}
        orig = site.handler
        site.handler = lambda req: httpx.Response(500) if not str(req.url).endswith("robots.txt") else orig(req)
        rep = self._run(site, [url], store)
        self.assertEqual(rep["failed"], 1)
        self.assertEqual(site.hits.count(url) if site.hits else 0, 0)  # orig not called for the page
        self.assertEqual(store.pages()[0]["attempts"], 3)


class PipelineTests(unittest.TestCase):
    """The full cycle: first read, no-op rerun, update, removal - all against the simulated site."""

    def run_pipeline(self, store, site, xml):
        async def go():
            async with site.client() as c:
                return await pipeline.run(store, None, sitemap_xml=xml, client=c, embed=fake_embed)
        return asyncio.run(go())

    def test_full_cycle(self):
        store, site = KBStore(":memory:"), SimSite(PAGES)
        r1 = self.run_pipeline(store, site, sitemap((A1, "1"), (A2, "1")))
        self.assertEqual(r1["crawl"]["added"], 2)
        n_chunks = store.status()["active_chunks"]

        site.hits.clear()
        r2 = self.run_pipeline(store, site, sitemap((A1, "1"), (A2, "1")))
        self.assertEqual(r2["diff"]["unchanged"], 2)
        self.assertEqual([h for h in site.hits if "articles" in h], [])   # nothing re-fetched
        self.assertEqual(store.status()["active_chunks"], n_chunks)      # no duplicates

        site.pages[A1] = page("How to Reset a Password", "<p>New flow: use Settings » Security » Reset.</p>")
        r3 = self.run_pipeline(store, site, sitemap((A1, "2"), (A2, "1")))
        self.assertEqual(r3["crawl"]["updated"], 1)
        self.assertIn("Settings » Security", store.article("111")["body_text"])
        self.assertEqual(store.article("111")["version"], 2)

        r4 = self.run_pipeline(store, site, sitemap((A1, "2")))
        self.assertEqual(r4["diff"]["removed"], 1)
        self.assertEqual({c["article_id"] for c in store.active_chunks()}, {"111"})  # removed article hidden


class IndexTests(unittest.TestCase):
    def test_bm25_and_hybrid_find_the_right_article(self):
        store = KBStore(":memory:")
        for url in (A1, A2):
            art = parse_article(url, PAGES[url])
            chunks = chunk_article(art)
            for c, v in zip(chunks, fake_embed([c["text"] for c in chunks])):
                c["embedding"] = v.tobytes()
            store.save_article(art, chunks)
        idx = HybridIndex(store.active_chunks())
        q = "how do I issue a refund"
        for mode in ("bm25", "hybrid"):
            self.assertEqual(idx.search(q, fake_embed([q])[0], k=1, mode=mode)[0]["article_id"], "222", mode)


class BrainTests(unittest.TestCase):
    def _brain(self, min_score):
        from app.answer import Brain
        from shared.llm_gateway import Gateway, UsageStore
        store = KBStore(":memory:")
        for url in (A1, A2):
            art = parse_article(url, PAGES[url])
            chunks = chunk_article(art)
            for c, v in zip(chunks, fake_embed([c["text"] for c in chunks])):
                c["embedding"] = v.tobytes()
            store.save_article(art, chunks)
        b = Brain.__new__(Brain)
        b.store, b.gateway = store, Gateway(store=UsageStore(":memory:"), mode="offline")
        b.embedder = type("E", (), {"dim": 384, "encode_query": staticmethod(lambda q: fake_embed([q])[0])})()
        b._index, b._lock = None, __import__("threading").Lock()
        settings.MIN_SCORE = min_score
        return b

    def test_offline_answer_is_extractive_with_sources(self):
        r = self._brain(0.0).query("How do I reset my password?")
        self.assertEqual((r["status"], r["answer_mode"]), ("answered", "extractive"))
        self.assertIn("[1]", r["answer"])
        self.assertEqual(r["sources"][0]["url"], A1)

    def test_weak_evidence_goes_to_human(self):
        r = self._brain(0.999).query("Do you sell pizza?")
        self.assertEqual((r["status"], r["needs_human"], r["sources"]), ("insufficient_evidence", True, []))

    def tearDown(self):
        settings.MIN_SCORE = 0.65


class ApiTests(unittest.TestCase):
    def test_validation_and_health(self):
        from fastapi.testclient import TestClient
        from app import main
        main.store = KBStore(":memory:")
        c = TestClient(main.app)
        self.assertEqual(c.get("/health").json()["status"], "empty")
        self.assertEqual(c.post("/query", json={"question": "hi"}).status_code, 422)
        self.assertEqual(c.post("/query", json={"question": "x" * 2001}).status_code, 422)
        self.assertEqual(c.post("/query", json={"question": "valid question", "top_k": 99}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
