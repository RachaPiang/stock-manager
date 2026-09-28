import json

import pytest

from app.portfolio import attach_live_valuation, load_portfolio, analyst_context


def test_missing_profile(tmp_path):
    assert load_portfolio(tmp_path) is None


def test_snapshot_calculations_and_context(tmp_path):
    data = {"as_of": "2026-09-23", "policy": {}, "dca": {},
            "holdings": [{"symbol": "META", "value_usd": 110, "gain_pct": 10, "quantity": None, "sector": "Tech"},
                         {"symbol": "V", "value_usd": 90, "gain_pct": -10, "quantity": None, "sector": "Finance"}]}
    (tmp_path / "portfolio-profile.json").write_text(json.dumps(data), encoding="utf-8")
    profile = load_portfolio(tmp_path)
    assert profile["total_usd"] == 200
    assert profile["holdings"][0]["estimated_cost_usd"] == 100
    assert profile["holdings"][0]["weight_pct"] == 55
    assert profile["holdings"][1]["estimated_gain_usd"] == -10
    assert profile["allocations"]["sectors"][0] == {"label": "Tech", "value_usd": 110.0, "weight_pct": 55.0}
    assert profile["holdings"][0]["quantity"] is None
    context = analyst_context(profile, "META")
    assert context["holding"]["symbol"] == "META"
    assert "holdings" not in context
    assert "not_live" in context["status"]


@pytest.mark.parametrize("value,gain", [(0, 1), (100, -100), (float("inf"), 0)])
def test_invalid_snapshot(tmp_path, value, gain):
    (tmp_path / "portfolio-profile.json").write_text(json.dumps({"holdings": [
        {"symbol": "META", "value_usd": value, "gain_pct": gain}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_portfolio(tmp_path)


@pytest.mark.parametrize("quantity", [0, -1, float("inf")])
def test_invalid_quantity(tmp_path, quantity):
    (tmp_path / "portfolio-profile.json").write_text(json.dumps({"holdings": [
        {"symbol": "META", "value_usd": 100, "gain_pct": 1, "quantity": quantity}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_portfolio(tmp_path)


def test_live_valuation_uses_shares_without_changing_snapshot(tmp_path):
    data = {"as_of": "2026-09-23", "policy": {}, "dca": {}, "holdings": [
        {"symbol": "META", "value_usd": 100, "gain_pct": 1, "quantity": 2, "sector": "Tech"},
        {"symbol": "V", "value_usd": 100, "gain_pct": 1, "quantity": 1, "sector": "Finance"}]}
    (tmp_path / "portfolio-profile.json").write_text(json.dumps(data), encoding="utf-8")
    profile = load_portfolio(tmp_path)
    attach_live_valuation(profile, {"META": {"price": 75, "as_of": "2026-09-24T00:00:00+00:00"},
                                    "V": {"price": 50, "as_of": "2026-09-24T00:01:00+00:00"}})
    assert profile["total_usd"] == 200
    assert profile["live"]["total_usd"] == 200
    assert profile["holdings"][0]["live_value_usd"] == 150
    assert profile["holdings"][0]["live_weight_pct"] == 75


def test_live_valuation_refuses_partial_prices(tmp_path):
    data = {"as_of": "2026-09-23", "policy": {}, "dca": {}, "holdings": [
        {"symbol": "META", "value_usd": 100, "gain_pct": 1, "quantity": 1}]}
    (tmp_path / "portfolio-profile.json").write_text(json.dumps(data), encoding="utf-8")
    profile = load_portfolio(tmp_path)
    attach_live_valuation(profile, {})
    assert not profile["live"]["complete"]
