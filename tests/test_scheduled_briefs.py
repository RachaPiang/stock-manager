from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.manager_store import ManagerStore
from app.period_reports import due_periods, period_numbers, render_period
from app.scheduled_briefs import run_due
from app.notifier import text_messages, line_messages


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def test_close_deadline_dst_weekend_and_holidays():
    before = due_periods(at('2026-09-25T20:29:00'))
    assert not any(p['end'] == '2026-09-25' for p in before)
    after = due_periods(at('2026-09-25T20:30:00'))
    week = next(p for p in after if p['kind'] == 'weekly')
    assert (week['start'], week['end']) == ('2026-09-18', '2026-09-25')
    early = due_periods(at('2026-11-27T18:30:00'))
    assert any(p['kind'] == 'weekly' and p['end'] == '2026-11-27' for p in early)
    holiday = due_periods(at('2026-07-02T20:30:00'))
    assert any(p['kind'] == 'weekly' and p['end'] == '2026-07-02' for p in holiday)
    winter = due_periods(at('2026-12-01T21:29:00'))
    assert not any(p['end'] == '2026-12-01' for p in winter)
    month = next(p for p in due_periods(at('2026-05-29T20:30:00')) if p['kind'] == 'monthly')
    assert (month['start'], month['end']) == ('2026-04-30', '2026-05-29')
    assert due_periods(at('2030-01-01T21:30:00')) == []


def report_fixture():
    return {'portfolio': {'holdings': [{'symbol': 'META', 'quantity': 2}, {'symbol': 'V', 'quantity': 1}]},
            'stocks': [{'symbol': 'META', 'bars': [{'day': '2026-09-24', 'close': 100}, {'day': '2026-09-25', 'close': 110}]},
                       {'symbol': 'V', 'bars': [{'day': '2026-09-24', 'close': 200}, {'day': '2026-09-25', 'close': 180}]}]}


def test_period_uses_exact_closes_and_never_counts_deposits_as_profit():
    report = report_fixture()
    period = {'start': '2026-09-24', 'end': '2026-09-25'}
    result = period_numbers(report, period)
    assert result['change_pct'] == 0 and result['end_value'] == 400
    assert [r['contribution_usd'] for r in result['rows']] == [20, -20]
    assert 'จำนวนหุ้นปัจจุบันคงที่' in render_period(result, 'daily')
    report['stocks'][1]['bars'][0]['day'] = '2026-09-23'
    result = period_numbers(report, period)
    assert not result['complete'] and result['missing'] == ['V']
    assert result['change_pct'] is None and result['end_value'] is None


def test_close_reports_durable_and_no_extra_ai_on_retry(settings, tmp_path, monkeypatch):
    settings = replace(settings, mock_mode=False, analyst_mode='codex')
    store = ManagerStore(tmp_path/'manager.sqlite3')
    store.set('reports-enabled-at', '2026-09-25T00:00:00+00:00')
    report = report_fixture()
    for stock in report['stocks']:
        stock['bars'].append({'day': '2026-09-18', 'close': 100})
    monkeypatch.setattr('app.close_sync.sync_close', lambda *a: None)
    monkeypatch.setattr('app.report.report_data', lambda *a: report)
    calls = []
    def analyze(*args):
        calls.append(1)
        return dict(check_more='summary', risks='risk', options='options')
    monkeypatch.setattr('app.codex_client.analyze', analyze)
    now = at('2026-09-25T20:30:00')
    run_due(settings, store, now)
    run_due(settings, ManagerStore(store.path), now+timedelta(minutes=1))
    assert len(calls) == 1  # weekly only; daily is calculated locally
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM briefs').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 2
    assert store.brief(kind='daily')['ai_used'] == 0


