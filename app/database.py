"""SQLite event ledger and durable notification outbox."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from app.fetcher import Snapshot
from app.rules import Event


class AlreadyRunning(Exception):
    pass


@contextmanager
def run_lock(path: Path):
    """OS releases the lock on process exit, including crashes (Windows/Linux)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise AlreadyRunning("Another check is already running") from None
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class Database:
    def __init__(self, path: Path, mode: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS prices (
                symbol TEXT, day TEXT, source TEXT, close REAL NOT NULL,
                PRIMARY KEY (symbol, day, source));
            CREATE TABLE IF NOT EXISTS quotes (
                symbol TEXT, as_of TEXT, source TEXT, price REAL, previous_close REAL,
                PRIMARY KEY (symbol, as_of, source));
            CREATE TABLE IF NOT EXISTS intraday (
                symbol TEXT, at TEXT, source TEXT, open REAL, high REAL, low REAL, close REAL,
                PRIMARY KEY(symbol,at,source));
            CREATE TABLE IF NOT EXISTS ai_calls (
                notification_id TEXT PRIMARY KEY, day TEXT NOT NULL, symbol TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
                status TEXT, checked INTEGER DEFAULT 0, sent INTEGER DEFAULT 0, errors INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, channel TEXT NOT NULL,
                recipient TEXT NOT NULL, payload TEXT NOT NULL, message TEXT,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                first_attempt_at TEXT, accepted_at TEXT, last_error TEXT);
            CREATE TABLE IF NOT EXISTS events (
                event_key TEXT PRIMARY KEY, symbol TEXT NOT NULL, rule TEXT NOT NULL,
                event_date TEXT NOT NULL, notification_id TEXT NOT NULL REFERENCES notifications(id));
            CREATE INDEX IF NOT EXISTS events_symbol_rule ON events(symbol, rule);
            CREATE TABLE IF NOT EXISTS news_items (
                source_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, published_at TEXT NOT NULL,
                title TEXT NOT NULL, excerpt TEXT NOT NULL, importance TEXT NOT NULL, reason TEXT NOT NULL,
                source_name TEXT NOT NULL, source_url TEXT, fetched_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS news_items_published ON news_items(published_at DESC);
            CREATE TABLE IF NOT EXISTS portfolio_reviews (
                period TEXT PRIMARY KEY, created_at TEXT NOT NULL, message TEXT NOT NULL,
                source_count INTEGER NOT NULL, ai_used INTEGER NOT NULL);
        """)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('mode', ?)", (mode,))
        stored = self.connection.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()[0]
        if stored != mode:
            self.connection.close()
            raise ValueError("Database mode mismatch: mock and live must use separate files")
        # Additive migration: keep existing prices, alerts and cooldown history.
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(prices)")}
        with self.connection:
            for name in ("open", "high", "low"):
                if name not in columns:
                    self.connection.execute(f'ALTER TABLE prices ADD COLUMN "{name}" REAL')
            quote_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(quotes)")}
            if "price_kind" not in quote_columns:
                self.connection.execute("ALTER TABLE quotes ADD COLUMN price_kind TEXT DEFAULT 'quote'")

    def close(self) -> None:
        self.connection.close()

    def save_snapshot(self, snapshot: Snapshot) -> None:
        self.save_bars(snapshot.symbol, snapshot.source, snapshot.bars)
        self.save_intraday(snapshot.symbol, snapshot.source, snapshot.intraday)
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO quotes (symbol,as_of,source,price,previous_close,price_kind) VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot.symbol, snapshot.as_of.isoformat(), snapshot.source, snapshot.price, snapshot.previous_close, snapshot.price_kind))

    def save_intraday(self, symbol, source, bars):
        with self.connection:
            self.connection.executemany("INSERT OR REPLACE INTO intraday VALUES (?,?,?,?,?,?,?)",
                [(symbol,b.at.isoformat(),source,b.open,b.high,b.low,b.close) for b in bars])

    def save_bars(self, symbol, source, bars):
        with self.connection:
            self.connection.executemany('INSERT OR REPLACE INTO prices (symbol,day,source,close,"open",high,low) VALUES (?, ?, ?, ?, ?, ?, ?)',
                [(symbol, bar.day.isoformat(), source, bar.close, bar.open, bar.high, bar.low) for bar in bars])

    def eligible(self, events: list[Event], now: datetime, cooldown_hours: float) -> list[Event]:
        result = []
        cutoff = (now - timedelta(hours=cooldown_hours)).isoformat()
        for event in events:
            if self.connection.execute("SELECT 1 FROM events WHERE event_key=?", (event.key,)).fetchone():
                continue  # Same daily event stays deduplicated even after cooldown expires.
            family = event.rule.split('_level')[0]
            if family in {'price_drop', 'price_rise'} and event.evidence.get('severity_level'):
                level = event.evidence['severity_level']
                previous = self.connection.execute('''SELECT e.rule,e.event_date,n.status,n.accepted_at
                    FROM events e JOIN notifications n ON e.notification_id=n.id
                    WHERE e.symbol=? AND (e.rule=? OR e.rule IN (?,?))
                    AND n.status IN ('pending','accepted')''',
                    (event.symbol, family, family+'_level2', family+'_level3')).fetchall()
                same_day = [int(r['rule'][-1]) if '_level' in r['rule'] else 1
                            for r in previous if r['event_date'] == event.event_date]
                if same_day and max(same_day) >= level:
                    continue  # A milder reading after a large jump is not new bad news.
                if same_day and level > max(same_day):
                    result.append(event)  # A higher level bypasses the same-day cooldown.
                    continue
                if any(r['status'] == 'pending' or (r['accepted_at'] and r['accepted_at'] > cutoff) for r in previous):
                    continue
            recent = self.connection.execute("""
                SELECT 1 FROM events e JOIN notifications n ON e.notification_id=n.id
                WHERE e.symbol=? AND e.rule=? AND
                  (n.status='pending' OR (n.status='accepted' AND n.accepted_at>?)) LIMIT 1
            """, (event.symbol, event.rule, cutoff)).fetchone()
            if not recent:
                result.append(event)
        return result

    def price_recovery(self, snapshot, indicators, settings):
        """Once per session after an accepted severe decline; only price recovered."""
        if not settings.alert_escalation or indicators.daily_change_pct <= -settings.price_drop_pct:
            return []
        day = snapshot.session_date.isoformat()
        previous = self.connection.execute('''SELECT e.rule,n.payload FROM events e JOIN notifications n ON n.id=e.notification_id
            WHERE e.symbol=? AND e.event_date=? AND e.rule IN ('price_drop_level2','price_drop_level3')
            AND n.status='accepted' ORDER BY e.rule DESC LIMIT 1''', (snapshot.symbol, day)).fetchone()
        if not previous:
            return []
        try:
            old_change = json.loads(previous['payload'])['indicators']['daily_change_pct']
            if indicators.daily_change_pct <= float(old_change):
                return []  # Widening a user threshold is not an actual price recovery.
        except (ValueError, TypeError, KeyError):
            return []
        return [Event(f'{snapshot.source}:{snapshot.symbol}:price_drop_recovery:{day}', snapshot.symbol,
            'price_drop_recovery', day, 'ราคาฟื้นกลับเหนือเกณฑ์เตือนรายวัน',
            dict(change_pct=indicators.daily_change_pct, threshold_pct=-settings.price_drop_pct,
                 previous_severity=int(previous['rule'][-1]), meaning='Price threshold recovered, not company-risk resolution'))]

    def reserve_ai(self, notification_id, symbol, now, daily_limit, stock_limit):
        """Reserve before calling; failed/crashed attempts count. UTC day, persistent across restarts."""
        from datetime import UTC
        day = now.astimezone(UTC).date().isoformat()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            used = self.connection.execute("SELECT count(*) FROM ai_calls WHERE day=?", (day,)).fetchone()[0]
            own = self.connection.execute("SELECT count(*) FROM ai_calls WHERE day=? AND symbol=?", (day,symbol)).fetchone()[0]
            if used >= daily_limit or own >= stock_limit:
                return False
            return self.connection.execute("INSERT OR IGNORE INTO ai_calls VALUES (?,?,?)", (notification_id,day,symbol)).rowcount == 1

    def enqueue(self, events: list[Event], payload: dict, channel: str, recipient: str, now: datetime) -> str:
        notification_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute("""INSERT INTO notifications
                (id, created_at, channel, recipient, payload) VALUES (?, ?, ?, ?, ?)""",
                (notification_id, now.isoformat(), channel, recipient, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
            self.connection.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                [(e.key, e.symbol, e.rule, e.event_date, notification_id) for e in events])
        return notification_id

    def pending(self) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM notifications WHERE status='pending' ORDER BY created_at, id").fetchall()

    def set_message(self, notification_id: str, message: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE notifications SET message=? WHERE id=? AND message IS NULL", (message, notification_id))

    def start_attempt(self, notification_id: str, now: datetime) -> None:
        with self.connection:
            self.connection.execute("""UPDATE notifications SET attempts=attempts+1,
                first_attempt_at=COALESCE(first_attempt_at, ?) WHERE id=?""", (now.isoformat(), notification_id))

    def finish(self, notification_id: str, status: str, now: datetime, error: str | None = None) -> None:
        if status not in {"pending", "accepted", "expired", "failed"}:
            raise ValueError("Unknown notification status")
        with self.connection:
            self.connection.execute("""UPDATE notifications SET status=?, accepted_at=?, last_error=? WHERE id=?""",
                (status, now.isoformat() if status == "accepted" else None, error, notification_id))

    def start_run(self, now: datetime) -> int:
        with self.connection:
            cursor = self.connection.execute("INSERT INTO runs (started_at,status) VALUES (?, 'running')", (now.isoformat(),))
        return cursor.lastrowid

    def finish_run(self, run_id: int, now: datetime, checked: int, sent: int, errors: int) -> None:
        with self.connection:
            self.connection.execute("""UPDATE runs SET finished_at=?, status=?, checked=?, sent=?, errors=? WHERE id=?""",
                (now.isoformat(), "partial_failure" if errors else "ok", checked, sent, errors, run_id))

    def alerts(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def news_sync_due(self, now: datetime, minimum_hours: float) -> bool:
        row = self.connection.execute("SELECT value FROM metadata WHERE key='news_last_sync'").fetchone()
        return not row or now - datetime.fromisoformat(row[0]) >= timedelta(hours=minimum_hours)

    def mark_news_sync(self, now: datetime) -> None:
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO metadata VALUES ('news_last_sync', ?)", (now.isoformat(),))

    def save_news(self, items, now: datetime) -> int:
        values = [(item.source_id, item.symbol, item.published_at.isoformat(), item.title, item.excerpt,
                   item.importance, item.reason, item.source_name, item.source_url, now.isoformat()) for item in items]
        with self.connection:
            before = self.connection.total_changes
            self.connection.executemany("""INSERT OR IGNORE INTO news_items
                (source_id,symbol,published_at,title,excerpt,importance,reason,source_name,source_url,fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", values)
            return self.connection.total_changes - before

    def recent_news(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.connection.execute("""SELECT source_id,symbol,published_at,title,excerpt,importance,reason,
                source_name,source_url FROM news_items ORDER BY published_at DESC LIMIT ?""", (limit,)).fetchall()

    def review(self, period: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM portfolio_reviews WHERE period=?", (period,)).fetchone()

    def latest_review(self) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM portfolio_reviews ORDER BY created_at DESC LIMIT 1").fetchone()

    def save_review(self, period: str, now: datetime, message: str, source_count: int, ai_used: bool) -> None:
        with self.connection:
            self.connection.execute("""INSERT OR REPLACE INTO portfolio_reviews
                (period,created_at,message,source_count,ai_used) VALUES (?,?,?,?,?)""",
                (period, now.isoformat(), message, source_count, int(ai_used)))
