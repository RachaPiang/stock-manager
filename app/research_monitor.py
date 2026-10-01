"""Durable discovery every four hours, max two actionable digests per Thai day."""
import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

log=logging.getLogger(__name__)


def initialize(store,now):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS research_events(id TEXT PRIMARY KEY,published REAL,payload TEXT,state TEXT,batch TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS research_batches(id TEXT PRIMARY KEY,day TEXT,created REAL)')
        db.execute("INSERT OR IGNORE INTO state VALUES('research-enabled-at',?)",(now.isoformat(),))


def stage(store,items,now,catchup_days=7):
    enabled=datetime.fromisoformat(store.get('research-enabled-at'))
    with store.connect() as db:
        for item in items:
            at=datetime.fromisoformat(item['published_at'])
            # SEC accession identity deduplicates revisions only when the SEC says they are the same.
            # Headlines with identical company/title are collapsed across publisher suffixes.
            title=re.sub(r'\s+-\s+[^-]+$','',item['title']).lower()
            key=item['source_id'] if item['source_id'].startswith('sec:') else 'title:'+hashlib.sha256(
                (item['symbol']+at.date().isoformat()+re.sub(r'\W+','',title)).encode()).hexdigest()
            state='pending' if max(enabled,now-timedelta(days=catchup_days))<=at<=now else 'baseline'
            db.execute('INSERT OR IGNORE INTO research_events VALUES(?,?,?,?,NULL)',
                       (key,at.timestamp(),json.dumps(item,ensure_ascii=False),state))
            if state == 'pending':
                # Upgrade previously ignored/expired discoveries within the new
                # lookback; sent/queued/working events retain their identities.
                db.execute("UPDATE research_events SET state='pending',batch=NULL WHERE id=? AND state IN ('baseline','expired')", (key,))


def reserve_batch(store,now,catchup_days=7):
    day=now.astimezone(ZoneInfo('Asia/Bangkok')).date().isoformat()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE research_events SET state='expired' WHERE state IN ('pending','working') AND published<?",((now-timedelta(days=catchup_days)).timestamp(),))
        existing=db.execute("SELECT batch FROM research_events WHERE state='working' ORDER BY published LIMIT 1").fetchone()
        if existing:
            key=existing[0]
        else:
            if db.execute('SELECT count(*) FROM research_batches WHERE day=?',(day,)).fetchone()[0]>=2:
                return None,[]
            rows=db.execute("SELECT id FROM research_events WHERE state='pending' ORDER BY published LIMIT 3").fetchall()
            if not rows:
                return None,[]
            key='research:'+hashlib.sha256('|'.join(r[0] for r in rows).encode()).hexdigest()[:24]
            db.execute('INSERT INTO research_batches VALUES(?,?,?)',(key,day,now.timestamp()))
            db.executemany("UPDATE research_events SET state='working',batch=? WHERE id=?",[(key,r[0]) for r in rows])
        rows=db.execute("SELECT payload FROM research_events WHERE batch=? AND state='working'",(key,)).fetchall()
        return key,[json.loads(r[0]) for r in rows]


def render(settings,store,key,items,now):
    from app.filings import add_excerpt
    from app.report import report_data
    from app.research_context import context, for_model
    from app.scheduled_briefs import interpret
    from app.web_news import news_reference
    from dataclasses import replace
    from app.portfolio_catalog import PortfolioCatalog
    enriched=[add_excerpt(settings,item) if item['source_id'].startswith('sec:') else item for item in items]
    symbols={i['symbol'] for i in items}
    catalog = PortfolioCatalog(settings)
    related = []
    for p in sorted(catalog.read()['portfolios'],key=lambda p:-p['priority']):
        scoped = replace(settings,portfolio_id=p['id'])
        if 'MARKET' in symbols or symbols & {s.symbol for s in scoped.stocks(selected=True)}:
            related.append((p,scoped))
    # Use one interpretation for a shared event, with separate holdings/plans
    # for every affected portfolio. The currently open chat cannot hide another.
    evidence = {}
    affected = []
    for p, scoped in related:
        try:
            current = context(scoped,report_data(scoped,now),symbols,now)
        except (ValueError, OSError, KeyError) as exc:
            log.warning('Research context unavailable for %s (%s)',p['id'],type(exc).__name__)
            affected.append(dict(id=p['id'],name=p['name'],context_unavailable=True))
            continue
        if not evidence:
            evidence = current
        affected.append(dict(id=p['id'],name=p['name'],context={k:v for k,v in current.items()
            if k not in {'news','annual_fundamentals','limitations'}}))
    if not related:
        evidence=context(settings,report_data(settings,now),symbols,now)
    payload=dict(events=enriched,context=evidence,affected_portfolios=affected)
    answer=interpret(settings,key,for_model(payload),now,
        'สรุปเหตุการณ์ใหม่ที่ตรวจพบเป็นไทย check_more=เกิดอะไรขึ้นและหลักฐานจากสำนัก/วันที่ '
        'risks=เกี่ยวข้องกับหุ้น/เหตุผลถือและพอร์ตเราอย่างไร options=สิ่งที่ควรทำหรือรออย่างมีเงื่อนไข '
        'ข่าว RSS เป็นเพียงหัวข่าว ห้ามแต่งรายละเอียดหรือบอกว่าข่าวทำให้ราคาขยับ '
        'เอกสาร SEC อาจอ่านเพียงต้นเอกสารหรือ metadata ห้ามอ้างว่าอ่านทั้งฉบับหรือพบงบดี/แย่ถ้าไม่มีหลักฐาน '
        'งบที่แนบเป็นรายปีที่เก็บไว้ อาจยังไม่ใช่งบฉบับใหม่ที่เพิ่งพบ '
        'ห้ามเดาตัวเลขหรือคำนวณเงินใหม่เอง ห้ามกำหนดวงเงินเพิ่มเพราะไม่ได้ระบุงบ '
        'เสนอเงื่อนไขทบทวน DCA หรือการถือได้ แต่ไม่เปลี่ยนแผนเอง '
        'affected_portfolios เป็นพอร์ตแยกกัน ระบุชื่อพอร์ตเมื่อกล่าวถึงผลกระทบ '
        'ไม่รวมจำนวนหุ้น เงิน DCA หรือคำแนะนำของต่างพอร์ตเข้าด้วยกัน',private=True)
    if answer:
        message='มีเรื่องใหม่ที่ควรดูครับ\n\n'+answer['check_more']+'\n\nเกี่ยวกับพอร์ตเรา\n'+answer['risks']+'\n\nสิ่งที่พิจารณาต่อ\n'+answer['options']
    else:
        message='พบข้อมูลใหม่ครับ (รอบนี้ยังไม่มีบทวิเคราะห์ AI)\n\n'+'\n\n'.join(i['symbol']+' · '+i['title'] for i in enriched)
        message+='\n\nควรอ่านต้นฉบับก่อนพิจารณาเปลี่ยนแผนครับ'
    local_day = now.astimezone(ZoneInfo('Asia/Bangkok')).date()
    delayed = any(datetime.fromisoformat(i['published_at']).astimezone(ZoneInfo('Asia/Bangkok')).date() < local_day
                  or now-datetime.fromisoformat(i['published_at']) >= timedelta(hours=4) for i in items)
    dates = '\n'.join(i['symbol']+' · '+datetime.fromisoformat(i['published_at']).astimezone(
        ZoneInfo('Asia/Bangkok')).strftime('%d/%m/%Y %H:%M น.') for i in items)
    heading = 'ตามเก็บข่าวที่ยังไม่ได้แจ้งครับ\n' if delayed else ''
    message = heading+'เผยแพร่ข่าว (เวลาไทย)\n'+dates+'\n\n'+message
    if len(catalog.read()['portfolios'])>1 and related:
        message = 'เกี่ยวข้องกับพอร์ต: '+' / '.join(p['name'] for p,_ in related)+'\n\n'+message
    message+='\n\nข่าวเว็บอ่านจากหัวข่าว; เอกสาร SEC อ่านได้เฉพาะส่วนที่แนบ ไม่รับรองว่าครบทุกประเด็น'
    message+='\n'+'\n'.join(news_reference(i) for i in enriched)
    store.save_brief(key,'research_alert',now.timestamp(),message,payload,bool(answer))


def run_due(settings,store,now=None):
    now=now or datetime.now(UTC)
    if settings.mock_mode or store.get('paused')=='1' or settings.news_mode=='off':
        return
    initialize(store,now)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        last=db.execute("SELECT value FROM state WHERE key='research-scan-at'").fetchone()
        due=not last or now.timestamp()-float(last[0])>=14400
        if due:
            db.execute("INSERT OR REPLACE INTO state VALUES('research-scan-at',?)",(str(now.timestamp()),))
    if due:
        from app.web_news import collect as news_collect
        from app.filings import collect as filing_collect
        from app.fundamentals import collect as fundamentals_collect
        from app.config import load_watchlist
        from app.database import Database
        from app.news import NewsItem
        symbols=[s.symbol for s in settings.stocks()]
        statuses={}
        for label,fetch in [('news',lambda:news_collect(settings,now,monitor=True)),
                            ('filings',lambda:filing_collect(settings,symbols,now))]:
            try:
                feeds=fetch()
                items=[i for feed in feeds.values() for i in feed['items']]
                stage(store,items,now,settings.manager_catchup_days)
                if label=='filings':
                    db=Database(settings.database_path,'live')
                    try:
                        db.save_news([NewsItem(i['source_id'],i['symbol'],datetime.fromisoformat(i['published_at']),
                            i['title'],'','high','พบการยื่นเอกสาร SEC ยังไม่ประเมินผลกระทบ',i['source_name'],i['source_url']) for i in items],now)
                    finally:
                        db.close()
                statuses[label]={s:f['status'] for s,f in feeds.items()}
            except Exception as exc:
                log.warning('Research %s failed (%s)',label,type(exc).__name__)
                statuses[label]={'error':type(exc).__name__}
        try:
            facts=fundamentals_collect(settings,symbols,now)
            statuses['fundamentals']={s:f['status'] for s,f in facts.items()}
        except Exception as exc:
            statuses['fundamentals']={'error':type(exc).__name__}
        store.set('research-status',json.dumps(dict(at=now.isoformat(),sources=statuses)))
    key,items=reserve_batch(store,now,settings.manager_catchup_days)
    if not key:
        return
    saved=store.brief(key)
    if not saved:
        render(settings,store,key,items,now)
        saved=store.brief(key)
    queued=store.enqueue(key,saved['message'],now.timestamp(),settings.manager_push_limit)
    with store.connect() as db:
        exists=db.execute('SELECT state FROM outbox WHERE id=?',('schedule:'+key,)).fetchone()
        if queued or exists:
            db.execute("UPDATE research_events SET state='queued' WHERE batch=?",(key,))
