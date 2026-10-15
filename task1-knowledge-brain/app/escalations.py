"""Human-expert escalations and the path back into memory.

1. A question the brain cannot answer becomes an open escalation (question, reason, evidence it saw).
2. An expert resolves it with an answer, a scope and an optional expiry date.
   - scope "general": a reusable product fact. It is stored as an "expert answer" article
     (versioned, chunked, embedded) and becomes searchable on the next index reload.
   - scope "customer": specific to one customer. It is kept for the record but NOT added to memory,
     so one customer's situation never becomes a "product fact" for everyone.
   - expires_at: after this date the expert answer is deactivated and stops appearing in answers.
3. Every escalated question is appended to evaluation/escalated_questions.jsonl for future evals.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import settings
from ingestion.store import KBStore
from retrieval import embedder
from retrieval.chunker import chunk_article, embed_text
from shared.guardrails import redact_pii

SCHEMA = """
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    question TEXT NOT NULL,          -- PII-redacted
    reason TEXT NOT NULL,
    evidence TEXT NOT NULL,          -- JSON list of {title, url, score}
    source TEXT NOT NULL,            -- query | manual | inbox | scope
    status TEXT NOT NULL,            -- open | resolved | dismissed
    expert TEXT, answer TEXT, scope TEXT, expires_at TEXT, resolved_at REAL,
    article_id TEXT                  -- set when a general answer was added to memory
);
"""
EXPERT_URL = "kb://expert-answers/{id}"
EVAL_FILE = settings.ROOT / "evaluation" / "escalated_questions.jsonl"


class Escalations:
    def __init__(self, store: KBStore, eval_file: Path | None = EVAL_FILE):
        self.store = store
        self.eval_file = eval_file  # None = do not log questions for evals (tests)
        with store.conn() as c:
            c.executescript(SCHEMA)

    def create(self, question: str, reason: str, evidence: list[dict] | None = None, source: str = "query") -> dict:
        q = redact_pii(question)
        with self.store.conn() as c:
            dup = c.execute("SELECT id FROM escalations WHERE status='open' AND lower(question)=lower(?)", (q,)).fetchone()
            if dup:  # same open question asked again: one task, not many
                return self.get(dup["id"])
            eid = c.execute("""INSERT INTO escalations (created_at, question, reason, evidence, source, status)
                               VALUES (?,?,?,?,?, 'open')""",
                            (time.time(), q, reason, json.dumps(evidence or []), source)).lastrowid
        if self.eval_file is None:
            return self.get(eid)
        self.eval_file.parent.mkdir(parents=True, exist_ok=True)
        with self.eval_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"escalation_id": eid, "question": q, "reason": reason,
                                "created_at": datetime.now().isoformat(timespec="seconds")}) + "\n")
        return self.get(eid)

    def get(self, eid: int) -> dict | None:
        with self.store.conn() as c:
            r = c.execute("SELECT * FROM escalations WHERE id = ?", (eid,)).fetchone()
        return {**dict(r), "evidence": json.loads(r["evidence"])} if r else None

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        with self.store.conn() as c:
            q = "SELECT * FROM escalations" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT ?"
            rows = c.execute(q, ((status,) if status else ()) + (limit,)).fetchall()
        return [{**dict(r), "evidence": json.loads(r["evidence"])} for r in rows]

    def resolve(self, eid: int, answer: str, expert: str, scope: str = "general",
                expires_at: str | None = None, embed=None) -> dict:
        esc = self.get(eid)
        if esc is None:
            raise KeyError(eid)
        if esc["status"] != "open":
            raise ValueError(f"escalation {eid} is already {esc['status']}")
        if scope not in ("general", "customer"):
            raise ValueError("scope must be 'general' or 'customer'")
        if expires_at:
            datetime.fromisoformat(expires_at)  # validate
        article_id = None
        if scope == "general":
            article_id = f"expert-{eid}"
            art = {"article_id": article_id, "url": EXPERT_URL.format(id=eid),
                   "title": f"Expert answer: {esc['question'][:120]}", "breadcrumb": ["Expert answers"],
                   "sections": [{"heading": "", "text": f"Question: {esc['question']}\nAnswer: {answer}", "images": []}],
                   "images": [], "body_text": answer, "lastmod": expires_at,
                   "content_hash": f"expert-{eid}-{hash(answer) & 0xffffffff:x}"}
            chunks = chunk_article(art, settings.CHUNK_MAX_TOKENS, settings.CHUNK_OVERLAP_SENTENCES)
            embed = embed or embedder.get(settings.EMBED_MODEL).encode
            for ch, v in zip(chunks, embed([embed_text(art["title"], ch["heading"], ch["text"]) for ch in chunks])):
                ch["embedding"] = v.tobytes()
            self.store.save_article(art, chunks)
        with self.store.conn() as c:
            c.execute("""UPDATE escalations SET status='resolved', expert=?, answer=?, scope=?, expires_at=?,
                         resolved_at=?, article_id=? WHERE id=?""",
                      (expert, answer, scope, expires_at, time.time(), article_id, eid))
        return self.get(eid)

    def expire(self, today: str | None = None) -> int:
        """Deactivate expert answers past their expiry date. Returns how many were deactivated."""
        today = today or datetime.now().date().isoformat()
        rows = [r for r in self.list("resolved", 10_000) if r["article_id"] and r["expires_at"] and r["expires_at"] < today]
        for r in rows:
            self.store.deactivate_article(r["article_id"])
        return len(rows)
