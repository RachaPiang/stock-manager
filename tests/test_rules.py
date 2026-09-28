from dataclasses import replace

import pytest

from app.config import Stock
from app.indicators import Indicators
from app.rules import analysis_payload, evaluate


def neutral(**changes):
    return replace(Indicators(0, 100, 100, 100, 100, 50, None), **changes)


@pytest.mark.parametrize("change,expected", [(-5, "price_drop"), (8, "price_rise"), (-4.99, None), (7.99, None)])
def test_price_boundaries(snapshot, settings, change, expected):
    rules = {e.rule for e in evaluate(snapshot, neutral(daily_change_pct=change), Stock("META"), settings)}
    assert rules == ({expected} if expected else set())


@pytest.mark.parametrize("value,expected", [(29.9, "rsi_low"), (30, None), (70, None), (70.1, "rsi_high"), (None, None)])
def test_rsi_strict_boundaries(snapshot, settings, value, expected):
    rules = {e.rule for e in evaluate(snapshot, neutral(rsi14=value), Stock("META"), settings)}
    assert rules == ({expected} if expected else set())


def test_target_is_optional_and_strict(snapshot, settings):
    assert not evaluate(snapshot, neutral(), Stock("META"), settings)
    assert not evaluate(snapshot, neutral(), Stock("META", target_price=snapshot.price), settings)
    result = evaluate(snapshot, neutral(target_gap_pct=-1), Stock("META", target_price=snapshot.price + 1), settings)
    assert result[0].rule == "below_target"


@pytest.mark.parametrize("values,expected", [
    (dict(sma20=101), "sma_cross_up"), (dict(sma20=99), "sma_cross_down"),
    (dict(sma20=101, previous_sma20=101), None),
    (dict(sma20=100), None), (dict(previous_sma50=None, sma20=101), None)])
def test_cross_needs_previous_observation(snapshot, settings, values, expected):
    result = evaluate(snapshot, neutral(**values), Stock("META"), settings)
    assert {e.rule for e in result} == ({expected} if expected else set())


def test_minimal_payload_and_distinct_event_dates(snapshot, settings):
    indicators = neutral(daily_change_pct=-6, rsi14=20)
    events = evaluate(snapshot, indicators, Stock("META", shares=999, average_cost=100), settings)
    assert events[0].event_date == snapshot.session_date.isoformat()
    assert events[1].event_date == snapshot.bars[-1].day.isoformat()
    payload = analysis_payload(snapshot, indicators, events)
    assert not {"shares", "average_cost", "api_key", "bars"} & payload.keys()
    assert payload["news_verified"] is False
