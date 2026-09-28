"""Completed five-minute OHLC bars; never approximate highs/lows from sampled prices."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite, sin
from zoneinfo import ZoneInfo

from app.market import is_open

NY = ZoneInfo("America/New_York")


def previous_session_day(at: datetime):
    day = at.astimezone(NY).replace(hour=9, minute=30, second=0, microsecond=0)
    for _ in range(14):
        day -= timedelta(days=1)
        if is_open(day):
            return day.date()
    raise ValueError("Previous session is outside supported calendar")


@dataclass(frozen=True)
class IntradayBar:
    at: datetime  # inclusive start of the five-minute interval, UTC
    open: float
    high: float
    low: float
    close: float

    @property
    def end(self):
        return self.at + timedelta(minutes=5)


def parse_bars(payload: dict, symbol: str, now: datetime) -> tuple[IntradayBar, ...]:
    meta = payload["meta"]
    if meta["symbol"] != symbol or meta["currency"] != "USD" or meta.get("interval") != "5min":
        raise ValueError("Unexpected intraday instrument/interval")
    if meta.get("exchange_timezone") not in {None, "America/New_York"}:
        raise ValueError("Unexpected exchange timezone")
    result = []
    for row in payload["values"]:
        at = datetime.fromisoformat(row["datetime"])
        at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
        if at.second or at.microsecond or at.minute % 5:
            raise ValueError("Misaligned five-minute bar")
        bar = IntradayBar(at, *(float(row[k]) for k in ("open", "high", "low", "close")))
        values = (bar.open, bar.high, bar.low, bar.close)
        if not all(isfinite(v) and v > 0 for v in values):
            raise ValueError("Invalid intraday OHLC")
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("Inconsistent intraday OHLC")
        # Omit partial/future bars and extended hours. Never invent gaps.
        if bar.end <= now and is_open(bar.at) and is_open(bar.end-timedelta(microseconds=1)):
            result.append(bar)
    result.sort(key=lambda b: b.at)
    if len({b.at for b in result}) != len(result):
        raise ValueError("Duplicate intraday bars")
    if not result:
        raise ValueError("No completed regular-session five-minute bars")
    return tuple(result)


def mock_bars(now: datetime, price: float) -> tuple[IntradayBar, ...]:
    day = now.astimezone(NY).replace(hour=9, minute=30, second=0, microsecond=0)
    for _ in range(14):
        if is_open(day) and day + timedelta(minutes=5) <= now:
            break
        day -= timedelta(days=1)
    else:
        return ()
    result = []
    for i in range(78):
        at = (day + timedelta(minutes=i*5)).astimezone(UTC)
        if at + timedelta(minutes=5) > now or not is_open(at):
            break
        opening = price * (1 + sin(i*.2)*.008)
        closing = price * (1 + sin((i+1)*.2)*.008)
        result.append(IntradayBar(at, opening, max(opening, closing)*1.001,
                                 min(opening, closing)*.999, closing))
    return tuple(result)
