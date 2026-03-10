"""Classifier Agent — 零样本新闻分类（金融/政策/公司事件等）。

使用 DeBERTa-v3-base-mnli 做 zero-shot classification，
输出类别 + 置信度。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from transformers import pipeline

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ClassificationResult:
    """单条新闻的分类结果。"""
    category: str          # 最高置信度类别
    confidence: float      # 对应置信度 0-1
    all_scores: dict[str, float]  # 所有候选类别 → 置信度


class ClassifierAgent:
    """零样本新闻分类 Agent。

    Prompt 设计思路（论文依据 LLM-Assisted News Discovery）：
    - 候选标签映射到金融领域语义：
      financial_market  → 金融市场动态
      policy_regulation → 政策法规变动
      company_event     → 公司事件（财报/并购/人事）
      macro_economy     → 宏观经济指标
      industry_trend    → 行业趋势
    """

    # 候选标签 → 自然语言假设模板（提升 NLI 零样本效果）
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
        # 构建假设模板列表（与 candidate_labels 顺序对齐）
        self._hypothesis_template = "This text is about {}."
        logger.info("loading classifier model: %s", model_name)
        self._clf = pipeline(
            "zero-shot-classification",
            model=model_name,
            device=-1,  # CPU，可改为 0 用 GPU
        )

    def classify(self, text: str) -> ClassificationResult:
        """对单条新闻文本做零样本分类。"""
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
        """批量分类。"""
        if not texts:
            return []
        results = self._clf(
            texts,
            candidate_labels=self.candidate_labels,
            hypothesis_template=self._hypothesis_template,
            multi_label=False,
        )
        # 单条时 pipeline 返回 dict 而非 list
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
