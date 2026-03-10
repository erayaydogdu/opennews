from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import feedparser
from dateutil import parser as dt_parser

logger = logging.getLogger(__name__)


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


# ── Step3: 多平台并行抓取 ─────────────────────────────────────

def _detect_platform(source: str) -> str:
    """根据 URL 识别平台名。"""
    if "reuters" in source:
        return "reuters"
    if "weibo" in source:
        return "weibo"
    if "caixin" in source:
        return "caixin"
    if "sina" in source:
        return "sina"
    return "rss"


def _fetch_single_source(
    source: str, limit: int, since: datetime | None
) -> list[NewsItem]:
    """抓取单个源（线程安全）。"""
    platform = _detect_platform(source)
    try:
        items = fetch_rss_news([source], limit=limit, since=since)
        logger.info("fetched %d items from %s (%s)", len(items), platform, source[:60])
        return items
    except Exception:
        logger.exception("failed to fetch from %s (%s)", platform, source[:60])
        return []


def fetch_multi_platform(
    sources: list[str],
    limit: int = 100,
    since: datetime | None = None,
    max_workers: int = 4,
) -> list[NewsItem]:
    """并行抓取多平台新闻源。

    Step3: 支持微博 + 财新 + Reuters 等多源并行。
    """
    all_items: list[NewsItem] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(sources))) as pool:
        futures = {
            pool.submit(_fetch_single_source, src, limit, since): src
            for src in sources
        }
        for future in as_completed(futures):
            try:
                items = future.result(timeout=30)
                all_items.extend(items)
            except Exception:
                src = futures[future]
                logger.exception("timeout/error fetching %s", src[:60])

    return deduplicate_news(all_items)
