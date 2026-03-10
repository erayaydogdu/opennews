from __future__ import annotations

import logging
from dataclasses import dataclass

from bertopic import BERTopic
from hdbscan import HDBSCAN
from umap import UMAP

logger = logging.getLogger(__name__)

# BERTopic 内部 UMAP 默认 n_neighbors=15，样本 < 15 就会崩。
# 这里用更宽松的参数，让小批量也能跑通。
MIN_DOCS_FOR_TOPIC = 5


@dataclass(slots=True)
class TopicAssignment:
    topic_id: int
    probability: float


class OnlineTopicModel:
    def __init__(self, embedding_model):
        # UMAP / HDBSCAN 参数适配小批量
        umap_model = UMAP(
            n_neighbors=2,
            n_components=5,
            min_dist=0.0,
            metric="cosine",
        )
        hdbscan_model = HDBSCAN(
            min_cluster_size=2,
            min_samples=1,
            prediction_data=True,
        )
        self.topic_model = BERTopic(
            embedding_model=embedding_model,
            umap_model=umap_model,
            hdbscan_model=hdbscan_model,
            verbose=False,
        )
        self.is_fitted = False

    def update_and_assign(self, docs: list[str]) -> list[TopicAssignment]:
        if not docs:
            return []

        # 样本太少时 UMAP 仍然可能失败，直接返回 outlier
        if len(docs) < MIN_DOCS_FOR_TOPIC:
            logger.warning(
                "docs count %d < %d, skip topic modeling, all assigned to outlier",
                len(docs),
                MIN_DOCS_FOR_TOPIC,
            )
            return [TopicAssignment(topic_id=-1, probability=0.0) for _ in docs]

        try:
            if not self.is_fitted:
                topics, probs = self.topic_model.fit_transform(docs)
                self.is_fitted = True
            else:
                topics, probs = self.topic_model.transform(docs)
        except Exception:
            logger.exception("BERTopic failed, fallback all to outlier")
            return [TopicAssignment(topic_id=-1, probability=0.0) for _ in docs]

        assignments: list[TopicAssignment] = []
        for tid, p in zip(topics, probs):
            prob = float(max(p)) if hasattr(p, "__len__") else float(p or 0.0)
            assignments.append(TopicAssignment(topic_id=int(tid), probability=prob))
        return assignments

    def get_topic_label(self, topic_id: int) -> str:
        if topic_id == -1:
            return "outlier"
        info = self.topic_model.get_topic(topic_id)
        if not info:
            return f"topic_{topic_id}"
        return ", ".join([w for w, _ in info[:5]])
