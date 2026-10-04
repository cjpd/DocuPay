"""SQLite state. Code owns all state; models never write here directly."""
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, ticket INTEGER, src TEXT NOT NULL, dst TEXT NOT NULL,
  edge_type TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, ticket INTEGER, node TEXT NOT NULL, name TEXT NOT NULL,
  decision TEXT NOT NULL, source TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS opus_calls (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, ticket INTEGER, node TEXT NOT NULL, purpose TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0, duration_ms REAL, ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
  number INTEGER PRIMARY KEY, title TEXT, state TEXT NOT NULL, node TEXT, data TEXT NOT NULL DEFAULT '{}',
  plan_attempts INTEGER NOT NULL DEFAULT 0, build_rounds INTEGER NOT NULL DEFAULT 0,
  review_rounds INTEGER NOT NULL DEFAULT 0, opus_calls INTEGER NOT NULL DEFAULT 0,
  usd REAL NOT NULL DEFAULT 0, started_at REAL, closed_at REAL, result TEXT,
  labeled_by TEXT, labeled_at REAL, branch TEXT
);
CREATE TABLE IF NOT EXISTS gates (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, gate TEXT NOT NULL, ticket INTEGER, summary TEXT NOT NULL,
  payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', decided_at REAL, decided_by TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)

    # -- low level ---------------------------------------------------------------
    def exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, args)

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.exec(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        r = self.exec(sql, args).fetchone()
        return dict(r) if r else None

    # -- events ------------------------------------------------------------------
    def edge(self, ticket: Optional[int], src: str, dst: str, payload: Any, kind: str = "forward") -> int:
        edge_type = type(payload).__name__
        body = payload.model_dump_json() if hasattr(payload, "model_dump_json") else json.dumps(payload)
        return self.exec(
            "INSERT INTO events (ts, ticket, src, dst, edge_type, kind, payload) VALUES (?,?,?,?,?,?,?)",
            (time.time(), ticket, src, dst, edge_type, kind, body),
        ).lastrowid

    # -- decisions -----------------------------------------------------------------
    def decision(self, *, ticket, node, name, decision, source, detail="") -> int:
        return self.exec(
            "INSERT INTO decisions (ts, ticket, node, name, decision, source, detail) VALUES (?,?,?,?,?,?,?)",
            (time.time(), ticket, node, name, json.dumps(decision), source, detail),
        ).lastrowid

    # -- tickets -----------------------------------------------------------------
    def ticket(self, number: int) -> Optional[dict]:
        t = self.one("SELECT * FROM tickets WHERE number=?", (number,))
        if t:
            t["data"] = json.loads(t["data"])
        return t

    def upsert_ticket(self, number: int, **fields) -> None:
        if "data" in fields and not isinstance(fields["data"], str):
            fields["data"] = json.dumps(fields["data"])
        if not self.one("SELECT number FROM tickets WHERE number=?", (number,)):
            self.exec("INSERT INTO tickets (number, state, started_at) VALUES (?, 'new', ?)", (number, time.time()))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            self.exec(f"UPDATE tickets SET {sets} WHERE number=?", (*fields.values(), number))

    def bump(self, number: int, column: str, by: float = 1) -> float:
        if column not in {"plan_attempts", "build_rounds", "review_rounds", "opus_calls", "usd"}:
            raise ValueError(column)
        self.exec(f"UPDATE tickets SET {column} = {column} + ? WHERE number=?", (by, number))
        return self.one(f"SELECT {column} AS v FROM tickets WHERE number=?", (number,))["v"]

    # -- opus --------------------------------------------------------------------
    def opus_call(self, ticket, node, purpose, cost_usd, duration_ms, ok) -> None:
        self.exec("INSERT INTO opus_calls (ts, ticket, node, purpose, cost_usd, duration_ms, ok) VALUES (?,?,?,?,?,?,?)",
                  (time.time(), ticket, node, purpose, cost_usd, duration_ms, int(ok)))

    def usd_since(self, since: float) -> float:
        return self.one("SELECT COALESCE(SUM(cost_usd),0) AS v FROM opus_calls WHERE ts>=?", (since,))["v"]

    # -- gates -------------------------------------------------------------------
    def open_gate(self, gate: str, ticket: Optional[int], summary: str, payload: Any) -> int:
        body = payload.model_dump_json() if hasattr(payload, "model_dump_json") else json.dumps(payload)
        return self.exec("INSERT INTO gates (ts, gate, ticket, summary, payload) VALUES (?,?,?,?,?)",
                         (time.time(), gate, ticket, summary, body)).lastrowid

    def decide_gate(self, gate_id: int, approved: bool, by: str) -> bool:
        cur = self.exec("UPDATE gates SET status=?, decided_at=?, decided_by=? WHERE id=? AND status='pending'",
                        ("approved" if approved else "rejected", time.time(), by, gate_id))
        return cur.rowcount == 1

    # -- meta ----------------------------------------------------------------------
    def meta(self, key: str) -> Optional[str]:
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else None

    def set_meta(self, key: str, value: Optional[str]) -> None:
        if value is None:
            self.exec("DELETE FROM meta WHERE key=?", (key,))
        else:
            self.exec("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, value))
