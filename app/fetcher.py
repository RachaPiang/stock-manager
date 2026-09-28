"""Swappable market data providers. Only fetchers know vendor-specific schemas."""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from app.config import Settings, positive
from app.intraday import IntradayBar, parse_bars, mock_bars, previous_session_day

NY = ZoneInfo("America/New_York")


class DataError(Exception):
    """Safe, redacted error; never include response bodies or request URLs."""


@dataclass(frozen=True)
class DailyBar:
    day: date
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None


@dataclass(frozen=True)
class Snapshot:
    symbol: str
    price: float
    previous_close: float
    as_of: datetime
    bars: tuple[DailyBar, ...]
    source: str
    currency: str = "USD"
    simulated: bool = False
    intraday: tuple[IntradayBar, ...] = ()
    price_kind: str = "quote"

    @property
    def session_date(self) -> date:
        return self.as_of.astimezone(NY).date()

    def validate(self, now: datetime, max_age_hours: float) -> None:
        if self.as_of.tzinfo is None:
            raise DataError("Quote timestamp must include timezone")
        if self.currency != "USD":
            raise DataError("Expected a USD listing")
        try:
            positive(self.price, "price")
            positive(self.previous_close, "previous_close")
            for bar in self.bars:
                positive(bar.close, "daily close")
                ohl = (bar.open, bar.high, bar.low)
                if any(value is not None for value in ohl):
                    for value in ohl:
                        positive(value, "daily OHLC")
                    if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
                        raise ValueError("Inconsistent OHLC")
        except (ValueError, TypeError):
            raise DataError("Invalid or missing price") from None
        age = (now - self.as_of).total_seconds() / 3600
        if age < -0.1 or age > max_age_hours:
            raise DataError("Quote is stale or has a future timestamp")
        days = [bar.day for bar in self.bars]
        if not days or days != sorted(set(days)):
            raise DataError("Daily history is empty, duplicated or out of order")
        from app.market import completed_session
        completed = completed_session(now)
        if completed is None or days[-1] > completed:
            raise DataError("Daily history includes today's incomplete bar")
        if days[-1] > self.session_date or (now.astimezone(NY).date() - days[-1]).days > 8:
            raise DataError("Daily history is stale or inconsistent with quote")


class StockProvider(ABC):
    @abstractmethod
    def fetch(self, symbol: str, now: datetime) -> Snapshot:
        """Return normalized USD quote and ascending split-adjusted daily bars."""


class MockStockProvider(StockProvider):
    """Deterministic synthetic scenarios, never based on actual stock prices."""
    def __init__(self, history_bars: int = 100):
        self.history_bars = max(100, history_bars)

    def fetch(self, symbol: str, now: datetime) -> Snapshot:
        days = []
        day = now.astimezone(NY).date() - timedelta(days=1)
        while len(days) < self.history_bars:
            if day.weekday() < 5:
                days.append(day)
            day -= timedelta(days=1)
        days.reverse()
        base = 100 + sum(map(ord, symbol)) % 80
        # Quiet stocks stay flat so only intentionally designed scenarios trigger.
        closes = [float(base)] * 100
        if symbol == "NVDA":
            closes = [base + i * .12 + math.sin(i * .28) * 2 + math.cos(i * .63) * .65 for i in range(100)]
        elif symbol == "META":
            closes = [base - i * .08 + math.sin(i * .23) * 1.4 for i in range(100)]
        elif symbol == "TXN":
            closes = [base - i * 0.35 for i in range(100)]
        elif symbol == "ASML":
            closes = [base + i * 0.35 for i in range(100)]
        elif symbol == "MSFT":
            closes = [float(base)] * 99 + [base + 10.0]
        elif symbol == "AMZN":
            closes = [float(base)] * 99 + [base - 10.0]
        delta = {"META": -6.0, "NVDA": 10.0}.get(symbol, 0.5)
        extra = self.history_bars - 100
        closes = [closes[0] * (.35 + .65 * i / max(1, extra)) * (1 + .04 * math.sin(i * .05))
                  for i in range(extra)] + closes
        # Synthetic OHLC for demo only; never used as a fallback for live data.
        bars = tuple(DailyBar(day, round(close, 4), round(close * (1 + math.sin(i * .8) * .008), 4),
                             round(max(close, close * (1 + math.sin(i * .8) * .008)) * 1.009, 4),
                             round(min(close, close * (1 + math.sin(i * .8) * .008)) * .991, 4))
                     for i, (day, close) in enumerate(zip(days, closes)))
        return Snapshot(symbol, round(bars[-1].close * (1 + delta / 100), 4),
                        bars[-1].close, now, bars, "mock", simulated=True,
                        intraday=mock_bars(now, bars[-1].close))


