import json
from dataclasses import replace
from datetime import timedelta

from app.fundamentals import extract, collect, CIKS, current_period_only
from app.decision_memo import create_memo
from app.manager_store import ManagerStore
from app.notifier import line_messages
from app.web_news import news_reference
from app.portfolio import validate_portfolio


def fact(value, start='2025-01-01', end='2025-12-31', filed='2026-02-01'):
    return dict(val=value, start=start, end=end, filed=filed, form='10-K', accn='0001326801-26-000001')


def test_sec_annual_period_currency_future_and_identity(now):
    payload = {'cik': CIKS['META'], 'facts': {'us-gaap': {
        'Revenues': {'units': {'USD': [fact(100), fact(900, start='2025-10-01'), fact(9000, filed='2027-01-01')]}},
        'NetIncomeLoss': {'units': {'USD': [fact(20)]}},
        'NetCashProvidedByUsedInOperatingActivities': {'units': {'USD': [fact(40)]}},
        'PaymentsToAcquirePropertyPlantAndEquipment': {'units': {'EUR': [fact(10)]}}}}}
    values = extract(payload, 'META', now)['metrics']
    assert values['revenue']['value'] == 100
    assert values['net_margin_pct']['value'] == 20
    assert 'free_cash_flow' not in values
    payload['facts']['us-gaap']['PaymentsToAcquirePropertyPlantAndEquipment']['units'] = {'USD': [fact(10)]}
    assert extract(payload, 'META', now)['metrics']['free_cash_flow']['value'] == 30
    import pytest
    with pytest.raises(ValueError):
        extract(payload, 'MSFT', now)


def test_unconfigured_sec_makes_no_network_call(settings, now, monkeypatch):
    monkeypatch.delenv('SEC_CONTACT_EMAIL', raising=False)
    assert collect(settings, ['META'], now)['META']['status'] == 'not_configured'


def test_retired_debt_tag_is_missing_instead_of_current():
    result = current_period_only({'metrics': {
        'revenue': {'value': 100, 'end': '2025-12-31'},
        'current_debt': {'value': 10, 'end': '2016-12-31'}}})
    assert 'current_debt' not in result['metrics']
    assert 'current_debt' in result['missing']


def test_sec_failure_is_cached_for_a_day(settings, now, monkeypatch):
    import requests
    monkeypatch.setenv('SEC_CONTACT_EMAIL', 'test@example.org')
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise requests.ConnectionError()
    monkeypatch.setattr('app.fundamentals.requests.get', fail)
    collect(settings, ['META'], now)
    collect(settings, ['META'], now+timedelta(hours=1))
    assert len(calls) == 1


def test_memo_reuses_analysis_and_preserves_plan(settings, now, monkeypatch):
    settings = replace(settings, mock_mode=False, analyst_mode='codex')
    portfolio = validate_portfolio(dict(as_of='2026-09-23', policy={}, dca={'monthly_total': 1800}, holdings=[
        dict(symbol='META', quantity=1, value_usd=100, gain_pct=0, thesis='test')]))
    report = dict(portfolio=portfolio, stocks=[], news=[])
    original = json.dumps(portfolio)
    monkeypatch.setattr('app.fundamentals.collect', lambda *a: {'META': {'metrics': {}, 'status': 'unavailable'}})
    calls = []
    def analyze(settings, prompt):
        calls.append(prompt)
        return dict(check_more='รอข้อมูลเพิ่มเติม', risks='งบยังไม่ครบ', options='คงแผนที่บันทึกไว้ก่อน')
    monkeypatch.setattr('app.codex_client.analyze', analyze)
    store = ManagerStore(settings.database_path.parent/'manager.sqlite3')
    first = create_memo(settings, store, report, 'แผนลงทุน 1000', now)
    assert first == create_memo(settings, store, report, 'แผนลงทุน 1000', now)
    assert len(calls) == 1 and 'extra_investment_thb' in calls[0]
    assert '1,000.00' in first and json.dumps(portfolio) == original
    assert 'พิมพ์ แผนลงทุน' in create_memo(settings, store, report, 'แผนลงทุน -100', now)


def test_sec_reference_remains_clickable_but_not_raw_url():
    url = 'https://www.sec.gov/Archives/edgar/data/1326801/000132680126000001/0001326801-26-000001-index.html'
    text = 'งบที่ใช้\n'+news_reference(dict(symbol='META', source_url=url, title='Annual results', source_name='SEC', published_at='2026-02-01'))
    messages = line_messages(text)
    assert messages[0]['text'] == 'งบที่ใช้'
    assert messages[1]['contents']['contents'][0]['footer']['contents'][0]['action']['uri'] == url
