from dataclasses import replace
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

from app.setup import doctor, save_environment


def test_settings_update_preserves_other_values_and_literal_dollars(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# my settings\nMOCK_MODE=true\nPRICE_DROP_PCT=7\n", encoding="utf-8")
    save_environment(path, {"OPENAI_API_KEY": "test-${LITERAL}-'key", "OPENAI_MODEL": "example-model"})
    values = dotenv_values(path, interpolate=False)
    assert values["PRICE_DROP_PCT"] == "7"
    assert values["MOCK_MODE"] == "true"
    assert values["OPENAI_API_KEY"] == "test-${LITERAL}-'key"
    assert "# my settings" in path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.env.tmp"))


def test_newline_settings_rejected_before_writing(tmp_path):
    path = tmp_path / ".env"
    with pytest.raises(ValueError):
        save_environment(path, {"OPENAI_API_KEY": "a\nMOCK_MODE=false"})
    assert not path.exists()


def test_doctor_offline_never_displays_secrets(settings, capsys):
    settings = replace(settings, stock_api_key="stock-secret", openai_api_key="ai-secret", openai_model="a-model", line_token="line-secret", line_user_id="private-user")
    assert doctor(settings) == 0
    output = capsys.readouterr().out
    assert not any(v in output for v in ("stock-secret", "ai-secret", "line-secret", "private-user"))


def test_missing_credentials_reported_without_network(settings, capsys):
    assert doctor(settings, online=True) == 1
    assert "ยังไม่มีข้อมูลเชื่อมต่อครบ" in capsys.readouterr().out


def test_line_probe_checks_recipient_without_sending(settings, monkeypatch, capsys):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr("app.setup.requests.get", get)
    assert doctor(replace(settings, line_token="fake-token", line_user_id="U" + "a" * 32), online=True, service="line") == 0
    assert len(calls) == 2 and calls[-1].endswith("/profile/U" + "a" * 32)
    assert "fake-token" not in capsys.readouterr().out
