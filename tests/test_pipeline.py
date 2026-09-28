from dataclasses import replace
from datetime import timedelta

from app.analyst import AnalysisError, TemplateAnalyst
from app.database import Database
from app.fetcher import DataError, MockStockProvider
from app.main import check
from app.notifier import NotificationError, Notifier


class RecordingNotifier(Notifier):
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def send(self, message, retry_key, recipient):
        self.calls.append((message, retry_key, recipient))
        if self.fail:
            raise NotificationError("Simulated timeout")


def test_ai_cap_keeps_alerts_and_reuses_saved_messages(settings, now):
    class CountedAI(TemplateAnalyst):
        uses_ai = True
        calls = 0
        def summarize(self, payload):
            self.calls += 1
            return super().summarize(payload)
    settings = replace(settings, ai_max_calls_per_day=2)
    ai = CountedAI()
    notifier = RecordingNotifier(fail=True)
    check(settings, MockStockProvider(), ai, notifier, now)
    assert ai.calls == 2
    assert len(notifier.calls) == 6
    assert sum("เพื่อจำกัดการเรียก AI" in m for m, _, _ in notifier.calls) == 4
    notifier.fail = False
    result = check(settings, MockStockProvider(), ai, notifier, now + timedelta(minutes=5))
    assert result["sent"] == 6
    assert ai.calls == 2


def test_full_mock_run_nine_stocks_then_no_duplicate(settings, now):
    notifier = RecordingNotifier()
    first = check(settings, MockStockProvider(), TemplateAnalyst(), notifier, now)
    assert first == dict(checked=9, queued=6, sent=6, errors=0)
    assert all("ข้อมูลสมมุติ" in message for message, _, _ in notifier.calls)
    second = check(settings, MockStockProvider(), TemplateAnalyst(), notifier, now + timedelta(minutes=30))
    assert second == dict(checked=9, queued=0, sent=0, errors=0)
    assert len(notifier.calls) == 6


def test_failure_retries_same_message_same_key_and_recipient(settings, now):
    notifier = RecordingNotifier(fail=True)
    first = check(settings, MockStockProvider(), TemplateAnalyst(), notifier, now)
    assert first["sent"] == 0 and first["errors"] == 6
    attempts = set(notifier.calls)
    notifier.calls.clear()
    notifier.fail = False
    second = check(settings, MockStockProvider(), TemplateAnalyst(), notifier, now + timedelta(minutes=30))
    assert second["sent"] == 6 and second["queued"] == 0
    assert set(notifier.calls) == attempts


def test_api_failure_does_not_block_other_stocks(settings, now):
    class BrokenProvider(MockStockProvider):
        def fetch(self, symbol, now):
            if symbol == "META":
                raise DataError("Simulated outage")
            return super().fetch(symbol, now)
    result = check(settings, BrokenProvider(), TemplateAnalyst(), RecordingNotifier(), now)
    assert result["checked"] == 8 and result["errors"] == 1 and result["sent"] == 5


def test_ai_failure_falls_back_with_clear_label(settings, now):
    class BrokenAnalyst(TemplateAnalyst):
        def summarize(self, payload):
            raise AnalysisError("Simulated API outage")
    notifier = RecordingNotifier()
    result = check(settings, MockStockProvider(), BrokenAnalyst(), notifier, now)
    assert result["sent"] == 6
    assert all("AI ขัดข้อง" in message for message, _, _ in notifier.calls)


def test_old_pending_expires_without_retry(settings, now):
    check(settings, MockStockProvider(), TemplateAnalyst(), RecordingNotifier(fail=True), now)
    notifier = RecordingNotifier()
    # Same quote/date, but the outbox is expired at the delivery boundary.
    from app.main import deliver_pending
    db = Database(settings.database_path, "mock")
    assert deliver_pending(db, TemplateAnalyst(), notifier, now + timedelta(hours=23)) == (0, 6)
    assert not notifier.calls
    assert {r["status"] for r in db.alerts()} == {"expired"}
    db.close()


def test_destination_change_does_not_redirect_pending(settings, now):
    check(settings, MockStockProvider(), TemplateAnalyst(), RecordingNotifier(fail=True), now)
    notifier = RecordingNotifier()
    notifier.recipient = "someone-else"
    from app.main import deliver_pending
    db = Database(settings.database_path, "mock")
    assert deliver_pending(db, TemplateAnalyst(), notifier, now + timedelta(minutes=30)) == (0, 6)
    assert not notifier.calls
    db.close()
