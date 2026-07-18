"""SQLite-backed state for the gmail_calendar service.

Owns its own database file (default ``gmail_calendar/data/gmail_calendar.db``)
so the existing onboarding ``bot_data.db`` stays untouched.

Tables:
  * ``gmail_sync_state``          — singleton: current historyId + watch expiration
  * ``gmail_processed_messages``  — per-message audit log + dedup
  * ``calendar_events``           — upserted from incremental sync
  * ``calendar_sync_state``       — per-calendar syncToken
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from config import GMAIL_CALENDAR_DB_PATH

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GmailCalendarDB:
    """Thread-safe wrapper around a single SQLite file.

    A single connection guarded by an ``RLock`` — simpler than a pool and
    plenty for our low-concurrency asyncio loops (which all run in one thread
    anyway). ``check_same_thread=False`` is set so async-offloaded calls don't
    explode.
    """

    def __init__(self, db_path: str = GMAIL_CALENDAR_DB_PATH) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            finally:
                cur.close()

    def _init_schema(self) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS gmail_sync_state (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    history_id TEXT NOT NULL,
                    last_synced_at TEXT NOT NULL,
                    watch_expiration TEXT,
                    last_full_sync_at TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS gmail_processed_messages (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    from_addr TEXT,
                    subject TEXT,
                    importance_score REAL,
                    category TEXT,
                    reasoning TEXT,
                    forwarded INTEGER DEFAULT 0,
                    processed_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_events (
                    event_id TEXT,
                    calendar_id TEXT,
                    ical_uid TEXT,
                    title TEXT,
                    description TEXT,
                    start_time_utc TEXT NOT NULL,
                    end_time_utc TEXT NOT NULL,
                    timezone TEXT,
                    organizer TEXT,
                    attendees TEXT,
                    location TEXT,
                    meeting_link TEXT,
                    etag TEXT,
                    reminder_sent INTEGER DEFAULT 0,
                    reminder_sent_at TEXT,
                    updated_at TEXT NOT NULL,
                    deleted INTEGER DEFAULT 0,
                    PRIMARY KEY (event_id, calendar_id)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cal_reminder
                ON calendar_events (reminder_sent, start_time_utc)
                WHERE deleted = 0
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cal_ical
                ON calendar_events (ical_uid, start_time_utc)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_sync_state (
                    calendar_id TEXT PRIMARY KEY,
                    sync_token TEXT,
                    last_synced_at TEXT NOT NULL
                )
                """
            )

    # ------------------------------------------------------------------ gmail

    def get_gmail_state(self) -> dict[str, Any] | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM gmail_sync_state WHERE id = 1"
            ).fetchone()
            return dict(row) if row else None

    def init_gmail_state(self, history_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO gmail_sync_state
                    (id, history_id, last_synced_at)
                VALUES (1, ?, ?)
                """,
                (history_id, _utcnow_iso()),
            )

    def set_gmail_history_id(self, history_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE gmail_sync_state
                SET history_id = ?, last_synced_at = ?
                WHERE id = 1
                """,
                (history_id, _utcnow_iso()),
            )

    def set_gmail_watch_expiration(self, expiration_ms: str | None) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE gmail_sync_state SET watch_expiration = ? WHERE id = 1",
                (expiration_ms,),
            )

    def mark_gmail_full_sync(self) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE gmail_sync_state SET last_full_sync_at = ? WHERE id = 1",
                (_utcnow_iso(),),
            )

    def is_message_processed(self, message_id: str) -> bool:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT 1 FROM gmail_processed_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            return row is not None

    def record_message(
        self,
        message_id: str,
        thread_id: str | None,
        from_addr: str,
        subject: str,
        score: float | None,
        category: str | None,
        reasoning: str | None,
        forwarded: bool,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO gmail_processed_messages
                    (message_id, thread_id, from_addr, subject,
                     importance_score, category, reasoning, forwarded, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    from_addr,
                    subject,
                    score,
                    category,
                    reasoning,
                    1 if forwarded else 0,
                    _utcnow_iso(),
                ),
            )

    # ------------------------------------------------------------- calendar

    def get_calendar_sync_token(self, calendar_id: str) -> str | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT sync_token FROM calendar_sync_state WHERE calendar_id = ?",
                (calendar_id,),
            ).fetchone()
            return row["sync_token"] if row else None

    def set_calendar_sync_token(self, calendar_id: str, token: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO calendar_sync_state (calendar_id, sync_token, last_synced_at)
                VALUES (?, ?, ?)
                ON CONFLICT(calendar_id) DO UPDATE SET
                    sync_token = excluded.sync_token,
                    last_synced_at = excluded.last_synced_at
                """,
                (calendar_id, token, _utcnow_iso()),
            )

    def upsert_event(self, event: dict[str, Any]) -> None:
        cols = (
            event["event_id"],
            event["calendar_id"],
            event.get("ical_uid"),
            event.get("title"),
            event.get("description"),
            event["start_time_utc"],
            event["end_time_utc"],
            event.get("timezone"),
            event.get("organizer"),
            event.get("attendees"),
            event.get("location"),
            event.get("meeting_link"),
            event.get("etag"),
            0,
            event.get("updated_at", _utcnow_iso()),
        )
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO calendar_events
                    (event_id, calendar_id, ical_uid, title, description,
                     start_time_utc, end_time_utc, timezone, organizer,
                     attendees, location, meeting_link, etag,
                     reminder_sent, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, calendar_id) DO UPDATE SET
                    ical_uid = excluded.ical_uid,
                    title = excluded.title,
                    description = excluded.description,
                    start_time_utc = excluded.start_time_utc,
                    end_time_utc = excluded.end_time_utc,
                    timezone = excluded.timezone,
                    organizer = excluded.organizer,
                    attendees = excluded.attendees,
                    location = excluded.location,
                    meeting_link = excluded.meeting_link,
                    etag = excluded.etag,
                    -- if the event moved in time, re-arm the reminder
                    reminder_sent = CASE
                        WHEN calendar_events.start_time_utc = excluded.start_time_utc
                        THEN calendar_events.reminder_sent
                        ELSE 0
                    END,
                    deleted = 0,
                    updated_at = excluded.updated_at
                """,
                cols,
            )

    def mark_event_deleted(self, event_id: str, calendar_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE calendar_events
                SET deleted = 1, updated_at = ?
                WHERE event_id = ? AND calendar_id = ?
                """,
                (_utcnow_iso(), event_id, calendar_id),
            )

    def get_events_needing_reminder(
        self, window_start_iso: str, window_end_iso: str
    ) -> list[dict[str, Any]]:
        """Return events whose start falls in the window and have not been
        reminded yet, deduped by (ical_uid, start_time_utc) so the same meeting
        synced from multiple calendars only fires once.
        """
        with self._cursor() as cur:
            rows = cur.execute(
                """
                SELECT MIN(e.calendar_id) AS calendar_id,
                       e.event_id,
                       MIN(e.ical_uid) AS ical_uid,
                       MIN(e.title) AS title,
                       MIN(e.start_time_utc) AS start_time_utc,
                       MIN(e.end_time_utc) AS end_time_utc,
                       MIN(e.timezone) AS timezone,
                       MIN(e.organizer) AS organizer,
                       MIN(e.location) AS location,
                       MIN(e.meeting_link) AS meeting_link
                FROM calendar_events e
                WHERE e.deleted = 0
                  AND e.reminder_sent = 0
                  AND e.start_time_utc >= ?
                  AND e.start_time_utc <= ?
                GROUP BY COALESCE(e.ical_uid, e.event_id), e.start_time_utc
                ORDER BY e.start_time_utc ASC
                """,
                (window_start_iso, window_end_iso),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_reminder_sent(
        self, ical_uid: str | None, event_id: str, start_time_utc: str
    ) -> None:
        """Mark every row matching the dedup key as reminded."""
        with self._cursor() as cur:
            if ical_uid:
                cur.execute(
                    """
                    UPDATE calendar_events
                    SET reminder_sent = 1, reminder_sent_at = ?
                    WHERE ical_uid = ? AND start_time_utc = ?
                    """,
                    (_utcnow_iso(), ical_uid, start_time_utc),
                )
            # Always also mark by (event_id) as a safety net for events
            # without an ical_uid.
            cur.execute(
                """
                UPDATE calendar_events
                SET reminder_sent = 1, reminder_sent_at = ?
                WHERE event_id = ? AND start_time_utc = ?
                """,
                (_utcnow_iso(), event_id, start_time_utc),
            )

    def list_calendars_seen(self) -> list[str]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT calendar_id FROM calendar_sync_state"
            ).fetchall()
            return [r["calendar_id"] for r in rows]

    def purge_calendar(self, calendar_id: str) -> None:
        """Drop sync state + events for a calendar that has been removed."""
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM calendar_sync_state WHERE calendar_id = ?",
                (calendar_id,),
            )
            cur.execute(
                "DELETE FROM calendar_events WHERE calendar_id = ?",
                (calendar_id,),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# Convenience: decode JSON columns for events pulled out of DB.
def decode_event(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("attendees"):
        try:
            raw["attendees"] = json.loads(raw["attendees"])
        except (TypeError, json.JSONDecodeError):
            raw["attendees"] = []
    return raw
