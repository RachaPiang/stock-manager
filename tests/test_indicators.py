import math

import pytest

from app.indicators import calculate, percent_change, rsi, sma


def test_percentage_uses_previous_close():
    assert percent_change(110, 100) == pytest.approx(10)
    assert percent_change(90, 100) == pytest.approx(-10)


def test_sma_uses_last_n_closes():
    assert sma([10, 20, 30, 40], 3) == 30
    assert sma([1, 2], 3) is None


def test_rsi_known_wilder_example_and_smoothing():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert rsi(closes) == pytest.approx(70.464135, abs=0.000001)
    assert rsi(closes + [46.00]) == pytest.approx(66.249619, abs=0.000001)


@pytest.mark.parametrize("closes,expected", [
    (list(range(1, 21)), 100), (list(range(20, 0, -1)), 0), ([100] * 20, 50)])
def test_rsi_extremes(closes, expected):
    assert rsi(closes) == expected


def test_insufficient_rsi_needs_fifteen_prices():
    assert rsi([100] * 14) is None
    assert rsi([100] * 15) == 50


@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf])
def test_bad_prices_are_rejected(bad):
    with pytest.raises(ValueError):
        rsi([100] * 20 + [bad])
    with pytest.raises(ValueError):
        percent_change(100, bad)


@pytest.mark.parametrize("period", [0, -2, 1.5, True])
def test_invalid_period(period):
    with pytest.raises(ValueError):
        sma([1, 2], period)


def test_calculate_previous_sma_and_target():
    result = calculate(list(range(1, 52)), 55, 50, 50)
    assert result.sma50 == 26.5
    assert result.previous_sma50 == 25.5
    assert result.target_gap_pct == pytest.approx(10)
    assert calculate([100] * 50, 99, 100).previous_sma50 is None
