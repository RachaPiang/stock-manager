"""Daily cached SEC annual facts. No market-data credits or model calls."""
import json
import math
import os
import re
import sqlite3
from datetime import UTC, date, datetime

import requests

CIKS = {'META': 1326801, 'GOOGL': 1652044, 'ETN': 1551182, 'NVDA': 1045810,
        'MSFT': 789019, 'TXN': 97476, 'ASML': 937966, 'AMZN': 1018724, 'V': 1403161}
TAGS = {'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'],
        'net_income': ['NetIncomeLoss', 'ProfitLoss'],
        'operating_cash': ['NetCashProvidedByUsedInOperatingActivities'],
        'capex': ['PaymentsToAcquirePropertyPlantAndEquipment'],
        'cash': ['CashAndCashEquivalentsAtCarryingValue'],
        'long_term_debt_noncurrent': ['LongTermDebtNoncurrent', 'LongTermDebt'],
        'current_debt': ['LongTermDebtCurrent']}


def current_period_only(value):
    """Retired XBRL tags must not masquerade as current balance-sheet data."""
    metrics = value.get('metrics', {})
    if metrics:
        latest = max(m['end'] for m in metrics.values())
        value['metrics'] = {k: m for k, m in metrics.items() if m['end'] == latest}
        value['missing'] = [k for k in TAGS if k not in value['metrics']]
    return value


def extract(payload, symbol, now):
    if int(payload.get('cik', -1)) != CIKS[symbol]:
        raise ValueError('SEC company identity mismatch')
    facts = payload.get('facts', {}).get('us-gaap', {})
    result = {}
    for metric, tags in TAGS.items():
        candidates = []
        for tag in tags:
            for unit, rows in facts.get(tag, {}).get('units', {}).items():
                if unit not in {'USD', 'EUR'}:
                    continue
                for row in rows:
                    try:
                        end, filed = date.fromisoformat(row['end']), date.fromisoformat(row['filed'])
                        if (row.get('form') not in {'10-K', '20-F'} or end > now.date() or filed > now.date()
                                or not math.isfinite(float(row['val']))):
                            continue
                        if metric in {'revenue', 'net_income', 'operating_cash', 'capex'}:
                            if not 330 <= (end-date.fromisoformat(row['start'])).days <= 380:
                                continue  # never compare a YTD or quarter with an annual fact
                        elif row.get('start'):
                            continue
                        accession = row['accn']
                        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession):
                            continue
                        candidates.append(dict(value=float(row['val']), currency=unit, end=str(end),
                            start=row.get('start'), filed=str(filed), tag=tag,
                            source_url=f'https://www.sec.gov/Archives/edgar/data/{CIKS[symbol]}/{accession.replace("-", "")}/{accession}-index.html'))
                    except (ValueError, KeyError, TypeError, OverflowError):
                        continue
        if candidates:
            result[metric] = max(candidates, key=lambda r: (r['end'], r['filed'], -tags.index(r['tag'])))
    result = current_period_only({'metrics': result})['metrics']
    # Derived values use matching period AND currency; no FX or TTM guesses.
    for name, a, b, divide in [('net_margin_pct', 'net_income', 'revenue', True),
                                ('free_cash_flow', 'operating_cash', 'capex', False)]:
        left, right = result.get(a), result.get(b)
        if left and right and all(left[k] == right[k] for k in ('start', 'end', 'currency')):
            if divide and right['value'] <= 0:
                continue
            result[name] = dict(value=left['value']/right['value']*100 if divide else left['value']-right['value'],
                                currency='percent' if divide else left['currency'], end=left['end'],
                                formula=f'{a}/{b}*100' if divide else f'{a}-{b}')
    return dict(symbol=symbol, company=payload.get('entityName', symbol), metrics=result,
                missing=[k for k in TAGS if k not in result],
                basis='Annual filed US-GAAP facts; debt components may be incomplete. Not a current valuation or earnings forecast.')


def collect(settings, symbols, now):
    path = settings.database_path.parent/'fundamentals.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    contact = os.getenv('SEC_CONTACT_EMAIL', '').strip()
    configured = bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact))
    output = {}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS facts(symbol TEXT PRIMARY KEY, attempted REAL, fetched REAL, payload TEXT, error TEXT)')
    for symbol in symbols:
        if symbol not in CIKS:
            output[symbol] = {'status': 'unsupported', 'metrics': {}}
            continue
        with sqlite3.connect(path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT attempted,fetched,payload,error FROM facts WHERE symbol=?', (symbol,)).fetchone()
            refresh = configured and (not row or now.timestamp()-row[0] >= 86400)
            if refresh:
                db.execute('INSERT INTO facts VALUES(?,?,NULL,NULL,?) ON CONFLICT(symbol) DO UPDATE SET attempted=excluded.attempted,error=excluded.error',
                           (symbol, now.timestamp(), 'refresh_pending'))
        if refresh:
            try:
                url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{CIKS[symbol]:010d}.json'
                with requests.get(url, headers={'User-Agent': f'StockManager/1.0 {contact}'},
                                  timeout=settings.http_timeout, stream=True) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_content(65536):
                        content.extend(chunk)
                        if len(content) > 30_000_000:
                            raise ValueError('SEC response too large')
                    value = extract(json.loads(content), symbol, now)
                with sqlite3.connect(path) as db:
                    db.execute('UPDATE facts SET fetched=?,payload=?,error=NULL WHERE symbol=?',
                               (now.timestamp(), json.dumps(value, allow_nan=False), symbol))
            except (requests.RequestException, ValueError, TypeError, KeyError):
                with sqlite3.connect(path) as db:
                    db.execute('UPDATE facts SET error=? WHERE symbol=?', ('fetch_failed', symbol))
        with sqlite3.connect(path) as db:
            row = db.execute('SELECT attempted,fetched,payload,error FROM facts WHERE symbol=?', (symbol,)).fetchone()
        value = current_period_only(json.loads(row[2])) if row and row[2] else {'metrics': {}}
        value['status'] = ('not_configured' if not configured and not value['metrics'] else
                           'unavailable' if not value['metrics'] else 'refresh_failed' if row[3] else 'available')
        value['fetched_at'] = datetime.fromtimestamp(row[1], UTC).isoformat() if row and row[1] else None
        value['cache_age_days'] = (now.timestamp()-row[1])/86400 if row and row[1] else None
        output[symbol] = value
    return output


if __name__ == '__main__':
    from app.config import Settings, load_watchlist
    settings = Settings.from_env()
    if not settings.mock_mode:
        rows = collect(settings, [s.symbol for s in settings.stocks()], datetime.now(UTC))
        print('SEC fundamentals: '+', '.join(f'{s}={r["status"]}' for s, r in rows.items()))
