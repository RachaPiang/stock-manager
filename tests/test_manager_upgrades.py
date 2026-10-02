import json
import sqlite3
import zipfile
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from app import system_status
from app.backup import allowed, create_backup, restore_backup, validate_archive
from app.company_checks import checks
from app.config import Stock
from app.database import AlreadyRunning, Database, run_lock
from app.fundamentals import CIKS, extract
from app.indicators import Indicators
from app.manager_store import ManagerStore
from app.research_context import for_model
from app.rules import evaluate


def neutral(change=0):
    return Indicators(change, 100, 100, 100, 100, 50, None)


@pytest.mark.parametrize('change,rule,level', [(-5, 'price_drop', 1), (-10, 'price_drop_level2', 2),
    (-15, 'price_drop_level3', 3), (-25, 'price_drop_level3', 3), (8, 'price_rise', 1),
    (16, 'price_rise_level2', 2), (24, 'price_rise_level3', 3)])
def test_price_jump_produces_only_highest_level(snapshot, settings, change, rule, level):
    events = evaluate(snapshot, neutral(change), Stock('META'), settings)
    assert len(events) == 1 and events[0].rule == rule
    assert events[0].evidence['severity_level'] == level


def test_escalation_bypasses_cooldown_but_dedupes_and_never_regresses(snapshot, settings, now):
    db = Database(settings.database_path, 'mock')
    try:
        for change in (-5, -10, -15):
            events = evaluate(snapshot, neutral(change), Stock('META'), settings)
            assert db.eligible(events, now, 24) == events
            key = db.enqueue(events, {}, 'console', 'local', now)
            db.finish(key, 'accepted', now)
            assert not db.eligible(events, now + timedelta(days=5), 24)
        # Milder, previously unobserved levels are also blocked after a jump.
        assert not db.eligible(evaluate(snapshot, neutral(-7), Stock('META'), settings), now, 24)
        recovery = db.price_recovery(snapshot, neutral(-3), settings)
        assert recovery[0].rule == 'price_drop_recovery'
        key = db.enqueue(recovery, {}, 'console', 'local', now)
        assert not db.eligible(recovery, now, 24)
        assert not db.price_recovery(snapshot, neutral(-6), settings)
    finally:
        db.close()


def test_large_first_move_suppresses_later_milder_threshold(snapshot, settings, now):
    db = Database(settings.database_path, 'mock')
    try:
        events = evaluate(snapshot, neutral(-16), Stock('META'), settings)
        key = db.enqueue(events, {}, 'console', 'local', now)
        db.finish(key, 'accepted', now)
        assert not db.eligible(evaluate(snapshot, neutral(-7), Stock('META'), settings), now, 24)
        assert evaluate(snapshot, neutral(-16), Stock('META'), replace(settings, alert_escalation=False))[0].rule == 'price_drop'
    finally:
        db.close()


def row(value, start='2026-04-01', end='2026-06-30', filed='2026-08-01', form='10-Q'):
    return dict(val=value, start=start, end=end, filed=filed, form=form, accn='0001326801-26-000001')


def company(rows):
    return dict(cik=CIKS['META'], facts={'us-gaap': rows})


def units(*rows, currency='USD'):
    return {'units': {currency: list(rows)}}


def test_quarter_yoy_matches_periods_and_never_treats_ytd_as_quarter(now):
    prior = dict(start='2025-04-01', end='2025-06-30', filed='2025-08-01')
    data = company({'Revenues': units(row(120), row(100, **prior), row(999, start='2026-01-01')),
                    'NetIncomeLoss': units(row(12), row(20, **prior)),
                    'NetCashProvidedByUsedInOperatingActivities': units(row(1000, start='2026-01-01'))})
    value = extract(data, 'META', now)
    q = value['latest_quarter']
    assert q['metrics']['revenue']['value'] == 120
    assert q['trends']['revenue_yoy_pct'] == pytest.approx(20)
    assert q['trends']['net_income_yoy_pct'] == pytest.approx(-40)
    assert q['trends']['net_margin_change_pp'] == -10
    assert 'operating_cash' not in q['metrics']
    assert value['metrics'] == {}  # Quarterly facts do not leak into annual facts.
    observations = checks(value, {'focus': ['กำไรและกระแสเงินสด']}, now)
    assert any(i['status'] == 'review' for i in observations)


def test_quarter_derived_metrics_need_matching_start_end_currency(now):
    data = company({'Revenues': units(row(100)), 'NetIncomeLoss': units(row(20, start='2026-03-25')),
        'NetCashProvidedByUsedInOperatingActivities': units(row(30)),
        'PaymentsToAcquirePropertyPlantAndEquipment': units(row(10), currency='EUR')})
    metrics = extract(data, 'META', now)['latest_quarter']['metrics']
    assert 'net_margin_pct' not in metrics and 'free_cash_flow' not in metrics


