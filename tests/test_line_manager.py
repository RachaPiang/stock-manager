import base64
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.line_webhook import create_app, process_one, dca_day
from app.manager_store import ManagerStore
from app.line_commands import command
from app.notifier import NotificationError


@pytest.fixture
def bot(settings, tmp_path):
    settings = replace(settings, line_channel_secret='test-secret', line_user_id='owner')
    store = ManagerStore(tmp_path/'manager.sqlite3')
    tokens = {}
    return settings, store, tokens, create_app(settings, store, tokens).test_client()


def event(id='evt', owner='owner', source_type='user', text='เมนู'):
    return {'webhookEventId': id, 'type': 'message', 'source': {'type': source_type, 'userId': owner},
            'message': {'type': 'text', 'text': text}, 'replyToken': 'test-reply'}


def post(bot, events, valid=True):
    settings, _, _, client = bot
    body = json.dumps({'events': events}, ensure_ascii=False).encode()
    signature = base64.b64encode(hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()).decode()
    return client.post('/webhook', data=body, headers={'X-Line-Signature': signature if valid else 'bad'})


def test_signature_before_parsing_and_private_routes(bot):
    assert post(bot, [event()], False).status_code == 403
    assert bot[1].claim() is None
    assert post(bot, []).status_code == 200
    for path in ('/', '/.env', '/data/live-portfolio.html', '/static/portfolio-profile.json'):
        assert bot[3].get(path).status_code == 404
    assert bot[3].get('/health').json == {'ok': True, 'version': 'close-briefs-v2'}
    assert bot[3].post('/webhook', data=b'x'*65537).status_code == 413


@pytest.mark.parametrize('owner,kind', [('other', 'user'), ('owner', 'group'), ('owner', 'room')])
def test_unauthorized_ignored(bot, owner, kind):
    assert post(bot, [event(owner=owner, source_type=kind)]).status_code == 200
    assert bot[1].claim() is None


def test_redelivery_and_standby(bot):
    standby = event('standby'); standby['mode'] = 'standby'
    assert post(bot, [event(), event(), standby]).status_code == 200
    assert post(bot, [event()]).status_code == 200
    assert bot[1].claim()['id'] == 'evt'
    assert bot[1].claim() is None


def test_rate_limit_and_recovery(bot):
    _, store, _, _ = bot
    for i in range(12):
        assert store.accept(str(i), 'เมนู', 1000)
    assert not store.accept('13', 'เมนู', 1001)
    store.claim()
    store.recover(1002)
    assert 'หยุดกลางทาง' in store.pending(1002)['message']


def test_reply_no_push_no_ai(bot, now):
    class Fake:
        replies = []
        def reply(self, message, token):
            self.replies.append(message)
        def send(self, *args):
            pytest.fail('Simple command should use reply, not push')
    post(bot, [event()])
    fake = Fake()
    process_one(bot[0], bot[1], fake, bot[2])
    assert 'พอร์ต' in fake.replies[0] and 'สัปดาห์' in fake.replies[0]
    assert bot[1].pending(datetime.now(UTC).timestamp()) is None
    process_one(bot[0], bot[1], fake, bot[2])
    assert len(fake.replies) == 1


def test_push_retry_reuses_key_and_expires(bot, now):
    store = bot[1]
    store.finish('push', 'message', now.timestamp())
    keys = []
    class Fake:
        def send(self, message, key, recipient):
            keys.append(key)
            raise NotificationError('Temporary')
    process_one(bot[0], store, Fake(), {}, now)
    process_one(bot[0], store, Fake(), {}, now+timedelta(seconds=1))
    assert len(keys) == 1
    process_one(bot[0], store, Fake(), {}, now+timedelta(minutes=6))
    assert len(keys) == 2 and keys[0] == keys[1]
    assert store.pending((now+timedelta(hours=24)).timestamp()) is None


