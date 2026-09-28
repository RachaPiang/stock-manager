from datetime import UTC, datetime, timedelta

import pytest

from app.market import is_open
from app.market_state import MarketState, BudgetExceeded


@pytest.mark.parametrize("stamp,expected", [
    ("2026-09-23T13:29:59+00:00", False),
    ("2026-09-23T13:30:00+00:00", True),
    ("2026-09-23T19:59:59+00:00", True),
    ("2026-09-23T20:00:00+00:00", False),
    ("2026-09-26T15:00:00+00:00", False),
    ("2026-11-26T16:00:00+00:00", False),
    ("2026-11-27T17:59:59+00:00", True),
    ("2026-11-27T18:00:00+00:00", False),
    ("2026-12-24T18:00:00+00:00", False),
    ("2026-12-23T14:29:59+00:00", False),
    ("2026-12-23T14:30:00+00:00", True),
    ("2027-03-26T15:00:00+00:00", False),
    ("2028-07-03T17:00:00+00:00", False),
    ("2029-01-02T16:00:00+00:00", False),
])
def test_calendar(stamp, expected):
    assert is_open(datetime.fromisoformat(stamp)) is expected


def test_quota_spacing_survives_restart_and_never_reserves_while_waiting(tmp_path):
    path = tmp_path / "state.sqlite3"
    state = MarketState(path)
    assert state.reserve(1000) == 0
    assert MarketState(path).reserve(1001) == 7
    assert state.reserve(1008, spacing=0) == 0  # minimum eight seconds cannot be disabled
    assert state.reserve(1009) == 7


def test_daily_cap_and_utc_reset(tmp_path):
    state = MarketState(tmp_path / "state.sqlite3")
    now = datetime(2026, 9, 23, 20, tzinfo=UTC).timestamp()
    db = state.connect()
    with db:
        db.executemany("INSERT INTO requests VALUES (?)", [(now-100,)] * 760)
    db.close()
    with pytest.raises(BudgetExceeded):
        state.reserve(now)
    assert state.reserve(datetime(2026, 9, 24, tzinfo=UTC).timestamp()) == 0


def test_history_is_per_symbol_and_new_york_day(tmp_path):
    path = tmp_path / "state.sqlite3"
    state = MarketState(path)
    state.save_history("META", "2026-09-23", {"values": [1]})
    assert MarketState(path).history("META", "2026-09-23") == {"values": [1]}
    assert state.history("META", "2026-09-24") is None
    assert state.history("NVDA", "2026-09-23") is None


def test_one_scheduled_run_per_slot_persists(tmp_path):
    path = tmp_path / "state.sqlite3"
    now = datetime(2026, 9, 23, 14, tzinfo=UTC)
    assert MarketState(path).claim_slot(now)
    assert not MarketState(path).claim_slot(now + timedelta(seconds=100))
    assert MarketState(path).claim_slot(now + timedelta(minutes=5))


def test_no_reservation_when_market_closes(tmp_path):
    state = MarketState(tmp_path / "state.sqlite3")
    with pytest.raises(BudgetExceeded, match="Outside"):
        state.acquire(8, allowed=lambda: False)


def test_full_day_budget_math():
    # 6.5 hours / 5 minutes = 78 rounds; 9 daily histories fetched just once.
    assert (390 // 5) * 9 + 9 == 711


def test_concurrent_process_style_reservations_share_one_slot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    state = MarketState(tmp_path / "state.sqlite3")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: state.reserve(1000), range(4)))
    assert sorted(results) == [0, 8, 8, 8]


def test_scheduled_pipeline_closed_skips_all_work(settings):
    from dataclasses import replace
    from app.main import check
    result = check(replace(settings, mock_mode=False), None, None, None,
                   datetime(2026, 9, 26, 15, tzinfo=UTC), scheduled=True)
    assert result == dict(checked=0, queued=0, sent=0, errors=0)
    assert not settings.database_path.exists()


def test_scheduled_pipeline_claimed_slot_skips_all_work(settings):
    from dataclasses import replace
    from app.main import check
    now = datetime(2026, 9, 23, 15, tzinfo=UTC)
    MarketState(settings.database_path.parent / "market-api.sqlite3").claim_slot(now)
    result = check(replace(settings, mock_mode=False), None, None, None, now, scheduled=True)
    assert result == dict(checked=0, queued=0, sent=0, errors=0)
    assert not settings.database_path.exists()
