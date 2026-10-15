"""Sitemap parsing and the manifest diff: which article pages are new, changed or removed."""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from .store import KBStore

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_ARTICLE_ID = re.compile(r"/solutions/articles/(\d+)")
# Release notes answer few customer questions; a limited test read samples how-to articles first.
_LOW_PRIORITY = re.compile(r"software-update|changelog|release-notes", re.I)


def article_id(url: str) -> str | None:
    m = _ARTICLE_ID.search(url)
    return m.group(1) if m else None


def parse_sitemap(xml_text: str, host: str, article_path: str) -> list[tuple[str, str | None]]:
    """Return (url, lastmod) for every valid, de-duplicated article URL on `host`.

    Several URLs can point to one article (with or without the slug); the first one wins.
    """
    root = ET.fromstring(xml_text)
    seen_ids, out = set(), []
    for node in root.findall("sm:url", NS):
        loc = (node.findtext("sm:loc", default="", namespaces=NS) or "").strip()
        lastmod = (node.findtext("sm:lastmod", default="", namespaces=NS) or "").strip() or None
        u = urlparse(loc)
        if u.scheme != "https" or u.netloc != host or article_path not in u.path:
            continue
        aid = article_id(u.path)
        if not aid or aid in seen_ids:
            continue
        seen_ids.add(aid)
        out.append((loc, lastmod))
    return out


def spread(items: list, k: int) -> list:
    """k items evenly spaced across the list - a sample from every part of the help center."""
    if k <= 0:
        return []
    if k >= len(items):
        return list(items)
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def sync_manifest(store: KBStore, entries: list[tuple[str, str | None]], limit: int | None = None) -> dict:
    """Compare the sitemap with the saved manifest and queue work.

    - tracked page whose lastmod changed -> pending (re-fetch)
    - tracked page missing from the sitemap -> removed (its article is deactivated)
    - untracked page -> pending, up to `limit` tracked pages in total (None = all)
    """
    tracked = {p["url"]: p for p in store.pages() if p["state"] != "removed"}
    current = dict(entries)
    report = {"new": 0, "changed": 0, "unchanged": 0, "removed": 0}

    for url, page in tracked.items():
        if url not in current:
            store.mark_page(url, state="removed")
            store.deactivate_article(page["article_id"])
            report["removed"] += 1
        elif current[url] != page["lastmod"] or page["state"] == "failed":
            store.upsert_page(url, page["article_id"], current[url], "pending")
            report["changed"] += 1
        else:
            report["unchanged"] += 1

    untracked = [(u, lm) for u, lm in entries if u not in tracked]
    room = len(untracked) if limit is None else max(0, limit - (len(tracked) - report["removed"]))
    if limit is not None:
        main = [e for e in untracked if not _LOW_PRIORITY.search(e[0])]
        picked = spread(main, room)
        picked += spread([e for e in untracked if _LOW_PRIORITY.search(e[0])], room - len(picked))
    else:
        picked = untracked
    for url, lastmod in picked:
        store.upsert_page(url, article_id(url), lastmod, "pending")
        report["new"] += 1
    return report