class TwelveDataProvider(StockProvider):
    """Optional dormant live adapter; credentials/plan must be verified before use."""
    def __init__(self, settings: Settings, market_only: bool = False, intraday_prices: bool = False):
        from app.market_state import MarketState
        self.settings = settings
        self.market_only = market_only
        self.intraday_prices = intraday_prices
        self.state = MarketState(settings.database_path.parent / "market-api.sqlite3")
        self.session = requests.Session()

    def _get(self, endpoint: str, params: dict) -> dict:
        from app.market import is_open
        from app.market_state import BudgetExceeded
        try:
            self.state.acquire(self.settings.request_interval,
                               lambda: not self.market_only or is_open(datetime.now(UTC)))
        except BudgetExceeded as exc:
            raise DataError(str(exc)) from None
        try:
            response = self.session.get(f"https://api.twelvedata.com/{endpoint}",
                params={**params, "apikey": self.settings.stock_api_key},
                timeout=self.settings.http_timeout)
            if response.status_code != 200:
                raise DataError(f"Market API HTTP {response.status_code}; retry next run")
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise DataError("Market API unavailable or invalid JSON; retry next run") from None
        if not isinstance(payload, dict) or payload.get("status") == "error":
            raise DataError("Market API rejected request (check key, quota and plan)")
        return payload

    def fetch(self, symbol: str, now: datetime) -> Snapshot:
        if self.intraday_prices:
            bars = self.fetch_history(symbol, now)
            intraday = self.fetch_intraday(symbol, now)
            latest = intraday[-1]
            if latest.at.astimezone(NY).date() != now.astimezone(NY).date() or now-latest.end > timedelta(minutes=20):
                raise DataError("Five-minute data is delayed/stale; no new signals evaluated")
            previous = [b for b in bars if b.day < latest.at.astimezone(NY).date()]
            if not previous:
                raise DataError("Missing previous daily close")
            if previous[-1].day != previous_session_day(latest.at):
                raise DataError("Previous session close is not yet available; daily change cannot be trusted")
            snapshot = Snapshot(symbol, latest.close, previous[-1].close, latest.end, bars,
                                "twelvedata", intraday=intraday, price_kind="5min_close")
            snapshot.validate(now, self.settings.max_quote_age_hours)
            return snapshot
        quote = self._get("quote", {"symbol": symbol, "prepost": "false"})
        bars = self.fetch_history(symbol, now)
        try:
            if quote["symbol"] != symbol or quote["currency"] != "USD":
                raise DataError("Unexpected quote listing")
            snapshot = Snapshot(symbol, float(quote["close"]), float(quote["previous_close"]),
                                datetime.fromtimestamp(int(quote["timestamp"]), UTC), bars, "twelvedata")
        except (KeyError, ValueError, TypeError, OverflowError):
            raise DataError("Market API returned incomplete or malformed quote") from None
        snapshot.validate(now, self.settings.max_quote_age_hours)
        return snapshot

    def fetch_intraday(self, symbol: str, now: datetime) -> tuple[IntradayBar, ...]:
        payload = self._get("time_series", {"symbol": symbol, "interval": "5min", "outputsize": 500,
                                           "timezone": "UTC", "prepost": "false", "order": "ASC", "adjust": "splits"})
        try:
            return parse_bars(payload, symbol, now)
        except (KeyError, ValueError, TypeError, OverflowError):
            raise DataError("Five-minute OHLC unavailable or invalid; check plan and timestamps") from None

    def fetch_history(self, symbol: str, now: datetime) -> tuple[DailyBar, ...]:
        """One history request; versioned cache upgrades old short histories once."""
        from app.market import completed_session
        completed = completed_session(now)
        if completed is None:
            raise DataError('Outside supported session calendar')
        cache_day = 'closed:' + completed.isoformat()
        history = self.state.history(symbol, cache_day)
        if (history is None or history.get("_requested_bars") != self.settings.history_bars
                or history.get('_completed_through') != completed.isoformat()):
            history = self._get("time_series", {"symbol": symbol, "interval": "1day",
                                                "outputsize": self.settings.history_bars, "adjust": "splits", "order": "ASC"})
        try:
            if history["meta"]["symbol"] != symbol:
                raise DataError("Market API returned a different symbol")
            if history["meta"]["currency"] != "USD":
                raise DataError("Market API returned a non-USD listing")
            today = completed + timedelta(days=1)
            bars = tuple(sorted((DailyBar(date.fromisoformat(row["datetime"][:10]), float(row["close"]),
                *(float(row[key]) if row.get(key) is not None else None for key in ("open", "high", "low")))
                for row in history["values"] if date.fromisoformat(row["datetime"][:10]) < today), key=lambda b: b.day))
            if not bars:
                raise DataError("No completed daily bars")
            # Validate history independently; this is not a new market quote.
            snapshot = Snapshot(symbol, bars[-1].close, bars[-1].close, now, bars, "twelvedata")
        except (KeyError, ValueError, TypeError, OverflowError):
            raise DataError("Market API returned incomplete or malformed quote/history") from None
        snapshot.validate(now, self.settings.max_quote_age_hours)
        history["_requested_bars"] = self.settings.history_bars
        history['_completed_through'] = bars[-1].day.isoformat()
        self.state.save_history(symbol, cache_day, history)
        return bars
