"""OpenNews news source config loader — auto-detects and creates default config on startup."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "sources.yaml"

_DEFAULT_CONTENT = """\
# ═══════════════════════════════════════════════════════════
#  OpenNews — News Source Configuration
#  Auto-detected on startup; created with defaults if missing
# ═══════════════════════════════════════════════════════════

# Currently only supports newsnow-type data sources
# Each url maps to a NewsNow-compatible API endpoint; sources are the channels under that endpoint

newsnow:
  - url: https://newsnow.busiyi.world/api/s/entire
    sources:
      - wallstreetcn-news
"""


@dataclass(slots=True)
class NewsNowEndpoint:
    """Configuration for a single NewsNow API endpoint."""
    url: str
    sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourcesConfig:
    """Top-level news source configuration."""
    newsnow: list[NewsNowEndpoint] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SourcesConfig":
        """Load config from YAML; auto-creates default config if file doesn't exist."""
        if path:
            cfg_path = Path(path)
            # Resolve relative paths from project root
            if not cfg_path.is_absolute():
                cfg_path = _DEFAULT_CONFIG_PATH.parent.parent / cfg_path
        else:
            cfg_path = _DEFAULT_CONFIG_PATH

        if not cfg_path.exists():
            logger.info("sources config not found, creating default at %s", cfg_path)
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(_DEFAULT_CONTENT, encoding="utf-8")

        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        logger.info("loaded sources config from %s", cfg_path)

        endpoints = []
        for item in raw.get("newsnow", []):
            url = item.get("url", "")
            sources = item.get("sources", [])
            if url:
                endpoints.append(NewsNowEndpoint(url=url, sources=sources))

        return cls(newsnow=endpoints)
