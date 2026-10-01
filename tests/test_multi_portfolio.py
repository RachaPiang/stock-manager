import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.portfolio_catalog import PortfolioCatalog
from app.portfolio import attach_live_valuation, load_portfolio, holdings_version
from app.budget_planner import allocate, plan, due_symbols, ai_stock_limit
from app.database import Database
from app.fetcher import MockStockProvider
from app.manager_store import ManagerStore
from app.portfolio_scope import ScopedStore
from app.line_commands import command
from app.report import report_data, write_report


@pytest.fixture
def live(tmp_path):
    watch = tmp_path/'watchlist.json'
    watch.write_text(json.dumps(dict(stocks=[dict(symbol='META'),dict(symbol='V')])),encoding='utf-8')
    profile = dict(as_of='2026-09-01T00:00:00+00:00',policy={},
        dca=dict(day=28,monthly_total=1800,per_stock=900,currency='THB'),holdings=[
            dict(symbol='META',quantity=2,cost_basis_usd=100,value_usd=110,gain_pct=10),
            dict(symbol='V',quantity=1,cost_basis_usd=80,value_usd=90,gain_pct=12.5)])
    (tmp_path/'portfolio-profile.json').write_text(json.dumps(profile),encoding='utf-8')
    return Settings(mock_mode=False,database_path=tmp_path/'live.sqlite3',watchlist_path=watch)


def test_legacy_reads_do_not_migrate_or_change_holdings(live):
    original = (live.database_path.parent/'portfolio-profile.json').read_bytes()
    catalog = PortfolioCatalog(live)
    assert catalog.selected()['id']=='main'
    assert [s.symbol for s in live.stocks()]==['META','V']
    assert load_portfolio(catalog.directory)['holdings'][0]['quantity']==2
    assert not catalog.path.exists()
    assert (catalog.directory/'portfolio-profile.json').read_bytes()==original


def test_shared_ticker_separate_holdings_and_costs(live):
    catalog = PortfolioCatalog(live)
    original = catalog.profile_path('main').read_bytes()
    other = catalog.create('พอร์ตที่สอง')
    catalog.add_stock(other,'META',quantity=3,cost_basis_usd=200)
    catalog.add_stock(other,'AAPL')
    assert catalog.profile_path('main').read_bytes()==original
    assert [s.symbol for s in live.stocks()]==['META','V','AAPL']
    assert [s.symbol for s in replace(live,portfolio_id=other).stocks(selected=True)]==['META','AAPL']
    a,b = load_portfolio(catalog.directory,'main'),load_portfolio(catalog.directory,other)
    quote = dict(price=100,as_of='2026-09-23T15:00:00+00:00')
    attach_live_valuation(a,{'META':quote,'V':quote}); attach_live_valuation(b,{'META':quote})
    assert a['live']['total_usd']==300 and a['live']['gain_usd']==120
    assert b['live']['total_usd']==300 and b['live']['gain_usd']==100
    assert b['total_usd'] is None  # No made-up market snapshot on a new holding.
    assert not json.loads(catalog.profile_path(other).read_text())['dca']['enabled']
    assert catalog.profile_path(other).parent.joinpath('portfolio-reconciliation.jsonl').exists()


def test_watch_only_can_be_promoted_and_dca_requires_explicit_action(live):
    c = PortfolioCatalog(live); other=c.create('Growth')
    c.add_stock(other,'AAPL')
    assert load_portfolio(c.directory,other) is None
    with pytest.raises(ValueError): c.save_dca(other,300)
    c.add_stock(other,'AAPL',quantity=1,cost_basis_usd=100)
    c.save_dca(other,300,27)
    p=load_portfolio(c.directory,other)
    assert len(p['holdings'])==1 and p['dca']['monthly_total']==300 and p['dca']['day']==27
    assert json.loads(c.profile_path('main').read_text())['dca']['monthly_total']==1800


@pytest.mark.parametrize('symbol',['../secret','AAPL/xx','', '1ABC','<script>'])
def test_invalid_ticker_does_not_mutate(live,symbol):
    c=PortfolioCatalog(live)
    with pytest.raises(ValueError): c.add_stock('main',symbol)
    assert not c.path.exists()


