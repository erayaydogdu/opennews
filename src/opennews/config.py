from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    poll_interval_minutes: int = int(os.getenv("NEWS_POLL_INTERVAL_MIN", "5"))
    batch_size: int = int(os.getenv("BATCH_SIZE", "32"))
    # Recommended finance model: ProsusAI/finbert (768d) or your own FinRoBERTa/DeBERTa checkpoint
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "ProsusAI/finbert")
    ner_model: str = os.getenv("NER_MODEL", "dslim/bert-base-NER")

    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "Aa123456")

    # Step 2: Classification & feature extraction
    classifier_model: str = os.getenv("CLASSIFIER_MODEL", "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli")
    classifier_labels: str = os.getenv(
        "CLASSIFIER_LABELS",
        "financial_market,policy_regulation,company_event,macro_economy,industry_trend",
    )

    # Step 3: Redis time-series memory
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    memory_window_days: int = int(os.getenv("MEMORY_WINDOW_DAYS", "30"))

    # Step 4: ReportAgent configuration
    report_enabled: bool = os.getenv("REPORT_ENABLED", "true").lower() in ("true", "1", "yes")
    # DK-CoT four-dimensional weights: stock relevance / market sentiment / policy risk / spread breadth
    report_weight_stock: float = float(os.getenv("REPORT_WEIGHT_STOCK", "0.40"))
    report_weight_sentiment: float = float(os.getenv("REPORT_WEIGHT_SENTIMENT", "0.20"))
    report_weight_policy: float = float(os.getenv("REPORT_WEIGHT_POLICY", "0.20"))
    report_weight_spread: float = float(os.getenv("REPORT_WEIGHT_SPREAD", "0.20"))

    # PostgreSQL persistence
    pg_host: str = os.getenv("PG_HOST", "127.0.0.1")
    pg_port: int = int(os.getenv("PG_PORT", "5432"))
    pg_user: str = os.getenv("PG_USER", "postgres")
    pg_password: str = os.getenv("PG_PASSWORD", "123456")
    pg_database: str = os.getenv("PG_DATABASE", "opennews")

    checkpoint_file: str = os.getenv("CHECKPOINT_FILE", "seeds/checkpoint.json")

    # Config file paths
    llm_config_path: str = os.getenv("LLM_CONFIG_PATH", "config/llm.yaml")
    sources_config_path: str = os.getenv("SOURCES_CONFIG_PATH", "config/sources.yaml")


settings = Settings()
