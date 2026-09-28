from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.database import Database
from app.fetcher import DataError, TwelveDataProvider
from app.intraday import parse_bars, previous_session_day
from app.report import report_data


def payload(times):
    return {'meta': {'symbol': 'META', 'currency': 'USD', 'interval': '5min', 'exchange_timezone': 'America/New_York'},
            'values': [dict(datetime=at, open='100', high='103', low='99', close='102') for at in times]}


def test_completed_only_and_regular_hours():
    now = datetime(2026, 9, 23, 13, 37, tzinfo=UTC)
    bars = parse_bars(payload(['2026-09-23 13:25:00','2026-09-23 13:30:00','2026-09-23 13:35:00','2026-09-23 14:00:00']), 'META', now)
    assert len(bars) == 1 and bars[0].end.hour == 13 and bars[0].end.minute == 35
    assert bars[0].low == 99 and bars[0].high == 103


@pytest.mark.parametrize('edit', ['duplicate','bad_high','nan','missing','wrong_symbol','misaligned'])
def test_reject_bad_intraday(edit, now):
    p = payload(['2026-09-23 13:30:00'])
    if edit == 'duplicate': p['values'] *= 2
    if edit == 'bad_high': p['values'][0]['high'] = '80'
    if edit == 'nan': p['values'][0]['low'] = 'nan'
    if edit == 'missing': del p['values'][0]['open']
    if edit == 'wrong_symbol': p['meta']['symbol'] = 'OTHER'
    if edit == 'misaligned': p['values'][0]['datetime'] = '2026-09-23 13:31:00'
    with pytest.raises((ValueError, KeyError)):
        parse_bars(p, 'META', now)


def test_early_close_and_offsets():
    now = datetime(2026, 11, 27, 21, tzinfo=UTC)
    bars = parse_bars(payload(['2026-11-27 17:55:00', '2026-11-27 18:00:00']), 'META', now)
    assert len(bars) == 1
    assert previous_session_day(bars[0].at).isoformat() == '2026-11-25'
    assert previous_session_day(datetime(2026, 9, 8, 15, tzinfo=UTC)).isoformat() == '2026-09-04'


def test_poll_uses_intraday_instead_of_extra_quote(settings, snapshot, now, monkeypatch):
    provider = TwelveDataProvider(settings, intraday_prices=True)
    daily = {'meta': {'symbol': 'META', 'currency': 'USD'}, 'values': [
        dict(datetime=b.day.isoformat(), open=b.open, high=b.high, low=b.low, close=b.close) for b in snapshot.bars]}
    calls = []
    def get(endpoint, params):
        assert endpoint == 'time_series'
        calls.append(params['interval'])
        return daily if params['interval'] == '1day' else payload(['2026-09-23 14:55:00'])
    monkeypatch.setattr(provider, '_get', get)
    result = provider.fetch('META', now)
    assert result.price_kind == '5min_close' and result.price == 102
    assert result.previous_close == snapshot.bars[-1].close and result.as_of == now
    provider.fetch('META', now)
    assert calls == ['1day','5min','5min']
    with pytest.raises(DataError, match='stale'):
        provider.fetch('META', now+timedelta(minutes=21))


def test_intraday_report_without_fabricating_quote(settings, now):
    db = Database(settings.database_path, 'mock')
    bars = parse_bars(payload(['2026-09-23 14:55:00']), 'META', now)
    db.save_intraday('META', 'mock', bars)
    db.close()
    item = report_data(settings, now)['stocks'][0]
    assert item['price'] == 102.0
    assert item['price_kind'] == '5min_close'  # Actual saved close, explicitly labelled, never a fabricated quote.
    assert len(item['intraday']) == 1
    assert item['intraday'][0]['day'] == '2026-09-23'
    assert item['intraday'][0]['high'] == 103
