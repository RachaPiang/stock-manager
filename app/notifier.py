"""Outgoing notifications only; no HTTP listener or trading connection."""

from abc import ABC, abstractmethod
import json
import re
from urllib.parse import urlparse

import requests

from app.config import Settings


class NotificationError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def text_messages(message):
    """Split at paragraph/line boundaries, preserving every character and link."""
    remaining, parts = str(message), []
    while remaining:
        chunk = remaining.encode('utf-16-le')[:4500*2].decode('utf-16-le', errors='ignore')
        if len(chunk) < len(remaining):
            boundary = chunk.rfind('\n\n')
            if boundary < len(chunk)//2:
                boundary = chunk.rfind('\n')
            if boundary > 0:
                chunk = chunk[:boundary+1]
        parts.append({'type': 'text', 'text': chunk})
        remaining = remaining[len(chunk):]
    if not parts or len(parts) > 5:
        raise NotificationError('LINE message exceeds five text objects', retryable=False)
    return parts


_NEWS_REFERENCE = re.compile(r'\[\[NEWS_REFERENCE:([0-9a-f]+)\]\]')
_LEGACY_NEWS_SOURCE = re.compile(
    r'(?m)^(?P<symbol>MARKET|META|GOOGL|ETN|NVDA|MSFT|TXN|ASML|AMZN|V)\s·\s'
    r'(?P<publisher>[^\n]{1,100})\s·\s(?P<date>\d{4}-\d{2}-\d{2})\n'
    r'(?P<url>https://news\.google\.com/rss/articles/[^\s]+)\s*$')


def _news_references(message):
    """Remove internal reference records and return safe LINE button data."""
    references = []
    def take(match):
        try:
            value = json.loads(bytes.fromhex(match.group(1)).decode('utf-8'))
            url = str(value.get('source_url', ''))
            parsed = urlparse(url)
            # Feeds are already checked at collection time.  Validate again at
            # the delivery boundary so a manually changed database cannot make
            # LINE open an arbitrary scheme or host.
            if (parsed.scheme == 'https' and not parsed.username and not parsed.password and parsed.port in (None, 443)
                    and ((parsed.hostname == 'news.google.com' and parsed.path.startswith('/rss/articles/'))
                         or parsed.hostname in {'nvidianews.nvidia.com','investor.atmeta.com'}
                         or (parsed.hostname == 'www.sec.gov' and bool(re.fullmatch(
                             r'/Archives/edgar/data/\d+/\d+/\d{10}-\d{2}-\d{6}-index.html', parsed.path))))):
                references.append({key: str(value.get(key, '')) for key in
                                   ('symbol', 'title', 'source_name', 'source_url', 'published_at')})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        return ''
    visible = _NEWS_REFERENCE.sub(take, str(message))

    # A saved weekly note made by the previous version may still contain
    # visible Google redirect URLs. Convert only its tightly defined old
    # source block, so sending “ข่าว” immediately also gets the cleaner UI.
    def legacy(match):
        references.append({'symbol': match.group('symbol'), 'title': 'หัวข้ออ้างอิงในสรุปข่าว',
                           'source_name': match.group('publisher'), 'source_url': match.group('url'),
                           'published_at': match.group('date')})
        return ''
    visible = _LEGACY_NEWS_SOURCE.sub(legacy, visible)
    return visible.strip(), references[:20]


def _short(value, maximum):
    return str(value).encode('utf-16-le')[:maximum*2].decode('utf-16-le', errors='ignore')


def news_flex(references):
    """One labelled, clickable carousel; long URLs never appear in the chat."""
    bubbles = []
    for ref in references:
        publisher = _short(ref['source_name'] or 'แหล่งข่าว', 50)
        when = ref['published_at'][:10]
        bubbles.append({'type': 'bubble', 'size': 'micro',
                        'body': {'type': 'box', 'layout': 'vertical', 'spacing': 'sm', 'contents': [
                            {'type': 'text', 'text': _short(ref['symbol'] or 'MARKET', 20),
                             'weight': 'bold', 'size': 'sm', 'color': '#126d70'},
                            {'type': 'text', 'text': _short(ref['title'] or 'เปิดหัวข้อข่าว', 160),
                             'wrap': True, 'maxLines': 5, 'size': 'sm'},
                            {'type': 'text', 'text': _short((publisher+' · '+when).strip(' ·'), 80),
                             'wrap': True, 'size': 'xs', 'color': '#6b7280'}]},
                        'footer': {'type': 'box', 'layout': 'vertical', 'contents': [
                            {'type': 'button', 'style': 'link', 'height': 'sm',
                             'action': {'type': 'uri', 'label': 'เปิดแหล่งข่าว', 'uri': ref['source_url']}}]}})
    return {'type': 'flex', 'altText': 'แหล่งอ้างอิงข่าว '+str(len(bubbles))+' รายการ',
            'contents': {'type': 'carousel', 'contents': bubbles}}


def line_messages(message):
    """Normal text plus, when present, friendly reference buttons for news."""
    visible, references = _news_references(message)
    messages = text_messages(visible)
    if references:
        groups = [references[i:i+10] for i in range(0, len(references), 10)]
        if len(messages)+len(groups) > 5:
            raise NotificationError('LINE news summary is too long for source buttons', retryable=False)
        messages.extend(news_flex(group) for group in groups)
    return messages


class Notifier(ABC):
    channel = "console"
    recipient = "local"

    @abstractmethod
    def send(self, message: str, retry_key: str, recipient: str) -> None:
        pass


class ConsoleNotifier(Notifier):
    def send(self, message: str, retry_key: str, recipient: str) -> None:
        print(f"\n{message}\n", flush=True)


class LineNotifier(Notifier):
    channel = "line"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.token = settings.line_token
        self.recipient = settings.line_user_id
        self.timeout = settings.http_timeout

    def reply(self, message: str, reply_token: str) -> None:
        messages = line_messages(message)
        from app.line_portfolios import quick_replies
        messages[-1]['quickReply'] = quick_replies(self.settings)
        try:
            response = requests.post('https://api.line.me/v2/bot/message/reply',
                headers={'Authorization': f'Bearer {self.token}'},
                json={'replyToken': reply_token, 'messages': messages}, timeout=self.timeout)
        except requests.RequestException:
            raise NotificationError('LINE reply delivery unknown', retryable=False) from None
        if response.status_code != 200:
            raise NotificationError(f'LINE reply HTTP {response.status_code}', retryable=False)

    def send(self, message: str, retry_key: str, recipient: str) -> None:
        messages = line_messages(message)
        from app.line_portfolios import quick_replies
        messages[-1]['quickReply'] = quick_replies(self.settings, dynamic=False)
        try:
            response = requests.post("https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {self.token}", "X-Line-Retry-Key": retry_key},
                json={"to": recipient, "messages": messages}, timeout=self.timeout)
        except requests.RequestException:
            raise NotificationError("LINE connection failed; delivery status unknown") from None
        if response.status_code == 200:
            return
        if response.status_code == 409 and response.headers.get("x-line-accepted-request-id"):
            return  # Same retry key was previously accepted by LINE.
        raise NotificationError(f"LINE HTTP {response.status_code}",
                                retryable=response.status_code == 429 or response.status_code >= 500)
