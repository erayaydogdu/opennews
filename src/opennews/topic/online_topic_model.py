from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from bertopic import BERTopic
from hdbscan import HDBSCAN
from sklearn.feature_extraction.text import CountVectorizer
from umap import UMAP

logger = logging.getLogger(__name__)

MIN_DOCS_FOR_TOPIC = 5
MIN_TOPIC_PROBABILITY = 0.5

# 中文 embedding 模型，用于主题聚类（独立于 pipeline 的 FinBERT）
_CHINESE_EMBED_MODEL = "shibing624/text2vec-base-chinese"


def _jieba_tokenizer(text: str) -> list[str]:
    """jieba 中文分词 tokenizer，供 CountVectorizer 使用。"""
    import jieba
    return list(jieba.cut(text))


@dataclass(slots=True)
class TopicAssignment:
    topic_id: int
    probability: float


class OnlineTopicModel:
    def __init__(self, embedding_model=None):
        self.topic_model: BERTopic | None = None
        self.is_fitted = False
        self._embedder = None

    def _get_embedder(self):
        """懒加载中文 embedding 模型。"""
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(_CHINESE_EMBED_MODEL)
            logger.info("loaded topic embedding model: %s", _CHINESE_EMBED_MODEL)
        return self._embedder

    def _build_model(self, n_docs: int) -> BERTopic:
        """根据当前批次大小动态构建 BERTopic。"""
        n_neighbors = max(2, min(15, n_docs - 1))
        n_components = max(2, min(5, n_docs - 2))

        umap_model = UMAP(
            n_neighbors=n_neighbors,
            n_components=n_components,
            min_dist=0.0,
            metric="cosine",
        )
        hdbscan_model = HDBSCAN(
            min_cluster_size=max(4, n_docs // 4),
            min_samples=3,
            cluster_selection_epsilon=0.3,
            prediction_data=True,
        )
        vectorizer = CountVectorizer(tokenizer=_jieba_tokenizer, max_features=5000)

        return BERTopic(
            umap_model=umap_model,
            hdbscan_model=hdbscan_model,
            vectorizer_model=vectorizer,
            verbose=False,
        )

    def update_and_assign(
        self,
        docs: list[str],
        embeddings: list[list[float]] | None = None,
    ) -> list[TopicAssignment]:
        if not docs:
            return []

        if len(docs) < MIN_DOCS_FOR_TOPIC:
            logger.warning(
                "docs count %d < %d, skip topic modeling, all assigned to outlier",
                len(docs),
                MIN_DOCS_FOR_TOPIC,
            )
            return [TopicAssignment(topic_id=-1, probability=0.0) for _ in docs]

        try:
            self.topic_model = self._build_model(len(docs))
            # 使用中文 embedding 模型重新编码，而非 pipeline 的 FinBERT
            emb = self._get_embedder().encode(docs, show_progress_bar=False)
            topics, probs = self.topic_model.fit_transform(docs, embeddings=emb)
            self.is_fitted = True
        except Exception:
            logger.exception("BERTopic failed, fallback all to outlier")
            return [TopicAssignment(topic_id=-1, probability=0.0) for _ in docs]

        assignments: list[TopicAssignment] = []
        for tid, p in zip(topics, probs):
            prob = float(max(p)) if hasattr(p, "__len__") else float(p or 0.0)
            if prob < MIN_TOPIC_PROBABILITY:
                assignments.append(TopicAssignment(topic_id=-1, probability=prob))
            else:
                assignments.append(TopicAssignment(topic_id=int(tid), probability=prob))
        return assignments

    def get_topic_label(self, topic_id: int) -> str:
        if topic_id == -1:
            return "outlier"
        if self.topic_model is None:
            return f"topic_{topic_id}"
        info = self.topic_model.get_topic(topic_id)
        if not info:
            return f"topic_{topic_id}"
        return ", ".join([w for w, _ in info[:5]])
