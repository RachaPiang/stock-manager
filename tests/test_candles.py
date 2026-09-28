import sqlite3
from dataclasses import replace

import pytest

from app.database import Database
from app.fetcher import DataError, DailyBar, TwelveDataProvider
from app.report import report_data


@pytest.mark.parametrize('values', [
    dict(open=100, high=90, low=80), dict(open=100, high=120, low=110),
    dict(open=100, high=None, low=90), dict(open=100, high=float('nan'), low=90),
    dict(open=0, high=120, low=90),
])
def test_reject_invalid_ohlc(snapshot, now, values):
    bar = DailyBar(snapshot.bars[-1].day, 105, **values)
    with pytest.raises(DataError):
        replace(snapshot, bars=snapshot.bars[:-1] + (bar,)).validate(now, 96)


def test_provider_preserves_vendor_ohlc(settings, snapshot, now, monkeypatch):
    provider = TwelveDataProvider(settings)
    quote = dict(symbol='META', currency='USD', close='110', previous_close='100', timestamp=int(now.timestamp()))
    history = dict(meta=dict(symbol='META', currency='USD'), values=[
        dict(datetime=b.day.isoformat(), close='105', open='100', high='110', low='95') for b in snapshot.bars])
    monkeypatch.setattr(provider, '_get', lambda endpoint, params: quote if endpoint == 'quote' else history)
    result = provider.fetch('META', now)
    assert (result.bars[-1].open, result.bars[-1].high, result.bars[-1].low, result.bars[-1].close) == (100, 110, 95, 105)


def test_mock_ohlc_roundtrip_into_report(settings, snapshot, now):
    snapshot.validate(now, 96)
    db = Database(settings.database_path, 'mock')
    db.save_snapshot(snapshot)
    db.close()
    bars = report_data(settings, now)['stocks'][0]['bars']
    assert len(bars) == 100
    for row, expected in zip(bars, snapshot.bars):
        assert row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']
        assert row['high'] == expected.high


def test_additive_migration_keeps_old_prices(settings):
    con = sqlite3.connect(settings.database_path)
    con.execute('CREATE TABLE prices (symbol TEXT, day TEXT, source TEXT, close REAL, PRIMARY KEY(symbol,day,source))')
    con.execute("INSERT INTO prices VALUES ('META','2026-09-22','mock',100)")
    con.commit()
    con.close()
    db = Database(settings.database_path, 'mock')
    row = db.connection.execute('SELECT * FROM prices').fetchone()
    assert row['close'] == 100
    assert row['open'] is None and row['high'] is None and row['low'] is None
    db.close()
    Database(settings.database_path, 'mock').close()  # migration is repeatable
