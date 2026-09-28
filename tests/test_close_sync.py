import json
from dataclasses import replace
from datetime import UTC, datetime, date, timedelta

import pytest

from app.close_sync import sync_close
from app.database import Database
from app.fetcher import DailyBar, Snapshot
from app.intraday import IntradayBar
from app.market import completed_session, session_close
from app.report import report_data


@pytest.mark.parametrize('stamp,day,close', [
    ('2026-09-25T02:00:00+00:00', '2026-09-24', '2026-09-24T20:00:00+00:00'),
    ('2026-09-24T20:14:00+00:00', '2026-09-23', '2026-09-23T20:00:00+00:00'),
    ('2026-09-24T20:15:00+00:00', '2026-09-24', '2026-09-24T20:00:00+00:00'),
    ('2026-11-27T18:15:00+00:00', '2026-11-27', '2026-11-27T18:00:00+00:00'),
    ('2026-12-26T03:00:00+00:00', '2026-12-24', '2026-12-24T18:00:00+00:00'),
])
def test_completed_sessions(stamp, day, close):
    actual = completed_session(datetime.fromisoformat(stamp))
    assert actual.isoformat() == day
    assert session_close(actual).isoformat() == close


def live_settings(settings, tmp_path):
    watchlist = tmp_path/'watchlist.json'
    watchlist.write_text(json.dumps({'stocks':[{'symbol':'META'}]}))
    return replace(settings, mock_mode=False, database_path=tmp_path/'live.sqlite3',
                   stock_api_key='fake', watchlist_path=watchlist)


class Provider:
    def __init__(self, complete=True):
        self.calls = 0
        self.complete = complete
    def fetch_history(self, symbol, now):
        self.calls += 1
        return (DailyBar(date(2026,9,23),100), DailyBar(date(2026,9,24),110))
    def fetch_intraday(self, symbol, now):
        self.calls += 1
        return (IntradayBar(datetime(2026,9,24,19,55 if self.complete else 50,tzinfo=UTC),100,111,99,109),)


def test_recovers_close_once_and_updates_report(settings, tmp_path):
    settings = live_settings(settings, tmp_path)
    provider = Provider()
    now = datetime(2026,9,24,20,20,tzinfo=UTC)
    assert sync_close(settings, now, provider)['updated'] == 1
    report = report_data(settings, now)
    stock = report['stocks'][0]
    assert stock['price'] == 110  # Official daily close, not last interval approximation.
    assert stock['as_of'] == '2026-09-24T20:00:00+00:00'
    assert stock['bars'][-1]['day'] == '2026-09-24'
    assert not stock['stale']
    assert sync_close(settings, now+timedelta(hours=5), provider)['updated'] == 0
    assert provider.calls == 2


def test_partial_close_retries_bounded(settings, tmp_path):
    settings = live_settings(settings, tmp_path)
    provider = Provider(False)
    now = datetime(2026,9,25,2,tzinfo=UTC)
    assert sync_close(settings, now, provider)['errors'] == 1
    assert sync_close(settings, now+timedelta(minutes=5), provider)['skipped'] == 1
    assert provider.calls == 2
    for hours in (1,2,3,4):
        sync_close(settings, now+timedelta(hours=hours), provider)
    assert provider.calls == 6  # Maximum 3 attempts for this session/symbol.


def test_missed_close_is_stale_even_when_market_closed(settings, tmp_path):
    settings = live_settings(settings, tmp_path)
    db = Database(settings.database_path, 'live')
    db.save_snapshot(Snapshot('META',100,99,datetime(2026,9,24,15,20,tzinfo=UTC),
                              (DailyBar(date(2026,9,23),99),),'twelvedata'))
    db.close()
    stock = report_data(settings, datetime(2026,9,25,2,tzinfo=UTC))['stocks'][0]
    assert stock['stale'] and 'ราคาปิด' in stock['note']
