"""Recover completed-session prices after shutdown. No AI, alerts, or trades."""
import json
from datetime import UTC, datetime

from app.config import load_watchlist
from app.database import Database, run_lock
from app.fetcher import TwelveDataProvider, Snapshot, DataError
from app.market import completed_session, session_close, is_open


def sync_close(settings, now=None, provider=None):
    now = now or datetime.now(UTC)
    target = completed_session(now)
    result = {'updated': 0, 'errors': 0, 'skipped': 0}
    if settings.mock_mode or not settings.stock_api_key or target is None or is_open(now):
        return result
    close = session_close(target)
    provider = provider or TwelveDataProvider(settings)
    with run_lock(settings.database_path.with_suffix('.lock')):
        db = Database(settings.database_path, 'live')
        try:
            for stock in load_watchlist(settings.watchlist_path):
                key = f'close-sync:{target}:{stock.symbol}'
                row = db.connection.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if state.get('done') or state.get('attempts', 0) >= 3 or now.timestamp()-state.get('at', 0) < 3600:
                    result['skipped'] += 1
                    continue
                state = dict(at=now.timestamp(), attempts=state.get('attempts', 0)+1, done=False)
                with db.connection:
                    db.connection.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (key, json.dumps(state)))
                try:
                    daily = provider.fetch_history(stock.symbol, now)
                    bars = provider.fetch_intraday(stock.symbol, now)
                    eligible = [b for b in bars if b.end <= close]
                    if not eligible or eligible[-1].end != close or daily[-1].day != target:
                        raise DataError('Closing session is not yet complete at provider')
                    previous = [b for b in daily if b.day < target]
                    if not previous:
                        raise DataError('Missing previous close')
                    # Official daily close is authoritative for the final session value.
                    snapshot = Snapshot(stock.symbol, daily[-1].close, previous[-1].close, close,
                                        daily, 'twelvedata', intraday=bars, price_kind='daily_close')
                    snapshot.validate(now, max(settings.max_quote_age_hours, 24*14))
                    db.save_snapshot(snapshot)
                    state['done'] = True
                    result['updated'] += 1
                    with db.connection:
                        db.connection.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (key, json.dumps(state)))
                except (DataError, ValueError, TypeError):
                    result['errors'] += 1
        finally:
            db.close()
    return result