@pytest.mark.parametrize('priority',[0,-1,float('nan'),float('inf'),True,10001])
def test_invalid_weight_no_mutation(live,priority):
    c=PortfolioCatalog(live)
    with pytest.raises(ValueError): c.create('invalid',priority)
    assert not c.path.exists()


def test_stale_priority_draft_does_not_overwrite_new_stock(live):
    c=PortfolioCatalog(live); digest=c.digest()
    c.add_stock('main','AAPL')
    with pytest.raises(ValueError,match='เปลี่ยน'):
        c.save_priorities({'main':100},{'main':{'META':100,'V':100}},expected=digest)
    assert 'AAPL' in {s.symbol for s in live.stocks()}


def test_normalized_nested_weights_and_shared_price_budget(live):
    c=PortfolioCatalog(live); other=c.create('Other')
    c.add_stock(other,'META'); c.add_stock(other,'AAPL')
    c.save_priorities({'main':70,other:30},{'main':{'META':1,'V':1},other:{'META':1,'AAPL':1}})
    pw,sw=c.weights()
    assert pw['main']==pytest.approx(.7)
    assert sw==pytest.approx({'META':.5,'V':.35,'AAPL':.15})
    p=plan(live)
    assert sum(s['weight_pct'] for s in p['stocks'])==pytest.approx(100)
    assert len(p['stocks'])==3
    assert p['planned_credits'] <= 760
    assert sum(s['ai_slots'] for s in p['stocks']) <= p['ai_limit']-p['ai_reserve']


def make_large_catalog(live):
    c=PortfolioCatalog(live); other=c.create('Lower priority')
    for i in range(8): c.add_stock('main',f'HIGH{i}')
    for i in range(10): c.add_stock(other,f'LOW{i}')
    value=c.read()
    c.save_priorities({'main':80,other:20},
        {p['id']:{s['symbol']:1 for s in p['stocks']} for p in value['portfolios']})
    return c,other


def test_frequency_favors_priority_without_exceeding_daily_plan(live):
    c,other=make_large_catalog(live)
    start=datetime(2026,9,23,13,30,tzinfo=UTC)
    p=plan(live,start)
    scheduled=dict.fromkeys((s['symbol'] for s in p['stocks']),0)
    for i in range(78):
        for symbol in due_symbols(live,start+timedelta(minutes=5*i)): scheduled[symbol]+=1
    assert scheduled=={s['symbol']:s['price_checks'] for s in p['stocks']}
    assert scheduled['META']>scheduled['LOW0']>=1
    assert sum(scheduled.values())+p['overhead_reserve']<=760
    assert due_symbols(live,start-timedelta(minutes=1))==set()
    assert due_symbols(live,start+timedelta(minutes=390))==set()
    assert 'LOW0' in due_symbols(live,start)  # Initial observation at open.
    assert sum(s['ai_slots'] for s in p['stocks'] if s['symbol'].startswith('LOW'))==1
    assert sum(s['ai_slots'] for s in p['stocks'])==7


def test_early_close_shorter_plan_and_no_queries_after_close(live):
    make_large_catalog(live)
    opened=datetime(2026,11,27,14,30,tzinfo=UTC)
    sums={s.symbol:0 for s in live.stocks()}
    for i in range(42):
        for s in due_symbols(live,opened+timedelta(minutes=i*5)): sums[s]+=1
    assert all(n>=1 for n in sums.values()) and sum(sums.values())<=plan(live,opened)['price_budget']
    assert not due_symbols(live,opened+timedelta(minutes=210))


def test_discrete_ai_slots_rotate_and_fallback_has_no_ai_call(live):
    c,other=make_large_catalog(live)
    today=datetime(2026,9,23,15,tzinfo=UTC)
    p=plan(live,today)
    next_day=plan(live,today+timedelta(days=1))
    assert {s['symbol'] for s in p['stocks'] if s['ai_slots']} != {s['symbol'] for s in next_day['stocks'] if s['ai_slots']}
    blocked=next(s['symbol'] for s in p['stocks'] if not s['ai_slots'])
    assert ai_stock_limit(live,blocked,today)==0
    db=Database(live.database_path,'live')
    assert not db.reserve_ai('blocked',blocked,today,9,ai_stock_limit(live,blocked,today))
    for i in range(9): assert db.reserve_ai(str(i),'__brief__',today,9,9)
    assert not db.reserve_ai('over','META',today,9,1)
    db.close()


