"""ReportAgent — DK-CoT multi-dimensional scoring + Markdown report generation.

Based on:
  - DK-CoT (Domain-Knowledge Chain-of-Thought) multi-chain reasoning
  - LLM-Assisted News Discovery scoring system
  - FinSCRA fuzzy logic fusion

Four-dimensional scoring (0-100):
  Stock Relevance  (40%) — based on price_signal + market_impact features
  Market Sentiment (20%) — based on classification confidence + controversy features
  Policy Risk      (20%) — based on regulatory_risk features + classification results
  Spread Breadth   (20%) — based on entity count + cross-source coverage + community count

Final score persisted to Neo4j News node, supports score-based filtering.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────

@dataclass(slots=True)
class DKCoTScores:
    """DK-CoT four-dimensional score breakdown."""
    stock_relevance: float     # Stock relevance 0-100
    market_sentiment: float    # Market sentiment 0-100
    policy_risk: float         # Policy risk 0-100
    spread_breadth: float      # Spread breadth 0-100

    def to_dict(self) -> dict[str, float]:
        return {
            "stock_relevance": self.stock_relevance,
            "market_sentiment": self.market_sentiment,
            "policy_risk": self.policy_risk,
            "spread_breadth": self.spread_breadth,
        }


@dataclass(slots=True)
class NewsReport:
    """Impact assessment report for a single news item."""
    news_id: str
    final_score: float         # Final impact score 0-100
    impact_level: str          # "High" / "Medium" / "Low"
    dk_cot_scores: DKCoTScores
    reasoning: str             # DK-CoT reasoning chain text
    markdown: str              # Full Markdown report
    viz_suggestions: list[str] # Visualization suggestions

    def to_dict(self) -> dict:
        return {
            "news_id": self.news_id,
            "final_score": self.final_score,
            "impact_level": self.impact_level,
            "dk_cot_scores": self.dk_cot_scores.to_dict(),
            "reasoning": self.reasoning,
            "markdown": self.markdown,
            "viz_suggestions": self.viz_suggestions,
        }


# ── Scoring engine ──────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _feature_to_100(val: float, scale_max: float = 5.0) -> float:
    """Map 1-5 feature score to 0-100."""
    return _clamp((val - 1.0) / (scale_max - 1.0) * 100.0)


class ReportAgent:
    """Financial impact ReportAgent.

    Prompt template (internalized as algorithm logic):
    -----------------------------------------
    You are a financial impact ReportAgent.
    Input: same-topic news Graph subgraph + time-series memory
    Use DK-CoT (Domain-Knowledge Chain-of-Thought) to compute final impact score (0-100):
    Dimensions: Stock Relevance (40%), Market Sentiment (20%), Policy Risk (20%), Spread Breadth (20%)
    Output Markdown report + visualization suggestions (trend charts).
    -----------------------------------------
    """

    def __init__(
        self,
        weight_stock: float = 0.40,
        weight_sentiment: float = 0.20,
        weight_policy: float = 0.20,
        weight_spread: float = 0.20,
    ):
        self.weights = {
            "stock_relevance": weight_stock,
            "market_sentiment": weight_sentiment,
            "policy_risk": weight_policy,
            "spread_breadth": weight_spread,
        }
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning("DK-CoT weights sum to %.2f, normalizing", total)
            self.weights = {k: v / total for k, v in self.weights.items()}

    # ── DK-CoT chain reasoning ──────────────────────────────────────

    def _score_stock_relevance(
        self, features: dict, classification: dict, trend: dict | None
    ) -> tuple[float, str]:
        """Dimension 1: Stock Relevance — price_signal + market_impact + trend direction."""
        price_sig = features.get("price_signal", 1.0)
        mkt_impact = features.get("market_impact", 1.0)
        base = (_feature_to_100(price_sig) * 0.6 + _feature_to_100(mkt_impact) * 0.4)

        # Trend bonus: rising +10, falling -5
        if trend:
            direction = trend.get("trend_direction", "stable")
            if direction == "rising":
                base = _clamp(base + 10)
            elif direction == "falling":
                base = _clamp(base - 5)

        reasoning = (
            f"[Stock Relevance] price_signal={price_sig:.1f}→{_feature_to_100(price_sig):.0f}, "
            f"market_impact={mkt_impact:.1f}→{_feature_to_100(mkt_impact):.0f}, "
            f"trend={'N/A' if not trend else trend.get('trend_direction', 'stable')}, "
            f"score={base:.1f}"
        )
        return round(base, 2), reasoning

    def _score_market_sentiment(
        self, features: dict, classification: dict
    ) -> tuple[float, str]:
        """Dimension 2: Market Sentiment — controversy + classification confidence + impact features."""
        controversy = features.get("controversy", 1.0)
        impact_feat = features.get("impact", 1.0)
        confidence = classification.get("confidence", 0.0)

        base = (
            _feature_to_100(controversy) * 0.4
            + _feature_to_100(impact_feat) * 0.3
            + confidence * 100 * 0.3
        )

        reasoning = (
            f"[Market Sentiment] controversy={controversy:.1f}→{_feature_to_100(controversy):.0f}, "
            f"impact={impact_feat:.1f}→{_feature_to_100(impact_feat):.0f}, "
            f"clf_confidence={confidence:.2f}, score={base:.1f}"
        )
        return round(base, 2), reasoning

    def _score_policy_risk(
        self, features: dict, classification: dict
    ) -> tuple[float, str]:
        """Dimension 3: Policy Risk — regulatory_risk + whether classified as policy_regulation."""
        reg_risk = features.get("regulatory_risk", 1.0)
        base = _feature_to_100(reg_risk) * 0.7

        category = classification.get("category", "unknown")
        cat_scores = classification.get("all_scores", {})
        policy_prob = cat_scores.get("policy_regulation", 0.0)
        base += policy_prob * 100 * 0.3

        # Extra bonus if main category is policy_regulation
        if category == "policy_regulation":
            base = _clamp(base + 10)

        reasoning = (
            f"[Policy Risk] regulatory_risk={reg_risk:.1f}→{_feature_to_100(reg_risk):.0f}, "
            f"policy_prob={policy_prob:.2f}, category={category}, score={base:.1f}"
        )
        return round(base, 2), reasoning

    def _score_spread_breadth(
        self, entities: list[dict], news_source: str, trend: dict | None
    ) -> tuple[float, str]:
        """Dimension 4: Spread Breadth — entity count + cross-source coverage + community count."""
        entity_count = len(entities)
        # Entity count mapping: 0→0, 5→50, 10+→100
        entity_score = _clamp(entity_count * 10.0)

        # Source diversity (simple heuristic)
        source_score = 50.0  # baseline
        if any(k in news_source for k in ("reuters", "caixin")):
            source_score = 70.0
        if any(k in news_source for k in ("weibo", "sina")):
            source_score = max(source_score, 60.0)

        # Total news count in trend
        total_count = (trend or {}).get("total_news_count", 0)
        count_score = _clamp(total_count * 5.0)

        base = entity_score * 0.4 + source_score * 0.3 + count_score * 0.3

        reasoning = (
            f"[Spread Breadth] entities={entity_count}→{entity_score:.0f}, "
            f"source_score={source_score:.0f}, "
            f"topic_news_count={total_count}→{count_score:.0f}, score={base:.1f}"
        )
        return round(base, 2), reasoning

    # ── Main scoring entry ────────────────────────────────────────────

    def evaluate(
        self,
        news_id: str,
        title: str,
        features: dict,
        classification: dict,
        entities: list[dict],
        news_source: str,
        trend: dict | None = None,
    ) -> NewsReport:
        """Execute DK-CoT scoring + report generation for a single news item."""
        # Chain-of-Thought: per-dimension reasoning
        s1, r1 = self._score_stock_relevance(features, classification, trend)
        s2, r2 = self._score_market_sentiment(features, classification)
        s3, r3 = self._score_policy_risk(features, classification)
        s4, r4 = self._score_spread_breadth(entities, news_source, trend)

        scores = DKCoTScores(
            stock_relevance=s1,
            market_sentiment=s2,
            policy_risk=s3,
            spread_breadth=s4,
        )

        # Weighted fusion (FinSCRA fuzzy logic)
        final = (
            s1 * self.weights["stock_relevance"]
            + s2 * self.weights["market_sentiment"]
            + s3 * self.weights["policy_risk"]
            + s4 * self.weights["spread_breadth"]
        )

        # Domain relevance penalty: low classification confidence means it doesn't belong to any financial category,
        # apply discount to final score to avoid overestimating non-financial news
        clf_confidence = classification.get("confidence", 0.0)
        domain_penalty = 1.0
        if clf_confidence < 0.3:
            domain_penalty = 0.5
        elif clf_confidence < 0.4:
            domain_penalty = 0.7

        penalty_reason = ""
        if domain_penalty < 1.0:
            final *= domain_penalty
            penalty_reason = (
                f"\n  [Domain Penalty] clf_confidence={clf_confidence:.2f} < threshold, "
                f"penalty={domain_penalty:.1f}"
            )

        final = round(_clamp(final), 2)

        # Impact level
        if final > 75:
            level = "High"
        elif final > 40:
            level = "Medium"
        else:
            level = "Low"

        reasoning = f"DK-CoT Reasoning Chain:\n  {r1}\n  {r2}\n  {r3}\n  {r4}{penalty_reason}\n  → Weighted score={final}, Level={level}"

        # Generate Markdown report
        markdown = self._render_markdown(
            title=title,
            news_id=news_id,
            scores=scores,
            final=final,
            level=level,
            reasoning=reasoning,
            classification=classification,
            trend=trend,
        )

        # Visualization suggestions
        viz = self._suggest_visualizations(scores, trend)

        return NewsReport(
            news_id=news_id,
            final_score=final,
            impact_level=level,
            dk_cot_scores=scores,
            reasoning=reasoning,
            markdown=markdown,
            viz_suggestions=viz,
        )

    def evaluate_batch(
        self,
        payloads: list[dict],
        trends: dict | None = None,
    ) -> list[NewsReport]:
        """Batch evaluation. Each payload must contain news/features/classification/entities."""
        reports = []
        for p in payloads:
            news = p.get("news", {})
            topic_id = p.get("topic", {}).get("topic_id", -1)
            trend = None
            if trends and topic_id in trends:
                t = trends[topic_id]
                trend = {
                    "trend_direction": t.trend_direction if hasattr(t, "trend_direction") else t.get("trend_direction", "stable"),
                    "avg_impact": t.avg_impact if hasattr(t, "avg_impact") else t.get("avg_impact", 0.0),
                    "latest_impact": t.latest_impact if hasattr(t, "latest_impact") else t.get("latest_impact", 0.0),
                    "total_news_count": t.total_news_count if hasattr(t, "total_news_count") else t.get("total_news_count", 0),
                }

            report = self.evaluate(
                news_id=news.get("news_id", ""),
                title=news.get("title", ""),
                features=p.get("features") or {},
                classification=p.get("classification") or {},
                entities=p.get("entities", []),
                news_source=news.get("source", ""),
                trend=trend,
            )
            reports.append(report)
        return reports

    # ── Markdown rendering ─────────────────────────────────────────

    def _render_markdown(
        self,
        title: str,
        news_id: str,
        scores: DKCoTScores,
        final: float,
        level: str,
        reasoning: str,
        classification: dict,
        trend: dict | None,
    ) -> str:
        trend_dir = (trend or {}).get("trend_direction", "N/A")
        trend_avg = (trend or {}).get("avg_impact", 0.0)
        category = classification.get("category", "unknown")
        confidence = classification.get("confidence", 0.0)

        return f"""## Financial Impact Assessment Report

