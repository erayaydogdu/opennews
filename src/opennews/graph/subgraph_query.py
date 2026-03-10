"""GraphRAG 同主题子图查询 — 社区检测 + 同 topic 聚合。

从 Neo4j 中查询同一 topic 下的新闻子图，
支持社区检测（基于共享实体的连通分量）和趋势注入。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from neo4j.exceptions import ServiceUnavailable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SubgraphNews:
    """子图中的新闻节点摘要。"""
    news_id: str
    title: str
    published_at: str
    category: str
    impact_score: float
    source: str


@dataclass(slots=True)
class TopicSubgraph:
    """某 topic 的子图。"""
    topic_id: int
    topic_label: str
    news_items: list[SubgraphNews]
    shared_entities: list[dict]   # 共享实体列表
    community_count: int          # 基于共享实体的社区数


class GraphRAGQuerier:
    """GraphRAG 子图查询器。"""

    def __init__(self, graph_client):
        self.client = graph_client

    def query_topic_subgraph(self, topic_id: int, limit: int = 100) -> TopicSubgraph | None:
        """查询某 topic 下的新闻子图 + 共享实体。"""
        try:
            with self.client.session() as s:
                # 查询 topic 下的新闻
                result = s.run(
                    """
                    MATCH (n:News)-[:IN_TOPIC]->(t:Topic {topic_id: $topic_id})
                    RETURN n.news_id AS news_id, n.title AS title,
                           n.published_at AS published_at,
                           n.category AS category,
                           n.impact_score AS impact_score,
                           n.source AS source,
                           t.label AS topic_label
                    ORDER BY n.published_at DESC
                    LIMIT $limit
                    """,
                    {"topic_id": topic_id, "limit": limit},
                )
                records = list(result)
                if not records:
                    return None

                topic_label = records[0]["topic_label"] or f"topic_{topic_id}"
                news_items = [
                    SubgraphNews(
                        news_id=r["news_id"],
                        title=r["title"] or "",
                        published_at=r["published_at"] or "",
                        category=r["category"] or "unknown",
                        impact_score=float(r["impact_score"] or 0.0),
                        source=r["source"] or "",
                    )
                    for r in records
                ]

                # 查询共享实体（被 >=2 条新闻 MENTIONS 的实体）
                entity_result = s.run(
                    """
                    MATCH (n:News)-[:IN_TOPIC]->(t:Topic {topic_id: $topic_id})
                    MATCH (n)-[:MENTIONS]->(e:Entity)
                    WITH e, count(DISTINCT n) AS mention_count
                    WHERE mention_count >= 2
                    RETURN e.entity_id AS entity_id, e.name AS name,
                           e.type AS type, mention_count
                    ORDER BY mention_count DESC
                    LIMIT 50
                    """,
                    {"topic_id": topic_id},
                )
                shared_entities = [
                    {
                        "entity_id": r["entity_id"],
                        "name": r["name"],
                        "type": r["type"],
                        "mention_count": r["mention_count"],
                    }
                    for r in entity_result
                ]

                # 简单社区检测：基于共享实体的连通分量
                community_count = self._count_communities(s, topic_id)

                return TopicSubgraph(
                    topic_id=topic_id,
                    topic_label=topic_label,
                    news_items=news_items,
                    shared_entities=shared_entities,
                    community_count=community_count,
                )
        except ServiceUnavailable:
            logger.warning("neo4j unavailable for subgraph query")
            return None
        except Exception:
            logger.exception("subgraph query failed for topic %d", topic_id)
            return None

    def _count_communities(self, session, topic_id: int) -> int:
        """基于共享实体的连通分量计数（简化版社区检测）。

        思路：同 topic 下，两条新闻如果共享至少一个实体，
        则属于同一社区。用 Union-Find 计算连通分量数。
        """
        try:
            result = session.run(
                """
                MATCH (n1:News)-[:IN_TOPIC]->(t:Topic {topic_id: $topic_id})
                MATCH (n2:News)-[:IN_TOPIC]->(t)
                WHERE n1.news_id < n2.news_id
                MATCH (n1)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(n2)
                RETURN DISTINCT n1.news_id AS a, n2.news_id AS b
                """,
                {"topic_id": topic_id},
            )
            edges = [(r["a"], r["b"]) for r in result]

            # 收集所有节点
            all_news_result = session.run(
                """
                MATCH (n:News)-[:IN_TOPIC]->(t:Topic {topic_id: $topic_id})
                RETURN n.news_id AS nid
                """,
                {"topic_id": topic_id},
            )
            all_nodes = {r["nid"] for r in all_news_result}

            if not all_nodes:
                return 0

            # Union-Find
            parent: dict[str, str] = {n: n for n in all_nodes}

            def find(x: str) -> str:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: str, b: str) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            for a, b in edges:
                if a in parent and b in parent:
                    union(a, b)

            roots = {find(n) for n in all_nodes}
            return len(roots)
        except Exception:
            logger.exception("community detection failed")
            return 0

    def upsert_topic_trend(self, topic_id: int, trend_data: dict) -> bool:
        """将累积影响趋势写入 Topic 节点。"""
        try:
            with self.client.session() as s:
                s.run(
                    """
                    MATCH (t:Topic {topic_id: $topic_id})
                    SET t.trend_direction = $direction,
                        t.avg_impact = $avg_impact,
                        t.latest_impact = $latest_impact,
                        t.total_news_count = $total_count,
                        t.trend_updated_at = $now
                    """,
                    {
                        "topic_id": topic_id,
                        "direction": trend_data.get("trend_direction", "stable"),
                        "avg_impact": trend_data.get("avg_impact", 0.0),
                        "latest_impact": trend_data.get("latest_impact", 0.0),
                        "total_count": trend_data.get("total_news_count", 0),
                        "now": datetime.now(timezone.utc).isoformat(),
                    },
                )
            return True
        except ServiceUnavailable:
            logger.warning("neo4j unavailable for trend upsert")
            return False
        except Exception:
            logger.exception("trend upsert failed for topic %d", topic_id)
            return False
