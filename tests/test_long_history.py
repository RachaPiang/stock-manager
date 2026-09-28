from app.fetcher import MockStockProvider, TwelveDataProvider
from app.indicators import calculate
from app.database import Database
from app.report import report_data


def test_sma200_requires_200_prices_and_uses_latest_window():
    assert calculate([100] * 199, 100, 100).sma200 is None
    assert calculate(list(range(1, 201)), 200, 199).sma200 == 100.5
    assert calculate(list(range(1, 301)), 300, 299).sma200 == 200.5


def test_long_history_survives_database_and_report(settings, now):
    snapshot = MockStockProvider(3000).fetch('META', now)
    snapshot.validate(now, 96)
    db = Database(settings.database_path, 'mock')
    db.save_snapshot(snapshot)
    db.close()
    item = report_data(settings, now)['stocks'][0]
    assert len(item['bars']) == 3000
    assert item['bars'][0]['high'] is not None
    assert item['indicators']['sma200'] is not None


def test_short_legacy_cache_is_upgraded_once(settings, now, monkeypatch):
    provider = TwelveDataProvider(settings)
    day = now.date().isoformat()
    snapshot = MockStockProvider().fetch('META', now)
    history = {'meta': {'symbol': 'META', 'currency': 'USD'}, 'values': [
        {'datetime': b.day.isoformat(), 'open': b.open, 'high': b.high, 'low': b.low, 'close': b.close} for b in snapshot.bars]}
    provider.state.save_history('META', day, history)
    calls = []
    def fetch(endpoint, params):
        calls.append(endpoint)
        assert params['outputsize'] == 3000
        return history
    monkeypatch.setattr(provider, '_get', fetch)
    assert len(provider.fetch_history('META', now)) == 100
    assert len(provider.fetch_history('META', now)) == 100  # e.g. new listing: do not refetch forever
    assert calls == ['time_series']
