import json
from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest

from app.portfolio import validate_portfolio, attach_live_valuation, portfolio_history, analyst_context
from app.portfolio_editor import save_holdings
from app.portfolio_tracking import record_observations
from app.database import Database
from app.report import report_data
from app.review import review_payload


def profile():
    return validate_portfolio(dict(as_of='2026-09-23', policy={}, dca={}, holdings=[
        dict(symbol='META', quantity=2, value_usd=110, gain_pct=10, thesis='test'),
        dict(symbol='V', quantity=1, value_usd=90, gain_pct=-10, thesis='test')]))


def test_current_gains_and_day_change_reconcile_with_holdings():
    p=profile()
    quotes={s: dict(price=price, previous_close=prev, as_of='2026-09-23T15:00:00+00:00', stale=False)
            for s,price,prev in [('META',75,70),('V',80,85)]}
    attach_live_valuation(p,quotes)
    assert p['live']['total_usd']==230
    assert p['live']['cost_usd']==200
    assert p['live']['gain_usd']==30
    assert p['live']['gain_pct']==pytest.approx(15)
    assert p['live']['day_change_usd']==5
    assert p['holdings'][0]['live_gain_usd']==50
    assert p['holdings'][1]['live_gain_usd']==-20
    assert sum(h['live_gain_usd'] for h in p['holdings'])==p['live']['gain_usd']
    assert p['holdings'][0]['gain_pct']==10
    assert review_payload(p,[])['holdings'][0]['latest_saved_gain_pct']==50
    assert analyst_context(p,'META',quotes['META'])['holding']['live_gain_pct']==50
    assert 'live_weight_pct' not in analyst_context(p,'META',quotes['META'])['holding']


@pytest.mark.parametrize('price',[None,0,-1,float('nan'),float('inf')])
def test_missing_or_invalid_quotes_never_become_a_partial_total(price):
    p=profile()
    attach_live_valuation(p,{'META':dict(price=75,as_of='2026-09-23T15:00:00Z')})
    assert not p['live']['complete']
    attach_live_valuation(p,{'META':dict(price=price,as_of='2026-09-23T15:00:00Z')})
    assert not p['live']['complete']
    assert 'live_value_usd' not in p['holdings'][0]


def test_unknown_quantity_is_supported_and_stale_mixed_days_are_visible():
    p=profile();p['holdings'][0]['quantity']=None
    attach_live_valuation(p,{'META':dict(price=75,as_of='2026-09-23T15:00:00Z')})
    assert not p['live']['complete']
    assert portfolio_history(p,[])==[]
    p=profile()
    attach_live_valuation(p,{'META':dict(price=75,previous_close=70,as_of='2026-09-23T15:00:00Z',stale=True),
                             'V':dict(price=80,previous_close=75,as_of='2026-09-24T15:00:00Z')})
    assert p['live']['stale'] and p['live']['mixed_times']
    assert 'day_change_usd' not in p['live']


def test_simulated_history_requires_every_symbol_at_same_timestamp():
    stocks=[dict(symbol='META',bars=[dict(day='2026-01-01',close=50),dict(day='2026-01-02',close=55)]),
            dict(symbol='V',bars=[dict(day='2026-01-02',close=100)])]
    assert portfolio_history(profile(),stocks)==[dict(day='2026-01-02',value_usd=210)]
    assert portfolio_history(profile(),stocks[:1])==[]


