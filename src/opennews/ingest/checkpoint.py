from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class CheckpointStore:
    def __init__(self, file_path: str):
        self.path = Path(file_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_last_published_at(self) -> datetime | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        raw = data.get("last_published_at")
        if not raw:
            return None
        return datetime.fromisoformat(raw)

    def save_last_published_at(self, dt: datetime) -> None:
        self.path.write_text(
            json.dumps({"last_published_at": dt.isoformat()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
