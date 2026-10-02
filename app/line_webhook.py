"""Minimal, owner-only webhook. No report, file server, or public administration."""
import base64
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from datetime import UTC, datetime, date, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, request

from app.config import Settings
from app.database import run_lock, AlreadyRunning
from app.manager_store import ManagerStore
from app.notifier import LineNotifier, NotificationError
from app.line_commands import command, safe_text

log = logging.getLogger(__name__)
BANGKOK = ZoneInfo('Asia/Bangkok')


def dca_day(year, month, day_of_month=28):
    from app.market import HOLIDAYS
    from calendar import monthrange
    day = date(year, month, min(day_of_month, monthrange(year, month)[1]))
    if year not in HOLIDAYS:
        return None
    while day.weekday() >= 5 or day.strftime('%m-%d') in HOLIDAYS.get(day.year, '').split():
        day += timedelta(days=1)
        if day.year not in HOLIDAYS:
            return None
    return day


def schedule(settings, store, now):
    from dataclasses import replace
    from app.portfolio_catalog import PortfolioCatalog
    from app.portfolio_scope import ScopedStore
    entries = PortfolioCatalog(settings).read()['portfolios']
    for index, p in enumerate(entries, 1):
        try:
            _schedule_single(replace(settings, portfolio_id=p['id']), ScopedStore(store,p['id']), now,
                             label=p['name'] if len(entries)>1 else '', index=index)
        except (ValueError, OSError, KeyError) as exc:
            log.warning('DCA schedule unavailable for portfolio %s (%s); continuing others', p['id'], type(exc).__name__)


def _schedule_single(settings, store, now, *, label='', index=1):
    """No AI or stock API calls here. Send saved, bounded changes only."""
    if settings.mock_mode or store.get('paused') == '1':
        return
    local = now.astimezone(BANGKOK)
    from app.portfolio import load_portfolio
    profile = load_portfolio(settings.database_path.parent, settings.portfolio_id)
    if not profile:
        return
    plan = profile['dca']
    if plan.get('enabled') is False or plan.get('monthly_total', 0) <= 0:
        return
    # Consider previous month too: a February 28 weekend can execute in March.
    previous = local.date().replace(day=1)-timedelta(days=1)
    candidates = [(year, month, dca_day(year, month, plan['day']))
                  for year, month in ((previous.year, previous.month), (local.year, local.month))]
    from datetime import time as clock_time
    enabled = store.get('reports-enabled-at')
    enabled_day = datetime.fromisoformat(enabled).astimezone(BANGKOK).date() if enabled else None
    if plan.get('plan_as_of'):
        confirmed = datetime.fromisoformat(plan['plan_as_of']).astimezone(BANGKOK).date()
        enabled_day = max(enabled_day,confirmed) if enabled_day else confirmed
    latest_due = max((due for _, _, due in candidates if due and due < local.date()), default=None)
    at = now.timestamp()
    from app.line_portfolios import portfolio_heading
    update_hint = (f'พิมพ์ เลือกพอร์ต {index} แล้วกด อัปเดต เพื่อกรอกยอดของพอร์ตนี้' if label else
                   'พิมพ์ อัปเดต เพื่อกรอกยอดจริง')
    for year, month, due in candidates:
        cycle = f'{year}-{month:02d}'
        from calendar import monthrange
        calendar_day = date(year, month, min(plan['day'], monthrange(year, month)[1]))
        # The user's saving habit is on the 28th.  The broker may execute on
        # the next US business day, but the reminder itself must not disappear
        # merely because that date is a weekend or holiday.
        if enabled_day and calendar_day < enabled_day:
            continue
        ready = datetime.combine(calendar_day, clock_time(settings.manager_delivery_hour), BANGKOK)
        updated = bool(due and store.get('dca_updated_date', '') >= due.isoformat())
        if updated:
            store.cancel_schedule('dca:'+cycle)
            store.cancel_schedule('update:'+cycle)
        execution_ready = datetime.combine(due, clock_time(settings.manager_delivery_hour), BANGKOK) if due else ready
        if (not updated and ready <= now < execution_ready+timedelta(days=settings.manager_catchup_days)):
            prefix = (f"รอบ DCA วันที่ {plan['day']} ครับ" if local.date() == calendar_day else
                      f"ตามเก็บเตือน DCA รอบ {cycle} ที่ยังไม่ได้แจ้งครับ")
            if label:
                prefix = portfolio_heading(label)+'\n'+prefix
            store.enqueue('dca:'+cycle,
                          prefix+f" · ตามแผน {plan['monthly_total']:,.0f} {plan['currency']} · ตัวละ {plan['per_stock']:,.0f} {plan['currency']}\n"
                          'ตรวจเงินพร้อมและสถานะ Auto DCA ใน Dime; หากตลาดปิด โบรกเกอร์อาจเลื่อนไปวันทำการ\n'
                          'หลังรายการสำเร็จ '+update_hint,
                          at, settings.manager_push_limit, scheduled_for=ready.timestamp())
        if (due and due == latest_due and not updated and now >= datetime.combine(
                due+timedelta(days=1), clock_time(settings.manager_delivery_hour), BANGKOK)
                and now < execution_ready+timedelta(days=settings.manager_catchup_days+1)):
            heading = portfolio_heading(label)+'\n' if label else ''
            store.enqueue('update:'+cycle,
                          heading+'ตรวจรายการ DCA สำเร็จหรือยังครับ? ถ้าซื้อแล้ว อัปเดตจำนวนหุ้นและต้นทุนจาก Dime เพื่อให้มูลค่าและกำไรพอร์ตถูกต้อง\n'
                          +update_hint, at, settings.manager_push_limit,
                          scheduled_for=datetime.combine(due+timedelta(days=1), clock_time(settings.manager_delivery_hour), BANGKOK).timestamp())
    # Close reports and weekly news run in the independent research worker.


