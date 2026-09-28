"""Store portfolio observations with the holdings basis used at that time."""
from datetime import datetime, UTC, timedelta
from decimal import Decimal, ROUND_HALF_UP
import sqlite3

from app.portfolio import holdings_version


def record_observations(path, report):
    profile = report['portfolio']
    # A changed share total without the matching broker cost is still a useful
    # DCA checkpoint, but must not create a false profit history.
    if any(not holding.get('cost_reliable', True) for holding in profile['holdings']):
        return report.get('portfolio_observations', [])
    basis = holdings_version(profile)
    effective = profile.get('quantity_as_of', profile.get('holdings_as_of', profile['as_of']))
    effective = datetime.fromisoformat(effective)
    if effective.tzinfo is None:
        effective = effective.replace(tzinfo=UTC)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute('''CREATE TABLE IF NOT EXISTS portfolio_observations (
            basis TEXT NOT NULL, at TEXT NOT NULL, recorded_at TEXT NOT NULL,
            value_usd REAL NOT NULL, cost_usd REAL NOT NULL, PRIMARY KEY (basis, at))''')
        # Backfill actual saved quotes and complete historical five-minute bars,
        # only since the user-reported holdings date (not pre-holding simulations).
        # Exact common timestamps avoid inventing cross-symbol prices or forward-filling gaps.
        source = next((s['source'] for s in report['stocks']), None)
        maps = []
        for h in profile['holdings']:
            if h.get('quantity') is None:
                return report.get('portfolio_observations', [])
            rows = connection.execute('SELECT as_of,price FROM quotes WHERE symbol=? AND source=? AND as_of>=?',
                                      (h['symbol'], source, effective.isoformat())).fetchall()
            prices = {}
            for at, price in connection.execute('SELECT at,close FROM intraday WHERE symbol=? AND source=?', (h['symbol'], source)):
                end = datetime.fromisoformat(at)+timedelta(minutes=5)
                if effective <= end <= datetime.fromisoformat(report['generated_at']):
                    prices[end.isoformat()] = price
            prices.update(dict(rows))  # Official closing quotes take precedence.
            maps.append({at: Decimal(str(price)) * Decimal(str(h['quantity'])) for at, price in prices.items()
                         if price is not None and price > 0 and Decimal(str(price)).is_finite()})
        common = set.intersection(*(set(m) for m in maps)) if maps else set()
        generated = datetime.fromisoformat(report['generated_at'])
        with connection:
            for at in sorted(common):
                if datetime.fromisoformat(at) > generated:
                    continue
                value = sum(m[at].quantize(Decimal('.01'), rounding=ROUND_HALF_UP) for m in maps)
                connection.execute('INSERT OR IGNORE INTO portfolio_observations VALUES (?,?,?,?,?)',
                                   (basis, at, report['generated_at'], float(value), profile['estimated_cost_total_usd']))
        return [dict(day=r[0], value_usd=r[1], cost_usd=r[2]) for r in connection.execute(
            'SELECT at,value_usd,cost_usd FROM portfolio_observations WHERE basis=? ORDER BY at', (basis,))]
    finally:
        connection.close()
