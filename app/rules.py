"""Rules consume computed numbers; the language model cannot trigger an alert."""

from dataclasses import dataclass

from app.config import Settings, Stock
from app.fetcher import Snapshot
from app.indicators import Indicators


@dataclass(frozen=True)
class Event:
    key: str
    symbol: str
    rule: str
    event_date: str
    title: str
    evidence: dict


def evaluate(snapshot: Snapshot, indicators: Indicators, stock: Stock, settings: Settings) -> list[Event]:
    result = []
    def add(rule: str, title: str, daily_bar: bool = False, **evidence) -> None:
        day = (snapshot.bars[-1].day if daily_bar else snapshot.session_date).isoformat()
        result.append(Event(f"{snapshot.source}:{stock.symbol}:{rule}:{day}", stock.symbol,
                            rule, day, title, evidence))
    change = indicators.daily_change_pct
    if change <= -settings.price_drop_pct:
        add("price_drop", "ราคาลดลงรายวันถึงเกณฑ์", change_pct=change, threshold_pct=-settings.price_drop_pct)
    if change >= settings.price_rise_pct:
        add("price_rise", "ราคาเพิ่มขึ้นรายวันถึงเกณฑ์", change_pct=change, threshold_pct=settings.price_rise_pct)
    if stock.target_price is not None and snapshot.price < stock.target_price:
        add("below_target", "ราคาต่ำกว่าระดับที่คุณกำหนด", price=snapshot.price,
            target_price=stock.target_price, gap_pct=indicators.target_gap_pct)
    if indicators.rsi14 is not None:
        if indicators.rsi14 < settings.rsi_low:
            add("rsi_low", "RSI 14 ต่ำกว่าเกณฑ์", True, rsi14=indicators.rsi14, threshold=settings.rsi_low)
        if indicators.rsi14 > settings.rsi_high:
            add("rsi_high", "RSI 14 สูงกว่าเกณฑ์", True, rsi14=indicators.rsi14, threshold=settings.rsi_high)
    values = (indicators.sma20, indicators.sma50, indicators.previous_sma20, indicators.previous_sma50)
    if all(x is not None for x in values):
        fast, slow, old_fast, old_slow = values
        evidence = dict(sma20=fast, sma50=slow, previous_sma20=old_fast, previous_sma50=old_slow)
        if old_fast <= old_slow and fast > slow:
            add("sma_cross_up", "SMA 20 ตัดขึ้นเหนือ SMA 50", True, **evidence)
        elif old_fast >= old_slow and fast < slow:
            add("sma_cross_down", "SMA 20 ตัดลงใต้ SMA 50", True, **evidence)
    return result


def analysis_payload(snapshot: Snapshot, indicators: Indicators, events: list[Event]) -> dict:
    """Allowlist only; excludes holdings, cost basis, credentials and raw history."""
    return {
        "symbol": snapshot.symbol, "currency": snapshot.currency, "source": snapshot.source,
        "simulated": snapshot.simulated, "quote_as_of": snapshot.as_of.isoformat(),
        "indicators_as_of": snapshot.bars[-1].day.isoformat(),
        "price": snapshot.price, "previous_close": snapshot.previous_close,
        "indicators": indicators.to_dict(),
        "events": [{"rule": e.rule, "title": e.title, "date": e.event_date, "evidence": e.evidence} for e in events],
        "news_verified": False, "portfolio_context_available": False,
    }
