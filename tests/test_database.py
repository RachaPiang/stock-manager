from dataclasses import replace
from datetime import timedelta

import pytest

from app.database import AlreadyRunning, Database, run_lock
from app.rules import Event


def event(key="one", symbol="META", rule="price_drop"):
    return Event(key, symbol, rule, "2026-09-23", "test", {})


def test_ai_budget_persistence(settings, now):
    db = Database(settings.database_path, "mock")
    assert db.reserve_ai("a", "META", now, 2, 1)
    assert not db.reserve_ai("b", "META", now, 2, 1)
    assert not db.reserve_ai("a", "META", now, 99, 99)
    db.close()
    db = Database(settings.database_path, "mock")
    assert not db.reserve_ai("b", "META", now, 2, 1)
    assert db.reserve_ai("c", "NVDA", now, 2, 1)
    assert not db.reserve_ai("d", "MSFT", now, 2, 1)
    assert db.reserve_ai("e", "META", now + timedelta(days=1), 2, 1)
    assert not db.reserve_ai("f", "V", now, 0, 1)
    db.close()


def test_dedupe_persists_and_cooldown_is_per_stock_and_rule(settings, now):
    db = Database(settings.database_path, "mock")
    e = event()
    key = db.enqueue([e], {}, "console", "local", now)
    db.finish(key, "accepted", now)
    db.close()
    db = Database(settings.database_path, "mock")
    assert not db.eligible([e], now + timedelta(days=5), 24)
    other_day = replace(e, key="two", event_date="2026-09-24")
    assert not db.eligible([other_day], now + timedelta(hours=23), 24)
    assert db.eligible([other_day], now + timedelta(hours=24), 24) == [other_day]
    assert len(db.eligible([event("b", "NVDA"), event("c", rule="rsi_low")], now, 24)) == 2
    db.close()


def test_pending_does_not_start_cooldown_but_blocks_duplicate_inflight(settings, now):
    db = Database(settings.database_path, "mock")
    key = db.enqueue([event()], {}, "console", "local", now)
    assert not db.eligible([event("two")], now + timedelta(days=2), 24)
    db.finish(key, "failed", now)
    assert db.eligible([event("two")], now, 24)
    assert not db.eligible([event()], now, 0)
    db.close()


def test_mode_mismatch(settings):
    Database(settings.database_path, "mock").close()
    with pytest.raises(ValueError, match="mode mismatch"):
        Database(settings.database_path, "live")


def test_single_process_lock(tmp_path):
    path = tmp_path / "check.lock"
    with run_lock(path):
        with pytest.raises(AlreadyRunning):
            with run_lock(path):
                pytest.fail("Overlapping run should not enter")
    with run_lock(path):
        pass


def test_price_history_upsert(settings, snapshot):
    db = Database(settings.database_path, "mock")
    db.save_snapshot(snapshot)
    db.save_snapshot(snapshot)
    assert db.connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 100
    assert db.connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    db.close()
