from datetime import date

import pytest

from app.benchmark import parse_csv, comparisons, parse_recent_page


def test_parser():
    result = parse_csv('observation_date,SP500\n2026-09-21,100\n2026-09-22,.\n2026-09-23,110\n', date(2026, 9, 24))
    assert result == {'2026-09-21': 100, '2026-09-23': 110}


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '0'])
def test_invalid_price(value):
    with pytest.raises(ValueError):
        parse_csv('DATE,SP500\n2026-09-21,100\n2026-09-23,'+value, date(2026, 9, 24))


def test_common_dates_and_rebase():
    result = comparisons([{'day': '2026-09-01', 'value_usd': 10},
                          {'day': '2026-09-21', 'value_usd': 100},
                          {'day': '2026-09-22', 'value_usd': 900},
                          {'day': '2026-09-23', 'value_usd': 120}],
                         {'2026-09-01': 10, '2026-09-21': 200, '2026-09-23': 220})
    assert [r['day'] for r in result['1w']] == ['2026-09-21', '2026-09-23']
    assert result['1w'][0]['value_usd'] == result['1w'][0]['benchmark_pct'] == 0
    assert result['1w'][-1]['value_usd'] == pytest.approx(20)
    assert result['1w'][-1]['benchmark_pct'] == pytest.approx(10)
    assert result['1d'] == []


def test_insufficient_data():
    assert comparisons([{'day': '2026-09-21', 'value_usd': 100}], {'2026-09-21': 200})['all'] == []


def test_official_recent_page_fallback():
    html = '<table><tr><td>2026-09-23:&nbsp;</td><td>7,000.10</td></tr><tr><td>2026-09-24:&nbsp;</td><td>7,100.20</td></tr></table>'
    assert parse_recent_page(html, date(2026, 9, 25)) == {'2026-09-23': 7000.1, '2026-09-24': 7100.2}
    with pytest.raises(ValueError):
        parse_recent_page('<html>unavailable</html>', date(2026, 9, 25))
