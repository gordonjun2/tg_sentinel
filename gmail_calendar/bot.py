"""Long-running entry point for the Gmail + Calendar integration.

Runs four asyncio loops concurrently, each with independent error isolation
so one failing loop can't take down the others:

  * ``gmail_loop``         — Pub/Sub pull + history.list + LLM scoring (60s)
  * ``calendar_loop``      — incremental events.list sync across all calendars (60s)
  * ``reminder_loop``      — 30-min meeting reminder dispatch (60s)
  * ``watch_renewal_loop`` — users.watch() renewal, daily check

Run with: ``python -m gmail_calendar.bot`` (project root must be on sys.path
so ``config`` and the ``gmail_calendar`` package resolve).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from .auth import get_calendar_service, get_gmail_service
from .calendar_sync import CalendarSync
from .db import GmailCalendarDB
from .gmail_watcher import GmailWatcher, should_renew_watch
from .meeting_reminder import MeetingReminder
from .telegram_notifier import make_client
from config import (
    CALENDAR_POLL_INTERVAL_SECONDS,
    GMAIL_POLL_INTERVAL_SECONDS,
    REMINDER_CHECK_INTERVAL_SECONDS,
    REMINDER_LEAD_MINUTES,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("gmail_calendar.bot")


async def _sleep_with_log(seconds: int, label: str) -> None:
    logger.debug("%s: sleeping %ds", label, seconds)
    await asyncio.sleep(seconds)


async def _run_with_backoff(
    coro_factory,
    interval: int,
    label: str,
) -> None:
    """Run ``coro_factory()`` every ``interval`` seconds with exponential backoff.

    ``coro_factory`` is called to produce a fresh coroutine each iteration.
    If it raises, we back off (capped at 5 minutes) and try again rather than
    killing the loop.
    """
    backoff = min(interval, 10)
    while True:
        try:
            await coro_factory()
            backoff = min(interval, 10)
            await _sleep_with_log(interval, label)
        except Exception as exc:
            # noqa: BLE001 — top-of-loop catch-all is intentional so a single
            # failing loop doesn't take out the others.
            logger.exception("%s iteration failed: %s — backing off %ds", label, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


async def gmail_loop(watcher: GmailWatcher, tg_client) -> None:
    async def _step():
        forwarded = await watcher.run_once(tg_client)
        if forwarded:
            logger.info("Gmail loop forwarded %d important email(s)", forwarded)

    await _run_with_backoff(_step, GMAIL_POLL_INTERVAL_SECONDS, "gmail")


async def calendar_loop(syncer: CalendarSync) -> None:
    async def _step():
        # Sync calls are blocking; offload to a thread so they don't stall
        # the event loop the other loops share.
        result = await asyncio.get_event_loop().run_in_executor(
            None, syncer.sync_all
        )
        total = sum(result.values())
        if total:
            logger.info("Calendar sync touched %d events across %s", total, result)

    await _run_with_backoff(_step, CALENDAR_POLL_INTERVAL_SECONDS, "calendar")


async def reminder_loop(reminder: MeetingReminder, tg_client) -> None:
    async def _step():
        fired = await reminder.run_once(tg_client)
        if fired:
            logger.info("Reminder loop fired %d reminder(s)", fired)

    await _run_with_backoff(_step, REMINDER_CHECK_INTERVAL_SECONDS, "reminder")


async def watch_renewal_loop(watcher: GmailWatcher, db: GmailCalendarDB) -> None:
    """Daily check — renew Gmail watch if it expires within 24h."""

    async def _step():
        if should_renew_watch(db):
            logger.info("Watch renewal due — calling users.watch()")
            await asyncio.get_event_loop().run_in_executor(None, watcher.renew_watch)
        # Sleep 1h between checks; renewal window is 24h so this is plenty.
        await asyncio.sleep(3600)

    # Bootstrap the watch immediately on startup so we don't wait an hour.
    try:
        if should_renew_watch(db):
            logger.info("Initial watch renewal on startup")
            await asyncio.get_event_loop().run_in_executor(None, watcher.renew_watch)
    except Exception as exc:
        logger.warning("Initial watch renewal failed: %s", exc)

    await _run_with_backoff(_step, 3600, "watch_renewal")


async def main() -> None:
    logger.info("gmail_calendar.bot starting at %s", datetime.now(timezone.utc).isoformat())

    db = GmailCalendarDB()
    gmail_svc = get_gmail_service()
    cal_svc = get_calendar_service()

    watcher = GmailWatcher(gmail_svc, db)
    syncer = CalendarSync(cal_svc, db)
    reminder = MeetingReminder(db, lead_minutes=REMINDER_LEAD_MINUTES)

    client = make_client()
    await client.start()
    logger.info("Pyrogram client started — sending to ADMIN_GROUP_ID")

    try:
        await asyncio.gather(
            gmail_loop(watcher, client),
            calendar_loop(syncer),
            reminder_loop(reminder, client),
            watch_renewal_loop(watcher, db),
        )
    finally:
        try:
            await client.stop()
        except Exception:
            pass
        db.close()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
