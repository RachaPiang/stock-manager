"""Weekly public RSS discovery. No stock API credits; headlines, not full articles."""
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from app.news import plain_text

TOPICS = {
    'MARKET': '(US stock market OR Federal Reserve OR inflation)',
    'META': '"Meta Platforms"', 'GOOGL': '(Alphabet OR "Google earnings")',
    'ETN': '"Eaton" (earnings OR electrical OR aerospace OR acquisition)',
    'NVDA': 'Nvidia', 'MSFT': 'Microsoft', 'TXN': '"Texas Instruments"',
    'ASML': 'ASML', 'AMZN': 'Amazon (AWS OR earnings OR antitrust OR investment)',
    'V': '"Visa" (payments OR earnings OR antitrust OR acquisition)',
}
ALIASES = {'META': ('meta',), 'GOOGL': ('alphabet', 'google'), 'ETN': ('eaton',),
           'NVDA': ('nvidia',), 'MSFT': ('microsoft',), 'TXN': ('texas instruments',),
           'ASML': ('asml',), 'AMZN': ('amazon', 'aws'), 'V': ('visa',)}
PUBLISHERS = ('reuters.com', 'apnews.com', 'cnbc.com', 'ft.com', 'wsj.com',
              'bloomberg.com', 'finance.yahoo.com', 'marketwatch.com',
              'federalreserve.gov', 'bls.gov', 'bea.gov', 'sec.gov',
              'atmeta.com', 'abc.xyz', 'eaton.com', 'nvidia.com', 'microsoft.com',
              'ti.com', 'asml.com', 'amazon.com', 'visa.com', 'businesswire.com', 'prnewswire.com')
SIGNIFICANT = re.compile(r'earnings|revenue|profit|guidance|outlook|results|acquir|merger|antitrust|'
                         r'export|regulat|lawsuit|investigat|capex|capital spending|data cent|'
                         r'chip|AI\b|cloud|layoff|dividend|buyback|inflation|interest rate|'
                         r'federal reserve|fed\b|tariff|payroll|employment|S&P|Nasdaq|stocks', re.I)
NOISE = re.compile(r'should you buy|best stocks|bull of the day|price target|millionaire|'
                   r'stocks to buy|motley fool|sponsored|\bvs\.?\s|\bversus\b|'
                   r'better.{0,25}stock|which.{0,30}stock|stock.{0,30}buy|'
                   r'^is\b|^should\b|^why\b|^does\b|final trades|stocks making the biggest|'
                   r'^use .*AI|report on AI|how to|whisperx|sagemaker|'
                   r'undervalued|overvalued|valuation|trades? at|trading at', re.I)


def significant(title):
    return bool(SIGNIFICANT.search(title) and not NOISE.search(title))


def parse(content, symbol, now, limit=2):
    if len(content) > 2_000_000 or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('Invalid news feed')
    root = ET.fromstring(content)
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('Not an RSS feed')
    rows, seen = [], set()
    for item in root.findall('./channel/item')[:100]:
        source = item.find('source')
        if source is None:
            continue
        host = (urlparse(source.get('url', '')).hostname or '').lower()
        if not any(host == d or host.endswith('.'+d) for d in PUBLISHERS):
            continue
        title = plain_text(item.findtext('title'), 350)
        if not significant(title):
            continue
        if symbol != 'MARKET' and not any(re.search(r'\b'+re.escape(a)+r'\b', title, re.I) for a in ALIASES[symbol]):
            continue
        link = item.findtext('link', '').strip()
        url = urlparse(link)
        if url.scheme != 'https' or url.hostname != 'news.google.com' or not url.path.startswith('/rss/articles/') or url.username:
            continue
        try:
            published = parsedate_to_datetime(item.findtext('pubDate', '')).astimezone(UTC)
        except (ValueError, TypeError, OverflowError):
            continue
        normalized = re.sub(r'\W+', '', title.lower())
        if not now-timedelta(days=7) <= published <= now or normalized in seen:
            continue
        seen.add(normalized)
        rows.append(dict(symbol=symbol, title=title, published_at=published.isoformat(),
                         source_name=plain_text(source.text, 80), source_url=link,
                         source_id='web:'+symbol+':'+hashlib.sha256(link.encode()).hexdigest(),
                         evidence_scope='RSS headline only; original article not retrieved'))
    return sorted(rows, key=lambda r: r['published_at'], reverse=True)[:limit]


