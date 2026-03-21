"""GraphRAG same-topic subgraph query — community detection + topic aggregation.

Queries the news subgraph under the same topic from Neo4j,
supporting community detection (connected components based on shared entities) and trend injection.
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
    """News node summary within the subgraph."""
    news_id: str
    title: str
    published_at: str
    category: str
    impact_score: float
    source: str


@dataclass(slots=True)
class TopicSubgraph:
    """Subgraph for a topic."""
    topic_id: int
    topic_label: str
    news_items: list[SubgraphNews]
    shared_entities: list[dict]   # List of shared entities
    community_count: int          # Community count based on shared entities


class GraphRAGQuerier:
    """GraphRAG subgraph querier."""

    def __init__(self, graph_client):
        self.client = graph_client

    def query_topic_subgraph(self, topic_id: int, limit: int = 100) -> TopicSubgraph | None:
        """Query news subgraph + shared entities under a topic."""
        try:
            with self.client.session() as s:
                # Query news under the topic
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

                # Query shared entities (entities mentioned by >=2 news items)
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

                # Simple community detection: connected components based on shared entities
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
        """Connected component count based on shared entities (simplified community detection).

        Approach: within the same topic, if two news items share at least one entity,
        they belong to the same community. Uses Union-Find to count connected components.
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

            # Collect all nodes
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
        """Write cumulative impact trend to Topic node."""
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
