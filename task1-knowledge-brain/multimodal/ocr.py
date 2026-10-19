"""Read the text inside screenshots (offline OCR, $0) and make it searchable.

Help-center answers often live in screenshots: the exact button or setting name. This job downloads
each screenshot once, politely (one at a time, a pause between downloads), reads it with RapidOCR
(ONNX, runs locally), stores the text per image, then re-indexes the article so the screenshot text
sits next to the section it belongs to. It is resumable: results are saved per image, and an image is
never read twice. Human-edited articles are re-indexed with their edited text, never overwritten.

    python run.py images [--limit N]
"""
import io
import json
import re
import time
from functools import lru_cache

import httpx

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_text (
    src TEXT PRIMARY KEY,
    status TEXT NOT NULL,          -- ok | failed | skipped
    text TEXT,
    lines INTEGER DEFAULT 0,
    ms REAL,
    error TEXT,
    read_at REAL NOT NULL
);
"""
MIN_SIDE = 120            # icons and tiny images carry no answer
MAX_CHARS = 1200          # per screenshot, enough for labels and steps
LOW_PRIORITY = re.compile(r"software update|changelog|release notes", re.I)


@lru_cache(maxsize=1)
def engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def ocr_bytes(data: bytes) -> tuple[str, int]:
    """Text in an image, one line per detected text line (duplicates removed). (-1 lines = skipped icon)."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        if min(im.size) < MIN_SIDE:
            return "", -1
    result, _ = engine()(data)
    seen, lines = set(), []
    for item in result or []:
        line = re.sub(r"\s+", " ", item[1]).strip()
        if len(line) > 1 and line.lower() not in seen:
            seen.add(line.lower())
            lines.append(line)
    return "\n".join(lines)[:MAX_CHARS], len(lines)


def ensure_schema(store) -> None:
    with store.conn() as c:
        c.executescript(SCHEMA)


def lookup(store, srcs: list[str]) -> dict[str, str]:
    if not srcs:
        return {}
    with store.conn() as c:
        q = f"SELECT src, text FROM image_text WHERE status = 'ok' AND src IN ({','.join('?' * len(srcs))})"
        return {r["src"]: r["text"] for r in c.execute(q, srcs) if r["text"]}


def enrich(art: dict, texts: dict[str, str]) -> dict:
    """A copy of the article whose sections carry the text read from their screenshots."""
    if not texts:
        return art
    out = {**art, "sections": []}
    for s in art["sections"]:
        extra = [texts[src] for src in s.get("images", []) if texts.get(src)]
        text = s["text"] + ("\n" + "\n".join(f"Screenshot shows: {t.replace(chr(10), ' | ')}" for t in extra)
                            if extra else "")
        out["sections"].append({**s, "text": text.strip()})
    return out


def pending_images(store, limit: int | None = None) -> list[tuple[str, str, str]]:
    """(src, article_id, title) for unread screenshots of approved, active articles. How-to articles first."""
    ensure_schema(store)
    with store.conn() as c:
        done = {r[0] for r in c.execute("SELECT src FROM image_text")}
        rows = c.execute("""SELECT article_id, title, images FROM articles
                            WHERE active = 1 AND review_status = 'approved' AND images != '[]'""").fetchall()
    items, seen = [], set()
    for low in (False, True):
        for r in rows:
            if bool(LOW_PRIORITY.search(r["title"])) != low:
                continue
            for img in json.loads(r["images"]):
                src = img["src"]
                if src.startswith("http") and src not in done and src not in seen:
                    seen.add(src)
                    items.append((src, r["article_id"], r["title"]))
    return items[:limit] if limit else items


def reindex(store, article_id: str, embed) -> bool:
    """Rebuild one article's chunks with its screenshot text. Same version; only the index changes."""
    from retrieval.chunker import chunk_article, embed_text
    import settings
    a = store.article(article_id)
    if a is None:
        return False
    art = {"article_id": a["article_id"], "title": a["title"], "sections": json.loads(a["sections"])}
    srcs = [src for s in art["sections"] for src in s.get("images", [])]
    texts = lookup(store, srcs)
    chunks = chunk_article(enrich(art, texts), settings.CHUNK_MAX_TOKENS, settings.CHUNK_OVERLAP_SENTENCES)
    for ch, v in zip(chunks, embed([embed_text(art["title"], ch["heading"], ch["text"]) for ch in chunks])):
        ch["embedding"] = v.tobytes()
    store.replace_chunks(article_id, chunks)
    return True


def run(store, embed, limit: int | None = None, delay: float = 0.5, user_agent: str = "",
        http: httpx.Client | None = None, ocr=ocr_bytes, log=print) -> dict:
    """Read pending screenshots one by one; re-index each article once all its screenshots are read."""
    items = pending_images(store, limit)
    report = {"to_read": len(items), "read": 0, "with_text": 0, "failed": 0, "skipped": 0, "articles_reindexed": 0}
    if not items:
        return report
    http = http or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": user_agent})
    remaining: dict[str, int] = {}
    for _, aid, _ in items:
        remaining[aid] = remaining.get(aid, 0) + 1
    t_start = time.time()
    for n, (src, aid, title) in enumerate(items, 1):
        t0 = time.perf_counter()
        status, text, lines, err = "ok", "", 0, None
        try:
            r = http.get(src)
            r.raise_for_status()
            text, lines = ocr(r.content)
            if lines < 0:
                status, lines = "skipped", 0
        except Exception as e:  # a missing or broken image must never stop the job
            status, err = "failed", f"{type(e).__name__}: {e}"[:300]
        ms = (time.perf_counter() - t0) * 1000
        with store.conn() as c:
            c.execute("INSERT OR REPLACE INTO image_text VALUES (?,?,?,?,?,?,?)",
                      (src, status, text, lines, ms, err, time.time()))
        report["read" if status == "ok" else status] += 1
        report["with_text"] += bool(text)
        remaining[aid] -= 1
        if remaining[aid] == 0 and reindex(store, aid, embed):
            report["articles_reindexed"] += 1
        if n % 25 == 0 or n == len(items):
            rate = n / max(time.time() - t_start, 1)
            left = (len(items) - n) / rate / 60 if rate else 0
            log(f"  screenshots {n}/{len(items)} · with text {report['with_text']} · failed {report['failed']} "
                f"· about {left:.0f} min left")
        time.sleep(delay)
    return report


def status(store) -> dict:
    ensure_schema(store)
    with store.conn() as c:
        counts = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM image_text GROUP BY status")}
        with_text = c.execute("SELECT COUNT(*) FROM image_text WHERE text != ''").fetchone()[0]
    return {"read": counts, "with_text": with_text, "pending": len(pending_images(store))}

