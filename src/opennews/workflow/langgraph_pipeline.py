from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from neo4j.exceptions import Neo4jError, ServiceUnavailable

logger = logging.getLogger(__name__)

from langgraph.graph import END, StateGraph
from sentence_transformers import SentenceTransformer

from opennews.config import settings
from opennews.graph.neo4j_client import GraphPayload, Neo4jGraphClient
from opennews.graph.upsert import build_graph_payload
from opennews.ingest.checkpoint import CheckpointStore
from opennews.ingest.news_fetcher import NewsItem, deduplicate_news, fetch_rss_news
from opennews.ingest.seed_injector import RealtimeSeedInjector
from opennews.nlp.embedder import TextEmbedder
from opennews.nlp.entity_extractor import EntityExtractor
from opennews.topic.online_topic_model import OnlineTopicModel


class PipelineState(TypedDict, total=False):
    news_batch: list[NewsItem]
    docs: list[str]
    embeddings: list[list[float]]
    entities: list[list]
    topics: list
    payloads: list[GraphPayload]
    result: str


@dataclass
class PipelineRuntime:
    embedder: TextEmbedder = field(default_factory=lambda: TextEmbedder(settings.embedding_model))
    extractor: EntityExtractor = field(default_factory=lambda: EntityExtractor(settings.ner_model))
    topic_model: OnlineTopicModel = field(
        default_factory=lambda: OnlineTopicModel(SentenceTransformer(settings.embedding_model))
    )
    graph_client: Neo4jGraphClient = field(
        default_factory=lambda: Neo4jGraphClient(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
        )
    )
    checkpoint: CheckpointStore = field(
        default_factory=lambda: CheckpointStore(settings.checkpoint_file)
    )
    seed_injector: RealtimeSeedInjector = field(default_factory=RealtimeSeedInjector)


runtime = PipelineRuntime()


def init_graph_schema() -> bool:
    """延迟初始化图谱 schema，避免模块导入阶段因 Neo4j 未启动直接崩溃。"""
    try:
        runtime.graph_client.ensure_schema()
        return True
    except ServiceUnavailable:
        return False
    except Neo4jError:
        return False


def fetch_news_node(state: PipelineState) -> PipelineState:
    sources = [s.strip() for s in settings.news_sources.split(",") if s.strip()]
    last_dt = runtime.checkpoint.load_last_published_at()

    rss_batch = fetch_rss_news(sources=sources, limit=settings.batch_size, since=last_dt)
    seed_batch = runtime.seed_injector.load()

    batch = deduplicate_news(rss_batch + seed_batch)
    if last_dt:
        batch = [b for b in batch if b.published_at > last_dt]

    batch.sort(key=lambda x: x.published_at)
    return {"news_batch": batch}


def embed_node(state: PipelineState) -> PipelineState:
    news = state.get("news_batch", [])
    if not news:
        return {"docs": [], "embeddings": []}

    docs = [f"{n.title}\n{n.content}" for n in news]
    vecs = runtime.embedder.encode(docs).vectors
    return {"docs": docs, "embeddings": vecs.tolist()}


def entity_node(state: PipelineState) -> PipelineState:
    docs = state.get("docs", [])
    if not docs:
        return {"entities": []}
    entities = [runtime.extractor.extract(d) for d in docs]
    return {"entities": entities}


def topic_node(state: PipelineState) -> PipelineState:
    docs = state.get("docs", [])
    if not docs:
        return {"topics": []}
    topics = runtime.topic_model.update_and_assign(docs)
    return {"topics": topics}


def build_payload_node(state: PipelineState) -> PipelineState:
    news = state.get("news_batch", [])
    embeds = state.get("embeddings", [])
    entities = state.get("entities", [])
    topics = state.get("topics", [])

    if not news:
        return {"payloads": []}

    payloads = []
    safe_n = min(len(news), len(embeds), len(entities), len(topics))
    for i in range(safe_n):
        item = news[i]
        topic = topics[i]
        label = runtime.topic_model.get_topic_label(topic.topic_id)
        payload = build_graph_payload(
            item=item,
            embedding=embeds[i],
            entities=entities[i],
            topic=topic,
            topic_label=label,
        )
        payloads.append(GraphPayload(**payload))

    return {"payloads": payloads}


def dump_output_node(state: PipelineState) -> PipelineState:
    """每轮把解析结果写到 output/ 目录，方便本地查看。"""
    payloads = state.get("payloads", [])
    if not payloads:
        return {}

    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"batch_{ts}.json"

    records = []
    for p in payloads:
        news = p.news.copy()
        # embedding 只保留前 8 维，避免文件过大
        emb = news.get("embedding", [])
        news["embedding_preview"] = emb[:8]
        news.pop("embedding", None)

        records.append({
            "news": news,
            "entities": p.entities,
            "topic": p.topic,
            "impacts": p.impacts,
        })

    out_file.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("dumped %d records to %s", len(records), out_file)
    return {}


def write_graph_node(state: PipelineState) -> PipelineState:
    payloads = state.get("payloads", [])
    news_batch = state.get("news_batch", [])

    if not payloads:
        return {"result": "updated 0 news into graph"}

    if not init_graph_schema():
        return {"result": "neo4j unavailable, skip graph write this round"}

    try:
        runtime.graph_client.upsert_batch(payloads)
    except ServiceUnavailable:
        return {"result": "neo4j unavailable, skip graph write this round"}

    latest = max(n.published_at for n in news_batch)
    runtime.checkpoint.save_last_published_at(latest)
    return {"result": f"updated {len(payloads)} news into graph"}


def build_pipeline():
    g = StateGraph(PipelineState)
    g.add_node("fetch_news", fetch_news_node)
    g.add_node("embed", embed_node)
    g.add_node("extract_entities", entity_node)
    g.add_node("topics", topic_node)
    g.add_node("build_payload", build_payload_node)
    g.add_node("dump_output", dump_output_node)
    g.add_node("write_graph", write_graph_node)

    g.set_entry_point("fetch_news")
    g.add_edge("fetch_news", "embed")
    g.add_edge("embed", "extract_entities")
    g.add_edge("extract_entities", "topics")
    g.add_edge("topics", "build_payload")
    g.add_edge("build_payload", "dump_output")
    g.add_edge("dump_output", "write_graph")
    g.add_edge("write_graph", END)
    return g.compile()


def run_once() -> str:
    app = build_pipeline()
    out = app.invoke({})
    return out.get("result", "done")
