"""Projects, meetings, scope versions and an audit log - with the approval rules enforced here,
not in the screen, so no client can bypass them.

Rules:
- every generated version starts as a draft
- only a named human can approve a version; the approved content is hashed
- send_to_client() refuses anything that is not approved, or that changed after approval
- a follow-up is compared with the latest APPROVED version (the baseline), never with a draft
"""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, client TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, date TEXT, transcript TEXT NOT NULL,
    follows TEXT, added_at REAL);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, version INTEGER NOT NULL,
    meeting_id TEXT NOT NULL, data TEXT NOT NULL, content_hash TEXT NOT NULL,
    status TEXT NOT NULL,                 -- draft | approved | superseded | sent
    created_at REAL NOT NULL, approved_by TEXT, approved_at REAL, approved_hash TEXT, sent_at REAL,
    UNIQUE(project_id, version));
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, project_id INTEGER, version INTEGER,
    actor TEXT, event TEXT NOT NULL, detail TEXT);
"""


class ApprovalError(Exception):
    pass


def content_hash(data: dict) -> str:
    keep = {k: data.get(k) for k in ("requirements", "scope", "estimate", "proposal")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


class ScopeStore:
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

    def log(self, project_id, version, actor, event, detail=""):
        with self.conn() as c:
            c.execute("INSERT INTO audit (ts, project_id, version, actor, event, detail) VALUES (?,?,?,?,?,?)",
                      (time.time(), project_id, version, actor, event, detail))

    # --- projects & meetings ---------------------------------------------------
    def project(self, name: str, client: str = "") -> int:
        with self.conn() as c:
            r = c.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
            if r:
                return r["id"]
            pid = c.execute("INSERT INTO projects (name, client, created_at) VALUES (?,?,?)",
                            (name, client, time.time())).lastrowid
        self.log(pid, None, "system", "project_created", name)
        return pid

    def projects(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM projects ORDER BY id")]

    def add_meeting(self, mid: str, project_id: int, date: str, transcript: str, follows: str | None = None) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO meetings VALUES (?,?,?,?,?,?)",
                      (mid, project_id, date, transcript, follows, time.time()))
        self.log(project_id, None, "system", "meeting_added", f"{mid} ({date})" + (f" follows {follows}" if follows else ""))

    def meetings(self, project_id: int) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM meetings WHERE project_id = ? ORDER BY date", (project_id,))]

    # --- versions ----------------------------------------------------------------
    def save_version(self, project_id: int, meeting_id: str, data: dict, actor: str = "agents") -> int:
        with self.conn() as c:
            n = (c.execute("SELECT MAX(version) FROM versions WHERE project_id = ?", (project_id,)).fetchone()[0] or 0) + 1
            c.execute("""INSERT INTO versions (project_id, version, meeting_id, data, content_hash, status, created_at)
                         VALUES (?,?,?,?,?, 'draft', ?)""", (project_id, n, meeting_id, json.dumps(data),
                                                             content_hash(data), time.time()))
        self.log(project_id, n, actor, "draft_created", f"from {meeting_id}")
        return n

    def version(self, project_id: int, version: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM versions WHERE project_id = ? AND version = ?", (project_id, version)).fetchone()
        return {**dict(r), "data": json.loads(r["data"])} if r else None

    def versions(self, project_id: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM versions WHERE project_id = ? ORDER BY version", (project_id,)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def baseline(self, project_id: int) -> dict | None:
        """The latest approved (or approved-and-sent) version."""
        with self.conn() as c:
            r = c.execute("""SELECT version FROM versions WHERE project_id = ? AND status IN ('approved', 'sent')
                             ORDER BY version DESC LIMIT 1""", (project_id,)).fetchone()
        return self.version(project_id, r["version"]) if r else None

    def update_draft(self, project_id: int, version: int, data: dict, actor: str) -> None:
        """Human edits are allowed on drafts only."""
        v = self.version(project_id, version)
        if v["status"] != "draft":
            raise ApprovalError(f"version {version} is {v['status']}; create a new version instead")
        with self.conn() as c:
            c.execute("UPDATE versions SET data = ?, content_hash = ? WHERE project_id = ? AND version = ?",
                      (json.dumps(data), content_hash(data), project_id, version))
        self.log(project_id, version, actor, "draft_edited")

    def approve(self, project_id: int, version: int, reviewer: str) -> None:
        if not reviewer or not reviewer.strip():
            raise ApprovalError("a named reviewer is required")
        v = self.version(project_id, version)
        if v is None:
            raise ApprovalError(f"version {version} not found")
        if v["status"] != "draft":
            raise ApprovalError(f"version {version} is already {v['status']}")
        blockers = [i for i in v["data"].get("review_issues", []) if i["severity"] == "blocker"]
        if blockers:
            raise ApprovalError("fix the blocker issues first: " + "; ".join(i["issue"] for i in blockers))
        with self.conn() as c:
            c.execute("UPDATE versions SET status = 'superseded' WHERE project_id = ? AND status = 'approved'", (project_id,))
            c.execute("""UPDATE versions SET status = 'approved', approved_by = ?, approved_at = ?, approved_hash = ?
                         WHERE project_id = ? AND version = ?""", (reviewer, time.time(), v["content_hash"], project_id, version))
        self.log(project_id, version, reviewer, "approved", f"hash {v['content_hash']}")

    def send_to_client(self, project_id: int, version: int, actor: str) -> None:
        """Simulated sending. Refuses unapproved or changed-after-approval versions."""
        v = self.version(project_id, version)
        if v is None or v["status"] not in ("approved",):
            self.log(project_id, version, actor, "send_refused", f"status {v['status'] if v else 'missing'}")
            raise ApprovalError(f"version {version} is not approved; nothing reaches the client without approval")
        if v["approved_hash"] != content_hash(v["data"]):
            self.log(project_id, version, actor, "send_refused", "content changed after approval")
            raise ApprovalError("the content changed after approval; approve it again")
        with self.conn() as c:
            c.execute("UPDATE versions SET status = 'sent', sent_at = ? WHERE project_id = ? AND version = ?",
                      (time.time(), project_id, version))
        self.log(project_id, version, actor, "sent_to_client")

    def audit(self, project_id: int) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM audit WHERE project_id = ? ORDER BY id DESC", (project_id,))]
