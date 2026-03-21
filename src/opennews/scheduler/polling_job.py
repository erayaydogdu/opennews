from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from opennews.config import settings
from opennews.db import ensure_schema as ensure_pg_schema
from opennews.ingest.sources import SourcesConfig
from opennews.workflow.langgraph_pipeline import run_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("opennews.scheduler")


def job() -> None:
    try:
        result = run_once()
        logger.info("pipeline success: %s", result)
    except Exception as e:
        logger.exception("pipeline failed: %s", e)


def start_scheduler() -> None:
    # Ensure config file exists on startup (auto-creates default if missing)
    SourcesConfig.load(settings.sources_config_path)

    # Create tables immediately on startup to ensure PG schema is ready (independent of pipeline data)
    try:
        ensure_pg_schema()
    except Exception:
        logger.exception("failed to ensure PG schema on startup")

    scheduler = BlockingScheduler()
    scheduler.add_job(job, "interval", minutes=settings.poll_interval_minutes)
    logger.info("scheduler started, interval=%s min", settings.poll_interval_minutes)
    job()  # Run one round immediately on startup
    scheduler.start()
