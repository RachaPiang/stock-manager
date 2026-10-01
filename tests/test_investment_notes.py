import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.investment_notes import validate_notes
from app.portfolio_catalog import PortfolioCatalog
from app.portfolio import load_portfolio, holdings_version, analyst_context
from app.report import report_data
from app.research_context import context
from app.review import review_payload


@pytest.fixture
def settings(tmp_path):
    watch = tmp_path / 'watchlist.json'
    watch.write_text(json.dumps({'stocks': [{'symbol': 'META'}]}), encoding='utf-8')
    profile = dict(as_of='2026-09-01T00:00:00+00:00', policy={},
                   investor_profile={'objective': 'เป้าหมายเดิม'},
                   dca=dict(day=28, monthly_total=1800, per_stock=1800, currency='THB'),
                   holdings=[dict(symbol='META', quantity=2, cost_basis_usd=100,
                                  value_usd=110, gain_pct=10, thesis='เหตุผลเดิม')])
    (tmp_path / 'portfolio-profile.json').write_text(json.dumps(profile), encoding='utf-8')
    return Settings(mock_mode=False, database_path=tmp_path / 'live.sqlite3', watchlist_path=watch)


def test_notes_flow_to_reviews_and_alerts_without_changing_finances(settings):
    c = PortfolioCatalog(settings)
    before = c.profile_path().read_bytes()
    basis = holdings_version(load_portfolio(c.directory))
    c.save_notes('main', dict(role='พอร์ตหลัก', objective='ออมระยะยาว', horizon='มากกว่า 5 ปี',
                             constraints='ไม่ใช้เงินฉุกเฉิน', focus=['การกระจุกตัว']))
    c.save_notes('main', dict(rationale='คาดหวังกระแสเงินสด', risks='จับตาการแข่งขัน',
                             review_conditions='ทบทวนเมื่อกำไรชะลอ', focus=['กำไรและกระแสเงินสด']), symbol='META')
    profile = load_portfolio(c.directory)
    payload = review_payload(profile, [])
    assert payload['investment_notes']['constraints'] == 'ไม่ใช้เงินฉุกเฉิน'
    assert payload['investor_profile']['objective'] == 'ออมระยะยาว'
    assert payload['holdings'][0]['thesis'] == 'คาดหวังกระแสเงินสด'
    report = report_data(settings)
    evidence = context(settings, report, {'META'}, datetime.now(UTC))
    assert evidence['holdings'][0]['investment_notes']['risks'] == 'จับตาการแข่งขัน'
    assert evidence['investment_notes']['horizon'] == 'มากกว่า 5 ปี'
    affected = analyst_context(profile, 'META')
    assert affected['investment_notes']['constraints'] == 'ไม่ใช้เงินฉุกเฉิน'
    assert affected['holding']['investment_notes']['risks'] == 'จับตาการแข่งขัน'
    assert c.profile_path().read_bytes() == before
    assert holdings_version(profile) == basis
    assert not (c.directory / 'portfolio-reconciliation.jsonl').exists()


def test_shared_ticker_has_independent_descriptions_and_watch_only_context(settings):
    c = PortfolioCatalog(settings)
    other = c.create('หุ้นซิ่ง')
    c.add_stock(other, 'META')
    c.save_notes('main', {'rationale': 'ถือยาว'}, symbol='META')
    c.save_notes(other, {'role': 'หุ้นซิ่ง / เก็งกำไร', 'horizon': 'หลายวัน–หลายสัปดาห์'})
    c.save_notes(other, {'rationale': 'เฝ้าดูจังหวะ', 'focus': ['ราคาและปริมาณซื้อขาย']}, symbol='META')
    scoped = replace(settings, portfolio_id=other)
    report = report_data(scoped)
    assert report['portfolio'] is None
    evidence = context(scoped, report, {'META'}, datetime.now(UTC))
    assert evidence['holdings'] == []
    assert evidence['investment_notes']['role'] == 'หุ้นซิ่ง / เก็งกำไร'
    assert evidence['monitored_stock_notes'][0]['investment_notes']['rationale'] == 'เฝ้าดูจังหวะ'
    assert load_portfolio(c.directory, 'main')['holdings'][0]['thesis'] == 'ถือยาว'


def test_stale_notes_cannot_overwrite_newer_changes(settings):
    c = PortfolioCatalog(settings)
    digest = c.digest()
    c.save_notes('main', {'notes': 'ฉบับล่าสุด'})
    with pytest.raises(ValueError, match='เปลี่ยน'):
        c.save_notes('main', {'notes': 'ฉบับเก่า'}, expected=digest)
    assert c.selected()['investment_notes']['notes'] == 'ฉบับล่าสุด'


@pytest.mark.parametrize('notes,scope', [
    ({'notes': 'x' * 601}, 'portfolio'),
    ({'rationale': 'x' * 601}, 'stock'),
    ({'rationale': 10}, 'stock'),
    ({'role': 'invalid'}, 'portfolio'),
    ({'focus': ['unknown']}, 'stock'),
    ({'focus': ['ข่าวสำคัญ', 'ข่าวสำคัญ']}, 'stock'),
    ({'quantity': 999}, 'stock'),
])
def test_invalid_notes_rejected_before_writing(settings, notes, scope):
    c = PortfolioCatalog(settings)
    with pytest.raises(ValueError):
        c.save_notes('main', notes, symbol='META' if scope == 'stock' else None)
    assert not c.path.exists()


def test_clear_new_rationale_keeps_legacy_financial_file_intact(settings):
    c = PortfolioCatalog(settings)
    before = c.profile_path().read_bytes()
    c.save_notes('main', {'rationale': ''}, symbol='META')
    assert load_portfolio(c.directory)['holdings'][0]['thesis'] == ''
    assert c.profile_path().read_bytes() == before


def test_legacy_empty_notes_still_valid():
    validate_notes({}, 'portfolio')
    validate_notes({}, 'stock')
