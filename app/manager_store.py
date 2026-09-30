"""Private durable inbox/outbox. Never stores access tokens or reply tokens."""
import sqlite3
import uuid
from contextlib import contextmanager


class ManagerStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS inbox (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL, at REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending');
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, message TEXT NOT NULL, at REAL NOT NULL,
                    retry_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS briefs (
                    key TEXT PRIMARY KEY, kind TEXT NOT NULL, created REAL NOT NULL,
                    message TEXT NOT NULL, payload TEXT NOT NULL, ai_used INTEGER NOT NULL);
            ''')
            # Add delivery metadata without rewriting the existing message ledger.
            columns = {row[1] for row in db.execute('PRAGMA table_info(outbox)')}
            for name, kind in (('scheduled_for', 'REAL'), ('delivery_text', 'TEXT')):
                if name not in columns:
                    db.execute(f'ALTER TABLE outbox ADD COLUMN {name} {kind}')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def accept(self, event_id, text, at):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM inbox WHERE id=?', (event_id,)).fetchone():
                return False
            if db.execute("SELECT count(*) FROM inbox WHERE state='pending'").fetchone()[0] >= 100:
                return False
            # Bound accidental bursts and queue growth, even for the owner.
            if db.execute('SELECT count(*) FROM inbox WHERE at>?', (at-60,)).fetchone()[0] >= 12:
                return False
            return bool(db.execute('INSERT OR IGNORE INTO inbox(id,text,at) VALUES(?,?,?)',
                                   (event_id, text, at)).rowcount)

    def claim(self, at=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if at is not None:
                db.execute("UPDATE inbox SET state='expired',text='' WHERE state='pending' AND at<?", (at-3600,))
            row = db.execute("SELECT * FROM inbox WHERE state='pending' ORDER BY at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE inbox SET state='working' WHERE id=?", (row['id'],))
                return dict(row)

    def finish(self, event_id, message, at):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO outbox(id,message,at,retry_key) VALUES(?,?,?,?)',
                       (event_id, message, at, str(uuid.uuid4())))
            db.execute("UPDATE inbox SET state='done', text='' WHERE id=?", (event_id,))

    def recover(self, at):
        # An interrupted analysis is NOT repeated (avoids duplicate AI charges).
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM inbox WHERE state='working'")]
        for event_id in ids:
            self.finish(event_id, 'งานก่อนหน้าหยุดกลางทาง กรุณาส่งคำถามใหม่ ระบบไม่ได้เรียก AI ซ้ำอัตโนมัติ', at)

    def enqueue(self, key, message, at, daily_limit, *, scheduled_for=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # The owner sees this system in Thailand; use a Thai calendar day
            # for the proactive-message budget, not an arbitrary UTC midnight.
            from datetime import datetime
            from zoneinfo import ZoneInfo
            local = datetime.fromtimestamp(at, ZoneInfo('Asia/Bangkok'))
            start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            count = db.execute("SELECT count(*) FROM outbox WHERE id LIKE 'schedule:%' AND at>=?", (start,)).fetchone()[0]
            if count >= daily_limit:
                return False
            return bool(db.execute('INSERT OR IGNORE INTO outbox(id,message,at,retry_key,scheduled_for) VALUES(?,?,?,?,?)',
                                   ('schedule:'+key, message, at, str(uuid.uuid4()), scheduled_for)).rowcount)

    def pending(self, at, daily_limit=8):
        with self.connect() as db:
            # An offline machine never transmitted these scheduled messages.
            # Give them a fresh delivery window, keeping the deduplication id.
            # Attempted sends keep their original retry key/window: their
            # delivery may be uncertain, so never risk a duplicate after 24h.
            rows = db.execute("SELECT id,message,at,scheduled_for FROM outbox WHERE state='pending' AND attempts=0 AND id LIKE 'schedule:%' AND at<?", (at-23*3600,)).fetchall()
            from datetime import datetime
            from zoneinfo import ZoneInfo
            start = datetime.fromtimestamp(at, ZoneInfo('Asia/Bangkok')).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            used = db.execute("SELECT count(*) FROM outbox WHERE id LIKE 'schedule:%' AND at>=?", (start,)).fetchone()[0]
            for row in rows:
                if used >= daily_limit:
                    break
                message = row['message']
                if not message.startswith('ตามเก็บ'):
                    message = 'ตามเก็บข้อความที่ค้างตอนเครื่องไม่ได้ทำงานครับ\n\n'+message
                if row['scheduled_for'] is None and 'เข้าคิวเดิม:' not in message:
                    from app.voice import thai_time
                    message += '\n\nเข้าคิวเดิม: '+thai_time(datetime.fromtimestamp(row['at'], ZoneInfo('Asia/Bangkok')).isoformat())+' (เวลาไทย)'
                db.execute('UPDATE outbox SET at=?,next_try=0,retry_key=?,message=? WHERE id=?',
                           (at, str(uuid.uuid4()), message, row['id']))
                used += 1
            # LINE retry-key retention is 24h. Never retry after 23h.
            db.execute("UPDATE outbox SET state='expired',message='',delivery_text=NULL WHERE state='pending' AND at<? AND (attempts>0 OR id NOT LIKE 'schedule:%')", (at-23*3600,))
            row = db.execute("SELECT * FROM outbox WHERE state='pending' AND at>=? AND next_try<=? ORDER BY at LIMIT 1", (at-23*3600, at)).fetchone()
            if not row:
                return None
            result = dict(row)
            if row['id'].startswith('schedule:'):
                if not row['attempts'] or row['delivery_text'] is None:
                    from app.voice import thai_time
                    message, times = row['message'], []
                    delayed = row['scheduled_for'] is not None and at > row['scheduled_for']+60
                    if row['scheduled_for'] is not None:
                        planned = thai_time(datetime.fromtimestamp(row['scheduled_for'], ZoneInfo('Asia/Bangkok')).isoformat())
                        times.append(('กำหนดแจ้งเดิม: ' if delayed else 'กำหนดแจ้ง: ')+planned)
                    if delayed and not message.startswith('ตามเก็บ'):
                        message = 'ตามเก็บแจ้งเตือนที่พลาดเวลานัดครับ\n\n'+message
                    label = 'เริ่มแจ้งย้อนหลัง: ' if delayed or message.startswith('ตามเก็บ') else 'เริ่มแจ้งเมื่อ: '
                    times.append(label+thai_time(datetime.fromtimestamp(at, ZoneInfo('Asia/Bangkok')).isoformat()))
                    result['message'] = message+'\n\nเวลาแจ้งเตือน (ไทย)\n'+'\n'.join(times)
                    db.execute('UPDATE outbox SET delivery_text=? WHERE id=?', (result['message'], row['id']))
                else:
                    # Retries use exactly the original body/time and retry key.
                    result['message'] = row['delivery_text']
            return result

    def start_delivery(self, key):
        # Record before transport so an interrupted/unknown send cannot be
        # mistaken for a never-sent message after a long shutdown.
        with self.connect() as db:
            db.execute('UPDATE outbox SET attempts=attempts+1 WHERE id=?', (key,))

    def cancel_schedule(self, key):
        with self.connect() as db:
            db.execute("UPDATE outbox SET state='cancelled',message='',delivery_text=NULL WHERE id=? AND state='pending' AND attempts=0", ('schedule:'+key,))

    def delivered(self, key):
        with self.connect() as db:
            db.execute("UPDATE outbox SET state='sent',message='',delivery_text=NULL WHERE id=?", (key,))

    def failed(self, key, at, retryable):
        with self.connect() as db:
            db.execute("UPDATE outbox SET next_try=?,state=? WHERE id=?",
                       (at+300, 'pending' if retryable else 'failed', key))

    def get(self, key, default=''):
        with self.connect() as db:
            row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
            return row[0] if row else default

    def bind_owner(self, owner):
        import hashlib
        identity = hashlib.sha256(owner.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM state WHERE key='owner'").fetchone()
            if row and row[0] != identity:
                raise ValueError('Manager database belongs to a different LINE owner')
            db.execute("INSERT OR IGNORE INTO state VALUES('owner',?)", (identity,))

    def set(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, value))

    def brief(self, key=None, kind=None):
        with self.connect() as db:
            row = (db.execute('SELECT * FROM briefs WHERE key=?', (key,)).fetchone() if key else
                   db.execute('SELECT * FROM briefs WHERE kind=? ORDER BY created DESC LIMIT 1', (kind,)).fetchone())
            return dict(row) if row else None

    def save_brief(self, key, kind, at, message, payload, ai_used):
        import json
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO briefs VALUES(?,?,?,?,?,?)',
                       (key, kind, at, message, json.dumps(payload, ensure_ascii=False), int(ai_used)))

    def cancel_old_summaries(self):
        with self.connect() as db:
            db.execute("""UPDATE outbox SET state='cancelled' WHERE state='pending' AND
                (id LIKE 'schedule:market-brief:%' OR id LIKE 'schedule:news-digest:%' OR id LIKE 'schedule:review:%')""")
