"""Durable close/weekly/monthly reports and Monday web news, owned by the bot."""
import json
import logging
import re
import sqlite3
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.database import Database, AlreadyRunning
from app.config import load_watchlist
from app.period_reports import due_periods, period_numbers, render_period
from app.voice import STYLE, answer_text

log = logging.getLogger(__name__)
BANGKOK = ZoneInfo('Asia/Bangkok')


def portfolio_review(settings, report, numbers, key, now):
    """Replace the period's existing AI call with a cached-evidence review."""
    from app.research_context import context, for_model, source_buttons
    symbols = {h['symbol'] for h in (report.get('portfolio') or {}).get('holdings', [])}
    payload = {'period': numbers}
    try:
        payload['decision_context'] = context(settings, report, symbols | {'MARKET'}, now)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        log.warning('Saved review evidence unavailable; using period numbers only')
        payload['context_unavailable'] = True
    answer = interpret(settings, key, for_model(payload), now,
        'รีวิวพอร์ตระยะยาวตามรอบอัตโนมัติ ไม่ทวนตารางราคา '
        'check_more=ภาพรวมและสิ่งที่ควรทำตอนนี้ risks=ความเสี่ยงสำคัญต่อพอร์ต '
        'options=เงื่อนไขสำหรับถือ/DCA ต่อ รอดู หรือทบทวนแผน โดยยังไม่เปลี่ยนแผนเอง '
        'ใช้ข่าวและงบที่แนบโดยระบุแหล่งและวันที่เมื่ออ้างอิง ห้ามเดาสาเหตุราคา '
        'ข่าวอาจมีแค่หัวข่าว งบรายปีไม่ใช่งบไตรมาสล่าสุดหรือมูลค่าเหมาะสม '
        'หากราคาไม่ครบหรือล้าสมัยห้ามแนะนำซื้อทันทีจากราคานั้น '
        'ไม่มีวงเงินลงทุนเพิ่ม ห้ามนำเงินสำรองมาใช้หรือกำหนดวงเงินเอง '
        'ผลของช่วงนี้คำนวณด้วยจำนวนหุ้นปัจจุบันคงที่ ไม่ใช่ผลตอบแทนจริงหลังเงินเข้าออก '
        'หากหลักฐานไม่พอให้บอกเฉพาะข้อมูลที่ขาด ไม่แต่งข้อเสนอเพื่อให้ดูเหมือนมีคำแนะนำ', private=True)
    text = answer_text(answer) if answer else 'รอบนี้ผมสรุปตัวเลขให้ก่อนครับ ส่วนบทวิเคราะห์ AI ยังไม่พร้อม'
    if answer:
        sources = source_buttons(payload)
        if sources:
            text += '\n\nแหล่งข้อมูลประกอบรีวิว\n' + sources
    return text, payload, bool(answer)


