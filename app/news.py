"""Official-company press releases only; keyword triage before any AI review."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import requests

from app.config import Settings
from app.market_state import BudgetExceeded, MarketState


class NewsError(Exception):
    """Safe error that never includes vendor payloads or API keys."""


@dataclass(frozen=True)
class NewsItem:
    source_id: str
    symbol: str
    published_at: datetime
    title: str
    excerpt: str
    importance: str
    reason: str
    source_name: str = "Twelve Data · official company press release"
    source_url: str | None = None


class NewsProvider(ABC):
    @abstractmethod
    def fetch(self, symbol: str, now: datetime) -> list[NewsItem]:
        """Return already-filtered, source-labelled news items."""


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
    def handle_data(self, data: str):
        self.parts.append(data)


def plain_text(value: object, limit: int = 1200) -> str:
    parser = _TextOnly()
    parser.feed(str(value or ""))
    return " ".join(" ".join(parser.parts).split())[:limit]


KEYWORDS = (
    ("earnings", "ผลประกอบการหรือแนวโน้มธุรกิจ"),
    ("financial results", "ผลประกอบการหรือแนวโน้มธุรกิจ"),
    ("guidance", "แนวโน้มธุรกิจ"),
    ("outlook", "แนวโน้มธุรกิจ"),
    ("acquisition", "ดีลซื้อกิจการหรือการลงทุนสำคัญ"),
    ("merger", "ดีลควบรวมกิจการ"),
    ("chief executive", "การเปลี่ยนผู้บริหาร"),
    ("chief financial", "การเปลี่ยนผู้บริหาร"),
    ("ceo", "การเปลี่ยนผู้บริหาร"),
    ("cfo", "การเปลี่ยนผู้บริหาร"),
    ("regulatory", "ความเสี่ยงกฎระเบียบ"),
    ("antitrust", "ความเสี่ยงการแข่งขันหรือกฎระเบียบ"),
    ("export control", "ข้อจำกัดการส่งออก"),
    ("investigation", "ความเสี่ยงด้านกฎหมายหรือการสอบสวน"),
)


def classify(title: str, excerpt: str) -> tuple[str, str] | None:
    text = (title + " " + excerpt).lower()
    for keyword, reason in KEYWORDS:
        if keyword in text:
            return ("high", reason)
    return None


class TwelveDataPressReleaseProvider(NewsProvider):
    """Uses the documented /press_releases feed, never unlabelled web articles."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.state = MarketState(settings.database_path.parent / "market-api.sqlite3")
        self.session = requests.Session()

    def fetch(self, symbol: str, now: datetime) -> list[NewsItem]:
        try:
            self.state.acquire(self.settings.request_interval, lambda: True)
            response = self.session.get("https://api.twelvedata.com/press_releases", params={
                "symbol": symbol, "start_date": (now - timedelta(days=self.settings.news_lookback_days)).isoformat(),
                "end_date": now.isoformat(), "language": "en", "outputsize": self.settings.news_per_symbol,
                "apikey": self.settings.stock_api_key}, timeout=self.settings.http_timeout)
            if response.status_code != 200:
                raise NewsError("News source unavailable; retry a later sync")
            payload = response.json()
        except BudgetExceeded as exc:
            raise NewsError(str(exc)) from None
        except (requests.RequestException, ValueError):
            raise NewsError("News source unavailable or returned invalid data") from None
        rows = payload.get("press_releases") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise NewsError("News source rejected request or returned incomplete data")
        result = []
        for row in rows:
            try:
                title, excerpt = str(row["title"]).strip(), plain_text(row.get("body"))
                classification = classify(title, excerpt)
                published = datetime.fromisoformat(str(row["datetime"]).replace("Z", "+00:00")).astimezone(UTC)
                source_id = str(row["id"])
                if not title or not source_id or not classification:
                    continue
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            importance, reason = classification
            result.append(NewsItem(source_id, symbol, published, title[:500], excerpt, importance, reason))
        return result

