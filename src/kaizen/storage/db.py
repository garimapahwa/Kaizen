"""SQLite workspace database: schema and connection. Single file, no server, no network."""

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    canonical TEXT NOT NULL,
    aliases TEXT NOT NULL,
    scope TEXT NOT NULL,
    doc_types TEXT NOT NULL,
    item_anchors TEXT NOT NULL,
    provenance TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL,
    notes TEXT NOT NULL,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS relationship_versions (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    change_type TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    change_note TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    input_root TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    terminology_version TEXT NOT NULL,
    json_path TEXT NOT NULL,
    summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_relationships (
    run_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    used_count INTEGER NOT NULL,
    PRIMARY KEY (run_id, relationship_id)
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    reviewer_slot INTEGER NOT NULL,
    reviewer_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    blind INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, row_id, reviewer_slot)
);
CREATE TABLE IF NOT EXISTS action_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    check_type TEXT NOT NULL,
    discrepancy_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_in_run TEXT NOT NULL DEFAULT '',
    comparison_key TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS finals (
    run_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    final_decision TEXT NOT NULL,
    finalized_by TEXT NOT NULL,
    finalized_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, row_id)
);
CREATE TABLE IF NOT EXISTS mining_rejections (
    pair_key TEXT PRIMARY KEY,
    rejected_by TEXT NOT NULL,
    rejected_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS row_views (
    run_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    reviewer_slot INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    PRIMARY KEY (run_id, row_id, reviewer_slot)
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    reviewer TEXT NOT NULL,
    slot INTEGER NOT NULL,
    blind INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ended_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS otp_codes (
    email TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS otp_requests (
    email TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS otp_requests_email ON otp_requests (email, created_at);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class _Rows:
    """A fully materialised result set. Rows are fetched while the lock is held, so callers can iterate or
    fetch after the lock is released without ever stepping a cursor concurrently with another thread."""

    def __init__(self, rows: list, lastrowid: int | None, rowcount: int):
        self._rows = rows
        self._pos = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size: int = 1) -> list:
        out = self._rows[self._pos : self._pos + size]
        self._pos += len(out)
        return out

    def fetchall(self) -> list:
        out = self._rows[self._pos :]
        self._pos = len(self._rows)
        return out

    def __iter__(self):
        return iter(self.fetchall())


class LockedConnection:
    """The one sqlite3 connection, shared by every API thread. Every statement runs under the re-entrant
    lock and returns materialised rows: SQLite connections are not safe to use from two threads at once
    (`sqlite3.InterfaceError: bad parameter or other API misuse`), and the API thread pool does exactly that."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    def execute(self, sql: str, params=()) -> _Rows:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            return _Rows(rows, cur.lastrowid, cur.rowcount)

    def executemany(self, sql: str, seq) -> _Rows:
        with self._lock:
            cur = self._conn.executemany(sql, seq)
            return _Rows([], cur.lastrowid, cur.rowcount)

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self._conn.row_factory = value

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(self.path, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self.lock = threading.RLock()  # the API serves requests from a thread pool; every statement is serialised
        self.conn = LockedConnection(raw, self.lock)
        self.conn.executescript(SCHEMA)
        self._ensure_column("decisions", "override_classification", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("action_items", "resolved_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("decisions", "seconds_spent", "REAL")  # NULL = not timed
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def audit(self, actor: str, action: str, detail: str) -> None:
        from datetime import datetime, timezone

        self.conn.execute("INSERT INTO audit (at, actor, action, detail) VALUES (?,?,?,?)", (datetime.now(timezone.utc).isoformat(), actor, action, detail))

    def close(self) -> None:
        self.conn.close()