def test_schedule_dedup_and_budget(bot):
    store = bot[1]
    assert store.enqueue('one', 'x', 1000, 2)
    assert not store.enqueue('one', 'x', 1000, 2)
    assert store.enqueue('two', 'x', 1000, 2)
    assert not store.enqueue('three', 'x', 1000, 2)


def test_calendar():
    assert dca_day(2026, 9).isoformat() == '2026-09-28'
    assert dca_day(2026, 11).isoformat() == '2026-11-30'
    assert dca_day(2027, 2).isoformat() == '2027-03-01'
    assert dca_day(2030, 1) is None


def test_pause_and_confirm_are_not_trades(bot, now):
    settings, store, _, _ = bot
    assert 'ระบบเดิม' in command(settings, store, 'a', 'พักแจ้งเตือน', now)
    assert store.get('paused') == '1'
    assert 'ไม่ได้เปลี่ยนจำนวนหุ้น' in command(settings, store, 'b', 'อัปเดตพอร์ตแล้ว', now)
    assert not settings.database_path.exists()


def test_rich_menu_commands_are_read_only(bot, now, monkeypatch):
    settings, store, _, _ = bot
    monkeypatch.setattr('app.line_commands.report_data', lambda *a: {
        'portfolio': {'holdings': [{'symbol': 'META', 'live_price': 100, 'live_gain_pct': 5, 'live_stale': False}]},
        'news': [], 'ai': {'attempts_today': 0, 'daily_limit': 9}, 'market': {'open': False, 'used': 0, 'limit': 760}})
    assert 'META' in command(settings, store, 'stocks', 'หุ้นที่ถือ', now)
    assert 'ถามผมได้เลย' in command(settings, store, 'ask', 'ถาม AI', now)


def test_short_manager_commands_and_dca_status(bot, now, monkeypatch):
    settings, store, _, _ = bot
    overview = {'portfolio': {'dca': {'day': 28, 'monthly_total': 1800, 'per_stock': 200, 'currency': 'THB'},
                              'live': {'complete': False}, 'holdings': []},
                'news': [], 'stocks': [], 'ai': {'attempts_today': 0, 'daily_limit': 9},
                'market': {'open': False, 'used': 0, 'limit': 760}}
    monkeypatch.setattr('app.line_commands.report_data', lambda *a: overview)
    assert 'พอร์ตตอนนี้' in command(settings, store, 'today', 'วันนี้', now)
    assert '1,800' in command(settings, store, 'dca', 'DCA', now)
    assert 'จำนวนหุ้นรวม' in command(settings, store, 'update', 'อัปเดต', now)


def test_owner_cannot_change_for_existing_queue(bot):
    with pytest.raises(ValueError):
        bot[1].bind_owner('someone-else')


def test_live_webhook_refuses_malformed_secret(bot):
    with pytest.raises(ValueError):
        create_app(replace(bot[0], mock_mode=False, line_channel_secret='x'), bot[1])


def test_old_commands_not_executed(bot):
    bot[1].accept('old', 'ถาม test', 1000)
    assert bot[1].claim(5000) is None


def test_confirm_update_and_expiry(bot, now, monkeypatch):
    from app.line_commands import edit_holdings
    settings = replace(bot[0], mock_mode=False)
    path = settings.database_path.parent/'portfolio-profile.json'
    profile = {'as_of': '2026-09-23', 'policy': {}, 'dca': {}, 'holdings': [
        {'symbol': 'META', 'quantity': 2, 'value_usd': 110, 'gain_pct': 10, 'thesis': 'test'}]}
    path.write_text(json.dumps(profile), encoding='utf-8')
    monkeypatch.setattr('app.report.write_report', lambda *a: None)
    store = bot[1]
    edit_holdings(settings, store, 'บันทึก META 3 150', now)
    draft = json.loads(store.get('holding_draft'))
    assert json.loads(path.read_text())['holdings'][0]['quantity'] == 2
    assert 'ไม่ตรง' in edit_holdings(settings, store, 'ยืนยัน WRONG', now)
    answer = edit_holdings(settings, store, 'ยืนยัน '+draft['code'], now)
    assert 'บันทึกยอดรวม' in answer
    assert json.loads(path.read_text())['holdings'][0]['quantity'] == 3
    assert list(path.parent.glob('portfolio-profile-backup-*.json'))
    assert 'ไม่มีร่าง' in edit_holdings(settings, store, 'ยืนยัน '+draft['code'], now)
    edit_holdings(settings, store, 'บันทึก META 4 200', now)
    draft = json.loads(store.get('holding_draft'))
    assert 'หมดอายุ' in edit_holdings(settings, store, 'ยืนยัน '+draft['code'], now+timedelta(minutes=11))


