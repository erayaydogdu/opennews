from __future__ import annotations

from dataclasses import dataclass

from transformers import pipeline


@dataclass(slots=True)
class EntityMention:
    text: str
    label: str
    score: float


class EntityExtractor:
    def __init__(self, model_name: str):
        self.ner = pipeline("ner", model=model_name, aggregation_strategy="simple")

    def extract(self, text: str, min_score: float = 0.5) -> list[EntityMention]:
        raw = self.ner(text)
        entities: list[EntityMention] = []
        for r in raw:
            score = float(r.get("score", 0.0))
            if score < min_score:
                continue
            entities.append(
                EntityMention(
                    text=str(r.get("word", "")).strip(),
                    label=str(r.get("entity_group", "MISC")),
                    score=score,
                )
            )
        return entities
