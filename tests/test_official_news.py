from datetime import UTC, datetime

import pytest

from app.news import NewsError
from app.official_news import parse_feed


def test_source_links_and_title_filter():
    feed = b'''<rss><channel><item><title>Quarterly earnings results</title>
    <description>Revenue details</description><link>https://nvidianews.nvidia.com/releases/test</link>
    <pubDate>Wed, 23 Sep 2026 12:00:00 GMT</pubDate></item>
    <item><title>New product</title><description>CEO says hello</description>
    <link>https://nvidianews.nvidia.com/releases/product</link><pubDate>Wed, 23 Sep 2026 12:00:00 GMT</pubDate></item>
    <item><title>earnings</title><link>https://evil.example/test</link><pubDate>Wed, 23 Sep 2026 12:00:00 GMT</pubDate></item>
    </channel></rss>'''
    items = parse_feed(feed, 'NVDA', datetime(2026, 9, 24, tzinfo=UTC))
    assert len(items) == 1
    assert items[0].source_url == 'https://nvidianews.nvidia.com/releases/test'
    assert items[0].source_name == 'NVDA · official company RSS'


def test_unsafe_xml():
    with pytest.raises(NewsError):
        parse_feed(b'<!DOCTYPE rss><rss/>', 'NVDA', datetime.now(UTC))