@pytest.mark.parametrize('total',[1,3,7,100,1000])
def test_capped_allocation_is_conservative_and_deterministic(total):
    w={'a':.7,'b':.2,'c':.1}; caps={'a':20,'b':30,'c':40}
    first=allocate(total,w,caps,seed='day')
    assert sum(first.values())==min(total,sum(caps.values()))
    assert all(0<=v<=caps[k] for k,v in first.items())
    assert first==allocate(total,w,caps,seed='day')


def test_line_selection_and_dca_confirmations_are_isolated(live):
    c=PortfolioCatalog(live); other=c.create('Other')
    c.add_stock(other,'META',quantity=3,cost_basis_usd=200)
    store=ManagerStore(c.directory/'line-manager.sqlite3')
    now=datetime(2026,9,28,12,tzinfo=UTC)
    assert '2. Other' in command(live,store,'list','พอร์ตทั้งหมด',now)
    assert 'Other' in command(live,store,'choose','เลือกพอร์ต 2',now)
    command(live,store,'done','อัปเดตแล้ว',now)
    assert not store.get('dca_updated')
    assert ScopedStore(store,other).get('dca_updated')=='2026-09'
    draft=command(live,store,'edit','บันทึก META 4 300',now)
    assert '3.0 → 4' in draft
    command(live,store,'choose2','เลือกพอร์ต 1',now)
    assert 'ไม่มีร่าง' in command(live,store,'confirm','ยืนยัน BAD',now)
    assert load_portfolio(c.directory,other)['holdings'][0]['quantity']==3


def test_reports_select_filter_and_generate_all_files_without_network(live):
    c=PortfolioCatalog(live); other=c.create('Other')
    c.add_stock(other,'META',quantity=3,cost_basis_usd=200)
    c.add_stock(other,'AAPL')
    now=datetime(2026,10,1,12,tzinfo=UTC)
    db=Database(live.database_path,'live')
    for s in live.stocks(): db.save_snapshot(replace(MockStockProvider().fetch(s.symbol,now),simulated=False,source='twelvedata'))
    db.close()
    before=c.profile_path('main').read_bytes()
    a=report_data(replace(live,portfolio_id='main'),now)
    b=report_data(replace(live,portfolio_id=other),now)
    assert [s['symbol'] for s in a['stocks']]==['META','V']
    assert [s['symbol'] for s in b['stocks']]==['META','AAPL']
    assert a['portfolio']['holdings'][0]['quantity']==2
    assert b['portfolio']['holdings'][0]['quantity']==3
    path=write_report(live,now)
    assert path.exists()
    for p in a['portfolio_catalog']: assert path.parent.joinpath(p['url']).exists()
    assert c.profile_path('main').read_bytes()==before


def test_observation_basis_does_not_mix_identical_portfolios(live):
    a=load_portfolio(live.database_path.parent,'main'); b=copy.deepcopy(a)
    b['portfolio_id']='abcdefabcdef'
    assert holdings_version(a)!=holdings_version(b)
    legacy=copy.deepcopy(a); legacy.pop('portfolio_id')
    assert holdings_version(a)==holdings_version(legacy)


def test_path_traversal_and_unknown_selection_are_rejected(live):
    c=PortfolioCatalog(live)
    with pytest.raises(ValueError): c.profile_path('../.env')
    with pytest.raises(ValueError): c.select('abcdefabcdef')
    assert not c.path.exists()


def test_empty_portfolio_and_new_holdings_render_without_creating_a_market_database(live):
    c=PortfolioCatalog(live); other=c.create('Empty')
    settings=replace(live,portfolio_id=other)
    assert report_data(settings)['portfolio'] is None
    write_report(settings)
    assert not live.database_path.exists()
    c.add_stock(other,'AAPL',quantity=1,cost_basis_usd=100)
    report=report_data(settings)
    assert report['portfolio']['total_usd'] is None
    assert not report['portfolio']['live']['complete']
    assert report['stocks'][0]['research_coverage']['sec']=='unsupported'
    write_report(settings)
    assert not live.database_path.exists()


