import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analyst import AnalysisError, CodexAnalyst
from app.codex_client import CodexError, analyze, check_login, child_environment
from app.config import Settings, Stock
from app.indicators import calculate
from app.rules import analysis_payload, evaluate


@pytest.fixture
def fake_codex(monkeypatch):
    monkeypatch.setattr("app.codex_client.find_codex", lambda _: "codex.exe")
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:3] == ["login", "status"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="Logged in using ChatGPT")
        path = Path(args[args.index("--output-last-message") + 1])
        path.write_text(json.dumps({"check_more": "ตรวจงบล่าสุด", "risks": "ยังไม่ตรวจข่าว", "options": "พิจารณาทบทวนน้ำหนัก"}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("app.codex_client.subprocess.run", run)
    return calls


def test_codex_uses_stdin_and_read_only_without_secrets(fake_codex, settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-pass")
    monkeypatch.setenv("STOCK_API_KEY", "stock-secret")
    monkeypatch.setenv('LINE_CHANNEL_SECRET', 'webhook-secret')
    answer = analyze(settings, "only this selected data")
    assert answer["risks"] == "ยังไม่ตรวจข่าว"
    args, kwargs = fake_codex[-1]
    assert kwargs["input"] == "only this selected data"
    assert "only this selected data" not in args
    assert kwargs["shell"] is False
    assert "OPENAI_API_KEY" not in kwargs["env"] and "STOCK_API_KEY" not in kwargs["env"]
    assert 'LINE_CHANNEL_SECRET' not in kwargs['env']
    assert 'model_reasoning_effort="medium"' in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in args and "--ephemeral" in args
    assert "--model" not in args
    assert not Path(kwargs["cwd"]).exists()  # Temporary output is cleaned up.


def test_optional_codex_model_preserved(fake_codex, settings):
    analyze(replace(settings, codex_model="user-selected-model"), "test")
    args = fake_codex[-1][0]
    assert args[args.index("--model") + 1] == "user-selected-model"


def test_api_key_login_is_not_silently_used(monkeypatch, fake_codex, settings):
    monkeypatch.setattr("app.codex_client.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="Logged in using API key: SECRET", stderr=""))
    with pytest.raises(CodexError) as error:
        check_login(settings)
    assert "SECRET" not in str(error.value)


def test_codex_timeout_is_redacted(monkeypatch, fake_codex, settings):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret command", 1)
    monkeypatch.setattr("app.codex_client.subprocess.run", fail)
    with pytest.raises(CodexError) as error:
        analyze(settings, "test")
    assert "secret command" not in str(error.value)


def test_codex_analyst_formats_evidence(fake_codex, settings, snapshot):
    indicators = calculate([b.close for b in snapshot.bars], snapshot.price, snapshot.previous_close)
    payload = analysis_payload(snapshot, indicators, evaluate(snapshot, indicators, Stock("META"), settings))
    message = CodexAnalyst(settings).summarize(payload)
    assert "ตรวจงบล่าสุด" in message and "ตัวเลข/หลักฐาน" in message


def test_codex_unavailable_uses_existing_analysis_error(monkeypatch, settings):
    monkeypatch.setattr("app.codex_client.find_codex", lambda _: None)
    with pytest.raises(AnalysisError):
        CodexAnalyst(settings).summarize({"symbol": "META"})


def test_mock_remains_offline_when_codex_is_selected(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("ANALYST_MODE", "codex")
    settings = Settings.from_env()
    assert settings.analyst_mode == "template" and settings.configured_analyst_mode == "codex"


def test_codex_live_does_not_require_openai_key(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("ANALYST_MODE", "codex")
    monkeypatch.setenv("STOCK_API_KEY", "fake-price-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("NOTIFIER_MODE", "console")
    settings = Settings.from_env()
    settings.validate_connections()
    assert settings.analyst_mode == "codex"
