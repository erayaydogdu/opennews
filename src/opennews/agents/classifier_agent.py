"""Classifier Agent — zero-shot news classification (finance/policy/company events, etc.).

Uses DeBERTa-v3-base-mnli for zero-shot classification,
outputs category + confidence.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from transformers import pipeline

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ClassificationResult:
    """Classification result for a single news item."""
    category: str          # Highest-confidence category
    confidence: float      # Corresponding confidence 0-1
    all_scores: dict[str, float]  # All candidate categories → confidence


class ClassifierAgent:
    """Zero-shot news classification Agent.

    Prompt design rationale (based on LLM-Assisted News Discovery):
    - Candidate labels mapped to financial domain semantics:
      financial_market  → Financial market dynamics
      policy_regulation → Policy and regulatory changes
      company_event     → Company events (earnings/M&A/personnel)
      macro_economy     → Macroeconomic indicators
      industry_trend    → Industry trends
    """

    # Candidate labels → natural-language hypothesis templates (improves NLI zero-shot performance)
    LABEL_HYPOTHESES: dict[str, str] = {
        "financial_market": "This news is about financial markets, stock prices, bonds, or trading.",
        "policy_regulation": "This news is about government policy, regulation, or central bank decisions.",
        "company_event": "This news is about a specific company event such as earnings, mergers, or executive changes.",
        "macro_economy": "This news is about macroeconomic indicators like GDP, inflation, or employment.",
        "industry_trend": "This news is about industry trends, sector analysis, or technological shifts.",
    }

    def __init__(self, model_name: str, candidate_labels: list[str] | None = None):
        self.model_name = model_name
        self.candidate_labels = candidate_labels or list(self.LABEL_HYPOTHESES.keys())
        # Build hypothesis template list (aligned with candidate_labels order)
        self._hypothesis_template = "This text is about {}."
        logger.info("loading classifier model: %s", model_name)
        self._clf = pipeline(
            "zero-shot-classification",
            model=model_name,
            device=-1,  # CPU; change to 0 for GPU
        )

    def classify(self, text: str) -> ClassificationResult:
        """Perform zero-shot classification on a single news text."""
        result = self._clf(
            text,
            candidate_labels=self.candidate_labels,
            hypothesis_template=self._hypothesis_template,
            multi_label=False,
        )
        scores = {
            label: round(score, 4)
            for label, score in zip(result["labels"], result["scores"])
        }
        top_label = result["labels"][0]
        top_score = round(result["scores"][0], 4)
        return ClassificationResult(
            category=top_label,
            confidence=top_score,
            all_scores=scores,
        )

    def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        """Batch classification."""
        if not texts:
            return []
        results = self._clf(
            texts,
            candidate_labels=self.candidate_labels,
            hypothesis_template=self._hypothesis_template,
            multi_label=False,
        )
        # Pipeline returns dict instead of list for single input
        if isinstance(results, dict):
            results = [results]
        out: list[ClassificationResult] = []
        for r in results:
            scores = {
                label: round(score, 4)
                for label, score in zip(r["labels"], r["scores"])
            }
            out.append(ClassificationResult(
                category=r["labels"][0],
                confidence=round(r["scores"][0], 4),
                all_scores=scores,
            ))
        return out
