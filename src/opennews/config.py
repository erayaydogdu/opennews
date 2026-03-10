from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    poll_interval_minutes: int = int(os.getenv("NEWS_POLL_INTERVAL_MIN", "5"))
    batch_size: int = int(os.getenv("BATCH_SIZE", "32"))
    # 推荐金融模型：ProsusAI/finbert（768维）或你自己的 FinRoBERTa/DeBERTa checkpoint
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "ProsusAI/finbert")
    ner_model: str = os.getenv("NER_MODEL", "dslim/bert-base-NER")

    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "neo4j")

    # Step2: 分类 & 特征提取
    classifier_model: str = os.getenv("CLASSIFIER_MODEL", "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli")
    classifier_labels: str = os.getenv(
        "CLASSIFIER_LABELS",
        "financial_market,policy_regulation,company_event,macro_economy,industry_trend",
    )

    # Step3: Redis 时序记忆
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    memory_window_days: int = int(os.getenv("MEMORY_WINDOW_DAYS", "30"))

    checkpoint_file: str = os.getenv("CHECKPOINT_FILE", "seeds/checkpoint.json")
    news_sources: str = os.getenv(
        "NEWS_SOURCES",
        # Step3: 多平台新闻源（逗号分隔）
        "https://feeds.reuters.com/reuters/businessNews,"
        "https://rsshub.app/weibo/search/hot/财经,"
        "https://rsshub.app/caixin/latest",
    )


settings = Settings()