def interpret(settings, key, payload, now, instruction, *, private=False):
    if settings.analyst_mode not in {'codex', 'gemini'}:
        return None
    if settings.analyst_mode == 'gemini' and private and not settings.gemini_share_portfolio_context:
        return None
    db = Database(settings.database_path, 'live')
    try:
        suffix = ':portfolio:'+settings.portfolio_id if settings.portfolio_id not in {'','main'} and not key.startswith('web-week:') else ''
        allowed = db.reserve_ai('brief:'+key+suffix, '__brief__', now,
                                settings.ai_max_calls_per_day, settings.ai_max_calls_per_day)
    finally:
        db.close()
    if not allowed:
        return None
    try:
        from app.ai_client import analyze
        return analyze(settings, STYLE+'\n'+instruction+'\n'+json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except Exception:
        log.warning('Brief AI unavailable; saved numeric/source-based fallback')
        return None


def build_news_brief(settings, coverage, key, now):
    from app.web_news import news_fallback, news_sources
    compact = {s: dict(status=f['status'], items=f['items'][:1]) for s, f in coverage.items()}
    prompt_data = {s: dict(status=f['status'], items=[{k: r[k] for k in
                   ('title', 'published_at', 'source_name', 'evidence_scope')} for r in f['items']])
                   for s, f in compact.items()}
    answer = interpret(settings, key, prompt_data, now,
        'สรุปข่าวรับสัปดาห์ใหม่ check_more=ภาพตลาด, risks=รายการข่าวหุ้นทุกตัว '
        + ' '.join(s for s in compact if s != 'MARKET')+' ตัวละหนึ่งบรรทัด, options=ประเด็นติดตามสัปดาห์นี้ '
        'ใช้หัวข่าวที่ให้เท่านั้น ไม่แต่งรายละเอียดบทความ ถ้าไม่มีข่าวบอกตามสถานะ '
        'อย่าคัดลอก URL เพราะโค้ดจะแนบแหล่งให้เอง แปลไทยกระชับรวมไม่เกิน 1800 ตัวอักษร') if any(f['items'] for f in compact.values()) else None
    all_stocks_present = bool(answer) and all(re.search(r'\b'+re.escape(s)+r'\b', answer['risks']) for s in compact if s != 'MARKET')
    if all_stocks_present:
        message = ('ข่าวรับสัปดาห์ใหม่ครับ\n\n'+answer['check_more']+'\n\nหุ้นของเรา\n'
                   +answer['risks']+'\n\nสัปดาห์นี้จับตา\n'+answer['options'])
    else:
        message = news_fallback(compact)
        if any(f['items'] for f in compact.values()):
            message += '\n\nรอบนี้ใช้หัวข่าวต้นฉบับ บทสรุปภาษาไทยยังไม่พร้อมครับ'
    sources = news_sources(compact)
    message += '\n\nอ่านจากหัวข่าว RSS ในรอบ 7 วัน ยังไม่ได้อ่านบทความเต็ม'
    if sources:
        message += '\n\nกดปุ่มแหล่งข่าวด้านล่างเพื่อเปิดหัวข้อที่อ้างอิงครับ\n'+sources
    return message, compact, bool(all_stocks_present)


def run_due(settings, store, now=None):
    from dataclasses import replace
    from app.portfolio_catalog import PortfolioCatalog
    from app.portfolio_scope import ScopedStore
    from app.portfolio import load_portfolio
    for p in sorted(PortfolioCatalog(settings).read()['portfolios'], key=lambda p:-p['priority']):
        if p['id'] != 'main' and not load_portfolio(settings.database_path.parent, p['id']):
            continue
        _run_due_single(replace(settings,portfolio_id=p['id']),ScopedStore(store,p['id']),now)


def _run_due_single(settings, store, now=None):
    now = now or datetime.now(UTC)
    if settings.mock_mode or store.get('paused') == '1':
        return
    enabled = store.get('reports-enabled-at')
    if not enabled:
        enabled = now.isoformat()
        store.set('reports-enabled-at', enabled)
    enabled_at = datetime.fromisoformat(enabled)
    periods = [p for p in due_periods(now, settings.manager_delivery_hour, settings.manager_catchup_days)
               if p['closed'] >= enabled_at]
    missing = [p for p in periods if not store.brief(p['key'])]
    # Also recover prices on startup before the evening delivery time. Shared
    # history caches, locks and API accounting keep this to one hourly attempt.
    last_sync = float(store.get('brief-close-sync-at', '0'))
    if now.timestamp()-last_sync >= 3600:
        from app.close_sync import sync_close
        try:
            result = sync_close(settings, now)
        except AlreadyRunning:
            return  # The price task is writing; try next scheduler tick.
        store.set('brief-close-sync-at', str(now.timestamp()))
        if result and result['updated']:
            from app.report import write_report
            write_report(settings, now)
        from app.market import completed_session, session_close
        target = completed_session(now)
        if (target and session_close(target) >= enabled_at
                and store.get('close-signals-through') != target.isoformat()):
            from app.close_sync import check_close_signals
            try:
                signals = check_close_signals(settings, now)
            except AlreadyRunning:
                signals = None
            if signals and signals['checked'] == len(settings.stocks()) and not signals['errors']:
                store.set('close-signals-through', target.isoformat())
    if missing:
        from app.report import report_data
        report = report_data(settings, now)
        for period in missing:
            numbers = period_numbers(report, period)
            if not numbers['complete']:
                wait_key = 'brief-wait:'+period['key']
                first_try = store.get(wait_key)
                if not first_try:
                    first_try = now.isoformat()
                    store.set(wait_key, first_try)
                if now < datetime.fromisoformat(first_try)+timedelta(hours=2):
                    continue
            message = render_period(numbers, period['kind'])
            if len(report.get('portfolio_catalog', [])) > 1:
                message = 'พอร์ต '+report['portfolio_info']['name']+'\n\n'+message
            if now.astimezone(BANGKOK).date() > period['ready'].astimezone(BANGKOK).date():
                message = 'ตามเก็บสรุปที่ยังไม่ได้ส่งครับ\n\n'+message
            answer = None
            saved_payload = numbers
            if numbers['complete'] and period['kind'] != 'daily':
                review, evidence, answer = portfolio_review(settings, report, numbers, period['key'], now)
                saved_payload = dict(numbers, review_evidence=evidence)
                message += '\n\n' + review
            store.save_brief(period['key'], period['kind'], now.timestamp(), message, saved_payload, bool(answer))
    for period in periods:
        saved = store.brief(period['key'])
        if saved:
            store.enqueue('close:'+period['key'], saved['message'], now.timestamp(), settings.manager_push_limit,
                          scheduled_for=period['ready'].timestamp())

    # Monday evening Bangkok; replay only the latest eligible week after downtime.
    local = now.astimezone(BANGKOK)
    monday = local.date()-timedelta(days=local.weekday())
    ready = datetime.combine(monday, time(settings.manager_delivery_hour), BANGKOK)
    if now < ready:
        monday -= timedelta(days=7)
        ready -= timedelta(days=7)
    if not ready >= enabled_at or not ready <= now < ready+timedelta(days=7):
        return
    key = 'web-week:'+monday.isoformat()
    saved = store.brief(key)
    if not saved:
        from app.web_news import collect
        coverage = collect(settings, now)
        # First failure may be transient. Retry once after six hours before
        # publishing explicit gaps; cached successes make no additional calls.
        if any(f['status'] != 'ok' for f in coverage.values()) and now < ready+timedelta(hours=6):
            return
        message, compact, ai_used = build_news_brief(settings, coverage, key, now)
        store.save_brief(key, 'news_weekly', now.timestamp(), message, compact, ai_used)
        saved = store.brief(key)
    store.enqueue(key, saved['message'], now.timestamp(), settings.manager_push_limit, scheduled_for=ready.timestamp())
