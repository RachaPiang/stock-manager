"""Known identities plus a bounded, weekly SEC ticker directory for new stocks."""
import json
import os
import re
import sqlite3

import requests

CIKS = {'META': 1326801, 'GOOGL': 1652044, 'ETN': 1551182, 'NVDA': 1045810,
        'MSFT': 789019, 'TXN': 97476, 'ASML': 937966, 'AMZN': 1018724, 'V': 1403161}


def resolve(settings, symbol, now):
    if symbol in CIKS:
        return CIKS[symbol]
    contact = os.getenv('SEC_CONTACT_EMAIL', '').strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact):
        return None
    path = settings.database_path.parent / 'sec-directory.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS directory(id INTEGER PRIMARY KEY, attempted REAL, fetched REAL, payload TEXT)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT attempted,fetched,payload FROM directory WHERE id=1').fetchone()
        due = not row or (now.timestamp() - row[0] >= 86400 and
                          (not row[1] or now.timestamp() - row[1] >= 7 * 86400))
        if due:
            db.execute('INSERT INTO directory VALUES(1,?,NULL,NULL) ON CONFLICT(id) DO UPDATE SET attempted=excluded.attempted', (now.timestamp(),))
    if due:
        try:
            with requests.get('https://www.sec.gov/files/company_tickers.json',
                    headers={'User-Agent': 'StockManager/1.0 ' + contact}, timeout=settings.http_timeout,
                    stream=True, allow_redirects=False) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 5_000_000:
                        raise ValueError('SEC directory too large')
            mapping = {}
            for item in json.loads(content).values():
                ticker, cik = item['ticker'], item['cik_str']
                if isinstance(cik, int) and 0 < cik < 10**10 and re.fullmatch(r'[A-Z][A-Z0-9.-]{0,11}', ticker):
                    mapping[ticker] = cik
            with sqlite3.connect(path) as db:
                db.execute('UPDATE directory SET fetched=?,payload=? WHERE id=1', (now.timestamp(), json.dumps(mapping)))
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
            pass
    with sqlite3.connect(path) as db:
        row = db.execute('SELECT payload FROM directory WHERE id=1').fetchone()
    return json.loads(row[0]).get(symbol) if row and row[0] else None
