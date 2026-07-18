"""Tests for the GmailCalendarDB — schema, dedup, reminder window queries."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gmail_calendar.db import GmailCalendarDB


@pytest.fixture
def db(tmp_path: Path) -> GmailCalendarDB:
    return GmailCalendarDB(db_path=str(tmp_path / "test.db"))


def _event(
    *,
    event_id: str = "evt-1",
    calendar_id: str = "primary",
    ical_uid: str = "uid-1@goog",
    title: str = "Weekly Sync",
    start_iso: str,
    end_iso: str,
    meeting_link: str | None = "https://meet.google.com/abc",
) -> dict:
    return {
        "event_id": event_id,
        "calendar_id": calendar_id,
        "ical_uid": ical_uid,
        "title": title,
        "start_time_utc": start_iso,
        "end_time_utc": end_iso,
        "timezone": "UTC",
        "meeting_link": meeting_link,
        "attendees": json.dumps([{"email": "x@y.com"}]),
        "updated_at": "2026-01-01T00:00:00Z",
    }


# -- gmail state -------------------------------------------------------------


def test_gmail_state_singleton_init_and_update(db: GmailCalendarDB) -> None:
    assert db.get_gmail_state() is None
    db.init_gmail_state("100")
    state = db.get_gmail_state()
    assert state["history_id"] == "100"
    db.set_gmail_history_id("200")
    assert db.get_gmail_state()["history_id"] == "200"
    db.set_gmail_watch_expiration("9999999999999")
    assert db.get_gmail_state()["watch_expiration"] == "9999999999999"


def test_init_gmail_state_is_idempotent(db: GmailCalendarDB) -> None:
    db.init_gmail_state("100")
    db.init_gmail_state("999")  # should NOT overwrite an existing row
    assert db.get_gmail_state()["history_id"] == "100"


def test_processed_message_dedup(db: GmailCalendarDB) -> None:
    assert db.is_message_processed("m1") is False
    db.record_message(
        message_id="m1",
        thread_id="t1",
        from_addr="x@y.com",
        subject="hi",
        score=0.8,
        category="high",
        reasoning="personal",
        forwarded=True,
    )
    assert db.is_message_processed("m1") is True

    # Re-recording overwrites (used for re-scoring).
    db.record_message(
        message_id="m1",
        thread_id="t1",
        from_addr="x@y.com",
        subject="hi",
        score=0.2,
        category="low",
        reasoning="revised",
        forwarded=False,
    )


# -- calendar sync state -----------------------------------------------------


def test_calendar_sync_token_roundtrip(db: GmailCalendarDB) -> None:
    assert db.get_calendar_sync_token("primary") is None
    db.set_calendar_sync_token("primary", "token-1")
    assert db.get_calendar_sync_token("primary") == "token-1"
    # Update should replace, not insert duplicate.
    db.set_calendar_sync_token("primary", "token-2")
    assert db.get_calendar_sync_token("primary") == "token-2"


def test_list_and_purge_calendars(db: GmailCalendarDB) -> None:
    db.set_calendar_sync_token("primary", "t")
    db.set_calendar_sync_token("work@x.com", "t")
    assert set(db.list_calendars_seen()) == {"primary", "work@x.com"}
    db.purge_calendar("work@x.com")
    assert db.list_calendars_seen() == ["primary"]


# -- event upsert + reminder window -----------------------------------------


def test_upsert_event_inserts_then_updates(db: GmailCalendarDB) -> None:
    start = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()
    db.upsert_event(_event(start_iso=start, end_iso=end))
    rows = db.get_events_needing_reminder(
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "Weekly Sync"


def test_reminder_marked_sent_excludes_from_next_query(db: GmailCalendarDB) -> None:
    start = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()
    db.upsert_event(_event(start_iso=start, end_iso=end))
    rows = db.get_events_needing_reminder(
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert len(rows) == 1
    db.mark_reminder_sent(
        ical_uid=rows[0]["ical_uid"],
        event_id=rows[0]["event_id"],
        start_time_utc=rows[0]["start_time_utc"],
    )
    rows2 = db.get_events_needing_reminder(
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert rows2 == []


def test_reminder_dedup_across_calendars(db: GmailCalendarDB) -> None:
    """Same iCalUID appearing on two calendars should produce ONE reminder."""
    start = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    db.upsert_event(
        _event(
            event_id="evt-A",
            calendar_id="cal-1",
            ical_uid="shared-uid@goog",
            start_iso=start,
            end_iso=end,
        )
    )
    db.upsert_event(
        _event(
            event_id="evt-B",
            calendar_id="cal-2",
            ical_uid="shared-uid@goog",
            start_iso=start,
            end_iso=end,
        )
    )
    rows = db.get_events_needing_reminder(
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert len(rows) == 1  # deduped
    # Marking via iCalUID should mark BOTH underlying rows.
    db.mark_reminder_sent(
        ical_uid=rows[0]["ical_uid"],
        event_id=rows[0]["event_id"],
        start_time_utc=rows[0]["start_time_utc"],
    )
    rows_after = db.get_events_needing_reminder(
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert rows_after == []


def test_reminder_window_excludes_outside_events(db: GmailCalendarDB) -> None:
    too_early = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    too_late = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    in_window = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    db.upsert_event(
        _event(event_id="past", start_iso=too_early, end_iso=too_early)
    )
    db.upsert_event(
        _event(event_id="future", start_iso=too_late, end_iso=too_late)
    )
    db.upsert_event(
        _event(event_id="in", start_iso=in_window, end_iso=in_window)
    )
    rows = db.get_events_needing_reminder(
        (datetime.now(timezone.utc)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert {r["event_id"] for r in rows} == {"in"}


def test_upsert_rearms_reminder_when_start_moves(db: GmailCalendarDB) -> None:
    """If a meeting's start time changes, reminder_sent should reset to 0."""
    start = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()
    db.upsert_event(_event(start_iso=start, end_iso=end))
    db.mark_reminder_sent(
        ical_uid="uid-1@goog", event_id="evt-1", start_time_utc=start
    )
    # Move the meeting 1 day later.
    new_start = (datetime.now(timezone.utc) + timedelta(days=1, minutes=20)).isoformat()
    new_end = (datetime.now(timezone.utc) + timedelta(days=1, minutes=50)).isoformat()
    db.upsert_event(_event(start_iso=new_start, end_iso=new_end))
    rows = db.get_events_needing_reminder(
        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(days=1, minutes=30)).isoformat(),
    )
    # The WHERE clause already filters reminder_sent = 0, so finding the
    # event here IS the proof that the upsert re-armed it (otherwise the
    # earlier mark_reminder_sent would have excluded it).
    assert len(rows) == 1
    assert rows[0]["event_id"] == "evt-1"
    assert rows[0]["start_time_utc"] == new_start


def test_mark_event_deleted_excludes_from_reminders(db: GmailCalendarDB) -> None:
    start = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()
    db.upsert_event(_event(start_iso=start, end_iso=end))
    db.mark_event_deleted("evt-1", "primary")
    rows = db.get_events_needing_reminder(
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert rows == []


def test_schema_is_reentrant(tmp_path: Path) -> None:
    """Opening the DB twice (e.g. on restart) should not error."""
    path = str(tmp_path / "x.db")
    GmailCalendarDB(db_path=path).close()
    GmailCalendarDB(db_path=path).close()