def test_review_uses_saved_context_without_urls_or_extra_fetch(settings, monkeypatch):
    from app.scheduled_briefs import portfolio_review
    evidence = dict(news=[dict(symbol='META', title='Saved headline', source_name='Reuters',
        published_at='2026-09-25T12:00:00+00:00', source_url='https://news.google.com/rss/articles/test')],
        annual_fundamentals={}, investor_profile={'horizon': 'long-term'})
    monkeypatch.setattr('app.research_context.context', lambda *a: evidence)
    calls = []
    def interpret(*args):
        calls.append(args)
        return dict(check_more='ภาพรวม', risks='ความเสี่ยง', options='รอดูหลักฐาน')
    monkeypatch.setattr('app.scheduled_briefs.interpret', interpret)
    message, payload, used = portfolio_review(settings, report_fixture(), {'complete': True},
                                               'weekly:test', at('2026-09-25T20:30:00'))
    assert used and len(calls) == 1
    assert calls[0][2]['decision_context']['news'][0]['title'] == 'Saved headline'
    assert 'source_url' not in calls[0][2]['decision_context']['news'][0]
    assert payload['decision_context']['news'][0]['source_url']
    assert 'แหล่งข้อมูลประกอบรีวิว' in message


def test_review_cache_failure_and_ai_limit_still_produce_report(settings, monkeypatch):
    import sqlite3
    from app.scheduled_briefs import portfolio_review
    def broken(*args):
        raise sqlite3.DatabaseError('unavailable')
    monkeypatch.setattr('app.research_context.context', broken)
    monkeypatch.setattr('app.scheduled_briefs.interpret', lambda *a: None)
    message, payload, used = portfolio_review(settings, report_fixture(), {'complete': True},
                                               'monthly:test', at('2026-09-25T20:30:00'))
    assert not used and payload['context_unavailable']
    assert 'ยังไม่พร้อม' in message


def test_missing_close_waits_then_publishes_gap_once(settings, tmp_path, monkeypatch):
    settings = replace(settings, mock_mode=False)
    store = ManagerStore(tmp_path/'manager.sqlite3')
    store.set('reports-enabled-at', '2026-09-24T00:00:00+00:00')
    monkeypatch.setattr('app.close_sync.sync_close', lambda *a: None)
    monkeypatch.setattr('app.report.report_data', lambda *a: {'portfolio': {'holdings': [{'symbol': 'META'}]}, 'stocks': []})
    now = at('2026-09-24T20:30:00')
    run_due(settings, store, now)
    assert store.brief(kind='daily') is None
    run_due(settings, store, now+timedelta(hours=2))
    assert 'ราคาปิดยังไม่ครบ: META' in store.brief(kind='daily')['message']


def test_long_thai_text_and_sources_survive_line_delivery():
    message = ('ทดสอบ🙂\n\n'*800)+'https://news.google.com/rss/articles/'+('x'*1800)
    chunks = text_messages(message)
    assert len(chunks) > 1
    assert ''.join(c['text'] for c in chunks) == message
    assert all(len(c['text'].encode('utf-16-le'))//2 <= 4500 for c in chunks)


def test_news_sources_are_labelled_buttons_not_visible_redirect_urls():
    from app.web_news import news_reference
    url = 'https://news.google.com/rss/articles/test-link'
    message = 'สรุปข่าวสั้น ๆ\n\nกดปุ่มด้านล่าง\n'+news_reference({
        'symbol': 'NVDA', 'title': 'Nvidia reports a material update',
        'source_name': 'Reuters', 'source_url': url, 'published_at': '2026-09-25T00:00:00+00:00'})
    messages = line_messages(message)
    assert len(messages) == 2
    assert url not in messages[0]['text']
    button = messages[1]['contents']['contents'][0]['footer']['contents'][0]['action']
    assert button == {'type': 'uri', 'label': 'เปิดแหล่งข่าว', 'uri': url}


def test_saved_legacy_weekly_source_is_also_cleaned_up():
    url = 'https://news.google.com/rss/articles/old-link'
    messages = line_messages('สรุปเดิม\n\nNVDA · Reuters · 2026-09-25\n'+url)
    assert messages[0]['text'] == 'สรุปเดิม'
    assert messages[1]['contents']['contents'][0]['footer']['contents'][0]['action']['uri'] == url


def test_new_install_does_not_push_old_reports(settings, tmp_path):
    store = ManagerStore(tmp_path/'manager.sqlite3')
    run_due(replace(settings, mock_mode=False), store, at('2026-09-25T12:00:00'))
    assert store.brief(kind='daily') is None
