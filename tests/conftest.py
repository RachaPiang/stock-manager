import socket
import tempfile
from datetime import UTC, datetime

import pytest

from app.config import ROOT, Settings
from app.fetcher import MockStockProvider


def pytest_configure(config):
    # Avoid shared Windows temp symlinks owned by a different user/process.
    # Each run gets a newly created, empty directory inside this project.
    if not config.option.basetemp:
        (ROOT / "data").mkdir(exist_ok=True)
        config.option.basetemp = tempfile.mkdtemp(prefix="test-run-", dir=ROOT / "data")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must never access the network")
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def now():
    return datetime(2026, 9, 23, 15, 0, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=tmp_path / "mock.sqlite3")


@pytest.fixture
def snapshot(now):
    return MockStockProvider().fetch("META", now)
