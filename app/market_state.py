"""Persistent conservative API accounting and daily history cache, without secrets."""
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

DAILY_LIMIT = 760  # Reserve 40 credits for activity outside this installation.
INTERVAL_SECONDS = 300


class BudgetExceeded(Exception):
    pass


class MarketState:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self.connect()
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS requests_at ON requests(at);
                CREATE TABLE IF NOT EXISTS history (symbol TEXT PRIMARY KEY, day TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS slots (slot INTEGER PRIMARY KEY);
            """)
        finally:
            db.close()

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    def reserve(self, now: float, spacing: float = 8) -> float:
        """Reserve BEFORE sending (failed requests count). Return wait without reservation."""
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            start = datetime.fromtimestamp(now, UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            count = db.execute("SELECT count(*) FROM requests WHERE at>=?", (start,)).fetchone()[0]
            if count >= DAILY_LIMIT:
                raise BudgetExceeded("Local API budget reached (760/day UTC)")
            recent = [r[0] for r in db.execute("SELECT at FROM requests WHERE at>? ORDER BY at", (now-60,))]
            last = db.execute("SELECT max(at) FROM requests").fetchone()[0]
            delay = max(0, (last + max(8, spacing) - now) if last is not None else 0)
            if len(recent) >= 8:
                delay = max(delay, recent[-8] + 60.01 - now)
            if not delay:
                db.execute("INSERT INTO requests VALUES (?)", (now,))
            db.commit()
            return delay
        finally:
            db.close()

    def acquire(self, spacing: float, allowed=lambda: True):
        while True:
            if not allowed():
                raise BudgetExceeded("Outside regular market session; no request sent")
            delay = self.reserve(time.time(), spacing)
            if not delay:
                return
            time.sleep(min(delay, 30))

    def history(self, symbol: str, day: str):
        db = self.connect()
        try:
            row = db.execute("SELECT payload FROM history WHERE symbol=? AND day=?", (symbol, day)).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            db.close()

    def save_history(self, symbol: str, day: str, payload: dict):
        db = self.connect()
        try:
            with db:
                db.execute("INSERT OR REPLACE INTO history VALUES (?,?,?)", (symbol, day, json.dumps(payload)))
        finally:
            db.close()

    def claim_slot(self, now: datetime) -> bool:
        db = self.connect()
        try:
            with db:
                return db.execute("INSERT OR IGNORE INTO slots VALUES (?)", (int(now.timestamp()) // INTERVAL_SECONDS,)).rowcount == 1
        finally:
            db.close()
