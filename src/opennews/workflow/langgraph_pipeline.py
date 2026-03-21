from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypedDict

from neo4j.exceptions import Neo4jError, ServiceUnavailable

logger = logging.getLogger(__name__)

from langgraph.graph import END, StateGraph

from opennews.agents.classifier_agent import ClassificationResult, ClassifierAgent
from opennews.agents.feature_agent import FeatureAgent, FeatureVector
from opennews.agents.memory_agent import MemoryAgent, TopicTrend
from opennews.agents.report_agent import NewsReport, ReportAgent
from opennews.config import settings
from opennews.db import (
    ensure_schema as ensure_pg_schema,
    get_untranslated_topic_labels,
    insert_batch,
    insert_reports,
    update_topic_labels,
)
from opennews.graph.neo4j_client import GraphPayload, Neo4jGraphClient
from opennews.graph.subgraph_query import GraphRAGQuerier
from opennews.graph.upsert import build_graph_payload
from opennews.ingest.checkpoint import CheckpointStore
from opennews.ingest.news_fetcher import (
    NewsItem, deduplicate_news, fetch_newsnow,
)
from opennews.ingest.seed_injector import RealtimeSeedInjector
from opennews.ingest.sources import SourcesConfig
from opennews.memory import MemoryRecord, RedisMemoryStore
from opennews.nlp.embedder import TextEmbedder
from opennews.nlp.entity_extractor import EntityExtractor
from opennews.topic.online_topic_model import OnlineTopicModel
from opennews.agents.topic_refine_agent import TopicRefineAgent
from opennews.llm.client import LLMConfig


class PipelineState(TypedDict, total=False):
    news_batch: list[NewsItem]
    docs: list[str]
    embeddings: list[list[float]]
    entities: list[list]
    topics: list
    # Step 2: Classification & features
    classifications: list[ClassificationResult]
    features: list[FeatureVector]
    payloads: list[GraphPayload]
    # Step 3: Time-series memory & trends
    topic_trends: dict[int, TopicTrend]
    # Step 4: Impact assessment reports
    reports: list[NewsReport]
    result: str