def test_confirm_refuses_changed_profile(bot, now):
    from app.line_commands import edit_holdings
    settings = replace(bot[0], mock_mode=False)
    path = settings.database_path.parent/'portfolio-profile.json'
    raw = json.dumps({'holdings': [{'symbol': 'META', 'quantity': 2}]})
    path.write_text(raw, encoding='utf-8')
    edit_holdings(settings, bot[1], 'บันทึก META 3 150', now)
    draft = json.loads(bot[1].get('holding_draft'))
    path.write_text(raw+' ', encoding='utf-8')
    assert 'ข้อมูลพอร์ตเปลี่ยน' in edit_holdings(settings, bot[1], 'ยืนยัน '+draft['code'], now)
    assert json.loads(path.read_text())['holdings'][0]['quantity'] == 2


def test_schedule_rollover(bot, monkeypatch):
    from app.line_webhook import schedule
    settings = replace(bot[0], mock_mode=False)
    monkeypatch.setattr('app.portfolio.load_portfolio', lambda *a: {'dca': {
        'day': 28, 'monthly_total': 1800, 'per_stock': 200, 'currency': 'THB'}})
    monkeypatch.setattr('app.report.report_data', lambda *a: {'news': [], 'portfolio_review': None})
    now = datetime(2027, 3, 1, 3, tzinfo=UTC)
    schedule(settings, bot[1], now)
    row = bot[1].pending(now.timestamp())
    assert row['id'] == 'schedule:dca:2027-02'
    bot[1].delivered(row['id'])
    schedule(settings, bot[1], now)
    assert bot[1].pending(now.timestamp()) is None
    now = datetime(2027, 3, 2, 12, tzinfo=UTC)
    schedule(settings, bot[1], now)
    assert bot[1].pending(now.timestamp())['id'] == 'schedule:update:2027-02'


def test_ai_reserves_shared_daily_budget(bot, now, monkeypatch):
    settings = replace(bot[0], mock_mode=False, analyst_mode='codex', ai_max_calls_per_day=1)
    monkeypatch.setattr('app.line_commands.report_data', lambda *a: {'portfolio': None, 'news': []})
    calls = []
    def analyze(*args):
        calls.append(1)
        return {'check_more': 'test', 'risks': 'risk', 'options': 'conditional'}
    monkeypatch.setattr('app.codex_client.analyze', analyze)
    assert 'conditional' in command(settings, bot[1], 'first', 'ถาม test', now)
    assert 'เพดาน' in command(settings, bot[1], 'second', 'ถาม test', now)
    assert len(calls) == 1


def test_ai_failure_not_repeated(bot, now, monkeypatch):
    from app.codex_client import CodexError
    settings = replace(bot[0], mock_mode=False, analyst_mode='codex')
    monkeypatch.setattr('app.line_commands.report_data', lambda *a: {'portfolio': None, 'news': []})
    def fail(*args):
        raise CodexError('temporary')
    monkeypatch.setattr('app.codex_client.analyze', fail)
    assert 'วิเคราะห์ไม่สำเร็จ' in command(settings, bot[1], 'same', 'ถาม test', now)
    assert 'เคยถูกประมวลผล' in command(settings, bot[1], 'same', 'ถาม test', now)