def test_confirmation_saves_only_chosen_portfolio(live,monkeypatch):
    c=PortfolioCatalog(live); other=c.create('Other')
    c.add_stock(other,'META',quantity=3,cost_basis_usd=200)
    store=ManagerStore(c.directory/'line-manager.sqlite3')
    monkeypatch.setattr('app.report.write_report',lambda *a:None)
    now=datetime(2026,9,28,12,tzinfo=UTC)
    command(live,store,'choose','เลือกพอร์ต 2',now)
    command(live,store,'draft','บันทึก META 4 300',now)
    draft=json.loads(ScopedStore(store,other).get('holding_draft'))
    assert 'บันทึกยอดรวม' in command(live,store,'confirm','ยืนยัน '+draft['code'],now)
    assert load_portfolio(c.directory,'main')['holdings'][0]['quantity']==2
    assert load_portfolio(c.directory,other)['holdings'][0]['quantity']==4


def test_dca_alert_and_confirmation_do_not_cancel_another_portfolio(live):
    from app.line_webhook import schedule
    c=PortfolioCatalog(live); other=c.create('Other')
    c.add_stock(other,'META',quantity=3,cost_basis_usd=200)
    c.save_dca(other,500)
    store=ManagerStore(c.directory/'line-manager.sqlite3')
    now=datetime(2026,9,28,12,tzinfo=UTC)
    ScopedStore(store,other).set('dca_updated_date','2026-09-28')
    schedule(live,store,now)
    with store.connect() as db:
        pending=[r['id'] for r in db.execute("SELECT * FROM outbox WHERE state='pending'")]
    assert pending==['schedule:dca:2026-09']
    assert 'portfolio:'+other not in pending[0]


def test_scheduled_price_check_fetches_shared_symbol_once(live):
    from app.main import check
    from app.analyst import TemplateAnalyst
    from app.notifier import ConsoleNotifier
    c=PortfolioCatalog(live); other=c.create('Other')
    c.add_stock(other,'META',quantity=3,cost_basis_usd=200)
    c.add_stock(other,'AAPL')
    calls=[]
    class Provider:
        def fetch(self,symbol,now):
            calls.append(symbol)
            return replace(MockStockProvider().fetch(symbol,now),simulated=False,source='twelvedata')
    now=datetime(2026,9,23,15,tzinfo=UTC)
    result=check(live,Provider(),TemplateAnalyst(),ConsoleNotifier(),now,scheduled=True)
    assert result['checked']==3 and not result['errors']
    assert sorted(calls)==['AAPL','META','V']
    check(live,Provider(),TemplateAnalyst(),ConsoleNotifier(),now,scheduled=True)
    assert len(calls)==3  # Persistent five-minute slot guard, not one per portfolio.
    db=Database(live.database_path,'live')
    meta=[json.loads(r['payload']) for r in db.alerts() if json.loads(r['payload'])['symbol']=='META'][0]
    assert {p['id'] for p in meta['affected_portfolios']}=={'main',other}
    assert [p['context']['holding']['quantity'] for p in meta['affected_portfolios']]==[2,3]
    db.close()


def test_weighted_ai_denial_still_delivers_local_alert(live):
    from app.main import check
    from app.notifier import ConsoleNotifier
    # A one-call cap is reserved for manager reviews when there are two portfolios.
    c=PortfolioCatalog(live); other=c.create('Other'); c.add_stock(other,'AAPL')
    live=replace(live,ai_max_calls_per_day=1)
    class AI:
        uses_ai=True
        def summarize(self,payload): raise AssertionError('No AI entitlement left for stock alerts')
    class Provider:
        def fetch(self,symbol,now): return replace(MockStockProvider().fetch(symbol,now),simulated=False,source='twelvedata')
    result=check(live,Provider(),AI(),ConsoleNotifier(),datetime(2026,9,23,15,tzinfo=UTC))
    assert result['sent'] and not result['errors']
    db=Database(live.database_path,'live')
    assert db.connection.execute('SELECT count(*) FROM ai_calls').fetchone()[0]==0
    assert all('แม่แบบ' in r['message'] for r in db.alerts())
    db.close()


