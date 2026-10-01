from types import SimpleNamespace

import pytest

from app import desktop


def test_validation_does_not_save_or_mutate_parent_environment(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('STOCK_API_KEY=private-test-key\nRSI_LOW=30\n', encoding='utf-8')
    monkeypatch.setattr(desktop, 'ROOT', tmp_path)
    monkeypatch.delenv('STOCK_API_KEY', raising=False)
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs['env'])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(desktop, 'run_process', run)
    desktop.validate_changes({'RSI_LOW': '25'})
    assert calls[0]['RSI_LOW'] == '25'
    assert calls[0]['STOCK_API_KEY'] == 'private-test-key'
    assert 'STOCK_API_KEY' not in desktop.os.environ
    assert 'RSI_LOW=30' in (tmp_path / '.env').read_text()


def test_invalid_setting_rejected_without_exposing_values(monkeypatch):
    monkeypatch.setattr(desktop, 'run_process', lambda *a, **kw: SimpleNamespace(returncode=2))
    with pytest.raises(ValueError) as error:
        desktop.validate_changes({'RSI_LOW': 'private-invalid-value'})
    assert 'private-invalid-value' not in str(error.value)


@pytest.mark.parametrize('key', ['STOCK_API_KEY', 'OPENAI_API_KEY', 'LINE_CHANNEL_SECRET',
                                  'LINE_CHANNEL_ACCESS_TOKEN', 'LINE_USER_ID'])
def test_credentials_are_masked(key):
    assert desktop.is_secret(key)
