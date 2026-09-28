import json
from datetime import UTC, datetime

from app.portfolio import load_portfolio
from app.portfolio_editor import save_holdings


def test_cost_correction_preserves_quantity_start_and_real_buy_resets_it(tmp_path):
    path = tmp_path/'portfolio-profile.json'
    path.write_text(json.dumps(dict(as_of='2026-09-23', holdings=[dict(
        symbol='META', quantity=1, value_usd=110, gain_pct=10)])), encoding='utf-8')
    at = datetime(2026,9,26,tzinfo=UTC)
    save_holdings(path, {'META': dict(quantity=1,cost_basis_usd=100)}, at)
    assert load_portfolio(tmp_path)['quantity_as_of'] == '2026-09-23'
    save_holdings(path, {'META': dict(quantity=2,cost_basis_usd=210)}, at)
    assert load_portfolio(tmp_path)['quantity_as_of'] == at.isoformat()


def test_legacy_cost_only_audit_recovers_history_boundary(tmp_path):
    path = tmp_path/'portfolio-profile.json'
    path.write_text(json.dumps(dict(as_of='2026-09-23', holdings_as_of='2026-09-26T00:00:00+00:00',
        holdings=[dict(symbol='META',quantity=1,value_usd=110,gain_pct=10,cost_basis_usd=100)])), encoding='utf-8')
    audit = tmp_path/'portfolio-reconciliation.jsonl'
    audit.write_text(json.dumps(dict(at='2026-09-26T00:00:00+00:00',changes=[
        dict(before={'quantity':1},after={'quantity':1})]))+'\n',encoding='utf-8')
    assert load_portfolio(tmp_path)['quantity_as_of'] == '2026-09-23'
