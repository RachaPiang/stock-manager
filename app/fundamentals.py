"""Daily cached annual and discrete-quarter SEC facts; calculations stay in code."""
import json
import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime

import requests
from app.sec_identity import CIKS, resolve

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


def extract(payload, symbol, now, expected_cik=None):
    cik = expected_cik or CIKS.get(symbol)
    if cik is None or int(payload.get('cik', -1)) != cik:
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
                            source_url=f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/{accession}-index.html'))
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
    quarterly = extract_quarters(facts, cik, now)
    return dict(symbol=symbol, company=payload.get('entityName', symbol), metrics=result, version=2,
                quarters=quarterly, latest_quarter=quarterly[-1] if quarterly else None,
                missing=[k for k in TAGS if k not in result],
                basis='Annual and discrete-quarter filed US-GAAP facts. Missing quarter cash flow may be YTD-only. Debt components may be incomplete. Not a valuation or forecast.')


def derived(metrics):
    for name, a, b, divide in [('net_margin_pct', 'net_income', 'revenue', True),
                             ('free_cash_flow', 'operating_cash', 'capex', False)]:
        left, right = metrics.get(a), metrics.get(b)
        if not left or not right or any(left[k] != right[k] for k in ('start', 'end', 'currency')):
            continue
        if divide and right['value'] <= 0:
            continue
        metrics[name] = {**left, 'value': left['value'] / right['value'] * 100 if divide else left['value'] - right['value'],
                         'currency': 'percent' if divide else left['currency'],
                         'formula': f'{a}/{b}*100' if divide else f'{a}-{b}'}


def extract_quarters(facts, cik, now):
    periods = {}
    for metric, tags in TAGS.items():
        for tag in tags:
            for currency, rows in facts.get(tag, {}).get('units', {}).items():
                if currency not in {'USD', 'EUR'}:
                    continue
                for row in rows:
                    try:
                        end, filed = date.fromisoformat(row['end']), date.fromisoformat(row['filed'])
                        if row.get('form') not in {'10-Q', '10-K', '10-Q/A', '10-K/A'} or max(end, filed) > now.date():
                            continue
                        flow = metric in {'revenue', 'net_income', 'operating_cash', 'capex'}
                        if flow and not 70 <= (end - date.fromisoformat(row['start'])).days <= 110:
                            continue
                        if not flow and row.get('start'):
                            continue
                        value = float(row['val'])
                        accession = row['accn']
                        if not math.isfinite(value) or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession):
                            continue
                        item = dict(value=value, start=row.get('start'), end=str(end), filed=str(filed),
                                    currency=currency, tag=tag, source_url=f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/{accession}-index.html')
                        group = periods.setdefault(str(end), {})
                        old = group.get(metric)
                        if not old or (item['filed'], -tags.index(tag)) > (old['filed'], -tags.index(old['tag'])):
                            group[metric] = item
                    except (ValueError, TypeError, KeyError, OverflowError):
                        continue
    # Instant balance-sheet facts alone do not constitute a quarter's results.
    periods = {end: m for end, m in periods.items() if 'revenue' in m or 'net_income' in m}
    output = []
    for end in sorted(periods):
        metrics = periods[end]
        derived(metrics)
        trends = {}
        for name in ('revenue', 'net_income', 'operating_cash', 'free_cash_flow', 'net_margin_pct'):
            current = metrics.get(name)
            if not current:
                continue
            candidates = [q for q in output if 350 <= (date.fromisoformat(end)-date.fromisoformat(q['end'])).days <= 380
                          and name in q['metrics']]
            for previous in reversed(candidates):
                base = previous['metrics'][name]
                if base['currency'] != current['currency'] or not base.get('start') or not current.get('start'):
                    continue
                durations = [(date.fromisoformat(m['end']) - date.fromisoformat(m['start'])).days for m in (current, base)]
                if abs(durations[0] - durations[1]) > 10:
                    continue
                # A negative/zero comparison base has no ordinary growth percentage.
                if name == 'net_margin_pct':
                    trends['net_margin_change_pp'] = current['value'] - base['value']
                elif base['value'] > 0:
                    trends[name + '_yoy_pct'] = (current['value'] / base['value'] - 1) * 100
                break
        output.append(dict(end=end, metrics=metrics, trends=trends,
                           missing=[key for key in TAGS if key not in metrics]))
    return output[-12:]


def collect(settings, symbols, now):
    path = settings.database_path.parent/'fundamentals.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    contact = os.getenv('SEC_CONTACT_EMAIL', '').strip()
    configured = bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact))
    output = {}
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS facts(symbol TEXT PRIMARY KEY, attempted REAL, fetched REAL, payload TEXT, error TEXT)')
        if 'parser_version' not in {r[1] for r in db.execute('PRAGMA table_info(facts)')}:
            db.execute('ALTER TABLE facts ADD COLUMN parser_version INTEGER NOT NULL DEFAULT 0')
    for symbol in symbols:
        cik = resolve(settings, symbol, now)
        if cik is None:
            output[symbol] = {'status': 'identity_unavailable' if configured else 'not_configured', 'metrics': {}}
            continue
        with closing(sqlite3.connect(path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT attempted,fetched,payload,error,parser_version FROM facts WHERE symbol=?', (symbol,)).fetchone()
            refresh = configured and (not row or row[4] != 2 or now.timestamp()-row[0] >= 86400)
            if refresh:
                db.execute('INSERT INTO facts(symbol,attempted,fetched,payload,error,parser_version) VALUES(?,?,NULL,NULL,?,2) ON CONFLICT(symbol) DO UPDATE SET attempted=excluded.attempted,error=excluded.error,parser_version=2',
                           (symbol, now.timestamp(), 'refresh_pending'))
        if refresh:
            try:
                url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
                with requests.get(url, headers={'User-Agent': f'StockManager/1.0 {contact}'},
                                  timeout=settings.http_timeout, stream=True) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_content(65536):
                        content.extend(chunk)
                        if len(content) > 30_000_000:
                            raise ValueError('SEC response too large')
                    value = extract(json.loads(content), symbol, now, cik)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute('UPDATE facts SET fetched=?,payload=?,error=NULL WHERE symbol=?',
                               (now.timestamp(), json.dumps(value, allow_nan=False), symbol))
            except (requests.RequestException, ValueError, TypeError, KeyError):
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute('UPDATE facts SET error=? WHERE symbol=?', ('fetch_failed', symbol))
        with closing(sqlite3.connect(path)) as db:
            row = db.execute('SELECT attempted,fetched,payload,error FROM facts WHERE symbol=?', (symbol,)).fetchone()
        value = current_period_only(json.loads(row[2])) if row and row[2] else {'metrics': {}}
        available = bool(value['metrics'] or value.get('latest_quarter'))
        value['status'] = ('not_configured' if not configured and not available else
                           'unavailable' if not available else 'refresh_failed' if row[3] else 'available')
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