def test_no_growth_percent_with_negative_base_or_changed_currency(now):
    prior = dict(start='2025-04-01', end='2025-06-30', filed='2025-08-01')
    data = company({'NetIncomeLoss': units(row(5), row(-5, **prior)),
                    'Revenues': {'units': {'USD': [row(100)], 'EUR': [row(90, **prior)]}}})
    trends = extract(data, 'META', now)['latest_quarter']['trends']
    assert 'net_income_yoy_pct' not in trends and 'revenue_yoy_pct' not in trends


def test_future_filings_and_instant_only_facts_do_not_create_fake_quarter(now):
    data = company({'Revenues': units(row(100, filed='2027-01-01')),
                    'CashAndCashEquivalentsAtCarryingValue': units({k: v for k, v in row(30).items() if k != 'start'})})
    assert extract(data, 'META', now)['latest_quarter'] is None


def test_dynamic_identity_is_still_verified(now):
    data = company({'Revenues': units(row(100))})
    assert extract(data, 'NEW', now, CIKS['META'])['latest_quarter']
    with pytest.raises(ValueError):
        extract(data, 'NEW', now, 1)


def test_model_context_is_bounded_and_has_no_nested_long_source_urls(now):
    value = extract(company({'Revenues': units(row(100))}), 'META', now)
    value['quarters'] = value['quarters'] * 12
    result = for_model(value)
    assert 'quarters' not in result and len(result['quarter_history']) == 4
    assert 'source_url' not in json.dumps(result)


def test_status_tracks_real_process_and_stops_without_network(tmp_path):
    with system_status.heartbeat(tmp_path, 'market') as pulse:
        pulse.mark('ราคา', 'ok', 'ตรวจแล้ว')
        status = system_status.workers(tmp_path)
        assert status['market']['state'] == 'running'
        assert status['market']['jobs']['ราคา']['state'] == 'ok'
    assert system_status.workers(tmp_path)['market']['state'] == 'stopped'


@pytest.mark.parametrize('identity,state', [(None, 'stopped'), ('other-process', 'stopped'), ('unknown', 'stale')])
def test_stale_or_reused_pid_is_not_reported_running(tmp_path, monkeypatch, identity, state):
    with system_status.heartbeat(tmp_path, 'line'):
        monkeypatch.setattr(system_status, 'process_identity', lambda pid: identity)
        assert system_status.workers(tmp_path)['line']['state'] == state


def test_stale_heartbeat_and_no_status_are_distinct(tmp_path):
    assert system_status.workers(tmp_path)['line']['state'] == 'unknown'
    with system_status.heartbeat(tmp_path, 'line'):
        assert system_status.workers(tmp_path, now=10**12)['line']['state'] == 'stale'


def test_status_reads_queues_without_mutating_databases(settings, now):
    db = Database(settings.database_path, 'mock')
    run = db.start_run(now)
    db.finish_run(run, now, 2, 0, 1)
    db.close()
    store = ManagerStore(settings.database_path.parent / 'line-manager.sqlite3')
    store.enqueue('test', 'hello', now.timestamp(), 8)
    status = system_status.read_summary(settings.database_path)
    assert status['last_run'][3] == 1 and status['manager_pending'] == 1


