"""Async PostgreSQL state for the partner_message_summarisation pipeline.

Mirrors the single-connection style of ``gmail_calendar/db.py`` (one
connection guarded by a lock, raw SQL, idempotent ``CREATE TABLE IF NOT
EXISTS`` schema init) but over psycopg 3 async instead of SQLite.

Tables:
  * ``tg_chats``      — monitored partner groups discovered by title regex
  * ``tg_messages``   — archived messages; UNIQUE (chat_id, message_id) dedup
  * ``summary_runs``  — one row per summary run (audit + window bookkeeping)

The DSN is never logged — only a redacted ``host/dbname`` form.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg.rows import dict_row

from .config import DATABASE_URL
from .timeutil import ensure_utc

logger = logging.getLogger(__name__)

ADVISORY_LOCK_KEY = "partner_message_summarisation_daily_summary"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tg_chats (
    chat_id      BIGINT PRIMARY KEY,
    title        TEXT NOT NULL,
    chat_type    TEXT NOT NULL,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tg_messages (
    id                  BIGSERIAL PRIMARY KEY,
    chat_id             BIGINT NOT NULL REFERENCES tg_chats(chat_id),
    chat_title          TEXT,
    message_id          BIGINT NOT NULL,
    sender_user_id      BIGINT,
    sender_username     TEXT,
    sender_display_name TEXT,
    message_text        TEXT,
    caption             TEXT,
    message_date        TIMESTAMPTZ NOT NULL,
    reply_to_message_id BIGINT,
    is_forward          BOOLEAN NOT NULL DEFAULT FALSE,
    forward_from_name   TEXT,
    forward_from_chat_id   BIGINT,
    forward_from_chat_title TEXT,
    forward_date        TIMESTAMPTZ,
    media_type          TEXT,
    media_file_id       TEXT,
    media_file_name     TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','completed')),
    collected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ,
    summary_run_id      BIGINT,
    CONSTRAINT uq_tg_messages_chat_message UNIQUE (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_tg_messages_status_date
    ON tg_messages (status, message_date);

CREATE TABLE IF NOT EXISTS summary_runs (
    id               BIGSERIAL PRIMARY KEY,
    window_start     TIMESTAMPTZ NOT NULL,
    window_end       TIMESTAMPTZ NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running'
                     CHECK (status IN ('running','success','failed')),
    message_count    INTEGER,
    chat_count       INTEGER,
    llm_provider     TEXT,
    llm_latency_ms   INTEGER,
    report_parts     INTEGER,
    error            TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ
);
"""


def redact_dsn(dsn: str) -> str:
    """Return a log-safe form of the DSN: scheme://host[:port]/dbname."""
    try:
        parsed = urlparse(dsn)
        netloc = parsed.hostname or "?"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse((parsed.scheme or "postgres", netloc, parsed.path or "", "", "", ""))
    except Exception:  # noqa: BLE001 — redaction must never raise
        return "<redacted>"


