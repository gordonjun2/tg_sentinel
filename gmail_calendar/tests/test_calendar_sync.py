"""Tests for calendar_sync: event parsing + meeting link extraction."""

from __future__ import annotations

import pytest

from gmail_calendar.calendar_sync import (
    _extract_meeting_link,
    _parse_event,
)


def _raw_event(
    *,
    event_id: str = "e1",
    summary: str = "Sync",
    hangout_link: str | None = None,
    conference_data: dict | None = None,
    location: str | None = None,
) -> dict:
    return {
        "id": event_id,
        "iCalUID": f"{event_id}@google.com",
        "summary": summary,
        "description": "",
        "start": {"dateTime": "2026-07-18T14:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-07-18T15:00:00Z", "timeZone": "UTC"},
        "organizer": {"email": "alice@example.com"},
        "attendees": [{"email": "bob@example.com", "displayName": "Bob"}],
        "location": location,
        "hangoutLink": hangout_link,
        "conferenceData": conference_data,
        "etag": "etag1",
        "updated": "2026-07-17T00:00:00Z",
    }


def test_parse_event_baseline() -> None:
    parsed = _parse_event(_raw_event(), calendar_id="primary")
    assert parsed["event_id"] == "e1"
    assert parsed["calendar_id"] == "primary"
    assert parsed["ical_uid"] == "e1@google.com"
    assert parsed["title"] == "Sync"
    assert parsed["start_time_utc"] == "2026-07-18T14:00:00Z"
    assert parsed["organizer"] == "alice@example.com"
    assert '"bob@example.com"' in (parsed["attendees"] or "")


def test_parse_event_missing_summary_uses_empty() -> None:
    raw = _raw_event()
    del raw["summary"]
    parsed = _parse_event(raw, calendar_id="primary")
    assert parsed["title"] == ""


@pytest.mark.parametrize(
    "raw_overrides,expected",
    [
        ({"hangout_link": "https://meet.google.com/abc"}, "https://meet.google.com/abc"),
        (
            {
                "conference_data": {
                    "entryPoints": [
                        {"uri": "https://zoom.us/j/123", "entryPointType": "video"}
                    ]
                }
            },
            "https://zoom.us/j/123",
        ),
        # Hangout link takes precedence over conferenceData.
        (
            {
                "hangout_link": "https://meet.google.com/x",
                "conference_data": {
                    "entryPoints": [{"uri": "https://zoom.us/j/999"}]
                },
            },
            "https://meet.google.com/x",
        ),
        ({"location": "Conference Room A"}, None),
        # Location only used if it looks like a video URL.
        (
            {"location": "https://zoom.us/j/12345?pwd=abc"},
            "https://zoom.us/j/12345?pwd=abc",
        ),
        ({"location": "https://example.com/something"}, None),  # not a meeting URL
        ({}, None),  # nothing
    ],
)
def test_extract_meeting_link(raw_overrides, expected) -> None:
    raw = _raw_event(**raw_overrides)
    assert _extract_meeting_link(raw) == expected
