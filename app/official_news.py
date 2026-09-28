"""Supplemental verified company RSS feeds; no paid market-data credits or AI."""
import hashlib
import sqlite3
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from app.news import NewsItem, NewsError, classify, plain_text

# These feeds were obtained from the companies' own RSS pages. Coverage is explicit.
FEEDS = {
    'NVDA': ('https://nvidianews.nvidia.com/cats/press_release.xml', 'nvidianews.nvidia.com'),
    'META': ('https://investor.atmeta.com/rss/pressrelease.aspx', 'investor.atmeta.com'),
}


def parse_feed(content, symbol, now, lookback_days=14):
    if len(content) > 2_000_000 or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise NewsError('Unsafe or oversized RSS')
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise NewsError('Invalid company RSS') from None
    result = []
    for item in root.findall('./channel/item')[:100]:
        title = plain_text(item.findtext('title'), 500)
        excerpt = plain_text(item.findtext('description'), 1000)
        link = (item.findtext('link') or '').strip()
        url = urlparse(link)
        if url.scheme != 'https' or url.hostname != FEEDS[symbol][1] or url.username or url.password:
            continue
        try:
            at = parsedate_to_datetime(item.findtext('pubDate', '')).astimezone(UTC)
        except (ValueError, TypeError, OverflowError):
            continue
        # Title only prevents boilerplate CEO mentions in every release becoming alerts.
        category = classify(title, '')
        if not category or not now-timedelta(days=lookback_days) <= at <= now:
            continue
        result.append(NewsItem('rss:'+hashlib.sha256(link.encode()).hexdigest(), symbol, at,
                              title, excerpt, *category, source_name=f'{symbol} · official company RSS', source_url=link))
    return result


def sync(settings, now=None):
    from app.database import Database
    now = now or datetime.now(UTC)
    if settings.mock_mode or settings.news_mode == 'off':
        return {'saved': 0, 'errors': 0}
    state_path = settings.database_path.parent/'official-news.sqlite3'
    saved, errors = 0, 0
    db = Database(settings.database_path, 'live')
    try:
        for symbol, (url, _) in FEEDS.items():
            with sqlite3.connect(state_path) as state:
                state.execute('CREATE TABLE IF NOT EXISTS attempts(symbol TEXT PRIMARY KEY,at REAL,status TEXT)')
                state.execute('BEGIN IMMEDIATE')
                last = state.execute('SELECT at FROM attempts WHERE symbol=?', (symbol,)).fetchone()
                if last and now.timestamp()-last[0] < 86400:
                    continue
                state.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)', (symbol, now.timestamp(), 'attempting'))
            try:
                response = requests.get(url, timeout=settings.http_timeout)
                response.raise_for_status()
                items = parse_feed(response.content, symbol, now, settings.news_lookback_days)
                db.save_news(items, now)
                saved += len(items)
                status = 'ok'
            except (requests.RequestException, NewsError):
                errors += 1; status = 'failed'
            with sqlite3.connect(state_path) as state:
                state.execute('UPDATE attempts SET status=? WHERE symbol=?', (status, symbol))
    finally:
        db.close()
    return {'saved': saved, 'errors': errors}


if __name__ == '__main__':
    from app.config import Settings
    result = sync(Settings.from_env())
    print('Official RSS (META/NVDA only):', result)
    raise SystemExit(1 if result['errors'] else 0)
