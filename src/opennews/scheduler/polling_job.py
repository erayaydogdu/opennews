from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from opennews.config import settings
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
    scheduler = BlockingScheduler()
    scheduler.add_job(job, "interval", minutes=settings.poll_interval_minutes)
    logger.info("scheduler started, interval=%s min", settings.poll_interval_minutes)
    job()  # 启动时先跑一轮
    scheduler.start()
