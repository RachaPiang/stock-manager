import json
from dataclasses import replace

import pytest

from app.alert_policy import effective_settings, policies, validate
from app.config import Settings
from app.portfolio_catalog import PortfolioCatalog


def live(tmp_path):
    watch = tmp_path / 'watchlist.json'
    watch.write_text(json.dumps({'stocks': [{'symbol': 'META'}, {'symbol': 'V'}]}), encoding='utf-8')
    return Settings(mock_mode=False, database_path=tmp_path / 'live.sqlite3', watchlist_path=watch)


def test_per_stock_settings_preserve_defaults_holdings_and_quotas(tmp_path):
    settings = live(tmp_path)
    catalog = PortfolioCatalog(settings)
    catalog.save_alerts('main', 'META', dict(price_drop_pct=7, rsi_low=25), expected=catalog.digest())
    result = effective_settings(settings, 'META')
    assert result.price_drop_pct == 7 and result.rsi_low == 25
    assert result.price_rise_pct == settings.price_rise_pct
    assert result.ai_max_calls_per_day == settings.ai_max_calls_per_day
    assert result.stock_api_key == settings.stock_api_key
    assert effective_settings(settings, 'V').price_drop_pct == 5


def test_shared_stock_uses_most_sensitive_policy_without_duplicate_fetch(tmp_path):
    settings = live(tmp_path)
    c = PortfolioCatalog(settings)
    other = c.create('พอร์ตเสริม')
    c.add_stock(other, 'META')
    c.save_alerts('main', 'META', dict(price_drop_pct=8, rsi_low=25, rsi_high=80))
    c.save_alerts(other, 'META', dict(price_drop_pct=4, rsi_low=35, rsi_high=65))
    policy = effective_settings(settings, 'META')
    assert policy.price_drop_pct == 4 and policy.rsi_low == 35 and policy.rsi_high == 65
    assert effective_settings(replace(settings, portfolio_id='main'), 'META', selected=True).price_drop_pct == 8
    assert len(policies(settings, 'META')) == 2
    assert [s.symbol for s in settings.stocks()].count('META') == 1


@pytest.mark.parametrize('value', [dict(price_drop_pct=0), dict(price_rise_pct=-1), dict(rsi_low=100),
    dict(rsi_low=80, rsi_high=70), dict(price_drop_pct=float('nan')), dict(alert_escalation='true'), dict(api_key='bad')])
def test_invalid_stock_alert_config_is_rejected(value):
    with pytest.raises(ValueError):
        validate(value)


def test_save_rejects_inherited_rsi_conflict_and_stale_form(tmp_path):
    c = PortfolioCatalog(live(tmp_path))
    before = c.digest()
    with pytest.raises(ValueError):
        c.save_alerts('main', 'META', dict(rsi_low=80))
    assert c.digest() == before
    c.save_alerts('main', 'META', dict(price_drop_pct=6))
    with pytest.raises(ValueError):
        c.save_alerts('main', 'META', dict(price_drop_pct=7), expected=before)
    assert c.read()['portfolios'][0]['stocks'][0]['alert_settings']['price_drop_pct'] == 6
