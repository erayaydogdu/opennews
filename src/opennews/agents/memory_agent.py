"""Memory Agent — 同主题新闻时序聚合。

论文依据：arXiv 2602.00086 — 每日 sentiment 聚合。
对同一主题的新闻列表进行时序聚合：
  - 每日 sentiment sum / min / max / majority
  - 计算累积影响趋势（滑动窗口均值 + 方向）
  - 更新 GraphRAG 子图
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
    """单日某 topic 的聚合统计。"""
    date: str                    # YYYY-MM-DD
    topic_id: int
    count: int
    impact_sum: float
    impact_min: float
    impact_max: float
    impact_avg: float
    majority_category: str       # 当日出现最多的类别
    category_dist: dict[str, int]


@dataclass(slots=True)
class TopicTrend:
    """某 topic 的累积影响趋势。"""
    topic_id: int
    window_days: int
    total_news_count: int
    daily_aggs: list[DailySentimentAgg]
    trend_direction: str         # "rising" / "falling" / "stable"
    avg_impact: float            # 窗口内平均影响分
    latest_impact: float         # 最近一天平均影响分


class MemoryAgent:
    """时序聚合 Agent。

    Prompt 模板（内化为算法逻辑）：
    ─────────────────────────────────
    你是 Memory Agent。对同一主题的新闻列表进行时序聚合：
    计算每日 sentiment sum/min/max/majority（参考 arXiv 2602.00086）
    更新 GraphRAG 子图，注入"累积影响趋势"。
    ─────────────────────────────────
    """

    def __init__(self, memory_store: RedisMemoryStore):
        self.store = memory_store

    def ingest(self, records: list[MemoryRecord]) -> None:
        """将当前批次的新闻写入时序记忆。"""
        self.store.add_batch(records)
        logger.info("memory agent ingested %d records", len(records))

    def aggregate_topic(self, topic_id: int, window_days: int = 30) -> TopicTrend:
        """对某 topic 做时序聚合，返回趋势。"""
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

        # 按日期分组
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

        # 计算趋势方向
        all_scores = [r.impact_score for r in records]
        avg_impact = round(sum(all_scores) / len(all_scores), 4)
        latest_day_scores = [a.impact_avg for a in daily_aggs[-3:]]  # 最近 3 天
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
        """对一批 topic 做聚合。"""
        return {
            tid: self.aggregate_topic(tid, window_days)
            for tid in topic_ids
            if tid != -1  # 跳过 outlier
        }