def collect(settings, now, topics=None, monitor=False):
    """Each topic succeeds once per ISO week, or retries at most twice six hours apart."""
    topics = topics or TOPICS
    path = settings.database_path.parent/'web-news.sqlite3'
    week = now.astimezone(ZoneInfo('Asia/Bangkok')).strftime('%G-W%V')+':filter2'
    if monitor:
        week = 'monitor:'+str(int(now.timestamp())//14400)
    result = {}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS feeds (week TEXT, symbol TEXT, at REAL, attempts INTEGER, status TEXT, items TEXT, PRIMARY KEY(week,symbol))')
    for symbol, query in topics.items():
        with sqlite3.connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT at,attempts,status,items FROM feeds WHERE week=? AND symbol=?', (week, symbol)).fetchone()
            if row and (row[2] == 'ok' or row[1] >= 2 or now.timestamp()-row[0] < 21600):
                result[symbol] = {'status': row[2], 'items': [i for i in json.loads(row[3]) if significant(i['title'])]}
                continue
            db.execute('INSERT OR REPLACE INTO feeds VALUES(?,?,?,?,?,?)',
                       (week, symbol, now.timestamp(), (row[1] if row else 0)+1, 'failed', '[]'))
        items, status = [], 'failed'
        try:
            with requests.get('https://news.google.com/rss/search', params={
                    'q': query+' when:7d', 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'},
                    timeout=settings.http_timeout, stream=True) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(32768):
                    content.extend(chunk)
                    if len(content) > 2_000_000:
                        raise ValueError('News too large')
                items = parse(bytes(content), symbol, now, limit=10 if monitor else 2)
                status = 'ok'
        except (requests.RequestException, ValueError, ET.ParseError):
            pass
        with sqlite3.connect(path) as db:
            db.execute('UPDATE feeds SET status=?,items=? WHERE week=? AND symbol=?',
                       (status, json.dumps(items, ensure_ascii=False), week, symbol))
        result[symbol] = {'status': status, 'items': items}
    # Make selected sources available to on-demand questions and per-stock news.
    from app.database import Database
    from app.news import NewsItem
    db = Database(settings.database_path, 'live')
    try:
        db.save_news([NewsItem(r['source_id'], symbol, datetime.fromisoformat(r['published_at']),
                              r['title'], '', 'high', 'หัวข่าวเศรษฐกิจ/ธุรกิจที่คัดจากเว็บ',
                              r['source_name']+' · RSS headline', r['source_url'])
                      for symbol, feed in result.items() for r in feed['items']], now)
    finally:
        db.close()
    return result


def news_fallback(coverage):
    parts = ['ข่าวรับสัปดาห์ใหม่ครับ', 'ผมเช็กตลาดกับหุ้นในพอร์ตให้ครบตามรายการแล้ว']
    for symbol, feed in coverage.items():
        label = 'ภาพตลาด' if symbol == 'MARKET' else symbol
        items = feed['items']
        detail = items[0]['title'] if items else ('รอบนี้ดึงข่าวไม่สำเร็จ' if feed['status'] != 'ok' else 'ยังไม่พบหัวข่าวสำคัญผ่านตัวกรองใน 7 วันที่ผ่านมา')
        parts.append(f'{label}\n{detail}')
    return '\n\n'.join(parts)


def news_sources(coverage):
    """Hidden reference records for LINE's labelled source buttons.

    Google News RSS URLs are intentionally long redirect URLs.  Keeping them
    out of the prose makes the weekly note readable; LineNotifier turns these
    records into native, labelled buttons instead.  The records are harmless
    plain text when rendered by the local console.
    """
    return '\n'.join(news_reference(dict(item, symbol=symbol))
                     for symbol, feed in coverage.items() for item in feed['items'][:1])


def news_reference(item):
    """Return one machine-readable, JSON-safe source record for a LINE button."""
    record = {key: str(item.get(key, '')) for key in
              ('symbol', 'title', 'source_name', 'source_url', 'published_at')}
    encoded = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8').hex()
    return '[[NEWS_REFERENCE:'+encoded+']]'
