"""SQLite store for call usage, budget settings and alerts.

Speed-ups (so the dashboard and budget checks do not rescan every call):
- Hourly roll-up table keyed by (hour, task, model, endpoint, prompt_version, status).
  Each new call is an O(1) upsert into its key; queries read a few hundred
  roll-up rows instead of every raw call.
- Latency histogram with log-scale bins (10% wide). Percentiles come from one
  cumulative walk over the bins - no sorting of raw latencies.
- Raw calls are kept only for the "recent calls" table and for rebuilding roll-ups.
Days, hours and months are local-time buckets.
"""
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    request_id TEXT,
    task TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    requested_model TEXT,
    prompt_version TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    est_input_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    ttft_ms REAL,
    context_pct REAL DEFAULT 0,
    downgraded INTEGER DEFAULT 0,
    status TEXT NOT NULL,          -- ok | error | blocked
    note TEXT
);
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
CREATE TABLE IF NOT EXISTS rollup_hour (
    hour_ts REAL NOT NULL, hour TEXT NOT NULL, day TEXT NOT NULL,
    task TEXT NOT NULL, model TEXT NOT NULL, endpoint TEXT NOT NULL, prompt_version TEXT NOT NULL,
    status TEXT NOT NULL,
    calls INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0, est_input_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0, latency_sum REAL DEFAULT 0, ttft_sum REAL DEFAULT 0, ttft_n INTEGER DEFAULT 0,
    downgraded INTEGER DEFAULT 0, context_max REAL DEFAULT 0,
    PRIMARY KEY (hour_ts, task, model, endpoint, prompt_version, status)
);
CREATE INDEX IF NOT EXISTS rollup_task ON rollup_hour(task, hour_ts);
CREATE TABLE IF NOT EXISTS latency_hist (
    hour_ts REAL NOT NULL, task TEXT NOT NULL, model TEXT NOT NULL, bin INTEGER NOT NULL, n INTEGER DEFAULT 0,
    PRIMARY KEY (hour_ts, task, model, bin)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    task TEXT NOT NULL,
    level TEXT NOT NULL,           -- warning | critical
    kind TEXT NOT NULL,            -- usd | tokens
    message TEXT NOT NULL,
    UNIQUE(day, task, level, kind)
);
"""

CALL_FIELDS = ("ts", "request_id", "task", "endpoint", "model", "requested_model", "prompt_version",
               "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
               "est_input_tokens", "cost_usd", "latency_ms", "ttft_ms", "context_pct", "downgraded", "status", "note")
SUM_FIELDS = ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
              "est_input_tokens", "cost_usd", "latency_sum", "ttft_sum", "ttft_n", "downgraded")
GROUPABLE = {"task", "model", "endpoint", "prompt_version", "status"}
TOKEN_SUM = "input_tokens + output_tokens + cache_read_tokens + cache_write_tokens"
BIN_BASE = 1.1  # latency histogram bins are 10% wide

_UPSERT_ROLLUP = f"""
INSERT INTO rollup_hour (hour_ts, hour, day, task, model, endpoint, prompt_version, status, {','.join(SUM_FIELDS)}, context_max)
VALUES ({','.join('?' * (8 + len(SUM_FIELDS) + 1))})
ON CONFLICT(hour_ts, task, model, endpoint, prompt_version, status) DO UPDATE SET
    {', '.join(f'{f} = {f} + excluded.{f}' for f in SUM_FIELDS)},
    context_max = MAX(context_max, excluded.context_max)
