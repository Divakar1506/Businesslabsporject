"""SQLite store for the inbox: emails, versioned drafts, human reviews, the outbox (simulated sending),
autonomy state with an audit log, and versioned style rules learned from edits."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY, from_addr TEXT, subject TEXT, body TEXT, thread_id TEXT, received_at TEXT,
    status TEXT NOT NULL DEFAULT 'new'      -- new | drafted | sent | rejected | escalated | kb_unavailable
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_email ON drafts(email_id);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT NOT NULL, draft_id INTEGER NOT NULL, category TEXT NOT NULL,
    action TEXT NOT NULL,                    -- approve | edit | reject | escalate | auto
    draft_text TEXT, final_text TEXT, unchanged INTEGER NOT NULL, reason TEXT, reviewer TEXT,
    prompt_version TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    email_id TEXT PRIMARY KEY,               -- one send per email: retries cannot double-send
    text TEXT NOT NULL, sent_by TEXT NOT NULL, sent_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS autonomy (
    category TEXT PRIMARY KEY, streak INTEGER NOT NULL DEFAULT 0, eligible INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 0, updated_at REAL
);
CREATE TABLE IF NOT EXISTS autonomy_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, category TEXT NOT NULL, event TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS style_rules (
    version TEXT PRIMARY KEY, rules TEXT NOT NULL, note TEXT, created_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 0
);
"""


class InboxStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        with self.conn() as c:
            c.executescript(SCHEMA)
            if not c.execute("SELECT 1 FROM style_rules").fetchone():
                c.execute("INSERT INTO style_rules VALUES ('rules-v0', '{}', 'no learned rules', ?, 1)", (time.time(),))

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

    # --- emails & drafts ---------------------------------------------------
    def add_email(self, e: dict) -> bool:
        with self.conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO emails (id, from_addr, subject, body, thread_id, received_at) "
                            "VALUES (?,?,?,?,?,?)", (e["id"], e.get("from"), e["subject"], e["body"],
                                                     e.get("thread_id"), e.get("received_at")))
            return cur.rowcount == 1

    def email(self, eid: str) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM emails WHERE id = ?", (eid,)).fetchone()
        return dict(r) if r else None

    def emails(self, status: str | None = None) -> list[dict]:
        with self.conn() as c:
            q = "SELECT * FROM emails" + (" WHERE status = ?" if status else "") + " ORDER BY received_at, id"
            return [dict(r) for r in c.execute(q, (status,) if status else ())]

    def thread_subject(self, thread_id: str | None, exclude: str) -> str | None:
        if not thread_id:
            return None
        with self.conn() as c:
            r = c.execute("SELECT subject FROM emails WHERE (thread_id = ? OR id = ?) AND id != ? ORDER BY received_at LIMIT 1",
                          (thread_id, thread_id, exclude)).fetchone()
        return r["subject"] if r else None

    def set_status(self, eid: str, status: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE emails SET status = ? WHERE id = ?", (status, eid))

    def save_draft(self, draft: dict) -> int:
        with self.conn() as c:
            return c.execute("INSERT INTO drafts (email_id, data, created_at) VALUES (?,?,?)",
                             (draft["email_id"], json.dumps(draft), time.time())).lastrowid

    def latest_draft(self, eid: str) -> tuple[int, dict] | None:
        with self.conn() as c:
            r = c.execute("SELECT id, data FROM drafts WHERE email_id = ? ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
        return (r["id"], json.loads(r["data"])) if r else None

    # --- reviews & sending ---------------------------------------------------
    def add_review(self, **row) -> int:
        row["created_at"] = time.time()
        cols = ",".join(row)
        with self.conn() as c:
            return c.execute(f"INSERT INTO reviews ({cols}) VALUES ({','.join('?' * len(row))})",
                             tuple(row.values())).lastrowid

    def reviews(self, category: str | None = None, limit: int = 1000) -> list[dict]:
        with self.conn() as c:
            q = "SELECT * FROM reviews" + (" WHERE category = ?" if category else "") + " ORDER BY id DESC LIMIT ?"
            return [dict(r) for r in c.execute(q, ((category,) if category else ()) + (limit,))]

    def send(self, eid: str, text: str, sent_by: str) -> bool:
        """Simulated send. Returns False if this email was already sent (idempotent)."""
        with self.conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO outbox VALUES (?,?,?,?)", (eid, text, sent_by, time.time()))
            return cur.rowcount == 1

    def outbox(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM outbox ORDER BY sent_at DESC")]

    # --- autonomy ------------------------------------------------------------
    def autonomy(self, category: str) -> dict:
        with self.conn() as c:
            r = c.execute("SELECT * FROM autonomy WHERE category = ?", (category,)).fetchone()
        return dict(r) if r else {"category": category, "streak": 0, "eligible": 0, "enabled": 0, "updated_at": None}

    def all_autonomy(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM autonomy ORDER BY category")]

    def set_autonomy(self, category: str, **fields) -> None:
        cur = {**self.autonomy(category), **fields, "updated_at": time.time()}
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO autonomy VALUES (?,?,?,?,?)",
                      (category, cur["streak"], cur["eligible"], cur["enabled"], cur["updated_at"]))

    def log_event(self, category: str, event: str, detail: str = "") -> None:
        with self.conn() as c:
            c.execute("INSERT INTO autonomy_events (ts, category, event, detail) VALUES (?,?,?,?)",
                      (time.time(), category, event, detail))

    def events(self, limit: int = 100) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM autonomy_events ORDER BY id DESC LIMIT ?", (limit,))]

    # --- style rules -----------------------------------------------------------
    def active_rules(self) -> tuple[str, dict]:
        with self.conn() as c:
            r = c.execute("SELECT version, rules FROM style_rules WHERE active = 1").fetchone()
        return r["version"], json.loads(r["rules"])

    def all_rules(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM style_rules ORDER BY created_at DESC")]

    def add_rules(self, version: str, rules: dict, note: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO style_rules VALUES (?,?,?,?,0)", (version, json.dumps(rules), note, time.time()))

    def activate_rules(self, version: str) -> None:
        """Switch to a version (also used to roll back)."""
        with self.conn() as c:
            if not c.execute("SELECT 1 FROM style_rules WHERE version = ?", (version,)).fetchone():
                raise KeyError(version)
            c.execute("UPDATE style_rules SET active = (version = ?)", (version,))
