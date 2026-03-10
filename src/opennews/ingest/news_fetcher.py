from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import feedparser
from dateutil import parser as dt_parser


@dataclass(slots=True)
class NewsItem:
    news_id: str
    title: str
    content: str
    source: str
    url: str
    published_at: datetime


def _make_news_id(url: str, published_at: datetime) -> str:
    raw = f"{url}|{published_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def fetch_rss_news(
    sources: Iterable[str], limit: int = 100, since: datetime | None = None
) -> list[NewsItem]:
    items: list[NewsItem] = []
    for source in sources:
        feed = feedparser.parse(source)
        for entry in feed.entries[:limit]:
            title = getattr(entry, "title", "").strip()
            summary = getattr(entry, "summary", "").strip()
            link = getattr(entry, "link", "").strip()
            published_raw = getattr(entry, "published", None) or getattr(
                entry, "updated", None
            )
            if not title or not link:
                continue
            try:
                published_at = (
                    dt_parser.parse(published_raw).astimezone(timezone.utc)
                    if published_raw
                    else datetime.now(timezone.utc)
                )
            except Exception:
                published_at = datetime.now(timezone.utc)

            if since and published_at <= since:
                continue

            items.append(
                NewsItem(
                    news_id=_make_news_id(link, published_at),
                    title=title,
                    content=summary or title,
                    source=source,
                    url=link,
                    published_at=published_at,
                )
            )
    return items


def deduplicate_news(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        key = f"{item.url}|{item.published_at.isoformat()}"
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
