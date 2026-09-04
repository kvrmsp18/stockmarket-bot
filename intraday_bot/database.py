from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


class Database:
    """Small SQLite repository used by the paper-trading runtime.

    The runtime has more than one writer path (cycle persistence, execution
    events and EOD reconciliation). Connections therefore use autocommit plus
    SQLite's busy timeout. This prevents a long-lived implicit transaction from
    holding the database write lock while another repository method records an
    event. Multi-statement operations that need atomicity should use an
    explicit ``BEGIN``/``COMMIT`` on the same connection.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or settings.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
            isolation_level=None,
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    component TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    mode TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    signal_id TEXT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    state TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    stop REAL NOT NULL,
                    target REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    pnl REAL NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    signal_id TEXT,
                    symbol TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    gross_pnl REAL NOT NULL DEFAULT 0,
                    charges REAL NOT NULL DEFAULT 0,
                    net_pnl REAL NOT NULL DEFAULT 0,
                    exit_reason TEXT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cycles (
                    cycle_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    def event(
        self,
        component: str,
        severity: str,
        event_type: str,
        payload: dict[str, Any],
        symbol: str | None = None,
        mode: str = "PAPER",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(payload, default=str)
        with self._lock, self.connect() as con:
            con.execute(
                "INSERT INTO events(ts,component,severity,event_type,symbol,mode,payload) VALUES(?,?,?,?,?,?,?)",
                (now, component, severity, event_type, symbol, mode, encoded),
            )

    def signal(self, signal_id: str, symbol: str, decision: str, payload: dict[str, Any]) -> None:
        with self._lock, self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO signals(signal_id,ts,symbol,decision,payload) VALUES(?,?,?,?,?)",
                (
                    signal_id,
                    datetime.now(timezone.utc).isoformat(),
                    symbol,
                    decision,
                    json.dumps(payload, default=str),
                ),
            )

    def recent(self, table: str, limit: int = 100) -> list[dict[str, Any]]:
        allowed = {"events", "signals", "orders", "positions", "trades", "cycles"}
        if table not in allowed:
            raise ValueError("invalid table")
        with self.connect() as con:
            rows = con.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        with self.connect() as con:
            row = con.execute(sql, params).fetchone()
            return row[0] if row else None
