"""SQLite store for the Knowledge Brain: crawl manifest, articles, chunks with embeddings.

Idempotency: an article's chunks are replaced as a whole inside one transaction,
so re-processing an unchanged page never creates duplicates.
Rollback: every replaced article version is copied to article_versions first.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    article_id TEXT NOT NULL,
    lastmod TEXT,
    state TEXT NOT NULL,          -- pending | done | failed | removed
    attempts INTEGER DEFAULT 0,
    http_status INTEGER,
    content_hash TEXT,
    fetched_at REAL,
    error TEXT,
    first_seen REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS pages_state ON pages(state);
CREATE TABLE IF NOT EXISTS articles (
    article_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    breadcrumb TEXT,              -- JSON list
    body_text TEXT NOT NULL,
    sections TEXT NOT NULL,       -- JSON list of {heading, text}
    images TEXT NOT NULL,         -- JSON list of {src, heading, before, after}
    content_hash TEXT NOT NULL,
    lastmod TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    ingested_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS article_versions (
    article_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, archived_at REAL NOT NULL,
    PRIMARY KEY (article_id, version)
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL,
    ord INTEGER NOT NULL,
    heading TEXT,
    text TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    image_refs TEXT NOT NULL,     -- JSON list of image src
    embedding BLOB,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS chunks_article ON chunks(article_id);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL, finished_at REAL,
    report TEXT                   -- JSON {added, updated, unchanged, removed, failed}
);
"""


class KBStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = self._mem or sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            if self._mem is None:
                c.close()

    # --- manifest ---------------------------------------------------------
    def pages(self, state: str | None = None) -> list[dict]:
        with self.conn() as c:
            q = "SELECT * FROM pages" + (" WHERE state = ?" if state else "") + " ORDER BY url"
            return [dict(r) for r in c.execute(q, (state,) if state else ())]

    def upsert_page(self, url: str, article_id: str, lastmod: str | None, state: str) -> None:
        now = time.time()
        with self.conn() as c:
            c.execute("""INSERT INTO pages (url, article_id, lastmod, state, first_seen, updated_at)
                         VALUES (?,?,?,?,?,?)
                         ON CONFLICT(url) DO UPDATE SET lastmod=excluded.lastmod, state=excluded.state,
                                                        attempts=0, error=NULL, updated_at=excluded.updated_at""",
                      (url, article_id, lastmod, state, now, now))

    def mark_page(self, url: str, **fields) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE pages SET {cols} WHERE url = ?", (*fields.values(), url))

    def bump_attempt(self, url: str) -> int:
        with self.conn() as c:
            c.execute("UPDATE pages SET attempts = attempts + 1, updated_at = ? WHERE url = ?", (time.time(), url))
            return c.execute("SELECT attempts FROM pages WHERE url = ?", (url,)).fetchone()[0]

    # --- articles & chunks -------------------------------------------------
    def article(self, article_id: str) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM articles WHERE article_id = ?", (article_id,)).fetchone()
        return dict(r) if r else None

    def save_article(self, art: dict, chunks: list[dict]) -> str:
        """Insert or replace an article and all its chunks atomically. Returns added | updated | unchanged."""
        now = time.time()
        with self.conn() as c:
            old = c.execute("SELECT * FROM articles WHERE article_id = ?", (art["article_id"],)).fetchone()
            if old and old["content_hash"] == art["content_hash"] and old["active"]:
                return "unchanged"
            version = 1
            if old:
                version = old["version"] + 1
                c.execute("INSERT OR REPLACE INTO article_versions VALUES (?,?,?,?)",
                          (old["article_id"], old["version"], json.dumps(dict(old)), now))
            c.execute("""INSERT OR REPLACE INTO articles
                         (article_id, url, title, breadcrumb, body_text, sections, images, content_hash, lastmod,
                          version, active, ingested_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                      (art["article_id"], art["url"], art["title"], json.dumps(art["breadcrumb"]), art["body_text"],
                       json.dumps(art["sections"]), json.dumps(art["images"]), art["content_hash"],
                       art.get("lastmod"), version, now))
            c.execute("DELETE FROM chunks WHERE article_id = ?", (art["article_id"],))
            c.executemany("""INSERT INTO chunks (chunk_id, article_id, ord, heading, text, tokens, image_refs, embedding, active)
                             VALUES (?,?,?,?,?,?,?,?,1)""",
                          [(ch["chunk_id"], art["article_id"], ch["ord"], ch["heading"], ch["text"], ch["tokens"],
                            json.dumps(ch["image_refs"]), ch.get("embedding")) for ch in chunks])
        return "updated" if old else "added"

    def deactivate_article(self, article_id: str) -> None:
        """Removed from the help center: hide from answers but keep for audit/rollback."""
        with self.conn() as c:
            c.execute("UPDATE articles SET active = 0 WHERE article_id = ?", (article_id,))
            c.execute("UPDATE chunks SET active = 0 WHERE article_id = ?", (article_id,))

    def active_chunks(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                """SELECT ch.chunk_id, ch.article_id, ch.ord, ch.heading, ch.text, ch.tokens, ch.image_refs,
                          ch.embedding, a.title, a.url
                   FROM chunks ch JOIN articles a ON a.article_id = ch.article_id
                   WHERE ch.active = 1 AND a.active = 1 ORDER BY ch.article_id, ch.ord""")]

    # --- runs -------------------------------------------------------------
    def start_run(self) -> int:
        with self.conn() as c:
            return c.execute("INSERT INTO runs (started_at) VALUES (?)", (time.time(),)).lastrowid

    def finish_run(self, run_id: int, report: dict) -> None:
        with self.conn() as c:
            c.execute("UPDATE runs SET finished_at = ?, report = ? WHERE id = ?", (time.time(), json.dumps(report), run_id))

    def status(self) -> dict:
        with self.conn() as c:
            states = {r[0]: r[1] for r in c.execute("SELECT state, COUNT(*) FROM pages GROUP BY state")}
            arts = c.execute("SELECT COUNT(*) FROM articles WHERE active = 1").fetchone()[0]
            chunks = c.execute("SELECT COUNT(*) FROM chunks WHERE active = 1").fetchone()[0]
            last = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return {"pages": states, "active_articles": arts, "active_chunks": chunks,
                "last_run": ({**dict(last), "report": json.loads(last["report"]) if last["report"] else None}
                             if last else None)}
