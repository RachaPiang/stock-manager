from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests

from app.analyst import AnalysisError, OpenAIAnalyst
from app.config import Settings
from app.fetcher import DataError, DailyBar, TwelveDataProvider
from app.notifier import LineNotifier, NotificationError


@pytest.mark.parametrize("change", [
    {"price": 0}, {"previous_close": float("nan")}, {"currency": "EUR"}, {"bars": ()}])
def test_invalid_data(snapshot, now, change):
    with pytest.raises(DataError):
        replace(snapshot, **change).validate(now, 96)


def test_stale_future_duplicate_and_incomplete(snapshot, now):
    invalid = [replace(snapshot, as_of=now - timedelta(hours=97)),
               replace(snapshot, as_of=now + timedelta(hours=1)),
               replace(snapshot, bars=snapshot.bars + (snapshot.bars[-1],)),
               replace(snapshot, bars=snapshot.bars + (DailyBar(snapshot.session_date, 100),))]
    for item in invalid:
        with pytest.raises(DataError):
            item.validate(now, 96)


def test_provider_normalizes_and_excludes_current_daily_bar(monkeypatch, settings, snapshot, now):
    provider = TwelveDataProvider(settings)
    quote = {"symbol": "META", "currency": "USD", "close": "110", "previous_close": "100", "timestamp": int(now.timestamp())}
    rows = [{"datetime": b.day.isoformat(), "close": str(b.close)} for b in snapshot.bars]
    rows.append({"datetime": snapshot.session_date.isoformat(), "close": "999"})
    history = {"meta": {"symbol": "META", "currency": "USD"}, "values": list(reversed(rows))}
    monkeypatch.setattr(provider, "_get", lambda endpoint, params: quote if endpoint == "quote" else history)
    result = provider.fetch("META", now)
    assert len(result.bars) == 100
    assert result.price == 110 and result.previous_close == 100
    assert result.simulated is False
    calls = []
    def quote_only(endpoint, params):
        calls.append(endpoint)
        assert endpoint == "quote"
        return quote
    next_provider = TwelveDataProvider(settings)
    monkeypatch.setattr(next_provider, "_get", quote_only)
    assert next_provider.fetch("META", now).bars == result.bars
    assert calls == ["quote"]


def test_fetcher_redacts_api_secrets(monkeypatch, settings):
    provider = TwelveDataProvider(settings)
    def fail(*args, **kwargs):
        raise requests.ConnectionError("https://example.com?apikey=SECRET")
    monkeypatch.setattr(provider.session, "get", fail)
    with pytest.raises(DataError) as error:
        provider._get("quote", {})
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("status,headers,ok,retryable", [
    (200, {}, True, False), (409, {"x-line-accepted-request-id": "abc"}, True, False),
    (409, {}, False, False), (401, {}, False, False), (429, {}, False, True), (500, {}, False, True)])
def test_line_status_and_retry_key(monkeypatch, status, headers, ok, retryable):
    captured = {}
    def post(url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=status, headers=headers)
    monkeypatch.setattr(requests, "post", post)
    notifier = LineNotifier(Settings(line_token="secret", line_user_id="user"))
    if ok:
        notifier.send("hello", "key", "user")
    else:
        with pytest.raises(NotificationError) as error:
            notifier.send("hello", "key", "user")
        assert error.value.retryable == retryable
    assert captured["headers"]["X-Line-Retry-Key"] == "key"
    assert captured["json"]["to"] == "user"


def test_mock_overrides_live_modes(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("ANALYST_MODE", "openai")
    monkeypatch.setenv("NOTIFIER_MODE", "line")
    settings = Settings.from_env()
    assert settings.analyst_mode == "template" and settings.notifier_mode == "console"


def test_invalid_boolean_does_not_enable_network(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "typo")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_openai_minimal_payload_and_no_store(settings, snapshot, monkeypatch):
    from app.config import Stock
    from app.indicators import calculate
    from app.rules import analysis_payload, evaluate
    indicators = calculate([b.close for b in snapshot.bars], snapshot.price, snapshot.previous_close)
    payload = analysis_payload(snapshot, indicators, evaluate(snapshot, indicators, Stock("META"), settings))
    analyst = OpenAIAnalyst(replace(settings, openai_api_key="test-key", openai_model="configured-model"))
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_text="ปัจจัยที่ควรตรวจเพิ่ม: งบล่าสุด", status="completed")
    monkeypatch.setattr(analyst.client.responses, "create", create)
    message = analyst.summarize(payload)
    assert "บทวิเคราะห์ AI" in message
    assert captured["store"] is False and captured["model"] == "configured-model"
    assert "test-key" not in captured["input"] and "shares" not in captured["input"]
    analyst.client.close()
