"""Pure numeric functions. Input order: oldest to newest completed daily close."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from math import fsum, isfinite


def _validate(closes: Sequence[float], period: int) -> None:
    if not isinstance(period, int) or isinstance(period, bool) or period < 1:
        raise ValueError("period must be a positive integer")
    if any(not isfinite(x) or x <= 0 for x in closes):
        raise ValueError("closes must be finite positive numbers")


def percent_change(current: float, previous: float) -> float:
    _validate([current, previous], 1)
    return (current / previous - 1) * 100


def sma(closes: Sequence[float], period: int) -> float | None:
    _validate(closes, period)
    return fsum(closes[-period:]) / period if len(closes) >= period else None


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder RSI. First average is simple; subsequent averages use alpha=1/n.

    Requires n+1 prices. A completely flat sequence is defined as RSI=50.
    """
    _validate(closes, period)
    if len(closes) <= period:
        return None
    changes = [right - left for left, right in zip(closes, closes[1:])]
    gain = fsum(max(x, 0) for x in changes[:period]) / period
    loss = fsum(max(-x, 0) for x in changes[:period]) / period
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
    if gain == loss == 0:
        return 50.0
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


@dataclass(frozen=True)
class Indicators:
    daily_change_pct: float
    sma20: float | None
    sma50: float | None
    previous_sma20: float | None
    previous_sma50: float | None
    rsi14: float | None
    target_gap_pct: float | None
    sma200: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def calculate(closes: Sequence[float], price: float, previous_close: float,
              target_price: float | None = None) -> Indicators:
    return Indicators(percent_change(price, previous_close), sma(closes, 20), sma(closes, 50),
                      sma(closes[:-1], 20), sma(closes[:-1], 50), rsi(closes),
                      percent_change(price, target_price) if target_price is not None else None, sma(closes, 200))
