from dataclasses import replace
from datetime import UTC, datetime

from app.database import Database
from app.main import run_portfolio_review
from app.news import NewsItem, TwelveDataPressReleaseProvider, classify, plain_text


def test_news_text_and_importance_classifier():
    assert plain_text("<p>Hello <b>world</b></p>") == "Hello world"
    assert classify("Company reports financial results", "") == ("high", "ผลประกอบการหรือแนวโน้มธุรกิจ")
    assert classify("Company attends conference", "") is None


def test_press_release_provider_keeps_only_important_items(settings, now):
    class Response:
        status_code = 200
        def json(self):
            return {"press_releases": [
                {"id": "important", "datetime": "2026-09-22T12:00:00Z",
                 "title": "Company reports financial results", "body": "<p>Important update</p>"},
                {"id": "noise", "datetime": "2026-09-22T12:00:00Z",
                 "title": "Company event", "body": "<p>Meet us</p>"}]}
    class Session:
        def get(self, *args, **kwargs):
            return Response()
    provider = TwelveDataPressReleaseProvider(replace(settings, mock_mode=False, stock_api_key="not-a-real-key",
                                                       request_interval=0))
    provider.session = Session()
    items = provider.fetch("META", now)
    assert len(items) == 1
    assert items[0].source_id == "important"
    assert items[0].source_name.startswith("Twelve Data")


def test_news_and_review_are_durable_without_ai(settings, tmp_path, now):
    profile = {
        "as_of": "2026-09-23", "policy": {"goal": "long term"}, "dca": {"monthly_total": 1800},
        "holdings": [{"symbol": "META", "value_usd": 100, "gain_pct": 5, "quantity": 1,
                      "sector": "Tech", "exposure": "AI", "thesis": "User thesis"}]}
    (tmp_path / "portfolio-profile.json").write_text(__import__("json").dumps(profile), encoding="utf-8")
    live = replace(settings, mock_mode=False, analyst_mode="template", configured_analyst_mode="template",
                   database_path=tmp_path / "live.sqlite3")
    db = Database(live.database_path, "live")
    item = NewsItem("release-1", "META", now, "Financial results", "Body", "high", "ผลประกอบการ")
    assert db.save_news([item], now) == 1
    assert db.save_news([item], now) == 0
    assert len(db.recent_news()) == 1
    db.close()
    first = run_portfolio_review(live, now)
    assert first == {"created": True, "ai_used": False, "period": "2026-W39"}
    second = run_portfolio_review(live, now)
    assert second == {"created": False, "ai_used": False, "period": "2026-W39"}
    db = Database(live.database_path, "live")
    assert "แม่แบบในเครื่อง" in db.latest_review()["message"]
    db.close()