def test_report_uses_newest_closed_intraday_bar_for_portfolio_and_ai(settings,snapshot,now,tmp_path):
    live=replace(settings,mock_mode=False,stock_provider='mock')
    p=profile();p['holdings']=p['holdings'][:1]
    (tmp_path/'portfolio-profile.json').write_text(json.dumps(p),encoding='utf-8')
    db=Database(live.database_path,'live')
    db.save_snapshot(snapshot)
    latest=now+timedelta(minutes=5)
    with db.connection:
        db.connection.execute('INSERT OR REPLACE INTO intraday VALUES (?,?,?,?,?,?,?)',
                              ('META',now.isoformat(),'mock',199,201,198,200))
    db.close()
    data=report_data(live,latest)
    assert data['stocks'][0]['price']==200
    assert data['portfolio']['live']['total_usd']==400
    assert data['portfolio']['holdings'][0]['live_price_kind']=='5min_close'
    # During an open market, a 20-minute-old saved price is visible but marked stale.
    later=report_data(live,latest+timedelta(minutes=20))
    assert later['portfolio']['live']['stale']
    assert not data['portfolio']['live']['stale']


def test_future_intraday_close_does_not_replace_last_saved_quote(settings,snapshot,now):
    db=Database(settings.database_path,'mock');db.save_snapshot(snapshot)
    with db.connection:
        db.connection.execute('INSERT OR REPLACE INTO intraday VALUES (?,?,?,?,?,?,?)',
                              ('META',now.isoformat(),'mock',199,201,198,200))
    db.close()
    data=report_data(settings,now+timedelta(minutes=2))
    assert data['stocks'][0]['price']==snapshot.price


def test_editor_backs_up_allows_quantity_checkpoint_without_cost(tmp_path):
    path=tmp_path/'portfolio-profile.json';path.write_text(json.dumps(profile()),encoding='utf-8')
    original=path.read_text(encoding='utf-8')
    updates={'META':dict(quantity=3,cost_basis_usd=''),'V':dict(quantity=1,cost_basis_usd='')}
    backup=save_holdings(path,updates,datetime(2026,9,24,12,tzinfo=UTC))
    from pathlib import Path
    assert Path(backup).read_text(encoding='utf-8')==original
    pending=validate_portfolio(json.loads(path.read_text(encoding='utf-8')))
    assert pending['holdings'][0]['cost_source']=='awaiting_broker_total'
    quotes={'META':dict(price=75,as_of='2026-09-23T15:00:00+00:00'), 'V':dict(price=80,as_of='2026-09-23T15:00:00+00:00')}
    attach_live_valuation(pending,quotes)
    assert pending['live']['total_usd']==305 and pending['live']['gain_usd'] is None
    updates['META']['cost_basis_usd']=160
    backup=save_holdings(path,updates,datetime(2026,9,24,12,tzinfo=UTC))
    from pathlib import Path
    assert Path(backup).exists()  # the second backup is the pending checkpoint
    p=validate_portfolio(json.loads(path.read_text(encoding='utf-8')))
    assert p['holdings'][0]['estimated_cost_usd']==160
    assert p['holdings'][0]['cost_source']=='user_reported'
    ledger = tmp_path/'portfolio-reconciliation.jsonl'
    rows = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
    assert rows[-1]['type'] == 'holding_totals_confirmed'
    assert rows[-1]['changes'][0]['symbol'] == 'META'
    assert save_holdings(path,updates) is None


def test_observed_history_is_durable_deduplicated_and_bounded_by_holdings_date(tmp_path):
    path=tmp_path/'live.sqlite3';db=Database(path,'live')
    with db.connection:
        for at in ['2026-09-22T15:00:00+00:00','2026-09-23T15:00:00+00:00']:
            for symbol,price in [('META',75),('V',80)]:
                db.connection.execute('INSERT INTO quotes(symbol,as_of,source,price) VALUES (?,?,?,?)',(symbol,at,'twelvedata',price))
    db.close()
    report=dict(portfolio=profile(),stocks=[dict(source='twelvedata')],generated_at='2026-09-24T16:00:00+00:00')
    first=record_observations(path,report)
    assert len(first)==1 and first[0]['value_usd']==230
    assert record_observations(path,report)==first
    report['portfolio']['holdings_as_of']='2026-09-24T16:00:00+00:00'
    assert record_observations(path,report)==[]
    db=Database(path,'live')
    assert db.connection.execute('SELECT count(*) FROM portfolio_observations').fetchone()[0]==1
    db.close()
