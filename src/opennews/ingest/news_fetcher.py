from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


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


# ── NewsNow API 抓取 ──────────────────────────────────────────

def fetch_newsnow(
    api_url: str,
    sources: list[str],
    limit: int = 100,
    since: datetime | None = None,
    timeout: int = 30,
) -> list[NewsItem]:
    """从 NewsNow 格式接口批量抓取新闻。

    API 请求：POST api_url  body: {"sources": [...]}
    API 响应：[{id, status, items: [{id, title, url, extra: {date: ms_timestamp}}]}]
    """
    items: list[NewsItem] = []

    try:
        resp = requests.post(
            api_url,
            json={"sources": sources},
            headers={
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("NewsNow API request failed: %s", api_url)
        return []

    for source_block in data:
        source_id = source_block.get("id", "unknown")
        for entry in source_block.get("items", [])[:limit]:
            title = (entry.get("title") or "").strip()
            url = (entry.get("url") or "").strip()
            if not title or not url:
                continue

            # extra.date 是毫秒时间戳
            date_ms = (entry.get("extra") or {}).get("date")
            if date_ms:
                published_at = datetime.fromtimestamp(date_ms / 1000, tz=timezone.utc)
            else:
                published_at = datetime.now(timezone.utc)

            if since and published_at <= since:
                continue

            items.append(NewsItem(
                news_id=_make_news_id(url, published_at),
                title=title,
                content=title,  # NewsNow 接口不返回正文，用标题代替
                source=source_id,
                url=url,
                published_at=published_at,
            ))

        fetched = len([i for i in items if i.source == source_id])
        logger.info("fetched %d items from %s", fetched, source_id)

    return deduplicate_news(items)