def test_both_portfolios_receive_distinct_dca_reminders(live):
    from app.line_webhook import schedule
    c=PortfolioCatalog(live); other=c.create('Other')
    c.add_stock(other,'META',quantity=3,cost_basis_usd=200); c.save_dca(other,500)
    store=ManagerStore(c.directory/'line-manager.sqlite3')
    now=datetime(2026,10,28,12,tzinfo=UTC)
    schedule(live,store,now)
    with store.connect() as db:
        rows=list(db.execute("SELECT id,message FROM outbox WHERE state='pending' ORDER BY id"))
    assert len(rows)==2
    assert {r['id'] for r in rows}=={'schedule:dca:2026-10','schedule:dca:2026-10:portfolio:'+other}
    assert any('Other' in r['message'] and '500' in r['message'] for r in rows)


def test_automatic_period_briefs_are_scoped_without_duplicate_market_fetches(live,monkeypatch):
    from app.scheduled_briefs import run_due
    from app.period_reports import due_periods
    c=PortfolioCatalog(live); other=c.create('Other')
    c.add_stock(other,'META',quantity=3,cost_basis_usd=200)
    store=ManagerStore(c.directory/'line-manager.sqlite3')
    store.set('reports-enabled-at','2026-09-22T00:00:00+00:00')
    now=datetime(2026,10,3,12,tzinfo=UTC)
    periods=due_periods(now)
    dates=sorted({day for p in periods for day in (p['start'],p['end'])})
    close_sync=[]
    def sync(*a): close_sync.append(1)
    monkeypatch.setattr('app.close_sync.sync_close',sync)
    monkeypatch.setattr('app.close_sync.check_close_signals',lambda *a:None)
    monkeypatch.setattr('app.web_news.collect',lambda *a:{'MARKET':{'status':'ok','items':[]}})
    monkeypatch.setattr('app.scheduled_briefs.interpret',lambda *a,**kw:None)
    monkeypatch.setattr('app.research_context.context',lambda *a:{})
    def report(settings,now):
        p=c.entry(settings.portfolio_id,c.read())
        profile=load_portfolio(c.directory,p['id'])
        return dict(portfolio=profile,portfolio_info=p,portfolio_catalog=c.read()['portfolios'],
            stocks=[dict(symbol=h['symbol'],bars=[dict(day=d,close=100+i) for i,d in enumerate(dates)]) for h in profile['holdings']])
    monkeypatch.setattr('app.report.report_data',report)
    run_due(live,store,now)
    assert len(close_sync)==1
    main=store.brief(kind='daily'); second=ScopedStore(store,other).brief(kind='daily')
    assert main and second and 'พอร์ตหลัก' in main['message'] and 'Other' in second['message']
    assert main['key'] != second['key']
    run_due(live,store,now)
    assert len(close_sync)==1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM briefs WHERE kind LIKE 'daily%'").fetchone()[0]==2


def test_gemini_does_not_receive_multi_portfolio_data_without_opt_in(settings,snapshot,monkeypatch):
    from types import SimpleNamespace
    import requests
    from app.analyst import GeminiAnalyst
    from app.config import Stock
    from app.indicators import calculate
    from app.rules import analysis_payload,evaluate
    metrics=calculate([b.close for b in snapshot.bars],snapshot.price,snapshot.previous_close)
    payload=analysis_payload(snapshot,metrics,evaluate(snapshot,metrics,Stock('META'),settings))
    payload.update(affected_portfolios=[dict(name='private-portfolio-name',context={'quantity':999})],
        tracked_in=['private-watchlist-name'],portfolio_context={'private':'cost'},decision_context={'private':'age'})
    captured=[]
    def post(*a,**kw):
        captured.append(kw['json']['input'])
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'status':'completed','output_text':'บทวิเคราะห์ทั่วไป'})
    monkeypatch.setattr(requests,'post',post)
    GeminiAnalyst(settings).summarize(payload)
    assert 'private-' not in captured[0] and 'quantity' not in captured[0]


def test_line_can_show_watch_only_stock_without_faking_holdings(live):
    c=PortfolioCatalog(live); other=c.create('Watch')
    c.add_stock(other,'AAPL'); c.select(other)
    store=ManagerStore(c.directory/'line-manager.sqlite3')
    text=command(live,store,'watch','หุ้น AAPL')
    assert 'ติดตามอย่างเดียว' in text and 'ยังไม่มีราคา' in text
    assert load_portfolio(c.directory,other) is None
