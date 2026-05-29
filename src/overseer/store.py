"""store — SQLite persistence for the roster + state transitions.

Decoupled by design: the daemon writes; the CLI / web read. Either can die
without taking the other down (last-known state stays readable).
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB = Path(os.environ.get("OVERSEER_DB", str(Path.home() / "overseer" / "data" / "overseer.db")))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    session_id    TEXT PRIMARY KEY,
    pid           INTEGER,
    name          TEXT,
    cwd           TEXT,
    domain        TEXT,
    title         TEXT,
    raw_status    TEXT,
    derived_state TEXT,
    last_event    TEXT,
    last_mtime    REAL,
    started_at    REAL,
    restart_policy TEXT DEFAULT 'manual',
    first_seen    REAL,
    updated_at    REAL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts         REAL,
    kind       TEXT,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts);
"""


class Store:
    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        # WAL so the daemon's writes don't block the CLI / web / GUI readers (this
        # tool is multi-process by design — see the module docstring). In the default
        # rollback journal a writer takes an exclusive lock that stalls every reader,
        # which under a 24/7 poll loop surfaces as intermittent 'database is locked'.
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass        # e.g. :memory: or a FS without WAL — fall back to default
        self.conn.executescript(_SCHEMA)
        # migrate DBs created before a column existed (CREATE TABLE IF NOT EXISTS
        # is a no-op on an existing table, so new columns must be added explicitly).
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(leads)").fetchall()}
        if "title" not in cols:
            self.conn.execute("ALTER TABLE leads ADD COLUMN title TEXT")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- writes ----
    def upsert_lead(self, row: dict[str, Any]) -> str | None:
        """Insert/update a lead. Returns the previous derived_state (for transition detection)."""
        now = time.time()
        cur = self.conn.execute(
            "SELECT derived_state, first_seen FROM leads WHERE session_id = ?",
            (row["session_id"],),
        )
        existing = cur.fetchone()
        prev_state = existing["derived_state"] if existing else None
        first_seen = existing["first_seen"] if existing else now
        self.conn.execute(
            """
            INSERT INTO leads (session_id, pid, name, cwd, domain, title, raw_status,
                               derived_state, last_event, last_mtime, started_at,
                               restart_policy, first_seen, updated_at)
            VALUES (:session_id, :pid, :name, :cwd, :domain, :title, :raw_status,
                    :derived_state, :last_event, :last_mtime, :started_at,
                    COALESCE((SELECT restart_policy FROM leads WHERE session_id = :session_id), 'manual'),
                    :first_seen, :updated_at)
            ON CONFLICT(session_id) DO UPDATE SET
                pid=excluded.pid, name=excluded.name, cwd=excluded.cwd,
                domain=excluded.domain, title=excluded.title, raw_status=excluded.raw_status,
                derived_state=excluded.derived_state, last_event=excluded.last_event,
                last_mtime=excluded.last_mtime, started_at=excluded.started_at,
                updated_at=excluded.updated_at
            """,
            {**row, "title": row.get("title"), "first_seen": first_seen, "updated_at": now},
        )
        self.conn.commit()
        return prev_state

    def log_event(self, session_id: str, kind: str, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (session_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
            (session_id, time.time(), kind, detail),
        )
        self.conn.commit()

    def mark_gone(self, present_ids: Iterable[str]) -> list[str]:
        """Any lead in DB not in present_ids is no longer live. Returns those ids."""
        present = set(present_ids)
        rows = self.conn.execute(
            "SELECT session_id FROM leads WHERE derived_state != 'gone'"
        ).fetchall()
        gone = [r["session_id"] for r in rows if r["session_id"] not in present]
        for sid in gone:
            self.conn.execute(
                "UPDATE leads SET derived_state='gone', updated_at=? WHERE session_id=?",
                (time.time(), sid),
            )
        if gone:
            self.conn.commit()
        return gone

    # ---- reads ----
    def all_leads(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM leads ORDER BY updated_at DESC"
        ).fetchall()

    def recent_events(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