"""
_UPSERT_HIST = """
INSERT INTO latency_hist (hour_ts, task, model, bin, n) VALUES (?,?,?,?,1)
ON CONFLICT(hour_ts, task, model, bin) DO UPDATE SET n = n + 1
"""


def day_start(ts: float | None = None) -> float:
    d = datetime.fromtimestamp(ts or time.time())
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def month_start(ts: float | None = None) -> float:
    d = datetime.fromtimestamp(ts or time.time())
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def latency_bin(ms: float) -> int:
    return int(math.floor(math.log(max(ms, 1.0), BIN_BASE)))


def bin_value(b: int) -> float:
    """Geometric middle of a bin: at most ~5% from any latency in it."""
    return BIN_BASE ** (b + 0.5)


def percentiles_from_hist(counts: dict[int, int], ps=(50, 95, 99)) -> dict[int, float]:
    """One cumulative walk over sorted bins (B bins, B << calls)."""
    total = sum(counts.values())
    out = {p: 0.0 for p in ps}
    if not total:
        return out
    targets = sorted((math.ceil(p / 100 * total), p) for p in ps)
    seen, i = 0, 0
    for b in sorted(counts):
        seen += counts[b]
        while i < len(targets) and seen >= targets[i][0]:
            out[targets[i][1]] = round(bin_value(b), 1)
            i += 1
    return out


class UsageStore:
    def __init__(self, path: str | Path = "data/usage.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        with self._conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(calls)")}
            for col, decl in (("ttft_ms", "REAL"), ("downgraded", "INTEGER DEFAULT 0")):
                if cols and col not in cols:  # databases created before these columns existed
                    c.execute(f"ALTER TABLE calls ADD COLUMN {col} {decl}")
            c.executescript(SCHEMA)
            if not c.execute("SELECT 1 FROM rollup_hour LIMIT 1").fetchone() and \
                    c.execute("SELECT 1 FROM calls LIMIT 1").fetchone():
                self._rebuild(c)

    @contextmanager
    def _conn(self):
        conn = self._mem or sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            if self._mem is None:
                conn.close()

    # --- writes ---------------------------------------------------------
    @staticmethod
    def _apply(c, row: dict) -> None:
        """Add one call to its roll-up key and latency bin."""
        hour_dt = datetime.fromtimestamp(row["ts"]).replace(minute=0, second=0, microsecond=0)
        hour_ts = hour_dt.timestamp()
        ttft = row.get("ttft_ms")
        sums = {"calls": 1, "latency_sum": row.get("latency_ms") or 0,
                "ttft_sum": ttft or 0, "ttft_n": 1 if ttft else 0, "downgraded": 1 if row.get("downgraded") else 0}
        vals = [sums.get(f, row.get(f) or 0) for f in SUM_FIELDS]
        c.execute(_UPSERT_ROLLUP, [hour_ts, hour_dt.strftime("%Y-%m-%d %H:00"), hour_dt.strftime("%Y-%m-%d"),
                                   row["task"], row["model"], row["endpoint"], row.get("prompt_version") or "-",
                                   row["status"], *vals, row.get("context_pct") or 0])
        if row["status"] == "ok" and row.get("latency_ms"):
            c.execute(_UPSERT_HIST, (hour_ts, row["task"], row["model"], latency_bin(row["latency_ms"])))

    def record(self, **row) -> int:
        row.setdefault("ts", time.time())
        values = [row.get(f) for f in CALL_FIELDS]
        with self._conn() as c:
            cur = c.execute(f"INSERT INTO calls ({','.join(CALL_FIELDS)}) VALUES ({','.join('?' * len(CALL_FIELDS))})", values)
            self._apply(c, row)
            return cur.lastrowid

    def _rebuild(self, c) -> None:
        c.execute("DELETE FROM rollup_hour")
        c.execute("DELETE FROM latency_hist")
        for r in c.execute(f"SELECT {','.join(CALL_FIELDS)} FROM calls").fetchall():
            self._apply(c, dict(r))

    def rebuild_rollups(self) -> None:
        with self._conn() as c:
            self._rebuild(c)

    def add_alert(self, task: str, level: str, kind: str, message: str) -> bool:
        """Insert an alert once per day/task/level/kind. Returns True if it is new."""
        day = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO alerts (ts, day, task, level, kind, message) VALUES (?,?,?,?,?,?)",
                            (time.time(), day, task, level, kind, message))
            return cur.rowcount == 1

    def get_setting(self, key: str, default=None):
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))

    # --- reads (all from roll-ups) ---------------------------------------
    @staticmethod
    def _where(since, until=None, task=None, model=None):
        """Filter on the hour bucket. `since` is floored to its hour so the current hour is included."""
        clauses, args = [], []
        if since is not None:
            clauses.append("hour_ts >= ?")
            args.append(datetime.fromtimestamp(since).replace(minute=0, second=0, microsecond=0).timestamp())
        if until is not None:
            clauses.append("hour_ts < ?"); args.append(until)
        if task:
            clauses.append("task = ?"); args.append(task)
        if model:
            clauses.append("model = ?"); args.append(model)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def totals(self, since=None, until=None, task=None, model=None) -> dict:
        where, args = self._where(since, until, task, model)
        sums = ", ".join(f"COALESCE(SUM({f}),0) AS {f}" for f in SUM_FIELDS)
        q = f"""SELECT {sums},
                    COALESCE(SUM(CASE WHEN status='ok' THEN calls END),0) AS ok,
                    COALESCE(SUM(CASE WHEN status='error' THEN calls END),0) AS errors,
                    COALESCE(SUM(CASE WHEN status='blocked' THEN calls END),0) AS blocked,
                    COALESCE(SUM(CASE WHEN status='ok' THEN latency_sum END),0) AS ok_latency_sum,
                    COALESCE(MAX(context_max),0) AS max_context_pct
                FROM rollup_hour{where}"""
        with self._conn() as c:
            t = dict(c.execute(q, args).fetchone())
            hist = {r[0]: r[1] for r in c.execute(f"SELECT bin, SUM(n) FROM latency_hist{where} GROUP BY bin", args)}
        t["total_tokens"] = t["input_tokens"] + t["output_tokens"] + t["cache_read_tokens"] + t["cache_write_tokens"]
        prompt = t["input_tokens"] + t["cache_read_tokens"] + t["cache_write_tokens"]
        t["prompt_tokens"] = prompt
        t["cache_hit_pct"] = round(100 * t["cache_read_tokens"] / prompt, 1) if prompt else 0.0
        t["avg_cost_per_call"] = t["cost_usd"] / t["ok"] if t["ok"] else 0.0
        t["avg_tokens_per_call"] = t["total_tokens"] / t["ok"] if t["ok"] else 0.0
        t["cost_per_1k_tokens"] = 1000 * t["cost_usd"] / t["total_tokens"] if t["total_tokens"] else 0.0
        t["error_rate_pct"] = round(100 * t["errors"] / t["calls"], 2) if t["calls"] else 0.0
        t["downgrade_pct"] = round(100 * t["downgraded"] / t["ok"], 1) if t["ok"] else 0.0
        t["avg_ttft_ms"] = t["ttft_sum"] / t["ttft_n"] if t["ttft_n"] else None
        t["output_tokens_per_sec"] = 1000 * t["output_tokens"] / t["ok_latency_sum"] if t["ok_latency_sum"] else 0.0
        t["estimate_ratio"] = round(t["est_input_tokens"] / prompt, 2) if prompt else None
        pct = percentiles_from_hist(hist)
        t["p50_latency_ms"], t["p95_latency_ms"], t["p99_latency_ms"] = pct[50], pct[95], pct[99]
        return t

    def spend(self, task: str | None, since: float) -> tuple[float, int]:
        """(usd, tokens) spent since a timestamp, for one task or all tasks."""
        where, args = self._where(since, task=task)
        with self._conn() as c:
            row = c.execute(f"SELECT COALESCE(SUM(cost_usd),0), COALESCE(SUM({TOKEN_SUM}),0) FROM rollup_hour{where}",
                            args).fetchone()
        return float(row[0]), int(row[1])

    def cache_by_model(self, since: float, task=None) -> list[dict]:
        """Cache-read tokens per model, for computing what caching saved."""
        where, args = self._where(since, task=task)
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                f"SELECT model, SUM(cache_read_tokens) AS cache_read_tokens FROM rollup_hour{where} GROUP BY model", args)]

    def timeseries(self, since: float, bucket: str = "day", group: str = "kind", task=None, model=None) -> list[dict]:
        """Rows of {bucket, ...}. group='kind' returns one row per bucket with all token kinds."""
        col = {"hour": "hour", "day": "day"}[bucket]
        where, args = self._where(since, task=task, model=model)
        if group == "kind":
            q = f"""SELECT {col} AS bucket,
                        SUM(input_tokens) AS input_tokens, SUM(cache_read_tokens) AS cache_read_tokens,
                        SUM(cache_write_tokens) AS cache_write_tokens, SUM(output_tokens) AS output_tokens,
                        SUM(cost_usd) AS cost_usd, SUM(calls) AS calls
                    FROM rollup_hour{where} GROUP BY bucket ORDER BY bucket"""
        else:
            if group not in GROUPABLE:
                raise ValueError(f"cannot group by {group}")
            q = f"""SELECT {col} AS bucket, {group} AS grp,
                        SUM({TOKEN_SUM}) AS tokens, SUM(cost_usd) AS cost_usd, SUM(calls) AS calls
                    FROM rollup_hour{where} GROUP BY bucket, grp ORDER BY bucket, grp"""
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def breakdown(self, by: str, since: float, task=None) -> list[dict]:
        if by not in GROUPABLE:
            raise ValueError(f"cannot group by {by}")
        where, args = self._where(since, task=task)
        q = f"""SELECT {by} AS name, SUM(calls) AS calls, SUM({TOKEN_SUM}) AS tokens,
                    SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                    SUM(cache_read_tokens) AS cache_read_tokens, SUM(cache_write_tokens) AS cache_write_tokens,
                    SUM(cost_usd) AS cost_usd, SUM(est_input_tokens) AS est_input_tokens,
                    1.0 * SUM({TOKEN_SUM}) / SUM(calls) AS avg_tokens, SUM(latency_sum) / SUM(calls) AS avg_latency_ms
                FROM rollup_hour{where} GROUP BY {by} ORDER BY cost_usd DESC, tokens DESC"""
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def recent(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM calls ORDER BY ts DESC LIMIT ?", (limit,))]

    def alerts(self, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))]

    def tasks(self) -> list[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT task FROM rollup_hour ORDER BY task")]
