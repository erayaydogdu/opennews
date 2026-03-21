"""Feature Agent — 7-dimensional news value feature extraction + impact score calculation.

Based on: LLM-Assisted News Discovery value encoding.
7 dimensions:
  1. market_impact    — Market impact degree
  2. price_signal     — Price signal strength
  3. regulatory_risk  — Regulatory risk
  4. timeliness       — Timeliness
  5. impact           — Event impact
  6. controversy      — Controversy
  7. generalizability — Generalizability (weight x2)

Each dimension scored 1-5, impact_score = weighted average (generalizability weight x2).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from transformers import pipeline

logger = logging.getLogger(__name__)

# ── Feature dimension definitions ──────────────────────────────────────────────
FEATURE_DIMS = [
    "market_impact",
    "price_signal",
    "regulatory_risk",
    "timeliness",
    "impact",
    "controversy",
    "generalizability",
]

# Weight coefficients (generalizability x2, others x1)
FEATURE_WEIGHTS: dict[str, float] = {
    "market_impact": 1.0,
    "price_signal": 1.0,
    "regulatory_risk": 1.0,
    "timeliness": 1.0,
    "impact": 1.0,
    "controversy": 1.0,
    "generalizability": 2.0,
}


@dataclass(slots=True)
class FeatureVector:
    """7-dimensional features + composite impact score."""
    market_impact: float = 1.0
    price_signal: float = 1.0
    regulatory_risk: float = 1.0
    timeliness: float = 1.0
    impact: float = 1.0
    controversy: float = 1.0
    generalizability: float = 1.0
    impact_score: float = 1.0  # Weighted average

    def to_dict(self) -> dict[str, float]:
        return {
            "market_impact": self.market_impact,
            "price_signal": self.price_signal,
            "regulatory_risk": self.regulatory_risk,
            "timeliness": self.timeliness,
            "impact": self.impact,
            "controversy": self.controversy,
            "generalizability": self.generalizability,
            "impact_score": self.impact_score,
        }


def _compute_impact_score(features: dict[str, float]) -> float:
    """Weighted average, generalizability weight x2."""
    total_w = sum(FEATURE_WEIGHTS.values())
    weighted = sum(features.get(k, 1.0) * w for k, w in FEATURE_WEIGHTS.items())
    return round(weighted / total_w, 2)


# ── NLI proxy scoring ──────────────────────────────────────────────
# Each dimension uses NLI hypotheses to probe intensity,
# mapping entailment probability to a 1-5 score.

_DIM_HYPOTHESES: dict[str, str] = {
    "market_impact": "This news will significantly move financial markets.",
    "price_signal": "This news contains a clear signal about asset price direction.",
    "regulatory_risk": "This news involves regulatory or compliance risk.",
    "timeliness": "This news is breaking or very time-sensitive.",
    "impact": "This news will have a broad and lasting impact.",
    "controversy": "This news is controversial or divisive.",
    "generalizability": "The implications of this news extend beyond a single sector or region.",
}


def _entailment_to_score(prob: float) -> float:
    """Linearly map entailment probability [0,1] to [1,5]."""
    return round(1.0 + 4.0 * max(0.0, min(1.0, prob)), 2)


class FeatureAgent:
    """NLI zero-shot inference based 7-dimensional feature extraction Agent.

    Prompt template (internalized as NLI hypotheses):
    -----------------------------------------
    You are a financial news Feature Agent.
    For the following news, score on 7 dimensions (1-5):
    Timeliness / Impact / Controversy / Generalizability (x2)
    Market Impact / Price Signal / Regulatory Risk
    Output JSON: {"impact_score": 4.2, "features": {...}}
    -----------------------------------------
    Actual implementation: uses NLI model to perform entailment inference
    per dimension hypothesis, mapping probabilities to 1-5 scores,
    avoiding dependency on external LLM APIs.
    """

    def __init__(self, model_name: str):
        self.model_name = model_name
        logger.info("loading feature extraction NLI model: %s", model_name)
        self._nli = pipeline(
            "zero-shot-classification",
            model=model_name,
            device=-1,
        )

    def extract_features(self, text: str) -> FeatureVector:
        """Extract 7-dimensional features for a single news item (per-dimension inference)."""
        features: dict[str, float] = {}
        for dim, hypothesis in _DIM_HYPOTHESES.items():
            result = self._nli(
                text,
                candidate_labels=[hypothesis],
                hypothesis_template="This text is about {}.",
                multi_label=False,
            )
            prob = result["scores"][0]
            features[dim] = _entailment_to_score(prob)

        score = _compute_impact_score(features)
        return FeatureVector(
            market_impact=features["market_impact"],
            price_signal=features["price_signal"],
            regulatory_risk=features["regulatory_risk"],
            timeliness=features["timeliness"],
            impact=features["impact"],
            controversy=features["controversy"],
            generalizability=features["generalizability"],
            impact_score=score,
        )

    def extract_features_batch(self, texts: list[str]) -> list[FeatureVector]:
        """Batch feature extraction.

        For each text, all 7 hypotheses are sent as candidate_labels to NLI at once,
        mapping each hypothesis score to 1-5. 7x faster than per-dimension calls.
        """
        if not texts:
            return []

        hypotheses = list(_DIM_HYPOTHESES.values())
        dim_keys = list(_DIM_HYPOTHESES.keys())

        results = self._nli(
            texts,
            candidate_labels=hypotheses,
            hypothesis_template="This text is about {}.",
            multi_label=True,  # Multi-label mode, each hypothesis scored independently
        )
        if isinstance(results, dict):
            results = [results]

        out: list[FeatureVector] = []
        for r in results:
            label_score = dict(zip(r["labels"], r["scores"]))
            features: dict[str, float] = {}
            for key, hyp in zip(dim_keys, hypotheses):
                prob = label_score.get(hyp, 0.0)
                features[key] = _entailment_to_score(prob)

            score = _compute_impact_score(features)
            out.append(FeatureVector(
                market_impact=features["market_impact"],
                price_signal=features["price_signal"],
                regulatory_risk=features["regulatory_risk"],
                timeliness=features["timeliness"],
                impact=features["impact"],
                controversy=features["controversy"],
                generalizability=features["generalizability"],
                impact_score=score,
            ))
        return out
