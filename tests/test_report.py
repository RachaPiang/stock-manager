import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.config import Settings
from app.database import Database
from app.main import check
from app.analyst import TemplateAnalyst
from app.fetcher import MockStockProvider
from app.notifier import ConsoleNotifier
from app.report import report_data, write_report


def test_report_contains_nine_stocks_without_fetching_or_sending(settings, now):
    check(settings, MockStockProvider(), TemplateAnalyst(), ConsoleNotifier(), now)
    payload = report_data(settings, now)
    assert len(payload["stocks"]) == 9
    nvda = next(s for s in payload["stocks"] if s["symbol"] == "NVDA")
    assert len(nvda["bars"]) == 100
    assert nvda["indicators"]["daily_change_pct"] == pytest.approx(10, abs=.0001)
    assert nvda["stale"] is False
    assert nvda["alert"]["status"] == "accepted"
    assert payload["last_run"]["checked"] == 9


def test_no_live_database_stays_empty_and_does_not_fallback_to_mock(settings, now):
    live = replace(settings, mock_mode=False)
    payload = report_data(live, now)
    assert not payload["mock"]
    assert all(s["price"] is None and not s["bars"] for s in payload["stocks"])
    assert not settings.database_path.exists()


def test_old_prices_are_labelled_and_not_evaluated(settings, snapshot, now):
    db = Database(settings.database_path, "mock")
    db.save_snapshot(snapshot)
    db.close()
    payload = report_data(settings, now + timedelta(days=10))
    item = payload["stocks"][0]
    assert item["price"] is not None and item["stale"]
    assert item["indicators"] is None and not item["signals"]


def test_html_escapes_alert_content_and_omits_credentials(settings, now):
    check(settings, MockStockProvider(), TemplateAnalyst(), ConsoleNotifier(), now)
    db = Database(settings.database_path, "mock")
    attack = '</script><script>alert("injection")</script>'
    with db.connection:
        db.connection.execute("UPDATE notifications SET message=?", (attack,))
    db.close()
    settings = replace(settings, stock_api_key="secret-stock", openai_api_key="secret-ai", line_token="secret-line", line_user_id="private-user")
    output = write_report(settings, now)
    html = output.read_text(encoding="utf-8")
    assert attack not in html
    for secret in ("secret-stock", "secret-ai", "secret-line", "private-user"):
        assert secret not in html
    raw = html.split('<script id="portfolio-data" type="application/json">')[1].split('</script>')[0]
    assert any(s["alert"] and s["alert"]["message"] == attack for s in json.loads(raw)["stocks"])
    assert not list(output.parent.glob("*.html.tmp"))


def test_report_refuses_wrong_mode_database(settings, now):
    Database(settings.database_path, "mock").close()
    with pytest.raises(ValueError, match="mode mismatch"):
        report_data(replace(settings, mock_mode=False), now)


def test_template_has_unique_ids_and_balanced_elements():
    from html.parser import HTMLParser
    from app.config import ROOT
    class Structure(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.ids = [], set()
        def handle_starttag(self, tag, attrs):
            identity = dict(attrs).get('id')
            if identity:
                assert identity not in self.ids, identity
                self.ids.add(identity)
            if tag not in {'meta','input','br','hr','link','img'}:
                self.stack.append(tag)
        def handle_endtag(self, tag):
            assert self.stack.pop() == tag, tag
    parser = Structure()
    parser.feed((ROOT / 'app/templates/portfolio.html').read_text(encoding='utf-8'))
    assert not parser.stack
    assert {'chart-range','bar-size','fullscreen','cards','chart-panel','allocation-panel','sector-donut'} <= parser.ids
