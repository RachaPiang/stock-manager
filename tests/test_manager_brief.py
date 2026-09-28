from datetime import UTC, datetime

from app.manager_brief import news_digest, today_brief


def test_today_brief_reads_saved_data_without_instruction():
    report = {
        'portfolio': {'live': {'complete': True, 'total_usd': 1200, 'gain_usd': 100, 'gain_pct': 9,
                               'day_change_usd': -10, 'day_change_pct': -0.8, 'stale': False},
                      'holdings': [{'symbol': 'META', 'quantity': 1, 'live_price': 100,
                                    'live_previous_value_usd': 110}]},
        'stocks': [{'symbol': 'META', 'signals': ['RSI 14 ต่ำกว่าเกณฑ์']}],
        'news': [{'symbol': 'META', 'title': 'Results update'}],
    }
    message = today_brief(report, datetime(2026, 9, 25, 3, tzinfo=UTC))
    assert 'มูลค่า $1,200.00' in message
    assert 'META  -9.09%' in message
    assert 'RSI 14' in message
    assert 'ราคาอัปเดตถึง' in message


def test_news_digest_requires_source_links():
    assert not news_digest([{'symbol': 'NVDA', 'title': 'No source'}])
    message = news_digest([{'symbol': 'NVDA', 'title': 'Results', 'reason': 'ผลประกอบการ',
                            'source_url': 'https://example.test/source'}])
    assert 'https://example.test/source' in message
    assert 'ไม่ยืนยันว่าราคาควรขึ้นหรือลง' in message
