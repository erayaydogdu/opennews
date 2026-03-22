"""PostgreSQL persistence layer — batch data & report storage.

Table structure:
  batches       — one row per pipeline run (batch_id, created_at, record_count)
  batch_records — full analysis result for each news item (JSON, linked to batch_id)
  reports       — Markdown reports & summaries (linked to batch_id)
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from opennews.config import settings

logger = logging.getLogger(__name__)

_pool: pool.SimpleConnectionPool | None = None


def _get_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            host=settings.pg_host,
            port=settings.pg_port,
            user=settings.pg_user,
            password=settings.pg_password,
            database=settings.pg_database,
        )
    return _pool


@contextmanager
def get_conn():
    p = _get_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


# ── Schema ──────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id    SERIAL PRIMARY KEY,
    batch_ts    VARCHAR(20) NOT NULL UNIQUE,   -- YYYYMMDD_HHMMSS_mmm
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    record_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS batch_records (
    id          SERIAL PRIMARY KEY,
    batch_id    INTEGER NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
    news_id     VARCHAR(128),
    payload     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_records_batch ON batch_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_records_news  ON batch_records(news_id);

CREATE TABLE IF NOT EXISTS reports (
    id          SERIAL PRIMARY KEY,
    batch_id    INTEGER NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
    news_id     VARCHAR(128),
    impact_level VARCHAR(16),
    markdown    TEXT,
    summary     JSONB
);
CREATE INDEX IF NOT EXISTS idx_reports_batch ON reports(batch_id);

-- migrations: backward-compatible with existing tables
ALTER TABLE batches ALTER COLUMN batch_ts TYPE VARCHAR(20);

DO $$ BEGIN
    ALTER TABLE batch_records ADD COLUMN news_url TEXT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_batch_records_url ON batch_records(news_url);
"""


def ensure_schema():
    """Create tables (idempotent)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
    logger.info("PostgreSQL schema ensured")


# ── Write ──────────────────────────────────────────────────

def get_existing_urls(urls: list[str]) -> set[str]:
    """Query the set of news URLs that already exist in the database."""
    if not urls:
        return set()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT news_url FROM batch_records WHERE news_url = ANY(%s)",
                (urls,),
            )
            return {row[0] for row in cur.fetchall()}


def insert_batch(batch_ts: str, records: list[dict]) -> int:
    """Insert a batch and all its records (skip URLs that already exist), return batch_id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO batches (batch_ts, record_count) VALUES (%s, %s) RETURNING batch_id",
                (batch_ts, len(records)),
            )
            batch_id = cur.fetchone()[0]

            inserted = 0
            for rec in records:
                news = rec.get("news") or {}
                news_id = news.get("news_id")
                news_url = news.get("url")
                cur.execute(
                    "INSERT INTO batch_records (batch_id, news_id, news_url, payload) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (news_url) DO NOTHING",
                    (batch_id, news_id, news_url, json.dumps(rec, ensure_ascii=False)),
                )
                inserted += cur.rowcount

            # Update actual insert count
            cur.execute(
                "UPDATE batches SET record_count = %s WHERE batch_id = %s",
                (inserted, batch_id),
            )
    logger.info("inserted batch %s (%d/%d records, %d skipped) → batch_id=%d",
                batch_ts, inserted, len(records), len(records) - inserted, batch_id)
    return batch_id


def insert_reports(batch_id: int, reports_data: list[dict]):
    """Insert report summaries."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in reports_data:
                cur.execute(
                    "INSERT INTO reports (batch_id, news_id, impact_level, markdown, summary) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        batch_id,
                        r.get("news_id"),
                        r.get("impact_level"),
                        r.get("markdown"),
                        json.dumps({
                            "news_id": r.get("news_id"),
                            "final_score": r.get("final_score"),
                            "impact_level": r.get("impact_level"),
                            "dk_cot_scores": r.get("dk_cot_scores"),
                            "viz_suggestions": r.get("viz_suggestions"),
                        }, ensure_ascii=False),
                    ),
                )
    logger.info("inserted %d reports for batch_id=%d", len(reports_data), batch_id)


# ── Queries (used by Web Server) ────────────────────────────

def list_batches() -> list[dict]:
    """List all batches (reverse chronological)."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT batch_id, batch_ts, created_at, record_count "
                "FROM batches ORDER BY batch_ts DESC"
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_batch_records(batch_id: int) -> list[dict]:
    """Get all records for a given batch."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM batch_records WHERE batch_id = %s ORDER BY id",
                (batch_id,),
            )
            records = [row[0] for row in cur.fetchall()]
    # Inject batch_id into topic to make topic_id unique across batches
    for rec in records:
        topic = rec.get("topic")
        if topic and "batch_id" not in topic:
            topic["batch_id"] = batch_id
    return records


def get_latest_batch_records() -> list[dict]:
    """Get all records from the latest batch."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT batch_id FROM batches ORDER BY batch_ts DESC LIMIT 1")
            row = cur.fetchone()
            if not row:
                return []
            return get_batch_records(row[0])


