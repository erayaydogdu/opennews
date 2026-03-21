"""Redis time-series memory store — 30-day rolling window.

Each news item is stored in a Redis Sorted Set by topic_id (score = timestamp),
automatically cleaning up data beyond the window. Falls back to in-memory dict when Redis is unavailable.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryRecord:
    """A single time-series memory record."""
    news_id: str
    topic_id: int
    published_at: str          # ISO format
    category: str
    impact_score: float
    features: dict[str, float]

    def to_json(self) -> str:
        return json.dumps({
            "news_id": self.news_id,
            "topic_id": self.topic_id,
            "published_at": self.published_at,
            "category": self.category,
            "impact_score": self.impact_score,
            "features": self.features,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "MemoryRecord":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        d = json.loads(raw)
        return cls(**d)


def _topic_key(topic_id: int) -> str:
    return f"opennews:memory:topic:{topic_id}"


class RedisMemoryStore:
    """Redis-backed time-series memory, Sorted Set ordered by timestamp.

    Automatically falls back to in-memory dict when Redis is unavailable.
    """

    def __init__(self, redis_url: str, window_days: int = 30):
        self.window_days = window_days
        self._redis = None
        self._fallback: dict[str, list[tuple[float, str]]] = defaultdict(list)
        self._use_fallback = False

        try:
            import redis
            self._redis = redis.Redis.from_url(redis_url, decode_responses=False)
            self._redis.ping()
            logger.info("redis memory store connected: %s", redis_url)
        except Exception:
            logger.warning("redis unavailable, using in-memory fallback for memory store")
            self._use_fallback = True

    def add(self, record: MemoryRecord) -> None:
        """Write a single memory record."""
        key = _topic_key(record.topic_id)
        ts = datetime.fromisoformat(record.published_at).timestamp()
        payload = record.to_json()

        if self._use_fallback:
            self._fallback[key].append((ts, payload))
            self._trim_fallback(key)
        else:
            try:
                self._redis.zadd(key, {payload: ts})
                self._trim_redis(key)
            except Exception:
                logger.warning("redis write failed, fallback this record")
                self._fallback[key].append((ts, payload))

    def add_batch(self, records: list[MemoryRecord]) -> None:
        for r in records:
            self.add(r)

    def query_topic(self, topic_id: int, days: int | None = None) -> list[MemoryRecord]:
        """Query all memory records for a topic within the window."""
        key = _topic_key(topic_id)
        window = days or self.window_days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).timestamp()

        if self._use_fallback:
            entries = self._fallback.get(key, [])
            return [
                MemoryRecord.from_json(payload)
                for ts, payload in entries
                if ts >= cutoff
            ]
        else:
            try:
                raw_list = self._redis.zrangebyscore(key, cutoff, "+inf")
                return [MemoryRecord.from_json(r) for r in raw_list]
            except Exception:
                logger.warning("redis read failed, returning fallback data")
                entries = self._fallback.get(key, [])
                return [
                    MemoryRecord.from_json(payload)
                    for ts, payload in entries
                    if ts >= cutoff
                ]

    def _trim_redis(self, key: str) -> None:
        """Clean up data beyond the window."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.window_days)).timestamp()
        try:
            self._redis.zremrangebyscore(key, "-inf", cutoff)
        except Exception:
            pass

    def _trim_fallback(self, key: str) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.window_days)).timestamp()
        self._fallback[key] = [
            (ts, p) for ts, p in self._fallback[key] if ts >= cutoff
        ]
