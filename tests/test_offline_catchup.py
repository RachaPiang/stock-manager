"""Restart/offline scenarios: test the delivery boundary without any real APIs."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta, date

from app.analyst import TemplateAnalyst
from app.close_sync import check_close_signals
from app.database import Database
from app.fetcher import DailyBar, Snapshot
from app.line_webhook import schedule, process_one
from app.manager_store import ManagerStore
from app.period_reports import due_periods
from app.research_monitor import initialize, stage, reserve_batch, run_due as monitor_due
from app.scheduled_briefs import run_due


def at(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def test_latest_daily_before_evening_and_monthly_after_week_offline():
    morning = due_periods(at('2026-09-30T10:00:00+07:00'))
    assert next(p for p in morning if p['kind'] == 'daily')['end'] == '2026-09-28'
    evening = due_periods(at('2026-09-30T19:00:00+07:00'))
    assert next(p for p in evening if p['kind'] == 'daily')['end'] == '2026-09-29'
    resumed = due_periods(at('2026-10-10T19:00:00+07:00'))
    assert next(p for p in resumed if p['kind'] == 'monthly')['end'] == '2026-09-30'
    assert len([p for p in resumed if p['kind'] == 'daily']) == 1


def test_delayed_news_found_after_three_days_and_not_replayed(settings, tmp_path, monkeypatch):
    settings = replace(settings, mock_mode=False, news_mode='press_releases')
    store = ManagerStore(tmp_path/'manager.sqlite3')
    published = at('2026-09-24T12:00:00+07:00')
    now = published+timedelta(days=3)
    initialize(store, published-timedelta(days=1))
    item = dict(source_id='web:test', symbol='NVDA', title='Nvidia announces earnings - Reuters',
                published_at=published.isoformat(), source_name='Reuters', source_url='https://news.google.com/rss/articles/test')
    feeds = {'NVDA': dict(status='ok', items=[item])}
    monkeypatch.setattr('app.web_news.collect', lambda *a, **kw: feeds)
    monkeypatch.setattr('app.filings.collect', lambda *a: {})
    monkeypatch.setattr('app.fundamentals.collect', lambda *a: {})
    monkeypatch.setattr('app.report.report_data', lambda *a: {})
    monkeypatch.setattr('app.research_context.context', lambda *a: {})
    monkeypatch.setattr('app.scheduled_briefs.interpret', lambda *a, **kw: None)
    monitor_due(settings, store, now)
    row = store.pending(now.timestamp())
    assert row and 'ตามเก็บข่าว' in row['message'] and '24/09/2026' in row['message']
    assert 'เริ่มแจ้งย้อนหลัง: 27/09/2026 12:00 น.' in row['message']
    assert 'กำหนดแจ้งเดิม:' not in row['message']
    store.delivered(row['id'])
    monitor_due(settings, ManagerStore(store.path), now+timedelta(hours=5))
    assert store.pending((now+timedelta(hours=5)).timestamp()) is None


def test_news_older_than_window_and_initial_baseline_stay_silent(tmp_path):
    store = ManagerStore(tmp_path/'manager.sqlite3')
    now = at('2026-09-30T19:00:00+07:00')
    initialize(store, now-timedelta(days=10))
    item = dict(source_id='web:old', symbol='NVDA', title='Nvidia earnings',
                published_at=(now-timedelta(days=8)).isoformat())
    stage(store, [item], now)
    assert reserve_batch(store, now) == (None, [])
    fresh_store = ManagerStore(tmp_path/'new.sqlite3')
    initialize(fresh_store, now)
    item['published_at'] = (now-timedelta(hours=1)).isoformat()
    stage(fresh_store, [item], now)
    assert reserve_batch(fresh_store, now) == (None, [])


def test_monday_news_catches_up_on_friday_once(settings, tmp_path, monkeypatch):
    settings = replace(settings, mock_mode=False)
    store = ManagerStore(tmp_path/'manager.sqlite3')
    store.set('reports-enabled-at', '2026-09-22T00:00:00+00:00')
    monkeypatch.setattr('app.close_sync.sync_close', lambda *a: None)
    monkeypatch.setattr('app.report.report_data', lambda *a: {})
    calls = []
    def collect(*args):
        calls.append(1)
        return {'MARKET': {'status': 'ok', 'items': []}}
    monkeypatch.setattr('app.web_news.collect', collect)
    now = at('2026-10-02T19:00:00+07:00')
    run_due(settings, store, now)
    assert store.brief('web-week:2026-09-28')
    run_due(settings, ManagerStore(store.path), now+timedelta(minutes=1))
    assert len(calls) == 1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM outbox WHERE id='schedule:web-week:2026-09-28'").fetchone()[0] == 1


def test_dca_missed_28th_delivered_after_start_then_stops_when_confirmed(settings, tmp_path, monkeypatch):
    settings = replace(settings, mock_mode=False)
    store = ManagerStore(tmp_path/'manager.sqlite3')
    store.set('reports-enabled-at', '2026-09-23T00:00:00+00:00')
    monkeypatch.setattr('app.portfolio.load_portfolio', lambda *a: {'dca': {
        'day': 28, 'monthly_total': 1800, 'per_stock': 200, 'currency': 'THB'}})
    schedule(settings, store, at('2026-09-28T17:59:00+07:00'))
    assert store.pending(at('2026-09-28T17:59:00+07:00').timestamp()) is None
    resumed = at('2026-09-30T19:00:00+07:00')
    schedule(settings, store, resumed)
    with store.connect() as db:
        rows = db.execute('SELECT id,message FROM outbox').fetchall()
    assert {r['id'] for r in rows} == {'schedule:dca:2026-09', 'schedule:update:2026-09'}
    assert 'ตามเก็บ' in rows[0]['message']
    schedule(settings, ManagerStore(store.path), resumed+timedelta(minutes=1))
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 2
    other = ManagerStore(tmp_path/'confirmed.sqlite3')
    other.set('dca_updated_date', '2026-09-29')
    schedule(settings, other, resumed)
    assert other.pending(resumed.timestamp()) is None


def test_unattempted_scheduled_message_survives_shutdown_but_unknown_send_expires(settings, tmp_path):
    store = ManagerStore(tmp_path/'manager.sqlite3')
    now = at('2026-09-25T19:00:00+07:00')
    store.enqueue('test', 'saved report', now.timestamp(), 8)
    original = store.pending(now.timestamp())
    resumed = now+timedelta(days=3)
    row = store.pending(resumed.timestamp())
    assert row['id'] == original['id'] and row['retry_key'] != original['retry_key']
    assert 'saved report' in row['message'] and 'ตามเก็บ' in row['message']
    class Notifier:
        def send(self, *args):
            assert args[1] == row['retry_key']
    process_one(settings, store, Notifier(), {}, resumed)
    assert store.pending((resumed+timedelta(minutes=1)).timestamp()) is None
    store.enqueue('unknown', 'uncertain', resumed.timestamp(), 8)
    store.start_delivery('schedule:unknown')
    assert store.pending((resumed+timedelta(days=2)).timestamp()) is None


def test_offline_backlog_obeys_daily_push_budget(tmp_path):
    store = ManagerStore(tmp_path/'manager.sqlite3')
    now = at('2026-09-25T19:00:00+07:00')
    for index in range(3):
        store.enqueue(str(index), 'saved', now.timestamp(), 8)
    resumed = now+timedelta(days=3)
    for _ in range(2):
        row = store.pending(resumed.timestamp(), daily_limit=2)
        assert row
        store.delivered(row['id'])
    assert store.pending(resumed.timestamp(), daily_limit=2) is None
    assert store.pending((resumed+timedelta(days=1)).timestamp(), daily_limit=2)


def test_incomplete_report_waits_after_resume_not_after_old_deadline(settings, tmp_path, monkeypatch):
    store = ManagerStore(tmp_path/'manager.sqlite3')
    store.set('reports-enabled-at', '2026-09-23T00:00:00+00:00')
    settings = replace(settings, mock_mode=False)
    monkeypatch.setattr('app.close_sync.sync_close', lambda *a: None)
    monkeypatch.setattr('app.report.report_data', lambda *a: {})
    monkeypatch.setattr('app.web_news.collect', lambda *a: {})
    resumed = at('2026-09-30T19:00:00+07:00')
    run_due(settings, store, resumed)
    assert store.brief(kind='daily') is None
    run_due(settings, store, resumed+timedelta(hours=2))
    assert store.brief(kind='daily') and not store.brief(kind='daily')['ai_used']


def test_recovered_close_uses_shared_rules_and_dedup_without_provider_requests(settings, tmp_path, monkeypatch):
    watchlist = tmp_path/'watchlist.json'
    watchlist.write_text('{"stocks":[{"symbol":"META"}]}')
    settings = replace(settings, mock_mode=False, stock_api_key='fake', notifier_mode='line',
                       database_path=tmp_path/'live.sqlite3', watchlist_path=watchlist)
    now = at('2026-09-30T19:00:00+07:00')
    db = Database(settings.database_path, 'live')
    db.save_snapshot(Snapshot('META', 90, 100, at('2026-09-29T20:00:00+00:00'),
        (DailyBar(date(2026,9,28),100), DailyBar(date(2026,9,29),90)), 'twelvedata', price_kind='daily_close'))
    db.close()
    calls = []
    class Notifier:
        channel = 'line'
        recipient = 'test-owner'
        def send(self, *args):
            calls.append(args)
    result = check_close_signals(settings, now, analyst=TemplateAnalyst(), notifier=Notifier())
    assert result['checked'] == 1 and result['sent'] == 1
    assert 'ตามเก็บสัญญาณจากราคาปิดสหรัฐ 2026-09-29' in calls[0][0]
    assert 'ราคาปิด ณ 30/09/2026 03:00 น.' in calls[0][0]
    assert 'ตรวจพบย้อนหลังเมื่อ 30/09/2026 19:00 น.' in calls[0][0]
    again = check_close_signals(settings, now+timedelta(minutes=1), analyst=TemplateAnalyst(), notifier=Notifier())
    assert again['queued'] == 0 and len(calls) == 1


def test_scheduled_times_survive_restart_and_retry_body_is_identical(tmp_path):
    store = ManagerStore(tmp_path/'manager.sqlite3')
    planned = at('2026-09-28T18:00:00+07:00')
    resumed = at('2026-09-30T19:10:00+07:00')
    store.enqueue('dca:test', 'DCA reminder', planned.timestamp(), 8, scheduled_for=planned.timestamp())
    store = ManagerStore(store.path)
    row = store.pending(resumed.timestamp())
    assert 'กำหนดแจ้งเดิม: 28/09/2026 18:00 น.' in row['message']
    assert 'เริ่มแจ้งย้อนหลัง: 30/09/2026 19:10 น.' in row['message']
    assert 'เวลาแจ้งเตือน (ไทย)' in row['message']
    store.start_delivery(row['id'])
    store.failed(row['id'], resumed.timestamp(), True)
    retried = ManagerStore(store.path).pending((resumed+timedelta(minutes=6)).timestamp())
    assert retried['message'] == row['message'] and retried['retry_key'] == row['retry_key']


def test_old_database_migrates_without_losing_unsent_queue_time(tmp_path):
    import sqlite3
    path = tmp_path/'old.sqlite3'
    queued = at('2026-09-28T18:00:00+07:00')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE outbox(id TEXT PRIMARY KEY,message TEXT,at REAL,retry_key TEXT,state TEXT DEFAULT \'pending\',attempts INTEGER DEFAULT 0,next_try REAL DEFAULT 0)')
        db.execute('INSERT INTO outbox(id,message,at,retry_key) VALUES(?,?,?,?)',
                   ('schedule:legacy', 'old reminder', queued.timestamp(), 'original-key'))
    row = ManagerStore(path).pending(at('2026-09-30T19:00:00+07:00').timestamp())
    assert 'เข้าคิวเดิม: 28/09/2026 18:00 น.' in row['message']
    assert 'เริ่มแจ้งย้อนหลัง: 30/09/2026 19:00 น.' in row['message']
    assert 'กำหนดแจ้งเดิม:' not in row['message']  # Old queue time is not a known schedule.
