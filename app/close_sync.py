"""Recover completed prices and evaluate saved closing signals after shutdown."""
import json
from datetime import UTC, datetime, date

from app.config import load_watchlist
from app.database import Database, run_lock
from app.fetcher import TwelveDataProvider, Snapshot, DataError, DailyBar, StockProvider
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
            from app.portfolio_catalog import PortfolioCatalog
            weights = PortfolioCatalog(settings).weights()[1]
            for stock in sorted(settings.stocks(), key=lambda s:(-weights.get(s.symbol,0),s.symbol)):
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


class SavedCloseProvider(StockProvider):
    """Evaluate the recovered official close without any additional API requests."""
    def __init__(self, path, target):
        self.path, self.target = path, target

    def fetch(self, symbol, now):
        import sqlite3
        close = session_close(self.target)
        db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)
        try:
            quote = db.execute("SELECT price,previous_close FROM quotes WHERE symbol=? AND source='twelvedata' AND as_of=? AND price_kind='daily_close'",
                               (symbol, close.isoformat())).fetchone()
            rows = db.execute("SELECT day,close,open,high,low FROM prices WHERE symbol=? AND source='twelvedata' AND day<=? ORDER BY day",
                              (symbol, self.target.isoformat())).fetchall()
        finally:
            db.close()
        if not quote or not rows or rows[-1][0] != self.target.isoformat():
            raise DataError('Saved official close is not complete')
        bars = tuple(DailyBar(date.fromisoformat(row[0]), *row[1:]) for row in rows)
        return Snapshot(symbol, quote[0], quote[1], close, bars, 'twelvedata', price_kind='daily_close')


def check_close_signals(settings, now, *, analyst=None, notifier=None):
    """Latest completed close only; use the normal event ledger, cooldown and AI cap."""
    target = completed_session(now)
    if settings.mock_mode or not settings.stock_api_key or target is None or is_open(now) or settings.notifier_mode != 'line':
        return None
    from app.analyst import CodexAnalyst, GeminiAnalyst, OpenAIAnalyst, TemplateAnalyst
    from app.main import check
    from app.notifier import LineNotifier
    if analyst is None:
        analyst = (TemplateAnalyst() if settings.analyst_mode == 'template' else
                   {'codex': CodexAnalyst, 'gemini': GeminiAnalyst, 'openai': OpenAIAnalyst}[settings.analyst_mode](settings))
    return check(settings, SavedCloseProvider(settings.database_path, target), analyst,
                 notifier or LineNotifier(settings), now=now)
