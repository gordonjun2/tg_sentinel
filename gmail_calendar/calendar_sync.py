"""Incremental sync of all calendars on the account via ``events.list``.

For each calendar in ``calendarList``:
  * If we have a stored syncToken → incremental sync via ``events.list(
    syncToken=...)``.
  * Else → full sync of upcoming events (``timeMin=now``).

Handles HTTP 410 "sync token gone" by clearing the token and falling back
to a full sync on the next iteration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.errors import HttpError

from .db import GmailCalendarDB

logger = logging.getLogger(__name__)

# How far ahead the *initial* full sync pulls events. Once a syncToken is
# established, sync is incremental and ignores this.
INITIAL_SYNC_HORIZON_DAYS = 60


def _to_utc_iso(dt_str: str, tz_str: str | None) -> tuple[str, str | None]:
    """Return (utc_iso, original_tz). Accepts RFC3339 'Z' or with offset."""
    if not dt_str:
        return ("", tz_str)
    # Google always returns RFC3339; if it ends with Z it's already UTC.
    return (dt_str, tz_str)


def _extract_meeting_link(event: dict) -> str | None:
    """Pull a video call link out of wherever Google decided to put it."""
    # 1. hangoutLink (legacy Meet)
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    # 2. conferenceData.entryPoints (modern — Meet, Zoom, Teams)
    conf = event.get("conferenceData") or {}
    for entry in conf.get("entryPoints", []):
        uri = entry.get("uri")
        if uri and uri.startswith(("http://", "https://")):
            return uri
    # 3. Location field sometimes holds a Zoom/Teams URL.
    location = event.get("location") or ""
    if location.startswith(("https://", "http://")) and any(
        marker in location.lower()
        for marker in ("zoom.us", "teams.microsoft", "meet.", "webex.")
    ):
        return location
    return None


def _parse_event(raw: dict, calendar_id: str) -> dict[str, Any]:
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    start_dt = start.get("dateTime") or start.get("date") or ""
    end_dt = end.get("dateTime") or end.get("date") or ""
    tz = start.get("timeZone") or end.get("timeZone")
    organizer_obj = raw.get("organizer") or {}
    organizer = organizer_obj.get("email") or organizer_obj.get("displayName")
    attendees = [
        {"email": a.get("email"), "name": a.get("displayName")}
        for a in (raw.get("attendees") or [])
        if a.get("email")
    ]
    return {
        "event_id": raw["id"],
        "calendar_id": calendar_id,
        "ical_uid": raw.get("iCalUID"),
        "title": raw.get("summary") or "",
        "description": raw.get("description") or "",
        "start_time_utc": start_dt,
        "end_time_utc": end_dt,
        "timezone": tz,
        "organizer": organizer,
        "attendees": json.dumps(attendees) if attendees else None,
        "location": raw.get("location"),
        "meeting_link": _extract_meeting_link(raw),
        "etag": raw.get("etag"),
        "updated_at": raw.get("updated") or datetime.now(timezone.utc).isoformat(),
    }


class CalendarSync:
    def __init__(self, calendar_service, db: GmailCalendarDB) -> None:
        self.cal = calendar_service
        self.db = db

    # -- calendar list ------------------------------------------------------

    def list_calendars(self) -> list[dict[str, Any]]:
        try:
            resp = self.cal.calendarList().list().execute()
            return [
                c
                for c in resp.get("items", [])
                if not c.get("hidden") and not c.get("deleted")
            ]
        except HttpError as exc:
            logger.error("calendarList.list failed: %s", exc)
            return []

    # -- per-calendar sync --------------------------------------------------

    def sync_calendar(self, calendar_id: str) -> int:
        """Sync one calendar. Returns count of events touched (upserts+deletes)."""
        sync_token = self.db.get_calendar_sync_token(calendar_id)
        touched = 0
        page_token: str | None = None

        if sync_token:
            try:
                while True:
                    req = self.cal.events().list(
                        calendarId=calendar_id,
                        syncToken=sync_token,
                        pageToken=page_token,
                        singleEvents=True,
                    )
                    resp = req.execute()
                    touched += self._apply_changes(calendar_id, resp.get("items", []))
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        if resp.get("nextSyncToken"):
                            self.db.set_calendar_sync_token(
                                calendar_id, resp["nextSyncToken"]
                            )
                        break
                return touched
            except HttpError as exc:
                if exc.status_code == 410:
                    logger.warning(
                        "syncToken for %s expired (410) — full resync next pass",
                        calendar_id,
                    )
                    self.db.set_calendar_sync_token(calendar_id, "")  # cleared
                    return 0
                raise

        # No token → initial full sync.
        now_iso = datetime.now(timezone.utc).isoformat()
        horizon = (
            datetime.now(timezone.utc) + timedelta(days=INITIAL_SYNC_HORIZON_DAYS)
        ).isoformat()
        while True:
            req = self.cal.events().list(
                calendarId=calendar_id,
                timeMin=now_iso,
                timeMax=horizon,
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
                pageToken=page_token,
            )
            resp = req.execute()
            touched += self._apply_changes(calendar_id, resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                if resp.get("nextSyncToken"):
                    self.db.set_calendar_sync_token(
                        calendar_id, resp["nextSyncToken"]
                    )
                break
        return touched

    def _apply_changes(self, calendar_id: str, items: list[dict]) -> int:
        touched = 0
        for raw in items:
            eid = raw.get("id")
            if not eid:
                continue
            if raw.get("status") == "cancelled":
                self.db.mark_event_deleted(eid, calendar_id)
                touched += 1
                continue
            try:
                parsed = _parse_event(raw, calendar_id)
            except KeyError:
                continue
            self.db.upsert_event(parsed)
            touched += 1
        return touched

    # -- full sweep --------------------------------------------------------

    def sync_all(self) -> dict[str, int]:
        """Sync every calendar on the account. Returns per-calendar counts."""
        result: dict[str, int] = {}
        for cal in self.list_calendars():
            cid = cal.get("id")
            if not cid:
                continue
            try:
                result[cid] = self.sync_calendar(cid)
            except HttpError as exc:
                logger.error("Calendar %s sync failed: %s", cid, exc)
                result[cid] = 0
        # Prune calendars that no longer appear in the calendarList.
        seen = set(result.keys())
        for cid in self.db.list_calendars_seen():
            if cid not in seen:
                logger.info("Purging stale calendar %s", cid)
                self.db.purge_calendar(cid)
        return result
