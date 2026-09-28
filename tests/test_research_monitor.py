import json
from dataclasses import replace
from datetime import timedelta

from app.manager_store import ManagerStore
from app.research_monitor import initialize, stage, reserve_batch, run_due
from app.filings import parse, add_excerpt, VisibleText
from app.research_context import context, for_model


def news(now, title='Nvidia announces earnings - Reuters', identity='web:new'):
    return dict(source_id=identity,symbol='NVDA',title=title,published_at=now.isoformat(),
                source_name='Reuters',source_url='https://news.google.com/rss/articles/test')


def test_baseline_old_news_repeat_and_next_day(settings,now):
    store=ManagerStore(settings.database_path.parent/'manager.sqlite3')
    initialize(store,now)
    stage(store,[news(now-timedelta(hours=1),'Old earnings')],now)
    assert reserve_batch(store,now)==(None,[])
    stage(store,[news(now+timedelta(minutes=1)),news(now+timedelta(minutes=1),'Nvidia announces earnings - CNBC','web:other')],now+timedelta(minutes=2))
    key,items=reserve_batch(store,now+timedelta(minutes=2))
    assert key and len(items)==1
    again,items=reserve_batch(ManagerStore(store.path),now+timedelta(minutes=3))
    assert again==key and len(items)==1


def test_two_batches_per_day_and_expiry(settings,now):
    store=ManagerStore(settings.database_path.parent/'manager.sqlite3')
    initialize(store,now)
    stage(store,[news(now,'Nvidia earnings '+str(i),'web:'+str(i)) for i in range(8)],now)
    for _ in range(2):
        key,items=reserve_batch(store,now)
        assert len(items)==3
        with store.connect() as db:
            db.execute("UPDATE research_events SET state='queued' WHERE batch=?",(key,))
    assert reserve_batch(store,now)==(None,[])
    assert reserve_batch(store,now+timedelta(hours=49))==(None,[])


def test_sec_identity_times_and_paths(now):
    from app.fundamentals import CIKS
    payload=dict(cik=CIKS['NVDA'],filings={'recent':dict(form=['10-Q','4','8-K'],
        acceptanceDateTime=[now.isoformat()]*3,accessionNumber=['0001045810-26-000001']*3,
        primaryDocument=['nvda.htm','ignored.htm','../../evil.htm'])})
    rows=parse(payload,'NVDA',now)
    assert len(rows)==2 and rows[0]['document_url'].endswith('/nvda.htm')
    assert rows[1]['document_url'] is None
    payload['filings']['recent']['acceptanceDateTime']=[(now+timedelta(days=1)).isoformat()]*3
    assert parse(payload,'NVDA',now)==[]


def test_excerpts_remove_hidden_markup_and_never_fetch_untrusted(settings):
    p=VisibleText();p.feed('<head>secret</head><script>bad</script><ix:hidden>999</ix:hidden><p>Revenue update</p>')
    assert ''.join(p.parts)=='Revenue update'
    item={'document_url':'https://evil.example/steal'}
    assert add_excerpt(settings,item)==item


def test_price_context_never_fetches_and_keeps_missing_explicit(settings,now):
    report={'portfolio':{'live':{'complete':True,'total_usd':1000,'stale':False},'holdings':[
        dict(symbol='NVDA',live_weight_pct=10,live_day_change_usd=-5)],'dca':{'monthly_total':1800}},
        'news':[news(now),news(now-timedelta(days=15),'Expired earnings')]}
    result=context(settings,report,{'NVDA'},now)
    assert len(result['news'])==1 and result['annual_fundamentals']=={}
    assert result['holdings'][0]['live_day_change_usd']==-5
    assert 'source_url' not in for_model(result)['news'][0]


def test_monitor_restart_reuses_saved_message_and_failed_queue(settings,now,monkeypatch):
    settings=replace(settings,mock_mode=False,manager_push_limit=0)
    store=ManagerStore(settings.database_path.parent/'manager.sqlite3')
    initialize(store,now)
    store.set('research-scan-at',str(now.timestamp()))
    stage(store,[news(now)],now)
    calls=[]
    def render(settings,store,key,items,at):
        calls.append(1);store.save_brief(key,'research_alert',at.timestamp(),'saved',{},True)
    monkeypatch.setattr('app.research_monitor.render',render)
    run_due(settings,store,now)
    assert store.pending(now.timestamp()) is None
    run_due(replace(settings,manager_push_limit=8),ManagerStore(store.path),now+timedelta(minutes=1))
    assert len(calls)==1 and store.pending((now+timedelta(minutes=1)).timestamp())['message']=='saved'
    run_due(settings,store,now+timedelta(minutes=2))
    assert len(calls)==1
