"""Configuration has safe defaults; secrets are never logged."""

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def positive(value: object, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name}: expected a number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ValueError(f"{name}: expected a finite {'non-negative' if allow_zero else 'positive'} number")
    return number


@dataclass(frozen=True)
class Stock:
    symbol: str
    target_price: float | None = None
    shares: float | None = None
    average_cost: float | None = None
    target_weight: float | None = None


def load_watchlist(path: Path) -> list[Stock]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload.get("stocks") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("watchlist must contain a non-empty stocks list")
    stocks, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each watchlist item must be an object")
        symbol = str(row.get("symbol", "")).strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", symbol) or symbol in seen:
            raise ValueError("Invalid or duplicate stock symbol")
        values = {}
        for key in ("target_price", "shares", "average_cost", "target_weight"):
            value = row.get(key)
            values[key] = None if value is None else positive(value, key, allow_zero=key in {"shares", "target_weight"})
        if values["target_weight"] is not None and values["target_weight"] > 1:
            raise ValueError("target_weight must be a fraction between 0 and 1")
        stocks.append(Stock(symbol=symbol, **values))
        seen.add(symbol)
    return stocks


@dataclass(frozen=True)
class Settings:
    mock_mode: bool = True
    database_path: Path = ROOT / "data/mock.sqlite3"
    watchlist_path: Path = ROOT / "config/watchlist.json"
    price_drop_pct: float = 5.0
    price_rise_pct: float = 8.0
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    cooldown_hours: float = 24.0
    log_level: str = "INFO"
    stock_provider: str = "twelvedata"
    stock_api_key: str = field(default="", repr=False)
    request_interval: float = 8.0
    history_bars: int = 3000
    http_timeout: float = 20.0
    max_quote_age_hours: float = 96.0
    analyst_mode: str = "template"
    configured_analyst_mode: str = "template"
    codex_cli_path: str = ""
    codex_model: str = ""
    codex_timeout: float = 180.0
    ai_max_calls_per_day: int = 9
    ai_max_calls_per_stock_per_day: int = 1
    news_mode: str = "press_releases"
    news_lookback_days: int = 14
    news_per_symbol: int = 3
    news_sync_min_hours: float = 24.0
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = ""
    notifier_mode: str = "console"
    line_token: str = field(default="", repr=False)
    line_user_id: str = field(default="", repr=False)
    line_channel_secret: str = field(default='', repr=False)
    manager_push_limit: int = 8

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False, interpolate=False)
        def env(name: str, default: str = "") -> str:
            return os.getenv(name, default).strip()
        mode = env("MOCK_MODE", "true").lower()
        if mode not in {"true", "false"}:
            raise ValueError("MOCK_MODE must be true or false")
        mock = mode == "true"
        def path(name: str, default: str) -> Path:
            return (ROOT / env(name, default)).resolve()
        numeric = {
            "price_drop_pct": ("PRICE_DROP_PCT", "5"),
            "price_rise_pct": ("PRICE_RISE_PCT", "8"),
            "rsi_low": ("RSI_LOW", "30"), "rsi_high": ("RSI_HIGH", "70"),
            "cooldown_hours": ("COOLDOWN_HOURS", "24"),
            "request_interval": ("PROVIDER_REQUEST_INTERVAL_SECONDS", "8"),
            "http_timeout": ("HTTP_TIMEOUT_SECONDS", "20"),
            "max_quote_age_hours": ("MAX_QUOTE_AGE_HOURS", "96"),
            "codex_timeout": ("CODEX_TIMEOUT_SECONDS", "180"),
        }
        values = {key: positive(env(name, default), name, allow_zero=key in {"cooldown_hours", "request_interval", "rsi_low"})
                  for key, (name, default) in numeric.items()}
        if not 0 <= values["rsi_low"] < values["rsi_high"] <= 100:
            raise ValueError("Require 0 <= RSI_LOW < RSI_HIGH <= 100")
        level = env("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("Invalid LOG_LEVEL")
        settings = cls(
            mock_mode=mock, log_level=level, **values,
            history_bars=int(env("HISTORY_BARS", "3000")),
            ai_max_calls_per_day=int(env("AI_MAX_CALLS_PER_DAY", "9")),
            ai_max_calls_per_stock_per_day=int(env("AI_MAX_CALLS_PER_STOCK_PER_DAY", "1")),
            news_mode="off" if mock else env("NEWS_MODE", "press_releases").lower(),
            news_lookback_days=int(env("NEWS_LOOKBACK_DAYS", "14")),
            news_per_symbol=int(env("NEWS_PER_SYMBOL", "3")),
            news_sync_min_hours=positive(env("NEWS_SYNC_MIN_HOURS", "24"), "NEWS_SYNC_MIN_HOURS", allow_zero=True),
            database_path=path("MOCK_DATABASE_PATH" if mock else "LIVE_DATABASE_PATH",
                               "data/mock.sqlite3" if mock else "data/live.sqlite3"),
            watchlist_path=path("WATCHLIST_PATH", "config/watchlist.json"),
            stock_provider=env("STOCK_PROVIDER", "twelvedata").lower(),
            stock_api_key=env("STOCK_API_KEY"),
            analyst_mode="template" if mock else env("ANALYST_MODE", "codex").lower(),
            configured_analyst_mode=env("ANALYST_MODE", "codex").lower(),
            codex_cli_path=env("CODEX_CLI_PATH"), codex_model=env("CODEX_MODEL"),
            openai_api_key=env("OPENAI_API_KEY"), openai_model=env("OPENAI_MODEL"),
            notifier_mode="console" if mock else env("NOTIFIER_MODE", "console").lower(),
            line_token=env("LINE_CHANNEL_ACCESS_TOKEN"), line_user_id=env("LINE_USER_ID"),
            line_channel_secret=env('LINE_CHANNEL_SECRET'),
            manager_push_limit=int(env('MANAGER_PUSH_LIMIT_PER_DAY', '8')),
        )
        if (settings.configured_analyst_mode not in {"template", "openai", "codex"}
                or settings.notifier_mode not in {"console", "line"}
                or settings.news_mode not in {"off", "press_releases"}):
            raise ValueError("Invalid analyst, notifier or news mode")
        if not 200 <= settings.history_bars <= 5000:
            raise ValueError("HISTORY_BARS must be between 200 and 5000")
        if settings.ai_max_calls_per_day < 0 or settings.ai_max_calls_per_stock_per_day < 0:
            raise ValueError("AI call limits must be non-negative integers")
        if settings.manager_push_limit < 0:
            raise ValueError('MANAGER_PUSH_LIMIT_PER_DAY must be non-negative')
        if not 1 <= settings.news_lookback_days <= 90 or not 1 <= settings.news_per_symbol <= 10:
            raise ValueError("NEWS_LOOKBACK_DAYS must be 1-90 and NEWS_PER_SYMBOL must be 1-10")
        return settings

    def validate_connections(self) -> None:
        if self.mock_mode:
            return
        if self.stock_provider != "twelvedata" or not self.stock_api_key:
            raise ValueError("Live mode requires STOCK_PROVIDER=twelvedata and STOCK_API_KEY")
        if self.analyst_mode == "openai" and not (self.openai_api_key and self.openai_model):
            raise ValueError("OpenAI mode requires OPENAI_API_KEY and OPENAI_MODEL")
        if self.notifier_mode == "line" and not (self.line_token and self.line_user_id):
            raise ValueError("LINE mode requires LINE_CHANNEL_ACCESS_TOKEN and LINE_USER_ID")