@dataclass
class PipelineRuntime:
    embedder: TextEmbedder = field(default_factory=lambda: TextEmbedder(settings.embedding_model))
    extractor: EntityExtractor = field(default_factory=lambda: EntityExtractor(settings.ner_model))
    topic_model: OnlineTopicModel = field(
        default_factory=OnlineTopicModel
    )
    # LLM topic refinement agent
    topic_refine_agent: TopicRefineAgent = field(
        default_factory=lambda: TopicRefineAgent(LLMConfig.load(settings.llm_config_path))
    )
    # Step 2: Classifier Agent & Feature Agent (share the same NLI model)
    classifier: ClassifierAgent = field(
        default_factory=lambda: ClassifierAgent(
            model_name=settings.classifier_model,
            candidate_labels=[l.strip() for l in settings.classifier_labels.split(",") if l.strip()],
        )
    )
    feature_agent: FeatureAgent = field(
        default_factory=lambda: FeatureAgent(model_name=settings.classifier_model)
    )
    # Step3: Memory Agent + GraphRAG Querier
    memory_store: RedisMemoryStore = field(
        default_factory=lambda: RedisMemoryStore(
            redis_url=settings.redis_url,
            window_days=settings.memory_window_days,
        )
    )
    memory_agent: MemoryAgent = field(default=None)  # Lazy initialization
    graphrag_querier: GraphRAGQuerier = field(default=None)  # Lazy initialization
    # Step4: ReportAgent
    report_agent: ReportAgent = field(
        default_factory=lambda: ReportAgent(
            weight_stock=settings.report_weight_stock,
            weight_sentiment=settings.report_weight_sentiment,
            weight_policy=settings.report_weight_policy,
            weight_spread=settings.report_weight_spread,
        )
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
    sources_config: SourcesConfig = field(
        default_factory=lambda: SourcesConfig.load(settings.sources_config_path)
    )


runtime: PipelineRuntime | None = None


def _get_runtime() -> PipelineRuntime:
    """Lazy-initialize runtime to avoid loading heavy models at module import time."""
    global runtime
    if runtime is None:
        runtime = PipelineRuntime()
        runtime.memory_agent = MemoryAgent(runtime.memory_store)
        runtime.graphrag_querier = GraphRAGQuerier(runtime.graph_client)
    return runtime


def init_graph_schema() -> bool:
    """Lazy-initialize graph schema to avoid crashing at module import if Neo4j is not running."""
    try:
        _get_runtime().graph_client.ensure_schema()
        return True
    except ServiceUnavailable:
        return False
    except Neo4jError:
        return False


def retry_labels_node(state: PipelineState) -> PipelineState:
    """Retry previously failed topic label translations (with [EN]/[ZH] prefix)."""
    try:
        failed = get_untranslated_topic_labels(limit=100)
    except Exception:
        logger.exception("failed to query untranslated labels")
        return {}

    if not failed:
        return {}

    logger.info("found %d untranslated topic labels, retrying translation", len(failed))
    rt = _get_runtime()
    try:
        updates = rt.topic_refine_agent.retry_failed_labels(failed)
    except Exception:
        logger.exception("retry_failed_labels failed")
        return {}

    if updates:
        try:
            update_topic_labels(updates)
        except Exception:
            logger.exception("failed to update topic labels in DB")

    return {}


def fetch_news_node(state: PipelineState) -> PipelineState:
    rt = _get_runtime()
    last_dt = rt.checkpoint.load_last_published_at()

    # Fetch from all newsnow endpoints
    news_batch: list[NewsItem] = []
    for endpoint in rt.sources_config.newsnow:
        items = fetch_newsnow(
            api_url=endpoint.url,
            sources=endpoint.sources,
            limit=settings.batch_size,
            since=last_dt,
        )
        news_batch.extend(items)

    seed_batch = rt.seed_injector.load()

    batch = deduplicate_news(news_batch + seed_batch)
    if last_dt:
        batch = [b for b in batch if b.published_at > last_dt]

    # Exclude news already analyzed in the database (URL as unique identifier)
    if batch:
        from opennews.db import get_existing_urls
        try:
            existing = get_existing_urls([b.url for b in batch])
            if existing:
                before = len(batch)
                batch = [b for b in batch if b.url not in existing]
                logger.info("dedup: %d already in DB, %d new", before - len(batch), len(batch))
        except Exception:
            logger.exception("dedup check failed, proceeding with full batch")

    batch.sort(key=lambda x: x.published_at)
    return {"news_batch": batch}


def embed_node(state: PipelineState) -> PipelineState:
    news = state.get("news_batch", [])
    if not news:
        return {"docs": [], "embeddings": []}

    docs = [f"{n.title}\n{n.content}" for n in news]
    vecs = _get_runtime().embedder.encode(docs).vectors
    return {"docs": docs, "embeddings": vecs.tolist()}


def entity_node(state: PipelineState) -> PipelineState:
    docs = state.get("docs", [])
    if not docs:
        return {"entities": []}
    entities = [_get_runtime().extractor.extract(d) for d in docs]
    return {"entities": entities}


def topic_node(state: PipelineState) -> PipelineState:
    docs = state.get("docs", [])
    embeddings = state.get("embeddings", [])
    if not docs:
        return {"topics": []}
    topics = _get_runtime().topic_model.update_and_assign(docs, embeddings=embeddings or None)
    return {"topics": topics}


def refine_topics_node(state: PipelineState) -> PipelineState:
    """LLM topic refinement: semantic splitting of clustering candidate groups."""
    docs = state.get("docs", [])
    topics = state.get("topics", [])
    if not docs or not topics:
        return {"topics": topics}

    rt = _get_runtime()
    labels = {a.topic_id: rt.topic_model.get_topic_label(a.topic_id) for a in topics}
    refined, refined_labels = rt.topic_refine_agent.refine_topics(docs, topics, labels)

    # Write labels back to topic_model so subsequent get_topic_label calls work correctly
    rt.topic_model._labels.update(refined_labels)

    return {"topics": refined}


# ── Step 2: Classifier Agent node ──────────────────────────────
def classify_node(state: PipelineState) -> PipelineState:
    """Zero-shot classification: finance/policy/company events, etc."""
    docs = state.get("docs", [])
    if not docs:
        return {"classifications": []}
    try:
        classifications = _get_runtime().classifier.classify_batch(docs)
    except Exception:
        logger.exception("classify_node failed, fallback to empty")
        from opennews.agents.classifier_agent import ClassificationResult
        classifications = [
            ClassificationResult(category="unknown", confidence=0.0, all_scores={})
            for _ in docs
        ]
    return {"classifications": classifications}


# ── Step 2: Feature Agent node ─────────────────────────────────
def feature_node(state: PipelineState) -> PipelineState:
    """7-dimensional news value feature extraction + impact score."""
    docs = state.get("docs", [])
    if not docs:
        return {"features": []}
    try:
        features = _get_runtime().feature_agent.extract_features_batch(docs)
    except Exception:
        logger.exception("feature_node failed, fallback to defaults")
        from opennews.agents.feature_agent import FeatureVector
        features = [FeatureVector() for _ in docs]
    return {"features": features}


def build_payload_node(state: PipelineState) -> PipelineState:
    news = state.get("news_batch", [])
    embeds = state.get("embeddings", [])
    entities = state.get("entities", [])
    topics = state.get("topics", [])
    classifications = state.get("classifications", [])
    features = state.get("features", [])

    if not news:
        return {"payloads": []}

    payloads = []
    safe_n = min(len(news), len(embeds), len(entities), len(topics))
    for i in range(safe_n):
        item = news[i]
        topic = topics[i]
        label = _get_runtime().topic_model.get_topic_label(topic.topic_id)
        payload = build_graph_payload(
            item=item,
            embedding=embeds[i],
            entities=entities[i],
            topic=topic,
            topic_label=label,
        )
        # Step 2: Inject classification & features into payload
        clf_dict = None
        feat_dict = None
        if i < len(classifications):
            clf = classifications[i]
            clf_dict = {
                "category": clf.category,
                "confidence": clf.confidence,
                "all_scores": clf.all_scores,
            }
        if i < len(features):
            feat = features[i]
            feat_dict = feat.to_dict()

        payloads.append(GraphPayload(
            news=payload["news"],
            entities=payload["entities"],
            topic=payload["topic"],
            impacts=payload["impacts"],
            classification=clf_dict,
            features=feat_dict,
        ))

    return {"payloads": payloads}


def dump_output_node(state: PipelineState) -> PipelineState:
    """Write parsed results (including report) to PostgreSQL each round."""
    payloads = state.get("payloads", [])
    reports = state.get("reports", [])
    if not payloads:
        return {}

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"

    records = []
    for p in payloads:
        news = p.news.copy()
        # Keep only first 8 dims of embedding to avoid excessive data size
        emb = news.get("embedding", [])
        news["embedding_preview"] = emb[:8]
        news.pop("embedding", None)

        records.append({
            "news": news,
            "entities": p.entities,
            "topic": p.topic,
            "impacts": p.impacts,
            "classification": p.classification,
            "features": p.features,
            "report": p.report if p.report else None,
        })

    try:
        ensure_pg_schema()
        batch_id = insert_batch(ts, records)
        logger.info("dumped %d records to PostgreSQL (batch_id=%d, ts=%s)", len(records), batch_id, ts)

        # Write to reports table
        if reports:
            reports_data = [
                {
                    "news_id": r.news_id,
                    "final_score": r.final_score,
                    "impact_level": r.impact_level,
                    "dk_cot_scores": r.dk_cot_scores.to_dict(),
                    "markdown": r.markdown,
                    "viz_suggestions": r.viz_suggestions,
                }
                for r in reports
            ]
            insert_reports(batch_id, reports_data)
    except Exception:
        logger.exception("failed to dump batch to PostgreSQL")

    return {}


def write_graph_node(state: PipelineState) -> PipelineState:
    payloads = state.get("payloads", [])
    news_batch = state.get("news_batch", [])

    if not payloads:
        return {"result": "updated 0 news into graph"}

    if not init_graph_schema():
        return {"result": "neo4j unavailable, skip graph write this round"}

    try:
        _get_runtime().graph_client.upsert_batch(payloads)
    except ServiceUnavailable:
        return {"result": "neo4j unavailable, skip graph write this round"}

    latest = max(n.published_at for n in news_batch)
    _get_runtime().checkpoint.save_last_published_at(latest)
    return {"result": f"updated {len(payloads)} news into graph"}


# ── Step 3: Memory Agent node ──────────────────────────────────
def memory_ingest_node(state: PipelineState) -> PipelineState:
    """Write current batch to time-series memory (Redis / fallback)."""
    payloads = state.get("payloads", [])
    if not payloads:
        return {"topic_trends": {}}

    records: list[MemoryRecord] = []
    for p in payloads:
        records.append(MemoryRecord(
            news_id=p.news.get("news_id", ""),
            topic_id=p.topic.get("topic_id", -1),
            published_at=p.news.get("published_at", ""),
            category=(p.classification or {}).get("category", "unknown"),
            impact_score=(p.features or {}).get("impact_score", 0.0),
            features=p.features or {},
        ))

    try:
        _get_runtime().memory_agent.ingest(records)
    except Exception:
        logger.exception("memory ingest failed")

    # Aggregate all topics involved in the current batch
    topic_ids = {r.topic_id for r in records}
    try:
        trends = _get_runtime().memory_agent.aggregate_batch_topics(
            topic_ids, window_days=settings.memory_window_days
        )
    except Exception:
        logger.exception("memory aggregation failed")
        trends = {}

    return {"topic_trends": trends}


# ── Step 3: Write trends to GraphRAG node ─────────────────────────────
def update_trends_node(state: PipelineState) -> PipelineState:
    """Write cumulative impact trends to Neo4j Topic nodes."""
    trends = state.get("topic_trends", {})
    if not trends:
        return {}

    updated = 0
    for topic_id, trend in trends.items():
        trend_data = {
            "trend_direction": trend.trend_direction,
            "avg_impact": trend.avg_impact,
            "latest_impact": trend.latest_impact,
            "total_news_count": trend.total_news_count,
        }
        try:
            if _get_runtime().graphrag_querier.upsert_topic_trend(topic_id, trend_data):
                updated += 1
        except Exception:
            logger.exception("trend upsert failed for topic %d", topic_id)

    logger.info("updated trends for %d/%d topics", updated, len(trends))
    return {}


# ── Step 4: ReportAgent node ───────────────────────────────────
def report_node(state: PipelineState) -> PipelineState:
    """DK-CoT multi-dimensional scoring + Markdown report generation. Toggleable via REPORT_ENABLED."""
    if not settings.report_enabled:
        logger.info("report generation disabled, skipping")
        return {"reports": []}

    payloads = state.get("payloads", [])
    trends = state.get("topic_trends", {})
    if not payloads:
        return {"reports": []}

    # Build input format required by ReportAgent
    eval_items = []
    for p in payloads:
        eval_items.append({
            "news": p.news,
            "features": p.features or {},
            "classification": p.classification or {},
            "entities": p.entities,
            "topic": p.topic,
        })

    try:
        reports = _get_runtime().report_agent.evaluate_batch(eval_items, trends=trends)
    except Exception:
        logger.exception("report_node failed")
        reports = []

    # Write report data back to payloads (for subsequent persistence)
    for i, report in enumerate(reports):
        if i < len(payloads):
            payloads[i] = GraphPayload(
                news=payloads[i].news,
                entities=payloads[i].entities,
                topic=payloads[i].topic,
                impacts=payloads[i].impacts,
                classification=payloads[i].classification,
                features=payloads[i].features,
                report=report.to_dict(),
            )

    return {"reports": reports, "payloads": payloads}


def build_pipeline():
    g = StateGraph(PipelineState)
    # Retry previously failed topic label translations
    g.add_node("retry_labels", retry_labels_node)
    g.add_node("fetch_news", fetch_news_node)
    g.add_node("embed", embed_node)
    g.add_node("extract_entities", entity_node)
    g.add_node("topics", topic_node)
    g.add_node("refine_topics", refine_topics_node)
    # Step 2: Agent nodes
    g.add_node("classify", classify_node)
    g.add_node("extract_features", feature_node)
    g.add_node("build_payload", build_payload_node)
    g.add_node("dump_output", dump_output_node)
    # Step 3: Time-series memory & trends
    g.add_node("memory_ingest", memory_ingest_node)
    g.add_node("update_trends", update_trends_node)
    # Step 4: Report generation + graph write
    g.add_node("report", report_node)
    g.add_node("write_graph", write_graph_node)

    g.set_entry_point("retry_labels")
    g.add_edge("retry_labels", "fetch_news")
    g.add_edge("fetch_news", "embed")
    g.add_edge("embed", "extract_entities")
    # Parallel branches: entities → topics / classify / features
    g.add_edge("extract_entities", "topics")
    g.add_edge("extract_entities", "classify")
    g.add_edge("extract_entities", "extract_features")
    # topics → LLM refine → build_payload
    g.add_edge("topics", "refine_topics")
    g.add_edge("refine_topics", "build_payload")
    g.add_edge("classify", "build_payload")
    g.add_edge("extract_features", "build_payload")
    g.add_edge("build_payload", "memory_ingest")
    # Step 3: Time-series memory aggregation
    g.add_edge("memory_ingest", "update_trends")
    # Step 4: report needs trends → dump_output writes to PG after report (with full report)
    g.add_edge("update_trends", "report")
    g.add_edge("report", "dump_output")
    g.add_edge("dump_output", "write_graph")
    g.add_edge("write_graph", END)
    return g.compile()


def run_once() -> str:
    app = build_pipeline()
    out = app.invoke({})
    return out.get("result", "done")
