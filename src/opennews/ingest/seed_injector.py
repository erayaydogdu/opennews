from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from opennews.ingest.news_fetcher import NewsItem


class RealtimeSeedInjector:
    """从本地 JSONL 注入实时种子新闻，模拟 FinBloom 实时补丁流。"""

    def __init__(self, seed_file: str = "seeds/realtime_seeds.jsonl"):
        self.seed_file = Path(seed_file)
        self.seed_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.seed_file.exists():
            self.seed_file.write_text("", encoding="utf-8")

    def load(self) -> list[NewsItem]:
        items: list[NewsItem] = []
        for line in self.seed_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            published = row.get("published_at")
            published_at = (
                datetime.fromisoformat(published)
                if published
                else datetime.now(timezone.utc)
            )
            items.append(
                NewsItem(
                    news_id=row["news_id"],
                    title=row["title"],
                    content=row.get("content", row["title"]),
                    source=row.get("source", "seed"),
                    url=row.get("url", f"seed://{row['news_id']}"),
                    published_at=published_at,
                )
            )
        return items