def create_app(settings, store=None, reply_tokens=None):
    if not settings.line_channel_secret or not settings.line_user_id:
        raise ValueError('Configure LINE_CHANNEL_SECRET and LINE_USER_ID first')
    if not settings.mock_mode and not re.fullmatch(r'[0-9a-fA-F]{32}', settings.line_channel_secret):
        raise ValueError('Invalid LINE_CHANNEL_SECRET format; do not expose webhook')
    store = store or ManagerStore(settings.database_path.parent/'line-manager.sqlite3')
    store.bind_owner(settings.line_user_id)
    reply_tokens = reply_tokens if reply_tokens is not None else {}
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 64*1024

    @app.get('/health')
    def health():
        return {'ok': True, 'version': 'close-briefs-v2'}

    @app.post('/webhook')
    def webhook():
        body = request.get_data()
        expected = base64.b64encode(hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest())
        signature = request.headers.get('X-Line-Signature', '').encode('utf-8')
        if not hmac.compare_digest(expected, signature):
            return '', 403
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return '', 400
        if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
            return '', 400
        if len(payload['events']) > 100:
            return '', 400
        for event in payload['events']:
            if not isinstance(event, dict):
                continue
            source, message = event.get('source'), event.get('message')
            if (event.get('type') != 'message' or event.get('mode') == 'standby'
                    or not isinstance(source, dict) or source.get('type') != 'user'
                    or source.get('userId') != settings.line_user_id
                    or not isinstance(message, dict) or message.get('type') != 'text'):
                continue
            event_id, text = event.get('webhookEventId'), message.get('text')
            if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128 or not isinstance(text, str):
                continue
            now = time.time()
            # Bound memory even if the worker is busy; tokens never persist to disk.
            for key, (_, received) in list(reply_tokens.items()):
                if now-received > 45:
                    reply_tokens.pop(key, None)
            if store.accept(event_id, safe_text(text, 2000), now):
                token = event.get('replyToken')
                if isinstance(token, str) and 0 < len(token) <= 256:
                    reply_tokens[event_id] = (token, now)
        return '', 200

    return app


def process_one(settings, store, notifier, reply_tokens, now=None):
    live_clock = now is None
    now = now or datetime.now(UTC)
    item = store.claim(now.timestamp())
    if item:
        token_info = reply_tokens.pop(item['id'], None)
        token = token_info[0] if token_info and now.timestamp()-token_info[1] < 45 else None
        asking = ((item['text'].strip().startswith('ถาม ') and item['text'].strip() != 'ถาม AI')
                  or item['text'].strip() == 'แผนลงทุน' or item['text'].strip().startswith('แผนลงทุน '))
        if asking and token:
            try:
                notifier.reply('ได้ครับ ขอผมดูข้อมูลพอร์ตกับข่าวที่มีสักครู่ แล้วจะสรุปให้ครับ', token)
            except NotificationError:
                log.warning('LINE analysis acknowledgement not confirmed')
            token = None
        try:
            result = command(settings, store, item['id'], item['text'], now)
        except Exception as exc:
            log.error('LINE command failed (%s)', type(exc).__name__)
            result = 'อ่านข้อมูลหรือวิเคราะห์ไม่สำเร็จ ระบบไม่ได้ทำธุรกรรม ลองใหม่ภายหลังครับ'
        store.finish(item['id'], result, now.timestamp())
        if token:
            store.start_delivery(item['id'])
            try:
                notifier.reply(result, token)
            except NotificationError:
                # An unknown reply result must not produce a duplicate push.
                store.failed(item['id'], now.timestamp(), False)
                log.warning('LINE reply not confirmed; owner can resend command')
            else:
                store.delivered(item['id'])
    # A chat analysis may take minutes. Stamp scheduled delivery when it
    # actually starts, rather than with the worker tick's earlier timestamp.
    delivery_now = datetime.now(UTC) if live_clock else now
    pending = store.pending(delivery_now.timestamp(), settings.manager_push_limit)
    if pending:
        store.start_delivery(pending['id'])
        try:
            notifier.send(pending['message'], pending['retry_key'], settings.line_user_id)
        except NotificationError as exc:
            store.failed(pending['id'], delivery_now.timestamp(), exc.retryable)
            log.warning('LINE delivery not confirmed; retryable=%s', exc.retryable)
        else:
            store.delivered(pending['id'])


