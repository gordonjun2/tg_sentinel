"""Tests for the meeting reminder window logic."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gmail_calendar.db import GmailCalendarDB
from gmail_calendar.meeting_reminder import MeetingReminder, _to_display


@pytest.fixture
def db(tmp_path: Path) -> GmailCalendarDB:
    return GmailCalendarDB(db_path=str(tmp_path / "r.db"))


def test_to_display_converts_utc_to_sgt() -> None:
    # 14:00 UTC = 22:00 SGT (UTC+8)
    out = _to_display("2026-07-18T14:00:00Z")
    assert "10:00 PM SGT" in out


def test_to_display_handles_date_only() -> None:
    out = _to_display("2026-07-18")
    # midnight UTC = 08:00 AM SGT
    assert "08:00 AM SGT" == out


def test_to_display_handles_garbage() -> None:
    assert _to_display("not-a-date") == "not-a-date"
    assert _to_display("") == ""


def test_run_once_no_due_events_returns_zero(db: GmailCalendarDB) -> None:
    reminder = MeetingReminder(db, lead_minutes=30)
    tg = AsyncMock()
    fired = asyncio.run(reminder.run_once(tg))
    assert fired == 0
    tg.send_message.assert_not_called()


def test_run_once_fires_for_in_window_event(db: GmailCalendarDB) -> None:
    """An event starting in 20 min should trigger a reminder."""
    start = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()
    db.upsert_event(
        {
            "event_id": "e1",
            "calendar_id": "primary",
            "ical_uid": "e1@google.com",
            "title": "Standup",
            "start_time_utc": start,
            "end_time_utc": end,
            "timezone": "UTC",
            "meeting_link": "https://meet.google.com/abc",
            "updated_at": start,
        }
    )

    reminder = MeetingReminder(db, lead_minutes=30)
    tg = AsyncMock()
    tg.send_message.return_value = MagicMock(id=42)
    fired = asyncio.run(reminder.run_once(tg))
    assert fired == 1
    tg.send_message.assert_called_once()
    # Second pass should find nothing (reminder_sent = 1 now).
    fired2 = asyncio.run(reminder.run_once(tg))
    assert fired2 == 0


def test_run_once_skips_event_outside_window(db: GmailCalendarDB) -> None:
    """An event starting in 2 hours is outside a 30-min window."""
    start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=2, minutes=30)).isoformat()
    db.upsert_event(
        {
            "event_id": "e1",
            "calendar_id": "primary",
            "ical_uid": "e1@google.com",
            "title": "Standup",
            "start_time_utc": start,
            "end_time_utc": end,
            "updated_at": start,
        }
    )
    reminder = MeetingReminder(db, lead_minutes=30)
    tg = AsyncMock()
    fired = asyncio.run(reminder.run_once(tg))
    assert fired == 0


def test_run_once_continues_when_send_fails(db: GmailCalendarDB) -> None:
    """If send_message returns None, reminder should retry next pass."""
    start = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()
    db.upsert_event(
        {
            "event_id": "e1",
            "calendar_id": "primary",
            "ical_uid": "e1@google.com",
            "title": "Standup",
            "start_time_utc": start,
            "end_time_utc": end,
            "updated_at": start,
        }
    )
    reminder = MeetingReminder(db, lead_minutes=30)
    tg = AsyncMock()
    tg.send_message.return_value = None  # send failed
    fired = asyncio.run(reminder.run_once(tg))
    assert fired == 0
    # Should NOT be marked sent — retries next iteration.
    rows = db.get_events_needing_reminder(
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert len(rows) == 1
