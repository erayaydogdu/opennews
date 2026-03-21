"""Memory Agent — same-topic news time-series aggregation.

Based on: arXiv 2602.00086 — daily sentiment aggregation.
Performs time-series aggregation on news items of the same topic:
  - Daily sentiment sum / min / max / majority
  - Computes cumulative impact trend (sliding window average + direction)
  - Updates GraphRAG subgraph
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from opennews.memory import MemoryRecord, RedisMemoryStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DailySentimentAgg:
    """Aggregated statistics for a single day of a topic."""
    date: str                    # YYYY-MM-DD
    topic_id: int
    count: int
    impact_sum: float
    impact_min: float
    impact_max: float
    impact_avg: float
    majority_category: str       # Most frequent category of the day
    category_dist: dict[str, int]


@dataclass(slots=True)
class TopicTrend:
    """Cumulative impact trend for a topic."""
    topic_id: int
    window_days: int
    total_news_count: int
    daily_aggs: list[DailySentimentAgg]
    trend_direction: str         # "rising" / "falling" / "stable"
    avg_impact: float            # Average impact score within window
    latest_impact: float         # Latest day average impact score


class MemoryAgent:
    """Time-series aggregation Agent.

    Prompt template (internalized as algorithm logic):
    -----------------------------------------
    You are a Memory Agent. Perform time-series aggregation on same-topic news:
    Compute daily sentiment sum/min/max/majority (ref: arXiv 2602.00086)
    Update GraphRAG subgraph, injecting "cumulative impact trend".
    -----------------------------------------
    """

    def __init__(self, memory_store: RedisMemoryStore):
        self.store = memory_store

    def ingest(self, records: list[MemoryRecord]) -> None:
        """Write current batch news into time-series memory."""
        self.store.add_batch(records)
        logger.info("memory agent ingested %d records", len(records))

    def aggregate_topic(self, topic_id: int, window_days: int = 30) -> TopicTrend:
        """Perform time-series aggregation for a topic, return trend."""
        records = self.store.query_topic(topic_id, days=window_days)
        if not records:
            return TopicTrend(
                topic_id=topic_id,
                window_days=window_days,
                total_news_count=0,
                daily_aggs=[],
                trend_direction="stable",
                avg_impact=0.0,
                latest_impact=0.0,
            )

        # Group by date
        by_date: dict[str, list[MemoryRecord]] = defaultdict(list)
        for r in records:
            day = r.published_at[:10]  # YYYY-MM-DD
            by_date[day].append(r)

        daily_aggs: list[DailySentimentAgg] = []
        for day in sorted(by_date.keys()):
            day_records = by_date[day]
            scores = [r.impact_score for r in day_records]
            cats = [r.category for r in day_records]
            cat_dist: dict[str, int] = defaultdict(int)
            for c in cats:
                cat_dist[c] += 1
            majority = max(cat_dist, key=cat_dist.get)

            daily_aggs.append(DailySentimentAgg(
                date=day,
                topic_id=topic_id,
                count=len(day_records),
                impact_sum=round(sum(scores), 4),
                impact_min=round(min(scores), 4),
                impact_max=round(max(scores), 4),
                impact_avg=round(sum(scores) / len(scores), 4),
                majority_category=majority,
                category_dist=dict(cat_dist),
            ))

        # Compute trend direction
        all_scores = [r.impact_score for r in records]
        avg_impact = round(sum(all_scores) / len(all_scores), 4)
        latest_day_scores = [a.impact_avg for a in daily_aggs[-3:]]  # Last 3 days
        earlier_scores = [a.impact_avg for a in daily_aggs[:-3]] if len(daily_aggs) > 3 else []

        if earlier_scores and latest_day_scores:
            recent_avg = sum(latest_day_scores) / len(latest_day_scores)
            earlier_avg = sum(earlier_scores) / len(earlier_scores)
            if recent_avg > earlier_avg * 1.1:
                direction = "rising"
            elif recent_avg < earlier_avg * 0.9:
                direction = "falling"
            else:
                direction = "stable"
        else:
            direction = "stable"

        latest_impact = daily_aggs[-1].impact_avg if daily_aggs else 0.0

        return TopicTrend(
            topic_id=topic_id,
            window_days=window_days,
            total_news_count=len(records),
            daily_aggs=daily_aggs,
            trend_direction=direction,
            avg_impact=avg_impact,
            latest_impact=latest_impact,
        )

    def aggregate_batch_topics(self, topic_ids: set[int], window_days: int = 30) -> dict[int, TopicTrend]:
        """Aggregate a batch of topics."""
        return {
            tid: self.aggregate_topic(tid, window_days)
            for tid in topic_ids
            if tid != -1  # Skip outliers
        }