**News**: {title}
**ID**: `{news_id}`
**Category**: {category} (confidence {confidence:.1%})
**Impact Level**: **{level}** (score {final:.1f}/100)

### DK-CoT Four-Dimensional Scoring

| Dimension | Score | Weight |
|-----------|-------|--------|
| Stock Relevance | {scores.stock_relevance:.1f} | {self.weights['stock_relevance']:.0%} |
| Market Sentiment | {scores.market_sentiment:.1f} | {self.weights['market_sentiment']:.0%} |
| Policy Risk | {scores.policy_risk:.1f} | {self.weights['policy_risk']:.0%} |
| Spread Breadth | {scores.spread_breadth:.1f} | {self.weights['spread_breadth']:.0%} |
| **Weighted Total** | **{final:.1f}** | |

### Reasoning Process

```
{reasoning}
```

### Trend Context

- Trend direction: {trend_dir}
- Window average impact: {trend_avg:.2f}
"""

    # ── Visualization suggestions ─────────────────────────────────────────────

    def _suggest_visualizations(
        self, scores: DKCoTScores, trend: dict | None
    ) -> list[str]:
        suggestions = [
            "Radar chart: four-dimensional score comparison (stock_relevance / market_sentiment / policy_risk / spread_breadth)",
        ]
        if trend and trend.get("total_news_count", 0) > 3:
            suggestions.append(
                f"Time-series line chart: topic impact trend (direction={trend.get('trend_direction', 'stable')}, "
                f"{trend.get('total_news_count', 0)} news items in window)"
            )
        suggestions.append("Bar chart: impact score distribution for same-topic news")
        return suggestions
