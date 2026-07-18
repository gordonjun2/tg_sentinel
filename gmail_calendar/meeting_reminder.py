"""Meeting reminder loop.

Every ``REMINDER_CHECK_INTERVAL_SECONDS`` seconds, queries the local event
store for events starting in the next ``REMINDER_LEAD_MINUTES`` minutes that
haven't been reminded yet, and fires off a Telegram reminder.

The DB query groups by ``iCalUID`` so a meeting that appears on multiple
calendars only triggers one reminder.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import GmailCalendarDB, decode_event
from .telegram_notifier import format_meeting_reminder, send_to_admin

logger = logging.getLogger(__name__)

# Best-effort display formatting. Event start times are stored as RFC3339
# strings from the Calendar API. We render them in SGT to match the
# existing reminder format used by luma_reminder.py.
SGT = ZoneInfo("Asia/Singapore")


def _to_display(iso_str: str) -> str:
    """Convert an RFC3339 string to '2:00 PM SGT' style."""
    if not iso_str:
        return ""
    try:
        # Date-only events (no time) come through as 'YYYY-MM-DD'.
        if "T" not in iso_str:
            dt = datetime.fromisoformat(iso_str).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(SGT).strftime("%I:%M %p SGT")
    except ValueError:
        return iso_str


def _to_display_date(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        if "T" not in iso_str:
            dt = datetime.fromisoformat(iso_str).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(SGT).strftime("%d %b %Y")
    except ValueError:
        return iso_str


def _enrich_for_display(event: dict) -> dict:
    event = dict(event)
    event["start_time_display"] = _to_display(event.get("start_time_utc", ""))
    event["end_time_display"] = _to_display(event.get("end_time_utc", ""))
    return event


class MeetingReminder:
    def __init__(self, db: GmailCalendarDB, lead_minutes: int) -> None:
        self.db = db
        self.lead = timedelta(minutes=lead_minutes)

    async def run_once(self, tg_client) -> int:
        """Fire reminders for any events due in the next window. Returns count."""
        now = datetime.now(timezone.utc)
        window_end = now + self.lead
        due = self.db.get_events_needing_reminder(
            now.isoformat(), window_end.isoformat()
        )
        if not due:
            return 0

        fired = 0
        for raw in due:
            event = decode_event(raw)
            event = _enrich_for_display(event)
            text = format_meeting_reminder(event)
            msg_id = await send_to_admin(tg_client, text)
            if msg_id is not None:
                self.db.mark_reminder_sent(
                    ical_uid=event.get("ical_uid"),
                    event_id=event["event_id"],
                    start_time_utc=event["start_time_utc"],
                )
                fired += 1
            else:
                logger.warning(
                    "Reminder send failed for event %s; will retry next pass",
                    event.get("event_id"),
                )
        return fired
