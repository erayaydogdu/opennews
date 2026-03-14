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


def normalize_url(url: str) -> str:
    """URL 标准化：去除尾部斜杠、query 中的跟踪参数、fragment 等。"""
    from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
    url = url.strip()
    p = urlparse(url)
    # 去除 fragment
    # 去除常见跟踪参数
    drop_params = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "spm", "from"}
    qs = {k: v for k, v in parse_qs(p.query).items() if k not in drop_params}
    cleaned = urlunparse((
        p.scheme,
        p.netloc,
        p.path.rstrip("/"),
        p.params,
        urlencode(qs, doseq=True),
        "",  # drop fragment
    ))
    return cleaned


def _make_news_id(url: str, published_at: datetime) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def deduplicate_news(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        if item.url in seen:
            continue
        seen.add(item.url)
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
            url = normalize_url((entry.get("url") or ""))
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
