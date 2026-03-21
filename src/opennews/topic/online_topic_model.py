from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# Distance threshold for complete-linkage hierarchical clustering
# Distance = 1 - cosine_similarity; threshold 0.35 means any two items in a cluster have similarity >= 0.65
DISTANCE_THRESHOLD = 0.35

# Minimum 2 items to form an aggregated topic
MIN_CLUSTER_SIZE = 2

# Max cluster size; exceeding triggers recursive splitting with stricter threshold
MAX_CLUSTER_SIZE = 15

# Distance step to tighten per recursive split
_SPLIT_STEP = 0.05

# Chinese embedding model
_CHINESE_EMBED_MODEL = "BAAI/bge-base-zh-v1.5"


def _make_bilingual_label(title: str) -> dict[str, str]:
    """Generate initial bilingual label based on title language.

    Chinese title → zh=title, en="[ZH] title"
    English title → zh="[EN] title", en=title
    """
    cjk = sum(1 for c in title if '\u4e00' <= c <= '\u9fff')
    is_zh = cjk / max(len(title.replace(" ", "")), 1) > 0.3
    if is_zh:
        return {"zh": title, "en": f"[ZH] {title}"}
    else:
        return {"zh": f"[EN] {title}", "en": title}


@dataclass(slots=True)
class TopicAssignment:
    topic_id: int
    probability: float