class PartnerMessageSummarisationDB:
    """Single async connection guarded by an asyncio.Lock.

    A pool is unnecessary at our concurrency (ingest + one daily run share
    one loop); mirroring the repo's one-connection convention keeps the
    advisory-lock semantics trivially correct (session-scoped lock lives on
    the one connection the run uses).
    """

    def __init__(self, dsn: str = DATABASE_URL) -> None:
        self._dsn = dsn
        self._conn: psycopg.AsyncConnection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle

    async def connect(self) -> None:
        async with self._lock:
            if self._conn is not None and not self._conn.closed:
                return
            self._conn = await psycopg.AsyncConnection.connect(
                self._dsn, row_factory=dict_row, autocommit=True
            )
            logger.info(
                "Connected to Postgres at %s", redact_dsn(self._dsn)
            )
            await self._init_schema()

    async def _ensure_conn(self) -> psycopg.AsyncConnection:
        if self._conn is None or self._conn.closed:
            await self.connect()
        assert self._conn is not None
        return self._conn

    async def _init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.execute(_SCHEMA_SQL)
        logger.info("Schema initialized (idempotent)")

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None and not self._conn.closed:
                await self._conn.close()
            self._conn = None

    async def _execute(self, sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
        conn = await self._ensure_conn()
        async with self._lock:
            cur = await conn.execute(sql, params)
            try:
                if cur.description is not None:
                    return list(await cur.fetchall())
                return []
            finally:
                await cur.close()

    # ----------------------------------------------------------------- chats

    async def upsert_chat(self, chat_id: int, title: str, chat_type: str) -> None:
        await self._execute(
            """
            INSERT INTO tg_chats (chat_id, title, chat_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET
                title = excluded.title,
                chat_type = excluded.chat_type,
                is_active = TRUE,
                last_seen = now()
            """,
            (chat_id, title, chat_type),
        )

    async def set_chat_active(self, chat_id: int, is_active: bool) -> None:
        await self._execute(
            "UPDATE tg_chats SET is_active = %s WHERE chat_id = %s",
            (is_active, chat_id),
        )

    async def bump_chat_last_seen(self, chat_id: int) -> None:
        await self._execute(
            "UPDATE tg_chats SET last_seen = now() WHERE chat_id = %s",
            (chat_id,),
        )

    async def get_last_collected_message_id(self, chat_id: int) -> int | None:
        rows = await self._execute(
            """
            SELECT MAX(message_id) AS last_id FROM tg_messages
            WHERE chat_id = %s
            """,
            (chat_id,),
        )
        return rows[0]["last_id"] if rows else None

    async def active_chats(self) -> list[dict[str, Any]]:
        return await self._execute(
            "SELECT chat_id, title, chat_type, last_seen FROM tg_chats "
            "WHERE is_active ORDER BY title"
        )

    # -------------------------------------------------------------- messages

    async def insert_message(self, row: dict[str, Any]) -> bool:
        """Insert one message. Returns True when new, False on duplicate.

        Defaults to ``status = 'pending'``; callers may override via the row
        (``backfill`` inserts history as ``completed``/already-seen). Naive
        datetimes (Pyrogram gives naive UTC) are normalized to UTC-aware
        so ``TIMESTAMPTZ`` storage never depends on the DB session zone.
        """
        status = row.get("status", "pending")
        row = {
            **row,
            "message_date": ensure_utc(row["message_date"]),
            "forward_date": (
                ensure_utc(row["forward_date"])
                if row.get("forward_date") is not None
                else None
            ),
        }
        rows = await self._execute(
            """
            INSERT INTO tg_messages (
                chat_id, chat_title, message_id,
                sender_user_id, sender_username, sender_display_name,
                message_text, caption, message_date, reply_to_message_id,
                is_forward, forward_from_name, forward_from_chat_id,
                forward_from_chat_title, forward_date,
                media_type, media_file_id, media_file_name,
                status, processed_at
            ) VALUES (
                %(chat_id)s, %(chat_title)s, %(message_id)s,
                %(sender_user_id)s, %(sender_username)s, %(sender_display_name)s,
                %(message_text)s, %(caption)s, %(message_date)s, %(reply_to_message_id)s,
                %(is_forward)s, %(forward_from_name)s, %(forward_from_chat_id)s,
                %(forward_from_chat_title)s, %(forward_date)s,
                %(media_type)s, %(media_file_id)s, %(media_file_name)s,
                %(status)s, %(processed_at)s
            )
            ON CONFLICT ON CONSTRAINT uq_tg_messages_chat_message DO NOTHING
            RETURNING id
            """,
            {
                "status": status,
                "processed_at": row.get("processed_at"),
                "chat_id": row["chat_id"],
                "chat_title": row.get("chat_title"),
                "message_id": row["message_id"],
                "sender_user_id": row.get("sender_user_id"),
                "sender_username": row.get("sender_username"),
                "sender_display_name": row.get("sender_display_name"),
                "message_text": row.get("message_text"),
                "caption": row.get("caption"),
                "message_date": row["message_date"],
                "reply_to_message_id": row.get("reply_to_message_id"),
                "is_forward": bool(row.get("is_forward", False)),
                "forward_from_name": row.get("forward_from_name"),
                "forward_from_chat_id": row.get("forward_from_chat_id"),
                "forward_from_chat_title": row.get("forward_from_chat_title"),
                "forward_date": row.get("forward_date"),
                "media_type": row.get("media_type"),
                "media_file_id": row.get("media_file_id"),
                "media_file_name": row.get("media_file_name"),
            },
        )
        return bool(rows)

    async def fetch_pending(self) -> list[dict[str, Any]]:
        """All pending messages, per chat chronologically (§11 batch)."""
        return await self._execute(
            """
            SELECT m.*, COALESCE(m.chat_title, c.title) AS chat_title,
                   c.title AS chat_title_current
            FROM tg_messages m
            JOIN tg_chats c ON c.chat_id = m.chat_id
            WHERE m.status = 'pending'
            ORDER BY m.chat_id, m.message_date, m.message_id
            """
        )

    async def mark_completed(self, ids: list[int], summary_run_id: int) -> None:
        if not ids:
            return
        await self._execute(
            """
            UPDATE tg_messages
            SET status = 'completed', processed_at = now(),
                summary_run_id = %s
            WHERE id = ANY(%s)
            """,
            (summary_run_id, ids),
        )

    async def pending_stats(self) -> dict[str, Any]:
        """Backlog counts for the status CLI: total, per chat, oldest age."""
        total_rows = await self._execute(
            """
            SELECT COUNT(*) AS n, MIN(message_date) AS oldest
            FROM tg_messages WHERE status = 'pending'
            """
        )
        per_chat = await self._execute(
            """
            SELECT chat_id, COALESCE(chat_title, '') AS chat_title,
                   COUNT(*) AS n, MIN(message_date) AS oldest
            FROM tg_messages WHERE status = 'pending'
            GROUP BY chat_id, chat_title ORDER BY n DESC
            """
        )
        return {
            "total": total_rows[0]["n"] if total_rows else 0,
            "oldest": total_rows[0]["oldest"] if total_rows else None,
            "per_chat": per_chat,
        }

    # ------------------------------------------------------------------ runs

    async def start_run(self, window_start, window_end) -> int:
        rows = await self._execute(
            """
            INSERT INTO summary_runs (window_start, window_end, status)
            VALUES (%s, %s, 'running')
            RETURNING id
            """,
            (window_start, window_end),
        )
        return int(rows[0]["id"])

    async def succeed_run(
        self,
        run_id: int,
        *,
        message_count: int,
        chat_count: int,
        llm_provider: str | None,
        llm_latency_ms: int | None,
        report_parts: int | None,
    ) -> None:
        await self._execute(
            """
            UPDATE summary_runs
            SET status = 'success', completed_at = now(),
                message_count = %s, chat_count = %s,
                llm_provider = %s, llm_latency_ms = %s, report_parts = %s,
                error = NULL
            WHERE id = %s
            """,
            (message_count, chat_count, llm_provider, llm_latency_ms, report_parts, run_id),
        )

    async def fail_run(self, run_id: int, error: str) -> None:
        await self._execute(
            """
            UPDATE summary_runs
            SET status = 'failed', completed_at = now(), error = %s
            WHERE id = %s
            """,
            (error[:2000], run_id),
        )

    async def get_last_success_window_end(self):
        rows = await self._execute(
            """
            SELECT window_end FROM summary_runs
            WHERE status = 'success'
            ORDER BY window_end DESC LIMIT 1
            """
        )
        return rows[0]["window_end"] if rows else None

    async def last_runs(self, limit: int = 7) -> list[dict[str, Any]]:
        return await self._execute(
            """
            SELECT id, window_start, window_end, status, message_count,
                   chat_count, llm_provider, llm_latency_ms, report_parts,
                   error, started_at, completed_at
            FROM summary_runs ORDER BY id DESC LIMIT %s
            """,
            (limit,),
        )

    # --------------------------------------------------------- advisory lock

    async def try_advisory_lock(self) -> bool:
        rows = await self._execute(
            "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
            (ADVISORY_LOCK_KEY,),
        )
        return bool(rows and rows[0]["acquired"])

    async def unlock_advisory(self) -> None:
        await self._execute(
            "SELECT pg_advisory_unlock(hashtext(%s))",
            (ADVISORY_LOCK_KEY,),
        )