def main():
    from app.system_status import heartbeat
    from logging.handlers import RotatingFileHandler
    from app.config import ROOT
    from waitress import serve
    (ROOT/'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
        ROOT/'logs/line-manager.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')],
        format='%(asctime)s %(levelname)s %(message)s')
    try:
        settings = Settings.from_env()
        if settings.mock_mode or settings.notifier_mode != 'line' or not settings.line_token:
            raise ValueError('Live LINE configuration required')
        with run_lock(settings.database_path.parent/'line-manager.lock'), heartbeat(settings.database_path.parent, 'line') as pulse:
            store = ManagerStore(settings.database_path.parent/'line-manager.sqlite3')
            tokens = {}
            app = create_app(settings, store, tokens)
            store.recover(time.time())
            store.cancel_old_summaries()
            if not store.get('reports-enabled-at'):
                store.set('reports-enabled-at', datetime.now(UTC).isoformat())
            stop = threading.Event()
            from app.line_tunnel import maintain
            threading.Thread(target=maintain, args=(settings, stop, pulse), daemon=True, name='line-tunnel').start()
            def worker():
                notifier, last_schedule, last_pulse = LineNotifier(settings), 0, 0
                while not stop.is_set():
                    try:
                        now = datetime.now(UTC)
                        process_one(settings, store, notifier, tokens)
                        if now.timestamp() - last_pulse >= 30:
                            pulse.mark('รับคำสั่งและส่ง LINE', 'ok', 'พร้อมรับข้อความ · ส่งตามคิวและโควตา')
                            last_pulse = now.timestamp()
                        if now.timestamp()-last_schedule >= 60:
                            last_schedule = now.timestamp()
                            schedule(settings, store, now)
                            pulse.mark('สรุปพอร์ตและเตือน DCA', 'ok', 'ตรวจเวลานัดหมายแล้ว')
                    except Exception as exc:
                        pulse.mark('รับคำสั่งและส่ง LINE', 'error', 'งานล่าสุดไม่สำเร็จ ดูบันทึกระบบ')
                        log.error('Manager worker error (%s)', type(exc).__name__)
                    stop.wait(1)
            threading.Thread(target=worker, daemon=True, name='line-manager').start()
            def research_worker():
                from app.scheduled_briefs import run_due
                from app.research_monitor import run_due as monitor_due
                while not stop.is_set():
                    try:
                        pulse.mark('รีวิวตามเวลา', 'busy')
                        run_due(settings, store)
                        pulse.mark('รีวิวตามเวลา', 'ok', 'ตรวจคิวแล้ว · ไม่ได้เรียก AI ทุกครั้ง')
                    except Exception as exc:
                        pulse.mark('รีวิวตามเวลา', 'error', 'งานล่าสุดไม่สำเร็จ')
                        log.error('Scheduled brief failed (%s); retry next tick', type(exc).__name__)
                    try:
                        pulse.mark('ข่าวและงบ SEC', 'busy')
                        monitor_due(settings,store)
                        pulse.mark('ข่าวและงบ SEC', 'ok', 'ตรวจคิวแล้ว · ดึงข้อมูลเมื่อถึงรอบ')
                    except Exception as exc:
                        pulse.mark('ข่าวและงบ SEC', 'error', 'งานล่าสุดไม่สำเร็จ')
                        log.error('Research monitor failed (%s)',type(exc).__name__)
                    stop.wait(60)
            threading.Thread(target=research_worker, daemon=True, name='line-research').start()
            log.info('Owner-only webhook listening on 127.0.0.1:8787; no portfolio HTTP routes')
            try:
                serve(app, host='127.0.0.1', port=8787, threads=4, max_request_body_size=65536)
            finally:
                stop.set()
    except AlreadyRunning:
        log.info('LINE manager already running; skipped duplicate start')
    except Exception as exc:
        log.error('Cannot start LINE manager (%s); check private configuration', type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