class OnlineTopicModel:
    def __init__(self, embedding_model=None):
        self._embedder = None
        self._labels: dict[int, dict[str, str]] = {}  # {topic_id: {"zh": "...", "en": "..."}}

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

        # Only reuse external embeddings if dimensions match this model,
        # otherwise re-encode with own embedder (avoid mixing embedding spaces from different models)
        if embeddings is not None:
            emb_arr = np.array(embeddings) if not isinstance(embeddings, np.ndarray) else embeddings
            expected_dim = self._get_embedder().get_sentence_embedding_dimension()
            if emb_arr.ndim == 2 and emb_arr.shape[1] == expected_dim:
                emb = emb_arr
                logger.debug("reusing external embeddings (dim=%d)", expected_dim)
            else:
                logger.info(
                    "external embeddings dim %s != topic model dim %d, re-encoding",
                    emb_arr.shape[1] if emb_arr.ndim == 2 else "?", expected_dim,
                )
                emb = self._get_embedder().encode(docs, show_progress_bar=False)
        else:
            emb = self._get_embedder().encode(docs, show_progress_bar=False)
        sim = cosine_similarity(emb)

        # Single document, assign as solo
        if n == 1:
            return self._assign_all_solo(docs)

        # ── Hierarchical clustering (complete linkage) ──────────────
        # Complete linkage ensures distance between any two points in a cluster <= threshold
        dist = 1.0 - sim
        np.fill_diagonal(dist, 0.0)
        dist = np.clip(dist, 0, None)  # Prevent negative values from floating-point errors
        condensed = squareform(dist, checks=False)
        Z = linkage(condensed, method="complete")
        labels = fcluster(Z, t=DISTANCE_THRESHOLD, criterion="distance")

        # ── Count each cluster ───────────────────────────────
        from collections import Counter
        cluster_counts = Counter(labels)

        # ── Split oversized clusters ──────────────────────────────
        # For clusters exceeding MAX_CLUSTER_SIZE, recursively re-cluster with stricter threshold
        final_clusters: list[list[int]] = []  # Each element is a list of global document indices in that cluster
        processed_scipy_labels: set[int] = set()

        for scipy_label, count in cluster_counts.items():
            if scipy_label in processed_scipy_labels:
                continue
            processed_scipy_labels.add(scipy_label)
            members = [j for j in range(n) if labels[j] == scipy_label]

            if count < MIN_CLUSTER_SIZE:
                # Solo news items, not added to final_clusters; handled separately below
                continue

            if count <= MAX_CLUSTER_SIZE:
                final_clusters.append(members)
            else:
                # Recursive split
                sub_clusters = self._split_large_cluster(members, dist, DISTANCE_THRESHOLD)
                final_clusters.extend(sub_clusters)

        # ── Assign topic_id ─────────────────────────────
        assignments: list[TopicAssignment] = [None] * n  # type: ignore
        cluster_id = 0
        solo_id = -1
        assigned: set[int] = set()

        for members in final_clusters:
            if len(members) < MIN_CLUSTER_SIZE:
                # Clusters with fewer than 2 items after splitting are demoted to solo
                for j in members:
                    title = docs[j].split("\n")[0]
                    self._labels[solo_id] = _make_bilingual_label(title)
                    assignments[j] = TopicAssignment(topic_id=solo_id, probability=0.0)
                    assigned.add(j)
                    solo_id -= 1
                continue

            best = max(members, key=lambda m: np.mean([sim[m][k] for k in members if k != m]))
            title = docs[best].split("\n")[0]
            self._labels[cluster_id] = _make_bilingual_label(title)

            for j in members:
                avg_sim = float(np.mean([sim[j][k] for k in members if k != j]))
                assignments[j] = TopicAssignment(topic_id=cluster_id, probability=avg_sim)
                assigned.add(j)

            cluster_id += 1

        # Handle solo news from original clustering (count < MIN_CLUSTER_SIZE)
        for i in range(n):
            if i not in assigned:
                title = docs[i].split("\n")[0]
                self._labels[solo_id] = _make_bilingual_label(title)
                assignments[i] = TopicAssignment(topic_id=solo_id, probability=0.0)
                solo_id -= 1

        clustered = sum(1 for a in assignments if a.topic_id >= 0)
        logger.info(
            "topic assignment: %d clustered in %d topics, %d solo",
            clustered, cluster_id, len(assignments) - clustered,
        )
        return assignments

    @staticmethod
    def _split_large_cluster(
        members: list[int],
        full_dist: np.ndarray,
        current_threshold: float,
    ) -> list[list[int]]:
        """Recursively split clusters exceeding MAX_CLUSTER_SIZE.

        Tightens distance threshold by _SPLIT_STEP each time until all sub-clusters <= MAX_CLUSTER_SIZE
        or threshold drops below 0.05 (fallback stop).
        """
        if len(members) <= MAX_CLUSTER_SIZE:
            return [members]

        new_threshold = current_threshold - _SPLIT_STEP
        if new_threshold < 0.05:
            # Threshold already very strict, force-accept current cluster
            logger.warning(
                "cluster of %d items cannot be split further (threshold=%.2f), keeping as-is",
                len(members), current_threshold,
            )
            return [members]

        # Extract sub-distance matrix
        idx = np.array(members)
        sub_dist = full_dist[np.ix_(idx, idx)]
        np.fill_diagonal(sub_dist, 0.0)
        sub_dist = np.clip(sub_dist, 0, None)

        condensed = squareform(sub_dist, checks=False)
        Z = linkage(condensed, method="complete")
        sub_labels = fcluster(Z, t=new_threshold, criterion="distance")

        from collections import Counter
        sub_counts = Counter(sub_labels)

        result: list[list[int]] = []
        seen_labels: set[int] = set()
        for local_i, sl in enumerate(sub_labels):
            if sl in seen_labels:
                continue
            seen_labels.add(sl)
            sub_members = [members[j] for j in range(len(members)) if sub_labels[j] == sl]

            if len(sub_members) > MAX_CLUSTER_SIZE:
                # Still too large, continue recursion
                result.extend(
                    OnlineTopicModel._split_large_cluster(sub_members, full_dist, new_threshold)
                )
            else:
                result.append(sub_members)

        logger.info(
            "split cluster of %d into %d sub-clusters (threshold %.2f → %.2f)",
            len(members), len(result), current_threshold, new_threshold,
        )
        return result

    def _assign_all_solo(self, docs: list[str]) -> list[TopicAssignment]:
        assignments = []
        for i, doc in enumerate(docs):
            solo_id = -(i + 1)
            title = doc.split("\n")[0]
            self._labels[solo_id] = _make_bilingual_label(title)
            assignments.append(TopicAssignment(topic_id=solo_id, probability=0.0))
        return assignments

    def get_topic_label(self, topic_id: int) -> dict[str, str]:
        """Return {"zh": "...", "en": "..."} bilingual label."""
        fallback = f"topic_{topic_id}"
        label = self._labels.get(topic_id)
        if label is None:
            return {"zh": fallback, "en": fallback}
        # Backward-compatible with old str format
        if isinstance(label, str):
            return {"zh": label, "en": label}
        return label
