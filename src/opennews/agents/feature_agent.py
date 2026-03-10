"""Feature Agent — 7 维新闻价值特征提取 + 影响分计算。

论文依据：LLM-Assisted News Discovery 新闻价值编码。
7 维特征：
  1. market_impact    — 市场影响程度
  2. price_signal     — 价格信号强度
  3. regulatory_risk  — 监管风险
  4. timeliness       — 时效性
  5. impact           — 事件影响力
  6. controversy      — 争议性
  7. generalizability — 可推广性（加权 ×2）

每维 1-5 分，impact_score = 加权平均（generalizability 权重 ×2）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from transformers import pipeline

logger = logging.getLogger(__name__)

# ── 特征维度定义 ──────────────────────────────────────────────
FEATURE_DIMS = [
    "market_impact",
    "price_signal",
    "regulatory_risk",
    "timeliness",
    "impact",
    "controversy",
    "generalizability",
]

# 加权系数（generalizability ×2，其余 ×1）
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
    """7 维特征 + 综合影响分。"""
    market_impact: float = 1.0
    price_signal: float = 1.0
    regulatory_risk: float = 1.0
    timeliness: float = 1.0
    impact: float = 1.0
    controversy: float = 1.0
    generalizability: float = 1.0
    impact_score: float = 1.0  # 加权平均

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
    """加权平均，generalizability 权重 ×2。"""
    total_w = sum(FEATURE_WEIGHTS.values())
    weighted = sum(features.get(k, 1.0) * w for k, w in FEATURE_WEIGHTS.items())
    return round(weighted / total_w, 2)


# ── NLI 代理评分 ──────────────────────────────────────────────
# 每个维度用一组 NLI 假设来探测强度，
# 将 entailment 概率映射到 1-5 分。

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
    """将 entailment 概率 [0,1] 线性映射到 [1,5]。"""
    return round(1.0 + 4.0 * max(0.0, min(1.0, prob)), 2)


class FeatureAgent:
    """基于 NLI 零样本推理的 7 维特征提取 Agent。

    Prompt 模板（内化为 NLI 假设）：
    ─────────────────────────────────
    你是金融新闻 Feature Agent。
    对以下新闻，按 7 个维度评分（1-5）：
    Timeliness / Impact / Controversy / Generalizability（×2）
    Market Impact / Price Signal / Regulatory Risk
    输出 JSON：{"impact_score": 4.2, "features": {...}}
    ─────────────────────────────────
    实际实现：用 NLI 模型对每个维度假设做 entailment 推理，
    将概率映射为 1-5 分，避免依赖外部 LLM API。
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
        """对单条新闻提取 7 维特征（逐维度推理）。"""
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
        """批量特征提取。

        对每条文本，一次性把 7 个假设作为 candidate_labels 送入 NLI，
        用各假设的得分映射为 1-5 分。比逐维度调用快 7 倍。
        """
        if not texts:
            return []

        hypotheses = list(_DIM_HYPOTHESES.values())
        dim_keys = list(_DIM_HYPOTHESES.keys())

        results = self._nli(
            texts,
            candidate_labels=hypotheses,
            hypothesis_template="This text is about {}.",
            multi_label=True,  # 多标签模式，每个假设独立评分
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
