from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# 完全链接层次聚类的距离阈值
# 距离 = 1 - cosine_similarity，阈值 0.50 意味着簇内任意两篇相似度 >= 0.50
DISTANCE_THRESHOLD = 0.40

# 至少 2 篇才算聚合主题
MIN_CLUSTER_SIZE = 2

# 中文 embedding 模型
_CHINESE_EMBED_MODEL = "shibing624/text2vec-base-chinese"


@dataclass(slots=True)
class TopicAssignment:
    topic_id: int
    probability: float


class OnlineTopicModel:
    def __init__(self, embedding_model=None):
        self._embedder = None
        self._labels: dict[int, str] = {}

    def _get_embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(_CHINESE_EMBED_MODEL)
            logger.info("loaded topic embedding model: %s", _CHINESE_EMBED_MODEL)
        return self._embedder

    def update_and_assign(
        self,
        docs: list[str],
        embeddings: list[list[float]] | None = None,
    ) -> list[TopicAssignment]:
        if not docs:
            return []

        self._labels.clear()
        n = len(docs)

        emb = self._get_embedder().encode(docs, show_progress_bar=False)
        sim = cosine_similarity(emb)

        # 单篇直接独立
        if n == 1:
            return self._assign_all_solo(docs)

        # ── 层次聚类（complete linkage）──────────────
        # complete linkage 保证簇内任意两点距离 <= 阈值
        dist = 1.0 - sim
        np.fill_diagonal(dist, 0.0)
        dist = np.clip(dist, 0, None)  # 避免浮点误差导致负值
        condensed = squareform(dist, checks=False)
        Z = linkage(condensed, method="complete")
        labels = fcluster(Z, t=DISTANCE_THRESHOLD, criterion="distance")

        # ── 统计每个簇 ───────────────────────────────
        from collections import Counter
        cluster_counts = Counter(labels)

        # ── 分配 topic_id ─────────────────────────────
        assignments: list[TopicAssignment] = [None] * n  # type: ignore
        cluster_id = 0
        solo_id = -1
        # 映射 scipy label → topic_id
        label_map: dict[int, int] = {}

        for i in range(n):
            scipy_label = int(labels[i])
            count = cluster_counts[scipy_label]

            if count >= MIN_CLUSTER_SIZE:
                # 聚合主题
                if scipy_label not in label_map:
                    label_map[scipy_label] = cluster_id
                    # 找该簇内平均相似度最高的文档作为代表标题
                    members = [j for j in range(n) if labels[j] == scipy_label]
                    best = max(members, key=lambda m: np.mean([sim[m][k] for k in members if k != m]))
                    self._labels[cluster_id] = docs[best].split("\n")[0]
                    cluster_id += 1

                tid = label_map[scipy_label]
                members = [j for j in range(n) if labels[j] == scipy_label]
                avg_sim = float(np.mean([sim[i][j] for j in members if j != i]))
                assignments[i] = TopicAssignment(topic_id=tid, probability=avg_sim)
            else:
                # 独立新闻
                title = docs[i].split("\n")[0]
                self._labels[solo_id] = title
                assignments[i] = TopicAssignment(topic_id=solo_id, probability=0.0)
                solo_id -= 1

        clustered = sum(1 for a in assignments if a.topic_id >= 0)
        logger.info(
            "topic assignment: %d clustered in %d topics, %d solo",
            clustered, cluster_id, len(assignments) - clustered,
        )
        return assignments

    def _assign_all_solo(self, docs: list[str]) -> list[TopicAssignment]:
        assignments = []
        for i, doc in enumerate(docs):
            solo_id = -(i + 1)
            self._labels[solo_id] = doc.split("\n")[0]
            assignments.append(TopicAssignment(topic_id=solo_id, probability=0.0))
        return assignments

    def get_topic_label(self, topic_id: int) -> str:
        return self._labels.get(topic_id, f"topic_{topic_id}")
