from dataclasses import replace
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape

import pytest

from app.web_news import parse, collect, TOPICS
from app.manager_store import ManagerStore
from app.scheduled_briefs import run_due


def feed(title='Nvidia reports earnings', source='https://www.reuters.com', link='https://news.google.com/rss/articles/abc', day='Fri, 25 Sep 2026 10:00:00 GMT'):
    return (f'<rss><channel><item><title>{escape(title)}</title><link>{escape(link)}</link>'
            f'<pubDate>{day}</pubDate><source url="{source}">Reuters</source></item></channel></rss>').encode()


def test_filters_source_time_company_and_opinion():
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    assert len(parse(feed(), 'NVDA', now)) == 1
    assert parse(feed(title='Should you buy Nvidia before earnings?'), 'NVDA', now) == []
    assert parse(feed(title='Nvidia vs. AMD: Which AI stock is better?'), 'NVDA', now) == []
    assert parse(feed(title='Build Amazon AI using WhisperX on SageMaker'), 'AMZN', now) == []
    assert parse(feed(title='Amazon reports earnings'), 'NVDA', now) == []
    assert parse(feed(source='https://reuters.com.evil.test'), 'NVDA', now) == []
    assert parse(feed(link='https://evil.test/article'), 'NVDA', now) == []
    assert parse(feed(day='Fri, 18 Sep 2026 10:00:00 GMT'), 'NVDA', now) == []
    with pytest.raises(ValueError):
        parse(b'<!DOCTYPE a><rss/>', 'NVDA', now)
    with pytest.raises(ValueError):
        parse(b'<html>blocked</html>', 'NVDA', now)


def test_weekly_cache_and_persistence_no_market_api(settings, monkeypatch):
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield feed()
    def get(*args, **kwargs):
        calls.append(args[0])
        return Response()
    monkeypatch.setattr('app.web_news.requests.get', get)
    settings = replace(settings, mock_mode=False)
    first = collect(settings, now, {'NVDA': 'Nvidia'})
    assert first['NVDA']['status'] == 'ok'
    assert collect(settings, now+timedelta(hours=2), {'NVDA': 'Nvidia'}) == first
    assert len(calls) == 1
    assert not (settings.database_path.parent/'market-api.sqlite3').exists()


def test_monday_all_symbols_news_once_and_readable_sources(settings, tmp_path, monkeypatch):
    settings = replace(settings, mock_mode=False)
    store = ManagerStore(tmp_path/'manager.sqlite3')
    store.set('reports-enabled-at', '2026-09-28T00:00:00+00:00')
    calls = []
    def collect(*args):
        calls.append(1)
        return {symbol: {'status': 'ok', 'items': []} for symbol in TOPICS}
    monkeypatch.setattr('app.web_news.collect', collect)
    before = datetime(2026, 9, 28, 10, 59, tzinfo=UTC)
    run_due(settings, store, before)
    assert calls == []
    now = before+timedelta(minutes=1)
    run_due(settings, store, now)
    run_due(settings, ManagerStore(store.path), now+timedelta(minutes=1))
    assert len(calls) == 1
    saved = store.brief(kind='news_weekly')
    assert all(s in saved['message'] for s in TOPICS if s != 'MARKET')
    assert 'ยังไม่พบหัวข่าวสำคัญ' in saved['message']
