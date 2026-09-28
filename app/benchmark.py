"""Daily S&P 500 close cache. Separate from Twelve Data; never fetched by a chart."""
import csv
import io
import math
import sqlite3
import re
from html.parser import HTMLParser
from datetime import UTC, date, datetime, timedelta

import requests

SOURCE = 'https://fred.stlouisfed.org/series/SP500'
CSV_URL = 'https://fred.stlouisfed.org/graph/graph.csv?id=SP500'
RANGES = {'1w': 7, '1m': 31, '3m': 93, '6m': 186, '1y': 366, '5y': 1827, '10y': 3653, 'all': None}


def parse_csv(text, today):
    result = {}
    for row in csv.DictReader(io.StringIO(text)):
        day = row.get('observation_date', row.get('DATE', ''))
        if row.get('SP500') in {None, '', '.'}:
            continue
        parsed = date.fromisoformat(day)
        value = float(row['SP500'])
        if not math.isfinite(value) or value <= 0 or parsed > today:
            raise ValueError('Invalid benchmark data')
        result[day] = value
    if len(result) < 2:
        raise ValueError('Insufficient benchmark history')
    return result


def parse_recent_page(text, today):
    """Fallback to the official page's recent-observations table, NOT invented history."""
    class Cells(HTMLParser):
        def __init__(self):
            super().__init__(); self.cells = []; self.current = None
        def handle_starttag(self, tag, attrs):
            if tag == 'td': self.current = ''
        def handle_data(self, data):
            if self.current is not None: self.current += data
        def handle_endtag(self, tag):
            if tag == 'td' and self.current is not None:
                self.cells.append(self.current.strip()); self.current = None
    parser = Cells(); parser.feed(text)
    rows = {}
    for i, value in enumerate(parser.cells[:-1]):
        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}):', value)
        if match:
            try:
                day = match[1]; close = float(parser.cells[i+1].replace(',', ''))
                if date.fromisoformat(day) <= today and math.isfinite(close) and close > 0:
                    rows[day] = close
            except ValueError:
                continue
    if len(rows) < 2:
        raise ValueError('Official benchmark page changed or has insufficient data')
    return rows


def sync(settings, now=None, *, retry=False):
    now = now or datetime.now(UTC)
    if settings.mock_mode:
        return False
    path = settings.database_path.parent/'benchmark.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE IF NOT EXISTS prices(day TEXT PRIMARY KEY,close REAL);'
                         'CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT);')
        db.execute('BEGIN IMMEDIATE')
        last = db.execute("SELECT value FROM state WHERE key='attempt'").fetchone()
        if last and not retry and now.timestamp()-float(last[0]) < 24*3600:
            return False
        db.execute("INSERT OR REPLACE INTO state VALUES('attempt',?)", (str(now.timestamp()),))
        db.commit()  # Failed requests are also rate-limited across processes.
    try:
        response = requests.get(CSV_URL, timeout=settings.http_timeout)
        recent_only = response.status_code == 404
        if recent_only:
            response = requests.get(SOURCE, timeout=settings.http_timeout)
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError('Benchmark response too large')
        rows = parse_recent_page(response.text, now.date()) if recent_only else parse_csv(response.text, now.date())
    except (requests.RequestException, ValueError, KeyError):
        raise ValueError('S&P 500 refresh failed; keeping last saved data') from None
    with sqlite3.connect(path) as db:
        db.executemany('INSERT OR REPLACE INTO prices VALUES(?,?)', sorted(rows.items()))
        db.execute("INSERT OR REPLACE INTO state VALUES('success',?)", (now.isoformat(),))
    return True


def comparisons(history, benchmark):
    """Inner join dates only. Rebase both to 0% on the same start of EACH range."""
    portfolio = {p['day']: p['value_usd'] for p in history
                 if len(p['day']) == 10 and math.isfinite(p['value_usd']) and p['value_usd'] > 0}
    dates = sorted(set(portfolio) & set(benchmark))
    result = {'1d': []}  # Daily close cannot truthfully be an intraday comparison.
    for key, days in RANGES.items():
        selected = dates
        if dates and days:
            cutoff = (date.fromisoformat(dates[-1])-timedelta(days=days)).isoformat()
            selected = [d for d in dates if d >= cutoff]
        result[key] = []
        if len(selected) < 2:
            continue
        first = selected[0]
        result[key] = [{'day': d, 'value_usd': (portfolio[d]/portfolio[first]-1)*100,
                        'benchmark_pct': (benchmark[d]/benchmark[first]-1)*100} for d in selected]
    return result


def report_comparison(settings, history):
    result = {'source_url': SOURCE, 'as_of': None, 'updated_at': None, 'ranges': comparisons([], {})}
    path = settings.database_path.parent/'benchmark.sqlite3'
    if settings.mock_mode or not path.exists():
        return result
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as db:
        prices = dict(db.execute('SELECT day,close FROM prices ORDER BY day'))
        updated = db.execute("SELECT value FROM state WHERE key='success'").fetchone()
    result.update(as_of=max(prices) if prices else None, updated_at=updated[0] if updated else None,
                  ranges=comparisons(history, prices))
    return result


if __name__ == '__main__':
    from app.config import Settings
    try:
        print('S&P 500 updated' if sync(Settings.from_env()) else 'Using saved S&P 500 data; daily refresh limit')
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(1)
