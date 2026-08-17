"""SQLite-backed memory: tasks, notes, and the command log.

Timestamps are stored as ISO-8601 strings without a timezone and interpreted as
local time throughout. Jarvis runs on one machine for one person; carrying
timezone machinery would buy nothing and complicate every comparison.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = os.path.expanduser("~/.jarvis/jarvis.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    domain     TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    due_at     TEXT,
    status     TEXT NOT NULL DEFAULT 'open',
    ref        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    domain     TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS command_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    raw       TEXT NOT NULL,
    corrected TEXT NOT NULL,
    intent    TEXT NOT NULL DEFAULT '',
    tier      INTEGER NOT NULL DEFAULT 1,
    ok        INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS seen (
    kind TEXT NOT NULL,
    key  TEXT NOT NULL,
    ts   TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS watch_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks (status, due_at);
"""

_FMT = "%Y-%m-%dT%H:%M:%S"


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime(_FMT) if dt else None


def _dt(raw: str | None) -> datetime | None:
    return datetime.strptime(raw, _FMT) if raw else None


@dataclass
class Task:
    id: int
    domain: str
    text: str
    created_at: datetime
    due_at: datetime | None
    status: str
    ref: str


@dataclass
class Note:
    id: int
    domain: str
    text: str
    created_at: datetime


@dataclass
class Command:
    id: int
    ts: datetime
    raw: str
    corrected: str
    intent: str
    tier: int
    ok: bool


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or DEFAULT_PATH
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- tasks -------------------------------------------------------

    def add_task(
        self,
        text: str,
        domain: str,
        due_at: datetime | None = None,
        ref: str = "",
        now: datetime | None = None,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO tasks (domain, text, created_at, due_at, ref) VALUES (?,?,?,?,?)",
                (domain, text, _iso(now or datetime.now()), _iso(due_at), ref),
            )
            return int(cur.lastrowid)

    def list_tasks(self, domain: str | None = None, include_done: bool = False) -> list[Task]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args: list = []
        if domain:
            sql += " AND domain = ?"
            args.append(domain)
        if not include_done:
            sql += " AND status = 'open'"
        sql += " ORDER BY (due_at IS NULL), due_at, id"
        with self._conn() as c:
            return [self._task(r) for r in c.execute(sql, args)]

    def complete_task(self, task_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE tasks SET status='done' WHERE id = ?", (task_id,))

    def due_tasks(self, now: datetime | None = None) -> list[Task]:
        cutoff = _iso(now or datetime.now())
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM tasks WHERE status='open' AND due_at IS NOT NULL "
                "AND due_at <= ? ORDER BY due_at",
                (cutoff,),
            )
            return [self._task(r) for r in rows]

    @staticmethod
    def _task(r: sqlite3.Row) -> Task:
        return Task(
            id=r["id"], domain=r["domain"], text=r["text"],
            created_at=_dt(r["created_at"]), due_at=_dt(r["due_at"]),
            status=r["status"], ref=r["ref"],
        )

    # ---- notes -------------------------------------------------------

    def add_note(self, text: str, domain: str, now: datetime | None = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO notes (domain, text, created_at) VALUES (?,?,?)",
                (domain, text, _iso(now or datetime.now())),
            )
            return int(cur.lastrowid)

    def list_notes(self, domain: str | None = None, limit: int = 20) -> list[Note]:
        sql = "SELECT * FROM notes"
        args: list = []
        if domain:
            sql += " WHERE domain = ?"
            args.append(domain)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [
                Note(id=r["id"], domain=r["domain"], text=r["text"],
                     created_at=_dt(r["created_at"]))
                for r in c.execute(sql, args)
            ]

    # ---- command log -------------------------------------------------

    def log_command(
        self, raw: str, corrected: str, intent: str, tier: int, ok: bool,
        now: datetime | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO command_log (ts, raw, corrected, intent, tier, ok) VALUES (?,?,?,?,?,?)",
                (_iso(now or datetime.now()), raw, corrected, intent, tier, int(ok)),
            )

    def recent_commands(self, limit: int = 50) -> list[Command]:
        with self._conn() as c:
            return [
                Command(id=r["id"], ts=_dt(r["ts"]), raw=r["raw"], corrected=r["corrected"],
                        intent=r["intent"], tier=r["tier"], ok=bool(r["ok"]))
                for r in c.execute("SELECT * FROM command_log ORDER BY id DESC LIMIT ?", (limit,))
            ]

    def tier3_counts(self, limit: int = 10) -> list[tuple[str, int]]:
        """Which commands fall through to tier 3 most often.

        This is the evidence for which intents to add next: a slow path you hit
        daily is worth ten lines of regex; one you hit twice a year is not.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT corrected, COUNT(*) n FROM command_log WHERE tier = 3 "
                "GROUP BY corrected ORDER BY n DESC, corrected LIMIT ?",
                (limit,),
            )
            return [(r["corrected"], r["n"]) for r in rows]

    # ---- watcher bookkeeping -----------------------------------------

    def has_seen(self, kind: str, key: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM seen WHERE kind = ? AND key = ?", (kind, key)
            ).fetchone()
            return row is not None

    def mark_seen(self, kind: str, key: str, now: datetime | None = None) -> None:
        """Idempotent. Without this table every restart would replay the last
        week of news at you."""
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO seen (kind, key, ts) VALUES (?,?,?)",
                (kind, key, _iso(now or datetime.now())),
            )

    def unseen(self, kind: str, keys: list[str]) -> list[str]:
        """Filter keys down to the ones not announced before, order preserved."""
        return [k for k in keys if not self.has_seen(kind, k)]

    def get_state(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            row = c.execute("SELECT value FROM watch_state WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO watch_state (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def dm_recipients(self, limit: int = 10) -> list[str]:
        """People Jarvis has DM'd, most recent first.

        The reply watcher polls only these — there is no point reading
        conversations Jarvis never started.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT key FROM seen WHERE kind = 'dm_sent' ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            return [r["key"] for r in rows]