def setup_root(tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/watchlist.json').write_text('{"stocks":[{"symbol":"META"}]}', encoding='utf-8')
    (tmp_path / 'data/portfolio-profile.json').write_text('{"quantity":1}', encoding='utf-8')
    (tmp_path / '.env').write_text('STOCK_API_KEY=very-private\n', encoding='utf-8')
    return tmp_path


def test_backup_includes_committed_wal_and_excludes_secrets_generated_files(tmp_path):
    root = setup_root(tmp_path)
    (root / 'data/report.html').write_text('private chart')
    (root / 'data/test-run-1').mkdir()
    (root / 'data/test-run-1/test.json').write_text('{}')
    source = sqlite3.connect(root / 'data/live.sqlite3')
    source.execute('PRAGMA journal_mode=WAL')
    source.execute('CREATE TABLE sample(value INTEGER)')
    source.execute('INSERT INTO sample VALUES(42)')
    source.commit()
    archive = root / 'backup.zip'
    create_backup(root, archive)
    with zipfile.ZipFile(archive) as z:
        validate_archive(z)
        assert set(z.namelist()) == {'manifest.json', 'data/live.sqlite3', 'data/portfolio-profile.json', 'config/watchlist.json'}
        saved = root / 'snapshot.sqlite3'
        saved.write_bytes(z.read('data/live.sqlite3'))
    with sqlite3.connect(saved) as db:
        assert db.execute('SELECT value FROM sample').fetchone()[0] == 42
    source.close()
    with pytest.raises(ValueError):
        create_backup(root, archive)


def test_restore_has_safety_copy_keeps_keys_and_cancels_old_pending_work(tmp_path, now):
    root = setup_root(tmp_path)
    db = Database(root / 'data/live.sqlite3', 'live')
    from app.rules import Event
    db.enqueue([Event('one', 'META', 'price_drop', '2026-09-23', 'test', {})], {}, 'line', 'owner', now)
    db.close()
    store = ManagerStore(root / 'data/line-manager.sqlite3')
    store.enqueue('old', 'old message', now.timestamp(), 8)
    archive = root / 'backup.zip'
    create_backup(root, archive)
    (root / 'data/portfolio-profile.json').write_text('{"quantity":2}')
    safety = restore_backup(root, archive)
    assert safety.exists()
    assert json.loads((root / 'data/portfolio-profile.json').read_text())['quantity'] == 1
    assert 'very-private' in (root / '.env').read_text()
    with zipfile.ZipFile(safety) as old:
        assert json.loads(old.read('data/portfolio-profile.json'))['quantity'] == 2
    with sqlite3.connect(root / 'data/live.sqlite3') as db:
        assert db.execute('SELECT status FROM notifications').fetchone()[0] == 'cancelled'
    assert store.pending(now.timestamp()) is None


@pytest.mark.parametrize('name', ['../.env', 'data/../../.env', 'C:/secret.json', '/data/p.json', 'data\\p.json',
                                 '.env', 'data/runtime/line.json', 'data/backup-old.json', 'data/test-run-a/a.json'])
def test_unsafe_or_generated_backup_paths_are_rejected(name):
    assert not allowed(name)


def test_restore_refuses_live_worker_and_active_lock(tmp_path):
    root = setup_root(tmp_path)
    archive = root / 'backup.zip'
    create_backup(root, archive)
    with system_status.heartbeat(root / 'data', 'market'):
        with pytest.raises(ValueError):
            restore_backup(root, archive)
    with run_lock(root / 'data/live.lock'):
        with pytest.raises(AlreadyRunning):
            restore_backup(root, archive)


def test_restore_checks_digest_before_changing_anything(tmp_path):
    root = setup_root(tmp_path)
    archive = root / 'backup.zip'
    create_backup(root, archive)
    with zipfile.ZipFile(archive) as z:
        entries = {name: z.read(name) for name in z.namelist()}
    entries['data/portfolio-profile.json'] = b'{"quantity":999}'
    bad = root / 'bad.zip'
    with zipfile.ZipFile(bad, 'w') as z:
        for name, content in entries.items():
            z.writestr(name, content)
    with pytest.raises(ValueError):
        restore_backup(root, bad)
    assert json.loads((root / 'data/portfolio-profile.json').read_text())['quantity'] == 1


def test_restore_never_resets_api_ai_budget_or_current_delivered_events(tmp_path, now):
    root = setup_root(tmp_path)
    from app.market_state import MarketState
    market = MarketState(root / 'data/market-api.sqlite3')
    market.reserve(now.timestamp())
    db = Database(root / 'data/live.sqlite3', 'live')
    assert db.reserve_ai('first', 'META', now, 9, 3)
    db.close()
    archive = root / 'backup.zip'
    create_backup(root, archive)
    market.reserve(now.timestamp() + 10)
    db = Database(root / 'data/live.sqlite3', 'live')
    assert db.reserve_ai('second', 'META', now, 9, 3)
    from app.rules import Event
    event = Event('new', 'META', 'price_drop', '2026-09-23', 'test', {})
    key = db.enqueue([event], {}, 'line', 'owner', now)
    db.finish(key, 'accepted', now)
    db.close()
    restore_backup(root, archive)
    db = Database(root / 'data/live.sqlite3', 'live')
    assert db.connection.execute('SELECT count(*) FROM ai_calls').fetchone()[0] == 2
    assert not db.eligible([event], now, 24)
    db.close()
    with market.connect() as connection:
        assert connection.execute('SELECT count(*) FROM requests').fetchone()[0] == 2


def test_old_sec_cache_upgrades_once_and_failed_upgrade_is_not_retried_hourly(settings, now, monkeypatch):
    from app.fundamentals import collect
    import requests
    from contextlib import closing
    path = settings.database_path.parent / 'fundamentals.sqlite3'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE facts(symbol TEXT PRIMARY KEY,attempted REAL,fetched REAL,payload TEXT,error TEXT)')
        db.execute('INSERT INTO facts VALUES(?,?,?,?,NULL)', ('META', now.timestamp(), now.timestamp(), json.dumps({'metrics': {'revenue': {'value': 100, 'end': '2025-12-31'}}})))
    monkeypatch.setenv('SEC_CONTACT_EMAIL', 'test@example.org')
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError()
    monkeypatch.setattr('app.fundamentals.requests.get', fail)
    first = collect(settings, ['META'], now)
    assert first['META']['status'] == 'refresh_failed'
    collect(settings, ['META'], now + timedelta(hours=1))
    assert len(calls) == 1


def test_sec_directory_is_cached_and_unknown_company_is_not_guessed(settings, now, monkeypatch):
    from app.sec_identity import resolve
    monkeypatch.setenv('SEC_CONTACT_EMAIL', 'test@example.org')
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size):
            yield json.dumps({'0': {'ticker': 'NEW', 'cik_str': 1234}}).encode()
    def fetch(*args, **kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setattr('app.sec_identity.requests.get', fetch)
    assert resolve(settings, 'NEW', now) == 1234
    assert resolve(settings, 'OTHER', now + timedelta(hours=1)) is None
    assert len(calls) == 1