def get_batch_id_by_ts(batch_ts: str) -> int | None:
    """Find batch_id by timestamp."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT batch_id FROM batches WHERE batch_ts = %s", (batch_ts,))
            row = cur.fetchone()
            return row[0] if row else None


def get_records_since(
    hours: float,
    page: int = 1,
    per_page: int = 15,
    score_lo: float = 0,
    score_hi: float = 100,
) -> dict:
    """Get records from the last N hours (deduplicated by news_url, keeping latest), paginated by topic.

    Pagination is based on topics after filtering (score_lo/score_hi);
    global stats (total_items / above75 / score_bins / levels) are always based on all data.

    Returns:
        {"items": [...], "page": int, "total_pages": int, ...}
    """
    import math

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (COALESCE(br.news_url, br.id::text)) "
                "br.batch_id, br.payload "
                "FROM batch_records br "
                "JOIN batches b ON br.batch_id = b.batch_id "
                "WHERE b.created_at >= now() - interval '%s hours' "
                "ORDER BY COALESCE(br.news_url, br.id::text), br.id DESC",
                (hours,),
            )
            all_records = []
            for row in cur.fetchall():
                bid, payload = row[0], row[1]
                topic = payload.get("topic")
                if topic and "batch_id" not in topic:
                    topic["batch_id"] = bid
                all_records.append(payload)

    # ── Global stats (unaffected by filters & pagination) ──────────────────
    total_items = len(all_records)
    above75 = sum(1 for r in all_records if (r.get("report") or {}).get("final_score", 0) >= 75)
    score_bins = [0] * 100
    levels: dict[str, int] = {"High": 0, "Medium": 0, "Low": 0}
    for r in all_records:
        report = r.get("report") or {}
        s = report.get("final_score", 0)
        idx = min(99, max(0, int(s)))
        score_bins[idx] += 1
        level = report.get("impact_level")
        if level in levels:
            levels[level] += 1

    # ── Filter by score ───────────────────────────────────────
    filtered = [
        r for r in all_records
        if score_lo <= (r.get("report") or {}).get("final_score", 0) <= score_hi
    ]

    # Group by topic
    groups: dict[str, list[dict]] = {}
    for rec in filtered:
        topic = rec.get("topic") or {}
        tid = topic.get("topic_id", -1)
        bid = topic.get("batch_id", 0)
        key = f"{bid}:{tid}"
        groups.setdefault(key, []).append(rec)

    # Sort by highest score within each topic (descending)
    sorted_keys = sorted(
        groups.keys(),
        key=lambda k: max((r.get("report", {}).get("final_score", 0) for r in groups[k]), default=0),
        reverse=True,
    )

    total_topics = len(sorted_keys)
    total_pages = max(1, math.ceil(total_topics / per_page))
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    page_keys = sorted_keys[start:start + per_page]

    items = []
    for k in page_keys:
        items.extend(groups[k])

    return {
        "items": items,
        "page": page,
        "total_pages": total_pages,
        "total_topics": total_topics,
        "total_items": total_items,
        "above75": above75,
        "score_bins": score_bins,
        "levels": levels,
    }


# ── Retry untranslated labels ─────────────────────────────────────

def get_untranslated_topic_labels(limit: int = 100) -> list[tuple[int, dict]]:
    """Query untranslated topic labels with [EN]/[ZH] prefix.

    Returns:
        [(record_id, {"zh": "...", "en": "..."}), ...]
    """
    sql = """
        SELECT id, payload->'topic'->'label' AS label
        FROM batch_records
        WHERE (payload->'topic'->'label'->>'zh') LIKE '[EN] %%'
           OR (payload->'topic'->'label'->>'en') LIKE '[ZH] %%'
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            return [(row[0], row[1]) for row in cur.fetchall()]


def update_topic_labels(updates: list[tuple[int, dict]]) -> int:
    """Batch update topic labels in batch_records.

    Args:
        updates: [(record_id, {"zh": "...", "en": "..."}), ...]

    Returns:
        Number of rows actually updated
    """
    if not updates:
        return 0
    updated = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for record_id, new_label in updates:
                cur.execute(
                    "UPDATE batch_records "
                    "SET payload = jsonb_set(payload, '{topic,label}', %s::jsonb) "
                    "WHERE id = %s",
                    (json.dumps(new_label, ensure_ascii=False), record_id),
                )
                updated += cur.rowcount
    logger.info("updated %d/%d topic labels in DB", updated, len(updates))
    return updated
